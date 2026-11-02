"""Skill registry: discovery, frontmatter parsing, ranking, selection.

Pure Python (no QGIS imports) so it can be unit-tested headless.

A skill is a folder containing SKILL.md (YAML frontmatter with `name` and
`description`, optional `keywords`, `always`) plus optional `references/*.md`.
Sources, in override order (later wins on name clash):
  1. skills/vendor/<repo>/<skill>/   third-party, MIT, unmodified
  2. skills/custom/<skill>/          written for this plugin
  3. ~/.qgis_ai_agent/skills/<skill>/ added by the user (UI or git install)
"""
import json
import math
import os
import re
import shutil
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS_DIR = os.path.join(HERE, "skills")
USER_DIR = os.path.expanduser("~/.qgis_ai_agent/skills")

STOP = set("""a an the and or of to in on for with from by is are be this that it as at into per use using make
create build please could would should show display get find give want will can all any some their there here
what which who when where why how than then now just also very much more most over under between through
buat bikin dan atau yang untuk dengan dari di ke pada ini itu saya aku tolong mohon agar supaya
sebuah dalam oleh jadi lalu kemudian juga serta buatkan tampilkan lakukan data layer lapisan
based base saja hanya perlu ada cuma mau inginkan need only just
ください 作成 作って 表示 追加 お願い おねがい したい して です ます だった こと もの よう
について による ため から まで における として あります あり への など また これ それ あれ どれ この その あの どの
ような ように 描画 閲覧 確認""".split())


