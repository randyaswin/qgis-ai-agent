"""Session management and persistent chat history bound to QGIS projects.

Dual persistence:
1. QGIS Project entries (p.writeEntry / p.readEntry) embedded directly into .qgs / .qgz.
2. Local project store at ~/.qgis_ai_agent/projects/<project_id>/sessions.json
   with an isolated workspace directory ~/.qgis_ai_agent/projects/<project_id>/workspace/
   ensuring CLI processes (agy, claude, gemini, codex) maintain isolated session contexts per project.
"""
import copy
import json
import os
import time
import uuid

from qgis.core import QgsProject

PROJECTS_DIR = os.path.expanduser("~/.qgis_ai_agent/projects")
ENDPOINT_DIR = os.path.expanduser("~/.qgis_ai_agent")
AUTOSAVE_REGISTRY_FILE = os.path.join(ENDPOINT_DIR, "project_autosaves.json")


def record_autosave_info(project_file, project_id=None):
    """Persists recovery metadata strictly bound to this specific saved project file.
    Recovery/autosave is ONLY allowed for projects that have already been saved to disk.
    """
    if not project_file or not os.path.isabs(project_file) or not os.path.exists(project_file):
        return
    try:
        p = QgsProject.instance()
        os.makedirs(ENDPOINT_DIR, exist_ok=True)
        pid = project_id
        if not pid:
            read_pid, ok = p.readEntry("qgis_ai_agent", "project_id")
            pid = read_pid if ok else ""
        norm_path = os.path.realpath(project_file)
        
        registry = {}
        if os.path.exists(AUTOSAVE_REGISTRY_FILE):
            try:
                with open(AUTOSAVE_REGISTRY_FILE, "r", encoding="utf-8") as f:
                    registry = json.load(f)
            except Exception:
                registry = {}
                
        registry[norm_path] = {
            "project_id": pid or "",
            "project_file": norm_path,
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "layer_count": p.count(),
            "layout_count": len(p.layoutManager().printLayouts()),
            "layers": [l.name() for l in p.mapLayers().values()],
            "layouts": [l.name() for l in p.layoutManager().printLayouts()]
        }
        with open(AUTOSAVE_REGISTRY_FILE, "w", encoding="utf-8") as f:
            json.dump(registry, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def get_autosave_info_for_current_project():
    """Returns recovery metadata ONLY if the currently active project is already saved on disk.
    Strictly isolated: Never returns metadata for any other project, and returns None for unsaved/new projects.
    """
    p = QgsProject.instance()
    cur_file = p.fileName()
    if not cur_file or not os.path.isabs(cur_file) or not os.path.exists(cur_file):
        return None
    norm_path = os.path.realpath(cur_file)
    if os.path.exists(AUTOSAVE_REGISTRY_FILE):
        try:
            with open(AUTOSAVE_REGISTRY_FILE, "r", encoding="utf-8") as f:
                registry = json.load(f)
            return registry.get(norm_path)
        except Exception:
            pass
    return None


def get_last_autosave_info():
    """Legacy alias that strictly resolves only for the current project."""
    return get_autosave_info_for_current_project()


class Message:
    def __init__(self, role, text="", html="", model_info="", tool_calls=None, timestamp=None, msg_id=None):
        self.id = msg_id or str(uuid.uuid4())
        self.role = role  # "user" | "assistant" | "tool" | "system"
        self.text = text
        self.html = html
        self.model_info = model_info
        self.tool_calls = tool_calls or []
        self.timestamp = timestamp or time.strftime("%Y-%m-%d %H:%M:%S")

    def to_dict(self):
        return {
            "id": self.id,
            "role": self.role,
            "text": self.text,
            "html": self.html,
            "model_info": self.model_info,
            "tool_calls": self.tool_calls,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            role=d.get("role", "user"),
            text=d.get("text", ""),
            html=d.get("html", ""),
            model_info=d.get("model_info", ""),
            tool_calls=d.get("tool_calls", []),
            timestamp=d.get("timestamp"),
            msg_id=d.get("id"),
        )


class Session:
    def __init__(self, session_id=None, name="Session 1", created_at=None, updated_at=None,
                 cli="", model="", effort="default", messages=None, turns_count=0):
        self.id = session_id or f"sess_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        self.name = name
        self.created_at = created_at or time.strftime("%Y-%m-%d %H:%M:%S")
        self.updated_at = updated_at or self.created_at
        self.cli = cli
        self.model = model
        self.effort = effort
        self.messages = [Message.from_dict(m) if isinstance(m, dict) else m for m in (messages or [])]
        self.turns_count = turns_count or sum(1 for m in self.messages if m.role == "user")

    def add_user_message(self, text, html_card):
        msg = Message(role="user", text=text, html=html_card)
        self.messages.append(msg)
        self.turns_count += 1
        self.updated_at = time.strftime("%Y-%m-%d %H:%M:%S")
        return msg

    def add_assistant_message(self, text, html_card, model_info=""):
        msg = Message(role="assistant", text=text, html=html_card, model_info=model_info)
        self.messages.append(msg)
        self.updated_at = time.strftime("%Y-%m-%d %H:%M:%S")
        return msg

    def add_tool_record(self, name, args, summary="", success=True, html_card="", image=None):
        rec = {
            "name": name,
            "args": args,
            "summary": summary,
            "success": success,
            "has_image": bool(image),
            "timestamp": time.strftime("%H:%M:%S"),
        }
        # Attach to the latest assistant message if available, or create a tool message
        if self.messages and self.messages[-1].role == "assistant":
            self.messages[-1].tool_calls.append(rec)
            if html_card:
                self.messages[-1].html += html_card
        else:
            msg = Message(role="tool", text=f"Tool: {name}", html=html_card, tool_calls=[rec])
            self.messages.append(msg)
        self.updated_at = time.strftime("%Y-%m-%d %H:%M:%S")

    def format_history_for_prompt(self, max_turns=6):
        """Format recent turns into a structured context block for LLM prompt injection."""
        user_turns = []
        current_turn = None

        for m in self.messages:
            if m.role == "user":
                if current_turn:
                    user_turns.append(current_turn)
                current_turn = {"user": m.text, "assistant": "", "tools": []}
            elif m.role in ("assistant", "tool") and current_turn is not None:
                if m.text and not current_turn["assistant"]:
                    current_turn["assistant"] = m.text
                if m.tool_calls:
                    for tc in m.tool_calls:
                        current_turn["tools"].append(f"{tc.get('name')}: {tc.get('summary') or 'done'}")

        if current_turn:
            user_turns.append(current_turn)

        if not user_turns:
            return ""

        recent = user_turns[-max_turns:]
        lines = [f"# Previous Conversation Context for Session: '{self.name}' ({len(recent)} turns)"]
        for idx, t in enumerate(recent, 1):
            lines.append(f"[Turn {idx}]")
            lines.append(f"User: {t['user']}")
            if t['tools']:
                lines.append(f"Actions Taken: {', '.join(t['tools'][:6])}")
            if t['assistant']:
                # Truncate assistant response text if long
                asst_preview = t['assistant'][:300] + ("..." if len(t['assistant']) > 300 else "")
                lines.append(f"Agent: {asst_preview}")
        return "\n".join(lines)

    def export_markdown(self):
        """Export session as a clean readable Markdown report."""
        lines = [
            f"# Chat History: {self.name}",
            f"- **Session ID:** `{self.id}`",
            f"- **Created:** {self.created_at}",
            f"- **Last Updated:** {self.updated_at}",
            f"- **CLI / Model:** {self.cli} ({self.model}) [Effort: {self.effort}]",
            f"- **Total Turns:** {self.turns_count}",
            "\n---\n",
        ]
        for m in self.messages:
            if m.role == "user":
                lines.append(f"### 🧑 User ({m.timestamp})")
                lines.append(m.text)
                lines.append("")
            elif m.role == "assistant":
                lines.append(f"### 🤖 Agent ({m.timestamp})")
                if m.model_info:
                    lines.append(f"*{m.model_info}*")
                if m.text:
                    lines.append(m.text)
                if m.tool_calls:
                    lines.append("\n**Tools Executed:**")
                    for tc in m.tool_calls:
                        lines.append(f"- `{tc.get('name')}`: {tc.get('summary', 'Executed')}")
                lines.append("")
            elif m.role == "tool":
                lines.append(f"**Action:** `{m.text}` ({m.timestamp})")
        return "\n".join(lines)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "cli": self.cli,
            "model": self.model,
            "effort": self.effort,
            "turns_count": self.turns_count,
            "messages": [m.to_dict() for m in self.messages],
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            session_id=d.get("id"),
            name=d.get("name", "Untitled Session"),
            created_at=d.get("created_at"),
            updated_at=d.get("updated_at"),
            cli=d.get("cli", ""),
            model=d.get("model", ""),
            effort=d.get("effort", "default"),
            messages=[Message.from_dict(m) for m in d.get("messages", [])],
            turns_count=d.get("turns_count", 0),
        )


