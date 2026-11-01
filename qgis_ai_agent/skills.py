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
create build buat bikin dan atau yang untuk dengan dari di ke pada ini itu saya aku tolong mohon agar supaya
sebuah dalam oleh jadi lalu kemudian juga serta buatkan tampilkan lakukan data layer lapisan""".split())


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
    return [t for t in re.findall(r"[a-z0-9\u00c0-\uffff]+", text.lower()) if t not in STOP and len(t) > 1]


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
    def __init__(self, dirs=None):
        self.dirs = dirs or [os.path.join(SKILLS_DIR, "vendor"), os.path.join(SKILLS_DIR, "custom"), USER_DIR]
        self.mode = "auto"          # auto | manual | off
        self.pinned = set()         # always injected in full
        self.disabled = set()       # never selected or loadable
        self.max_inject = 3
        self.budget_chars = 14000   # total body chars auto-injected
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
                out.extend(vals)
        return out

    def rank(self, query, names=None):
        pool = names if names is not None else self.available()
        q = self._expand(query)
        low = query.lower()
        res = []
        for n in pool:
            s = self.skills[n]
            terms = self._docs.get(n, {})
            score = sum(terms[t] * self._idf.get(t, 1.0) for t in set(q) if t in terms)
            if s.name.replace("-", " ") in low:
                score += 6
            res.append((round(score, 3), n))
        res.sort(reverse=True)
        return [(sc, n) for sc, n in res if sc > 0]

    def search(self, query, limit=8):
        r = self.rank(query)[:limit] if query.strip() else [(0, n) for n in sorted(self.available())][:limit]
        return [{**self.skills[n].brief(), "score": sc} for sc, n in r]

    def select(self, user_prompt, already=()):
        """Choose skills to inject. Returns dict(full=[Skill], hint=[Skill], pinned=[Skill], scores={name: score})."""
        out = {"full": [], "hint": [], "pinned": [], "scores": {}}
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
        if not ranked:
            return out
        top = ranked[0][0]
        floor = max(2.0, 0.4 * top)
        budget = self.budget_chars
        for sc, n in ranked[: self.max_inject + 2]:
            if sc < floor:
                break
            s = self.skills[n]
            out["scores"][n] = sc
            if len(out["full"]) < self.max_inject and s.size <= budget:
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
