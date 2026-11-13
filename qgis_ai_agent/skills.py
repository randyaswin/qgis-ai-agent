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

from . import paths

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS_DIR = os.path.join(HERE, "skills")
USER_DIR = paths.get_skills_dir()

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


def _bigrams(seq):
    return [" ".join((seq[i], seq[i + 1])) for i in range(len(seq) - 1)]


class Skill:
    def __init__(self, name, description, path, source, keywords="", always=False, size=0, references=None, body_text=None):
        self.name, self.description, self.path, self.source = name, description, path, source
        self.keywords, self.always, self.size = keywords, always, size
        self.references = references or []
        self._body = body_text.strip() if body_text is not None else None

    @property
    def file(self):
        return os.path.join(self.path, "SKILL.md")

    def body(self):
        if self._body is None:
            with open(self.file, encoding="utf-8") as f:
                self._body = parse_frontmatter(f.read())[1].strip()
        return self._body

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

    DEFAULT_MANUAL_PINNED = (
        "cartographic-symbology-and-styling",
        "advanced-map-layouting",
    )

    SUPERSEDED_BY_CUSTOM = {
        "cartography-geoviz": ("cartographic-symbology-and-styling",),
        "qgis-agents-map-generator": ("advanced-map-layouting",),
        "qgis-impl-print-layouts": ("advanced-map-layouting",),
        "qgis-core-coordinate-systems": ("coordinate-systems-and-crs",),
        "qgis-errors-projections": ("coordinate-systems-and-crs",),
        "qgis-impl-georeferencing": ("digitizing-and-georeferencing",),
        "terrain-hydrology": ("hydrology-watershed-and-coastal-analysis", "terrain-relief-map"),
        "geo-data-engineering": ("automated-data-cleaning-and-topology-qa",),
    }

    def __init__(self, dirs=None):
        self.dirs = dirs or [os.path.join(SKILLS_DIR, "vendor"), os.path.join(SKILLS_DIR, "custom"), USER_DIR]
        self.mode = "auto"          # auto | manual | off
        self.pinned = set()         # manually pre-loaded skills (manual mode)
        self.disabled = set()       # never selected or loadable
        self.max_inject = 4
        self.budget_chars = 45000   # bounded token budget for focused skill injection
        self.skills = {}
        self.extra = {"synonyms": {}, "keywords": {}}
        self._idf = {}
        self._phrases = {}
        self._bigrams = {}
        self._prefixes = {}
        self.reload()
        self.load_settings()

    def load_settings(self):
        """Load pinned and disabled skills and mode from QSettings."""
        try:
            from qgis.PyQt.QtCore import QSettings
            s = QSettings()
            saved_pinned = s.value("qgis_ai_agent/pinned_skills", None)
            if saved_pinned is not None:
                if isinstance(saved_pinned, str):
                    self.pinned = set(p.strip() for p in saved_pinned.split(",") if p.strip())
                elif isinstance(saved_pinned, (list, set)):
                    self.pinned = set(str(p).strip() for p in saved_pinned if str(p).strip())
            else:
                self.pinned = set(self.DEFAULT_MANUAL_PINNED)

            saved_disabled = s.value("qgis_ai_agent/disabled_skills", None)
            if saved_disabled is not None:
                if isinstance(saved_disabled, str):
                    self.disabled = set(p.strip() for p in saved_disabled.split(",") if p.strip())
                elif isinstance(saved_disabled, (list, set)):
                    self.disabled = set(str(p).strip() for p in saved_disabled if str(p).strip())

            saved_mode = s.value("qgis_ai_agent/skill_mode", "auto")
            if saved_mode in ("auto", "manual", "off"):
                self.mode = saved_mode
        except Exception:
            if not self.pinned:
                self.pinned = set(self.DEFAULT_MANUAL_PINNED)

    def save_settings(self):
        """Persist pinned and disabled skills and skill mode to QSettings."""
        try:
            from qgis.PyQt.QtCore import QSettings
            s = QSettings()
            s.setValue("qgis_ai_agent/pinned_skills", ",".join(sorted(self.pinned)))
            s.setValue("qgis_ai_agent/disabled_skills", ",".join(sorted(self.disabled)))
            s.setValue("qgis_ai_agent/skill_mode", self.mode)
        except Exception:
            pass

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
        stripped_body = body.strip()
        return Skill(name, meta.get("description", "").strip(), path, rel.replace(os.sep, "/"),
                     meta.get("keywords", ""), meta.get("always", "").lower() == "true",
                     len(stripped_body), refs, body_text=stripped_body)

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

        self._phrases = {}
        self._bigrams = {}
        self._prefixes = {}
        for name, s in self.skills.items():
            extra_kws = self.extra.get("keywords", {}).get(s.name, [])
            raw_phrases = [s.name.replace("-", " ").lower()]
            if s.keywords:
                raw_phrases.extend(kw.strip().lower() for kw in s.keywords.split(",") if kw.strip())
            raw_phrases.extend(kw.strip().lower() for kw in extra_kws if kw.strip())
            self._phrases[name] = tuple(dict.fromkeys(p for p in raw_phrases if " " in p))

            bg = set()
            for chunk in (s.name.replace("-", " "), s.keywords, s.description, *extra_kws):
                if chunk:
                    bg.update(_bigrams(tokens(chunk)))
            self._bigrams[name] = bg

            pref = {}
            for t, w in self._docs[name].items():
                if len(t) >= 6:
                    p5 = t[:5]
                    if w > pref.get(p5, 0.0):
                        pref[p5] = w
            self._prefixes[name] = pref

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

    def rank(self, query, names=None, layer_names=None):
        pool = names if names is not None else self.available()
        q = self._expand(query)
        q_counts = {}
        for t in q:
            q_counts[t] = q_counts.get(t, 0) + 1
        q_bigrams = set(_bigrams(q))
        layer_tokens = set(tokens(" ".join(str(x) for x in (layer_names or ()) if x))) if layer_names else set()
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
            score = sum(
                terms[t] * self._idf.get(t, 1.0) * (1.0 + 0.25 * math.log(cnt))
                for t, cnt in q_counts.items()
                if t in terms
            )

            # Prefix fallback for morphological inflections (tokens >= 6 chars)
            pref_map = self._prefixes.get(n, {})
            if pref_map:
                matched_pref = set()
                for t in q_counts:
                    if len(t) >= 6 and t not in terms:
                        p5 = t[:5]
                        if p5 in pref_map and p5 not in matched_pref:
                            matched_pref.add(p5)
                            score += 0.45 * pref_map[p5] * self._idf.get(t, 1.0)

            # Bigram overlap bonus
            skill_bigrams = self._bigrams.get(n)
            if skill_bigrams and q_bigrams:
                score += len(q_bigrams & skill_bigrams) * 2.5

            # Active project layer context boost (only reinforces existing topical signal)
            if score > 0 and layer_tokens:
                score += 1.5 * sum(1 for lt in layer_tokens if terms.get(lt, 0.0) >= 2.5)

            # Direct skill name match
            clean_name = s.name.replace("-", " ")
            if clean_name in low:
                score += 15.0

            # Precomputed multi-word phrase matching from keywords and extra keywords
            for phrase in self._phrases.get(n, ()):
                if phrase in low:
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

    def is_vague_request(self, user_prompt, layer_names=None):
        """Return True if user prompt is vague / underspecified and requires clarification before skills selection."""
        if not user_prompt or not user_prompt.strip():
            return True
        text = user_prompt.strip()
        words = text.split()
        low = text.lower()

        # 1. Greetings, test, or generic conversational calls
        greetings = {"halo", "hello", "hi", "hey", "help", "bantuan", "tes", "test", "ping", "p"}
        if low in greetings or (len(words) <= 2 and words[0].lower() in greetings):
            return True

        # 2. Direct utility actions on project are never vague (e.g. add basemap, zoom, export, clear)
        direct_commands = ("basemap", "zoom", "select", "pilih", "hapus", "delete", "clear", "simpan", "save", "export", "print")
        if any(dc in low for dc in direct_commands) and len(words) <= 6:
            return False

        # 3. If a known layer in the active project is explicitly referenced, it's not vague
        if layer_names:
            for lyr in layer_names:
                if lyr and lyr.lower() in low:
                    return False

        # 4. Ultra-short generic requests (<= 3 words without direct commands)
        # e.g. "make map", "buat peta", "bikin peta", "create map", "peta risiko", "analisis bencana"
        if len(words) <= 3:
            return True

        # 5. Generic patterns asking for a map without specifics
        generic_patterns = [
            r"^(make|create|generate|produce|build|draw|render)\s+(a\s+)?(map|layout|atlas)$",
            r"^(buat|buatkan|bikin|bikinkan|tolong buat|tolong buatkan)\s+(peta|layout|atlas)$",
            r"^(tampilkan|lihat|tunjukkan)\s+(peta)$",
            r"^(peta\s+risiko|peta\s+bencana|analisis\s+bencana|analisis\s+spasial)$",
            r"^(i\s+want\s+to\s+)?(make|create|build)\s+(a\s+)?map(\s+please)?$",
            r"^(tolong|bisa)?\s*(buatkan|bikin|buat)\s*(saya)?\s*peta\s*(dong)?$",
            r"^(bisa|tolong)?\s*(bantu|tolong)?\s*(saya)?\s*(buat|bikin|membuat)\s*peta\??$",
        ]
        for pat in generic_patterns:
            if re.search(pat, low):
                return True

        # 6. Check for attached references / geospatial file paths / loaded project context
        has_attached_file = (
            "# user attached references / files:" in low
            or bool(re.search(r"\.(?:tif|tiff|png|jpg|jpeg|webp|pdf|shp|gpkg|geojson|kml|csv|las|laz)\b", low))
        )
        has_project_context = bool(layer_names) or has_attached_file

        # 7. Check for named geographic location / boundary
        has_location = has_project_context or bool(re.search(
            r"\b(jakarta|bandung|surabaya|semarang|yogyakarta|jogja|bali|sumatera|sumatra|jawa|java|kalimantan|sulawesi|papua|maluku|aceh|medan|palembang|makassar|ntt|ntb|"
            r"indonesia|malaysia|singapore|tokyo|japan|zurich|switzerland|swiss|usa|europe|asia|"
            r"bbox)\b|"
            r"[-+]?\d{1,3}\.\d+.*[-+]?\d{1,3}\.\d+",
            low
        ))

        # 8. Check for specific GIS deliverable / data source / method keywords
        has_specific_theme = bool(re.search(
            r"\b(banjir|flood|genangan|dem|elevasi|elevation|kontur|contour|relief|hillshade|lereng|slope|curah hujan|rainfall|"
            r"sentinel|landsat|citra|satellite|ndvi|nbr|lst|stac|cog|overture|osm|openstreetmap|"
            r"kepadatan|density|heatmap|penduduk|population|isochrone|jangkauan|buffer|overlay|network|ahp|"
            r"digitasi|digitiz|georeferen|gcp|grid|fishnet|klasifikasi|classification|lulc|zonasi|rtrw|rdtr|persil|parcel|"
            r"sempadan|kdb|klb|rth|twi|strahler|slr|cleanup|cleaning|topologi|topology|sliver|duplikat|duplicate|"
            r"orthomosaic|drone|uav|lidar|bathymetry|batimetri|watershed|das|kriging|idw|interpolasi|interpolation|"
            r"kesesuaian lahan|tutupan lahan|land use|landcover|fasilitas|rumah sakit|sekolah|poi|jalan|roads|sungai|river|"
            r"gempa|tsunami|longsor|landslide|karhutla|wildfire|uhi|kekeringan|drought)\b",
            low
        ))

        is_spatial_action = bool(re.search(r"\b(make|create|generate|build|buat|buatkan|bikin|bikinkan|petakan|tampilkan|hitung|analisis|modelkan|analyze|calculate|peta|map|digitasi|digitize|georeferen|klasifikasi|classify|interpretasi)\b", low))

        # If it asks to create or analyze a map, but lacks either location/context or theme/method, it needs clarification!
        if is_spatial_action and len(words) <= 8:
            if not has_location or not has_specific_theme:
                return True

        return False

    def select(self, user_prompt, already=(), layer_names=None):
        """Choose skills to inject. Returns dict(full=[Skill], hint=[Skill], pinned=[Skill], scores={name: score}, is_e2e=bool)."""
        is_e2e = self.is_e2e_map_request(user_prompt)
        out = {"full": [], "hint": [], "pinned": [], "scores": {}, "is_e2e": is_e2e}
        if self.mode == "off":
            return out

        if self.mode == "auto" and self.is_vague_request(user_prompt, layer_names=layer_names):
            # In Auto mode, if request is vague and needs clarification: do NOT select or inject skills upfront!
            # Skills must be automatically selected AFTER clarification (or before execution if clear).
            out["is_e2e"] = False
            return out
        if self.mode == "manual":
            # In Manual mode: pre-load pinned skills in full; if empty, pre-load foundational baseline
            for n in sorted(self.pinned):
                if n in self.skills and n not in self.disabled and n not in already:
                    out["pinned"].append(self.skills[n])
            if not out["pinned"]:
                for bn in self.DEFAULT_MANUAL_PINNED:
                    if bn in self.skills and bn not in self.disabled and bn not in already:
                        out["pinned"].append(self.skills[bn])
            return out

        # In Auto mode: only skills explicitly marked always=True are automatically pre-loaded
        for n, s in self.skills.items():
            if s.always and n not in self.disabled and n not in already:
                out["pinned"].append(s)

        always_names = {s.name for s in out["pinned"]}
        ranked = [(sc, n) for sc, n in self.rank(user_prompt, layer_names=layer_names) if n not in always_names and n not in already]

        # In E2E map tasks: cartography and layout skills are mandatory
        mandatory_names = []
        if is_e2e:
            already_names = set(already) | always_names
            ranked_scores = dict(ranked)
            # 1. Best cartography skill (prefer specialized if strongly matched, else foundational symbology & styling)
            carto_candidate = None
            specialized_carto = [
                n for _, n in ranked
                if n in self.CARTOGRAPHY_SKILLS and n != "cartographic-symbology-and-styling"
                and n not in already_names and n not in self.disabled
            ]
            if specialized_carto and ranked_scores.get(specialized_carto[0], 0) >= 15.0:
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
        floor = min(10.0, max(2.0, 0.25 * top)) if ranked else 0.0
        budget = self.budget_chars
        max_items = self.max_inject if not is_e2e else max(self.max_inject, len(mandatory_names) + 2)

        # Determine prioritized selection list
        selection_candidates = []
        seen = set(mandatory_names)
        ranked_scores = dict(ranked)

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
            sc = ranked_scores.get(mn, 8.0)
            ordered_queue.append((sc, mn))
        ordered_queue.extend(cand_rest)

        active_customs = (
            set(always_names)
            | set(mandatory_names)
            | {cn for _, cn in selection_candidates[:max_items] if cn in self.skills and self.skills[cn].source == "custom"}
        )

        for sc, n in ordered_queue:
            if n not in self.skills:
                continue
            s = self.skills[n]
            out["scores"][n] = sc
            superseded_by = self.SUPERSEDED_BY_CUSTOM.get(n, ())
            if superseded_by and any(c in active_customs for c in superseded_by):
                out["hint"].append(s)
                continue
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