class SessionManager:
    def __init__(self, on_change_callback=None):
        self.on_change = on_change_callback
        self.project_id = None
        self.sessions = {}  # session_id -> Session
        self.active_session_id = None
        self._init_project(trigger_callback=False)

    # ------------------------------------------------------------ project binding
    def _init_project(self, trigger_callback=True):
        p = QgsProject.instance()
        # Bind QGIS project lifecycle signals
        try:
            p.readProject.connect(self.on_project_loaded)
            p.projectSaved.connect(self.on_project_saved)
            p.cleared.connect(self.on_project_cleared)
        except Exception:
            pass
        self.load_project_sessions(trigger_callback=trigger_callback)

    def _trigger_on_change(self):
        if callable(self.on_change):
            try:
                self.on_change()
            except RuntimeError as e:
                if "deleted" in str(e).lower():
                    self.disconnect_signals()
            except Exception:
                pass

    def disconnect_signals(self):
        """Disconnect project lifecycle signals when plugin or dock is unloaded."""
        p = QgsProject.instance()
        try:
            p.readProject.disconnect(self.on_project_loaded)
        except Exception:
            pass
        try:
            p.projectSaved.disconnect(self.on_project_saved)
        except Exception:
            pass
        try:
            p.cleared.disconnect(self.on_project_cleared)
        except Exception:
            pass
        self.on_change = None

    def get_project_id(self):
        p = QgsProject.instance()
        pid, ok = p.readEntry("qgis_ai_agent", "project_id")
        if not ok or not pid:
            pid = f"proj_{uuid.uuid4().hex[:12]}"
            p.writeEntry("qgis_ai_agent", "project_id", pid)
        self.project_id = pid
        return pid

    def get_project_dir(self):
        pid = self.get_project_id()
        pdir = os.path.join(PROJECTS_DIR, pid)
        os.makedirs(pdir, exist_ok=True)
        return pdir

    def get_workspace_dir(self):
        """Dedicated CLI working directory for this project to maintain isolated AI session states."""
        wdir = os.path.join(self.get_project_dir(), "workspace")
        os.makedirs(wdir, exist_ok=True)
        return wdir

    def get_sessions_file(self):
        return os.path.join(self.get_project_dir(), "sessions.json")

    # ------------------------------------------------------------ loading & saving
    def load_project_sessions(self, trigger_callback=True):
        self.sessions = {}
        pid = self.get_project_id()
        p = QgsProject.instance()

        # 1. Try reading embedded session data from QGIS Project
        raw_json, ok = p.readEntry("qgis_ai_agent", "session_manifest")
        loaded_data = None
        if ok and raw_json:
            try:
                loaded_data = json.loads(raw_json)
            except Exception:
                loaded_data = None

        # 2. Fall back or merge with local disk store
        sfile = self.get_sessions_file()
        if os.path.exists(sfile):
            try:
                with open(sfile, "r", encoding="utf-8") as f:
                    disk_data = json.load(f)
                    if not loaded_data or (disk_data.get("updated_at", "") > loaded_data.get("updated_at", "")):
                        loaded_data = disk_data
            except Exception:
                pass

        if loaded_data and "sessions" in loaded_data:
            for s_dict in loaded_data["sessions"]:
                s = Session.from_dict(s_dict)
                self.sessions[s.id] = s
            self.active_session_id = loaded_data.get("active_session_id")

        # Ensure at least one session exists
        if not self.sessions:
            init_session = Session(name="Session 1 (Initial Setup)")
            self.sessions[init_session.id] = init_session
            self.active_session_id = init_session.id
            self.save_project_sessions()
        elif not self.active_session_id or self.active_session_id not in self.sessions:
            # Pick latest session
            latest = max(self.sessions.values(), key=lambda s: s.updated_at)
            self.active_session_id = latest.id

        if trigger_callback:
            self._trigger_on_change()

    def autosave_qgis_project(self):
        """Auto-saves the active QGIS project to disk across iterations.
        STRICT RULE: Recovery/autosave is ONLY allowed for projects that have ALREADY been saved to disk by the user.
        Never autosave unsaved/blank projects, and never touch/restore other projects.
        """
        try:
            p = QgsProject.instance()
            cur_file = p.fileName()
            # Only autosave if the project has already been saved to a file on disk
            if not cur_file or not os.path.isabs(cur_file) or not os.path.exists(cur_file):
                return
            # Only autosave if project has actual content
            if p.count() == 0 and len(p.layoutManager().printLayouts()) == 0:
                return
            pid = self.get_project_id()
            p.write()
            record_autosave_info(cur_file, pid)
        except Exception:
            pass

    def save_project_sessions(self):
        pid = self.get_project_id()
        data = {
            "project_id": pid,
            "project_file": QgsProject.instance().fileName(),
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "active_session_id": self.active_session_id,
            "sessions": [s.to_dict() for s in self.sessions.values()],
        }

        # 1. Write to local project store
        sfile = self.get_sessions_file()
        try:
            with open(sfile, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

        # 2. Embed into QGIS Project entry
        try:
            manifest_json = json.dumps(data, ensure_ascii=False)
            # Limit embedded JSON size if needed; QGIS can store large strings in project XML
            p = QgsProject.instance()
            p.writeEntry("qgis_ai_agent", "project_id", pid)
            p.writeEntry("qgis_ai_agent", "session_manifest", manifest_json)
        except Exception:
            pass

        # 3. Auto-save QGIS project to disk across iterations
        self.autosave_qgis_project()

    # ------------------------------------------------------------ session lifecycle
    def get_active_session(self):
        if not self.active_session_id or self.active_session_id not in self.sessions:
            if self.sessions:
                self.active_session_id = next(iter(self.sessions.keys()))
            else:
                self.create_session("Session 1")
        return self.sessions[self.active_session_id]

    def create_session(self, name=None, cli="", model="", effort="default"):
        s_count = len(self.sessions) + 1
        s_name = name or f"Session {s_count} ({time.strftime('%b %d, %H:%M')})"
        new_session = Session(name=s_name, cli=cli, model=model, effort=effort)
        self.sessions[new_session.id] = new_session
        self.active_session_id = new_session.id
        self.save_project_sessions()
        self._trigger_on_change()
        return new_session

    def switch_session(self, session_id):
        if session_id in self.sessions:
            self.active_session_id = session_id
            self.save_project_sessions()
            self._trigger_on_change()
            return self.sessions[session_id]
        return None

    def rename_session(self, session_id, new_name):
        if session_id in self.sessions and new_name.strip():
            self.sessions[session_id].name = new_name.strip()
            self.sessions[session_id].updated_at = time.strftime("%Y-%m-%d %H:%M:%S")
            self.save_project_sessions()
            self._trigger_on_change()
            return True
        return False

    def duplicate_session(self, session_id):
        if session_id in self.sessions:
            orig = self.sessions[session_id]
            dup_dict = copy.deepcopy(orig.to_dict())
            dup_dict["id"] = f"sess_{int(time.time())}_{uuid.uuid4().hex[:6]}"
            dup_dict["name"] = f"{orig.name} (Copy)"
            dup_dict["created_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            dup_dict["updated_at"] = dup_dict["created_at"]
            new_s = Session.from_dict(dup_dict)
            self.sessions[new_s.id] = new_s
            self.active_session_id = new_s.id
            self.save_project_sessions()
            self._trigger_on_change()
            return new_s
        return None

    def delete_session(self, session_id):
        if session_id in self.sessions:
            del self.sessions[session_id]
            if not self.sessions:
                # Re-create fresh session so there is always at least one
                fresh = Session(name="Session 1")
                self.sessions[fresh.id] = fresh
                self.active_session_id = fresh.id
            elif self.active_session_id == session_id:
                latest = max(self.sessions.values(), key=lambda s: s.updated_at)
                self.active_session_id = latest.id
            self.save_project_sessions()
            self._trigger_on_change()
            return True
        return False

    # ------------------------------------------------------------ QGIS events
    def on_project_loaded(self):
        """Called when a user opens an existing QGIS project (.qgz / .qgs)."""
        self.load_project_sessions()

    def on_project_saved(self):
        """Called when a user saves the active QGIS project."""
        self.save_project_sessions()

    def on_project_cleared(self):
        """Called when QGIS creates a new project or clears canvas."""
        self.project_id = None
        self.load_project_sessions()