def parse_frontmatter(text):
    m = re.match(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?(.*)$", text, re.S)
    if not m:
        return {}, text
    lines, body, meta, i = m.group(1).split("\n"), m.group(2), {}, 0
    while i < len(lines):
        mm = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", lines[i].rstrip("\r"))
        i += 1
        if not mm:
            continue
        key, val = mm.group(1), mm.group(2).strip()
        if val in (">", ">-", ">+", "|", "|-", "|+") or val == "":
            block = []
            while i < len(lines) and (lines[i][:1] in (" ", "\t") or not lines[i].strip()):
                block.append(lines[i].strip())
                i += 1
            if val == "":
                meta[key] = "\n".join(block).strip()
            elif val.startswith(">"):
                meta[key] = " ".join(b for b in block if b)
            else:
                meta[key] = "\n".join(block).strip()
        else:
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            meta[key] = val
    return meta, body


def tokens(text):
    low = text.lower()
    latin = re.findall(r"[a-z0-9_]+", low)
    katakana = re.findall(r"[\u30a0-\u30ff]{2,}", low)
    kanji = re.findall(r"[\u4e00-\u9fff]+", low)
    kanji_ngrams = []
    for kw in kanji:
        if len(kw) > 2:
            for i in range(len(kw) - 1):
                kanji_ngrams.append(kw[i:i+2])
    other = re.findall(r"[\u00c0-\u024f\u0400-\u04ff]+", low)
    all_toks = latin + katakana + kanji + kanji_ngrams + other
    return [t for t in all_toks if t not in STOP and len(t) > 1]


class Skill:
    def __init__(self, name, description, path, source, keywords="", always=False, size=0, references=None):
        self.name, self.description, self.path, self.source = name, description, path, source
        self.keywords, self.always, self.size = keywords, always, size
        self.references = references or []

    @property
    def file(self):
        return os.path.join(self.path, "SKILL.md")

    def body(self):
        with open(self.file, encoding="utf-8") as f:
            return parse_frontmatter(f.read())[1].strip()

    def brief(self):
        return {"name": self.name, "source": self.source, "description": self.description[:400],
                "size_chars": self.size, "references": self.references}


class Registry:
    CARTOGRAPHY_SKILLS = (
        "cartographic-symbology-and-styling",
        "thematic-mapping-methods",
        "color-theory-and-palettes",
        "cartographic-typography-and-labeling",
        "cartography-geoviz",
        "natgeo-cartography-style",
        "swiss-relief-cartography",
    )

    LAYOUT_SKILLS = (
        "advanced-map-layouting",
        "qgis-agents-map-generator",
        "map-qa-and-export",
        "qgis-impl-print-layouts",
    )

    META_SKILLS = (
        "qgis-agents-analysis-orchestrator",
        "qgis-core-architecture",
        "reproducible-gis-project",
        "open-map-stack",
    )

    def __init__(self, dirs=None):
        self.dirs = dirs or [os.path.join(SKILLS_DIR, "vendor"), os.path.join(SKILLS_DIR, "custom"), USER_DIR]
        self.mode = "auto"          # auto | manual | off
        self.pinned = set()         # always injected in full
        self.disabled = set()       # never selected or loadable
        self.max_inject = 6
        self.budget_chars = 75000   # total body chars auto-injected (modern LLM context friendly)
        self.skills = {}
        self.extra = {"synonyms": {}, "keywords": {}}
        self._idf = {}
        self.reload()

    # ------------------------------------------------------------ discovery
    def reload(self):
        self.skills = {}
        p = os.path.join(SKILLS_DIR, "keywords_id.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                self.extra = json.load(f)
        for root in self.dirs:
            if not os.path.isdir(root):
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                if "SKILL.md" not in filenames:
                    continue
                dirnames[:] = []  # a skill folder is a leaf
                try:
                    s = self._load(dirpath, root)
                except Exception:
                    continue
                if s:
                    self.skills[s.name] = s
        self._build_index()

    def _load(self, path, root):
        with open(os.path.join(path, "SKILL.md"), encoding="utf-8") as f:
            text = f.read()
        meta, body = parse_frontmatter(text)
        name = meta.get("name") or os.path.basename(path)
        rel = os.path.relpath(root, SKILLS_DIR) if root.startswith(SKILLS_DIR) else "user"
        refs = []
        rd = os.path.join(path, "references")
        if os.path.isdir(rel_ref := rd):
            refs = sorted(f for f in os.listdir(rel_ref) if f.endswith((".md", ".txt", ".json", ".csv")))
        return Skill(name, meta.get("description", "").strip(), path, rel.replace(os.sep, "/"),
                     meta.get("keywords", ""), meta.get("always", "").lower() == "true", len(body), refs)

    def _doc_terms(self, s):
        weighted = {}
        def add(text, w):
            for t in tokens(text):
                weighted[t] = max(weighted.get(t, 0), w)
        add(s.name.replace("-", " "), 3.0)
        add(s.keywords, 3.0)
        add(" ".join(self.extra.get("keywords", {}).get(s.name, [])), 3.0)
        add(s.description, 1.0)
        return weighted

    def _build_index(self):
        self._docs = {n: self._doc_terms(s) for n, s in self.skills.items()}
        df = {}
        for terms in self._docs.values():
            for t in terms:
                df[t] = df.get(t, 0) + 1
        n = max(len(self._docs), 1)
        self._idf = {t: math.log(1 + n / (1 + c)) for t, c in df.items()}

    # ------------------------------------------------------------ query
    def available(self):
        """Skills the agent may see/load under the current mode."""
        if self.mode == "off":
            return {}
        out = {n: s for n, s in self.skills.items() if n not in self.disabled}
        if self.mode == "manual":
            out = {n: s for n, s in out.items() if n in self.pinned}
        return out

    def _expand(self, query):
        q = tokens(query)
        syn = self.extra.get("synonyms", {})
        out = list(q)
        low = query.lower()
        for k, vals in syn.items():  # multi-word keys supported
            if k in low:
                for v in vals:
                    out.extend(tokens(v))
        return out

    def rank(self, query, names=None):
        pool = names if names is not None else self.available()
        q = self._expand(query)
        low = query.lower()
        res = []

        orchestrator_triggers = (
            # English
            "workflow", "orchestrat", "which algorithm", "algorithm selection",
            "chain operations", "architecture", "reproducible", "how to start analysis",
            "workflow plan", "pipeline design", "model builder", "processing chain",
            # Indonesian
            "alur kerja", "alur analisis", "pilih algoritma", "arsitektur qgis", "rantai proses",
            # Japanese
            "ワークフロー", "アルゴリズム選定", "どのアルゴリズム", "処理の流れ", "アーキテクチャ",
            "処理の連鎖", "パイプライン", "モデルビルダー"
        )
        is_orchestrator_intent = any(trig in low for trig in orchestrator_triggers)

        for n in pool:
            s = self.skills[n]
            terms = self._docs.get(n, {})
            score = sum(terms[t] * self._idf.get(t, 1.0) for t in set(q) if t in terms)

            # Direct skill name match
            clean_name = s.name.replace("-", " ")
            if clean_name in low:
                score += 15.0

            # Multi-word phrase matching from keywords and extra keywords
            # Gives huge boost when exact multi-word concepts (e.g. "city planning", "tata ruang", "network accessibility") match
            all_phrases = []
            if s.keywords:
                all_phrases.extend([kw.strip().lower() for kw in s.keywords.split(",") if " " in kw.strip()])
            for extra_kw in self.extra.get("keywords", {}).get(s.name, []):
                if " " in extra_kw.strip():
                    all_phrases.append(extra_kw.strip().lower())

            for phrase in set(all_phrases):
                if phrase and phrase in low:
                    score += 20.0

            # Prefer purpose-built custom skills
            if s.source == "custom" and score > 0:
                score *= 1.4

            # Down-rank generic meta-orchestrator skills if prompt isn't asking for meta orchestration
            if n in self.META_SKILLS and not is_orchestrator_intent:
                score *= 0.1

            res.append((round(score, 3), n))
        res.sort(reverse=True)
        return [(sc, n) for sc, n in res if sc > 0]

    def search(self, query, limit=8):
        r = self.rank(query)[:limit] if query.strip() else [(0, n) for n in sorted(self.available())][:limit]
        return [{**self.skills[n].brief(), "score": sc} for sc, n in r]

    def is_e2e_map_request(self, user_prompt: str) -> bool:
        """Detect whether user prompt requests end-to-end map creation or layout generation."""
        if not user_prompt:
            return False
        low = user_prompt.lower()

        # 1. Multi-language explicit keywords/phrases
        e2e_phrases = (
            # English
            "make map", "create map", "generate map", "produce map", "build map", "design map",
            "print map", "e2e map", "end-to-end map", "end to end map", "hazard map", "thematic map",
            "density map", "choropleth", "susceptibility map", "risk map", "suitability map",
            "planning map", "zoning map", "land use map", "transport map", "transit map",
            "make a map", "create a map", "generate a map", "export map", "map layout", "print layout",
            "wayfinding map", "tourist map", "infographic map", "dashboard map", "cartography",
            "city planning map", "urban planning map", "master plan map", "flood map", "flood risk map",
            "inundation map", "evacuation map", "disaster map", "fire map", "wildfire map",
            "accessibility map", "network map", "transit network map", "isochrone map", "catchment map",
            "service area map", "buffer map", "density heatmap", "point density map", "heat map", "heatmap",
            "terrain map", "relief map", "elevation map", "contour map", "hillshade map", "slope map",
            "topographic map", "topo map", "satellite map", "sentinel map", "landsat map", "land cover map",
            "landcover map", "ndvi map", "site suitability map", "cadastral map", "atlas layout", "print atlas",
            # Indonesian
            "buat peta", "bikin peta", "membuat peta", "peta baru", "desain peta", "cetak peta",
            "layout peta", "tata letak peta", "peta banjir", "peta rawan", "peta risiko", "peta sebaran",
            "peta tematik", "peta bencana", "peta kepadatan", "peta tata ruang", "peta wisata",
            "peta transportasi", "peta jaringan", "peta zonasi", "peta permukiman", "peta fasilitas",
            "peta aksesibilitas", "peta penggunaan lahan",
            # Japanese
            "地図作成", "マップ作成", "地図を作", "マップを作", "ハザードマップ", "コロプレス",
            "印刷レイアウト", "マップデザイン", "地図デザイン", "レイアウト作成", "観光マップ", "案内図",
            "都市計画図", "都市計画マップ", "用途地域図", "土地利用図", "土地利用マップ", "マスタープラン図",
            "洪水ハザードマップ", "浸水想定区域図", "浸水マップ", "洪水マップ", "水害マップ", "防災マップ",
            "土砂災害マップ", "避難マップ", "火災マップ", "森林火災マップ", "交通マップ", "路線図",
            "鉄道マップ", "道路網図", "駅アクセスマップ", "到達圏マップ", "アクセシビリティマップ",
            "等時線マップ", "地形図", "起伏図", "標高マップ", "陰影起伏図", "傾斜区分図",
            "衛星画像マップ", "植生指標マップ", "土地被覆分類図", "土地被覆マップ", "ヒートマップ",
            "点密度マップ", "密度マップ", "階級区分図", "適地マップ", "適地判定図", "立地分析マップ",
            "地図出力", "マップ出力", "図郭作成", "地図帳作成", "カートグラフィ", "地図を作図",
            "主題図作成", "主題図", "統計地図", "鳥瞰図"
        )
        if any(p in low for p in e2e_phrases):
            return True

        # 2. Environmental, disaster, or thematic hazard requests (always an end-to-end mapping task in QGIS)
        hazard_terms = (
            # English
            "hazard", "disaster", "susceptibility", "inundation", "vulnerability", "risk",
            "flood", "flooding", "flash flood", "landslide", "earthquake", "tsunami", "wildfire",
            "forest fire", "burn severity", "smoke", "haze", "air quality", "air pollution",
            "hotspot", "active fire", "drought", "storm surge", "cyclone", "typhoon",
            # Indonesian
            "terdampak", "asap", "kualitas udara", "kebakaran", "polusi", "banjir", "longsor",
            "gempa", "tsunami", "kekeringan", "titik panas", "rawan bencana", "bencana", "kerusakan",
            # Japanese
            "被災", "影響", "被害", "煙", "大気汚染", "空気質", "大気環境", "火災", "森林火災",
            "山火事", "洪水", "浸水", "冠水", "土砂崩れ", "地すべり", "土砂災害", "地震", "津波",
            "干ばつ", "ホットスポット", "熱異常点", "ハザード", "脆弱性", "リスク", "危険度", "災害",
            "水害", "高潮", "台風被害"
        )
        has_hazard = any(h in low for h in hazard_terms)

        # 3. Action verbs combined with spatial/GIS deliverable nouns
        action_match_en = re.search(
            r"\b(make|create|generate|produce|build|design|export|render|map|visualize|model|analyze|plot|draw|draft|compose)\b.{0,35}\b(map|atlas|layout|dashboard|raster|surface|heatmap|heat map|impact|affected|hazard|susceptibility|risk|air quality|smoke|fire|flood|zoning|land use|planning|network|transport|transit|elevation|terrain|dem|slope|satellite|ndvi|coverage|accessibility|suitability)\b",
            low
        )
        if action_match_en:
            return True

        action_match_id = re.search(
            r"\b(buat|bikin|buatkan|bikinkan|susun|rancang|cetak|petakan|tampilkan|visualisasikan|analisis|modelkan)\b.{0,30}\b(peta|atlas|layout|raster|sebaran|dampak|terdampak|rawan|risiko|kualitas udara|asap|kebakaran|polusi|zonasi|dashboard|layer)\b",
            low
        )
        if action_match_id:
            return True

        action_match_ja = re.search(
            r"(作成|作図|作って|描画|出力|印刷|分析|デザイン|可視化|モデル|シミュレーション|生成).{0,20}(地図|マップ|レイアウト|ハザード|分布図|路線図|計画図|主題図|土地利用|用途地域|地形図|等時線|適地|ヒートマップ)|"
            r"(地図|マップ|レイアウト|ハザード|分布図|路線図|計画図|主題図|土地利用|用途地域|地形図|等時線|適地|ヒートマップ).{0,20}(作成|作図|作って|描画|出力|印刷|分析|デザイン|可視化|生成|表示|整飾)",
            low
        )
        if action_match_ja:
            return True

        if has_hazard and any(w in low for w in ("buat", "bikin", "make", "create", "raster", "peta", "map", "layout", "analisis", "tampilkan", "作成", "作図", "表示", "分析", "出力", "マップ", "地図")):
            return True

        if re.search(r"\b(map of|peta dari|peta untuk|peta wilayah)\b", low) or re.search(r"の(地図|マップ|ハザードマップ|都市計画図|分布図|レイアウト)", low):
            return True

        return False

    def select(self, user_prompt, already=()):
        """Choose skills to inject. Returns dict(full=[Skill], hint=[Skill], pinned=[Skill], scores={name: score}, is_e2e=bool)."""
        is_e2e = self.is_e2e_map_request(user_prompt)
        out = {"full": [], "hint": [], "pinned": [], "scores": {}, "is_e2e": is_e2e}
        if self.mode == "off":
            return out
        for n in sorted(self.pinned):
            if n in self.skills and n not in self.disabled and n not in already:
                out["pinned"].append(self.skills[n])
        if self.mode == "manual":
            return out

        ranked = [(sc, n) for sc, n in self.rank(user_prompt) if n not in self.pinned and n not in already]
        for n, s in self.skills.items():
            if s.always and n not in self.disabled and n not in already and n not in self.pinned:
                out["pinned"].append(s)

        # In E2E map tasks: cartography and layout skills are mandatory
        mandatory_names = []
        if is_e2e:
            already_names = set(already) | {s.name for s in out["pinned"]}
            # 1. Best cartography skill (prefer specialized if strongly matched, else foundational symbology & styling)
            carto_candidate = None
            specialized_carto = [n for _, n in ranked if n in self.CARTOGRAPHY_SKILLS and n != "cartographic-symbology-and-styling" and n not in already_names and n not in self.disabled]
            if specialized_carto and dict(ranked).get(specialized_carto[0], 0) >= 15.0:
                carto_candidate = specialized_carto[0]
            elif "cartographic-symbology-and-styling" in self.skills and "cartographic-symbology-and-styling" not in already_names and "cartographic-symbology-and-styling" not in self.disabled:
                carto_candidate = "cartographic-symbology-and-styling"
            elif specialized_carto:
                carto_candidate = specialized_carto[0]

            # 2. Best layout skill (prefer advanced-map-layouting as comprehensive custom authority)
            layout_candidate = None
            if "advanced-map-layouting" in self.skills and "advanced-map-layouting" not in already_names and "advanced-map-layouting" not in self.disabled:
                layout_candidate = "advanced-map-layouting"
            else:
                for _, n in ranked:
                    if n in self.LAYOUT_SKILLS and n not in already_names and n not in self.disabled:
                        layout_candidate = n
                        break

            if carto_candidate:
                mandatory_names.append(carto_candidate)
            if layout_candidate and layout_candidate != carto_candidate:
                mandatory_names.append(layout_candidate)

        if not ranked and not mandatory_names:
            return out

        top = ranked[0][0] if ranked else 5.0
        floor = min(8.0, max(2.0, 0.15 * top)) if ranked else 0.0
        budget = self.budget_chars
        max_items = self.max_inject if not is_e2e else max(self.max_inject, len(mandatory_names) + 3)

        # Determine prioritized selection list
        selection_candidates = []
        seen = set(mandatory_names)

        # Top domain skills from ranking
        for sc, n in ranked:
            if n in self.skills and n not in seen and sc >= floor:
                selection_candidates.append((sc, n))
                seen.add(n)

        # Build injection queue: top domain skills first, then mandatory cartography & layout, then remaining domain skills
        ordered_queue = []
        top_domain = selection_candidates[:2]
        cand_rest = selection_candidates[2:]

        ordered_queue.extend(top_domain)
        for mn in mandatory_names:
            sc = dict(ranked).get(mn, 8.0)
            ordered_queue.append((sc, mn))

        ordered_queue.extend(cand_rest)

        for sc, n in ordered_queue:
            if n not in self.skills:
                continue
            s = self.skills[n]
            out["scores"][n] = sc
            if len(out["full"]) < max_items and s.size <= budget:
                out["full"].append(s)
                budget -= s.size
            else:
                out["hint"].append(s)

        return out

    def load(self, name, reference=None):
        s = self.available().get(name)
        if not s:
            avail = sorted(self.available())
            raise ValueError(f"Skill {name!r} not available (mode={self.mode}). Available: {avail}")
        if reference:
            ref = os.path.normpath(os.path.join(s.path, "references", reference))
            if not ref.startswith(os.path.join(s.path, "references")) or not os.path.isfile(ref):
                raise ValueError(f"No reference {reference!r}. Have: {s.references}")
            with open(ref, encoding="utf-8") as f:
                return {"skill": name, "reference": reference, "content": f.read()}
        return {"skill": name, "content": s.body(), "references": s.references,
                "note": "Code samples use PyQGIS. Translate them into the qgis tools (run_processing, style_*, create_layout...) "
                        "unless exec_pyqgis is enabled."}

    # ------------------------------------------------------------ install
    def install_path(self, src):
        """Copy a skill folder (containing SKILL.md) or a single .md file into the user skills dir."""
        os.makedirs(USER_DIR, exist_ok=True)
        src = os.path.abspath(os.path.expanduser(src))
        if os.path.isfile(src):
            meta, _ = parse_frontmatter(open(src, encoding="utf-8").read())
            name = re.sub(r"[^\w.-]+", "-", meta.get("name") or os.path.splitext(os.path.basename(src))[0]).strip("-")
            dest = os.path.join(USER_DIR, name)
            os.makedirs(dest, exist_ok=True)
            shutil.copyfile(src, os.path.join(dest, "SKILL.md"))
            if not meta.get("name"):  # plain markdown without frontmatter
                text = open(src, encoding="utf-8").read()
                desc = next((l.strip("# ").strip() for l in text.splitlines() if l.strip()), name)
                with open(os.path.join(dest, "SKILL.md"), "w", encoding="utf-8") as f:
                    f.write(f"---\nname: {name}\ndescription: {desc}\n---\n\n{text}")
            installed = [name]
        else:
            installed = []
            for dirpath, dirnames, filenames in os.walk(src):
                if ".git" in dirnames:
                    dirnames.remove(".git")
                if "SKILL.md" in filenames:
                    dirnames[:] = []
                    meta, _ = parse_frontmatter(open(os.path.join(dirpath, "SKILL.md"), encoding="utf-8").read())
                    name = re.sub(r"[^\w.-]+", "-", meta.get("name") or os.path.basename(dirpath)).strip("-")
                    dest = os.path.join(USER_DIR, name)
                    if os.path.exists(dest):
                        shutil.rmtree(dest)
                    shutil.copytree(dirpath, dest, ignore=shutil.ignore_patterns(".git"))
                    installed.append(name)
        if not installed:
            raise ValueError("No SKILL.md found in the selected path")
        self.reload()
        return installed

    def install_git(self, url):
        if not re.match(r"^(https://|git@)[\w./:@~-]+$", url):
            raise ValueError("Only https:// or git@ URLs are accepted")
        tmp = tempfile.mkdtemp(prefix="qgis_ai_skill_")
        try:
            r = subprocess.run(["git", "clone", "--depth", "1", "-q", "--", url, tmp + "/repo"],
                               capture_output=True, text=True, timeout=180)
            if r.returncode:
                raise RuntimeError(r.stderr.strip() or "git clone failed")
            return self.install_path(tmp + "/repo")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def remove_user_skill(self, name):
        s = self.skills.get(name)
        if not s or s.source != "user":
            raise ValueError("Only user-installed skills can be removed")
        shutil.rmtree(s.path)
        self.reload()


REGISTRY = Registry()
