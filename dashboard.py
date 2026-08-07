#!/usr/bin/env python3
"""
ClaudeProjects status dashboard.

File convention
---------------
    <Project folder>/00-project-tracker.md          primary tracker (required)
    <Project folder>/00-project-tracker.sub.xx.md   optional sub-trackers

A folder with no primary tracker still appears on the dashboard, with no status
and the message "No tracker file found."

Source of truth: the YAML frontmatter of each tracker file. The "At a glance"
table and "Notes" section in those files are GENERATED into marker regions
(<!-- STATUS:START --> ... / <!-- NOTES:START --> ...). Everything outside
those regions is never touched.

Usage
-----
    python dashboard.py              # live server on http://127.0.0.1:8765
    python dashboard.py --once       # regenerate dashboard.html and exit
    python dashboard.py --no-watch   # server without the file watcher

Cross-platform (macOS / Windows / Linux). Binds to loopback only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import tempfile
import threading
import webbrowser
from pathlib import Path

import yaml

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                      # the ClaudeProjects folder
TEMPLATE = HERE / "template.html"
SNAPSHOT = ROOT / "dashboard.html"
PORT = 8765

PRIMARY_NAME = "00-project-tracker.md"
SUB_GLOB = "00-project-tracker.sub.*.md"
SUB_RE = re.compile(r"^00-project-tracker\.sub\.(.+)\.md$", re.IGNORECASE)
SKIP_DIRS = {"_dashboard", ".git", "node_modules", "__pycache__"}
# Two ways to keep a folder off the dashboard entirely:
#   1. prefix its name with "_" (or "."), e.g. "_Scratch"
#   2. drop an empty marker file called ".no-dashboard" inside it
SKIP_PREFIXES = ("_", ".")
IGNORE_FILE = ".no-dashboard"

# Status vocabulary.
#   todo     — yours to act on, not started ("ready to send", "book now")
#   active   — yours, under way
#   waiting  — you have acted; a third party now owes you a response
#   blocked  — a dependency is unfulfilled, or an external decision/authority
#              is required. NOT for work that is simply not started.
#   done     — complete
STATUS_EMOJI = {
    "todo": "🔵",
    "active": "🟡",
    "waiting": "🟣",
    "blocked": "⚫",
    "done": "🟢",
}
VALID_STATUS = set(STATUS_EMOJI)
STATUS_RANK = {"blocked": 0, "todo": 1, "active": 2, "waiting": 3, "done": 4}
PRIORITIES = {1, 2, 3, 4, 5}
ATTENTION_MAX_PRIORITY = 2

STATUS_START = "<!-- STATUS:START"
STATUS_END = "<!-- STATUS:END -->"
NOTES_START = "<!-- NOTES:START"
NOTES_END = "<!-- NOTES:END -->"
SUBTASKS_START = "<!-- SUBTASKS:START"
SUBTASKS_END = "<!-- SUBTASKS:END -->"

FM_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)

REVISION = {"n": 0, "at": dt.datetime.now().isoformat(timespec="seconds")}
WRITE_LOCK = threading.Lock()
RUNTIME = {"port": PORT}   # so the snapshot's "open live" link matches --port


# ----------------------------------------------------------------------------
# YAML helpers
# ----------------------------------------------------------------------------

def _represent_none(dumper, _):
    return dumper.represent_scalar("tag:yaml.org,2002:null", "")


class TidyDumper(yaml.SafeDumper):
    pass


TidyDumper.add_representer(type(None), _represent_none)

ITEM_KEY_ORDER = ["id", "title", "priority", "status", "next", "blocked_on", "due", "doc",
                  "subtasks", "notes"]
DOC_KEY_ORDER = ["project", "section", "updated", "columns", "items"]


def _ordered(d: dict, order: list[str]) -> dict:
    out = {k: d[k] for k in order if k in d}
    for k, v in d.items():
        if k not in out:
            out[k] = v
    return out


def dump_frontmatter(data: dict) -> str:
    data = _ordered(dict(data), DOC_KEY_ORDER)
    if isinstance(data.get("items"), list):
        data["items"] = [_ordered(dict(i), ITEM_KEY_ORDER) for i in data["items"]]
    return yaml.dump(
        data, Dumper=TidyDumper, sort_keys=False, allow_unicode=True,
        default_flow_style=False, width=10_000,
    )


# ----------------------------------------------------------------------------
# Discovery
# ----------------------------------------------------------------------------

def is_ignored(child: Path) -> bool:
    """A folder is off the dashboard if it is named out, prefixed out, or opts out."""
    return (child.name in SKIP_DIRS
            or child.name.startswith(SKIP_PREFIXES)
            or (child / IGNORE_FILE).exists())


def discover() -> list[dict]:
    """Find every project folder, whether or not it has a tracker."""
    out = []
    for child in sorted(ROOT.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir() or is_ignored(child):
            continue
        primary = child / PRIMARY_NAME
        subs = sorted(child.glob(SUB_GLOB), key=lambda p: p.name.lower())
        out.append({
            "folder": child,
            "primary": primary if primary.is_file() else None,
            "subs": subs,
        })
    return out


def sub_token(path: Path) -> str:
    m = SUB_RE.match(path.name)
    return m.group(1) if m else path.stem


# ----------------------------------------------------------------------------
# Read
# ----------------------------------------------------------------------------

def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def coerce_priority(value):
    """1-5 (1 = highest), or None when unset/invalid."""
    if value in (None, ""):
        return None
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return n if n in PRIORITIES else None


def normalise_subtasks(value) -> list[dict]:
    """Accept [{text, done}] or bare strings; return a clean [{text, done}] list."""
    out = []
    for s in value or []:
        if isinstance(s, str):
            text, done = s, False
        elif isinstance(s, dict):
            text, done = s.get("text"), bool(s.get("done"))
        else:
            continue
        text = str(text or "").strip()
        if text:
            out.append({"text": text, "done": done})
    return out


def subtask_progress(item: dict) -> tuple[int, int]:
    subs = item.get("subtasks") or []
    return sum(1 for s in subs if s.get("done")), len(subs)


def parse_file(path: Path) -> dict | None:
    """Parse one tracker file's frontmatter into a normalised dict."""
    raw = read_text(path)
    m = FM_RE.match(raw)
    if not m:
        return None
    try:
        data = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as exc:
        print(f"  ! YAML error in {path.name}: {exc}", file=sys.stderr)
        return None
    if not isinstance(data, dict):
        return None

    data.setdefault("columns", ["#", "P", "Item", "Status", "The one next action", "Blocked on"])
    norm = []
    for i, item in enumerate(data.get("items") or [], start=1):
        if not isinstance(item, dict):
            continue
        item.setdefault("id", i)
        item.setdefault("title", "(untitled)")
        status = str(item.get("status") or "todo").lower()
        item["status"] = status if status in VALID_STATUS else "todo"
        item["priority"] = coerce_priority(item.get("priority"))
        for k in ("next", "blocked_on", "due", "doc"):
            item.setdefault(k, None)
        item["notes"] = [n for n in (item.get("notes") or [])
                         if isinstance(n, dict) and n.get("text")]
        item["subtasks"] = normalise_subtasks(item.get("subtasks"))
        norm.append(item)
    data["items"] = norm
    data["_path"] = str(path)
    return data


def load_project(entry: dict) -> dict:
    """Build the dashboard model for one project folder."""
    folder: Path = entry["folder"]
    primary: Path | None = entry["primary"]
    subs: list[Path] = entry["subs"]

    if primary is None:
        return {
            "project": folder.name,
            "folder": folder.name,
            "missing": True,
            "message": "No tracker file found.",
            "orphan_subs": len(subs),
            "sections": [],
        }

    pdata = parse_file(primary)
    if pdata is None:
        return {
            "project": folder.name,
            "folder": folder.name,
            "missing": True,
            "message": f"{PRIMARY_NAME} could not be read (missing or invalid frontmatter).",
            "orphan_subs": len(subs),
            "sections": [],
        }

    sections = [{
        "key": "",
        "label": None,
        "source": primary.name,
        "columns": pdata["columns"],
        "items": pdata["items"],
    }]

    for sp in subs:
        sdata = parse_file(sp)
        token = sub_token(sp)
        if sdata is None:
            sections.append({
                "key": token, "label": token, "source": sp.name,
                "columns": pdata["columns"], "items": [], "error": "Could not be read.",
            })
            continue
        sections.append({
            "key": token,
            "label": str(sdata.get("section") or token),
            "source": sp.name,
            "columns": sdata["columns"],
            "items": sdata["items"],
        })

    return {
        "project": str(pdata.get("project") or folder.name),
        "folder": folder.name,
        "updated": str(pdata.get("updated") or ""),
        "missing": False,
        "sections": sections,
    }


def scan() -> dict:
    return {
        "generated": dt.datetime.now().isoformat(timespec="seconds"),
        "revision": REVISION["n"],
        "root": str(ROOT),
        "projects": [load_project(e) for e in discover()],
    }


def resolve_file(project: str, section_key: str) -> Path:
    """Map (project, section) back to the file it came from."""
    for entry in discover():
        if entry["primary"] is None:
            continue
        pdata = parse_file(entry["primary"])
        if pdata is None:
            continue
        name = str(pdata.get("project") or entry["folder"].name)
        if name != project:
            continue
        if not section_key:
            return entry["primary"]
        for sp in entry["subs"]:
            if sub_token(sp) == section_key:
                return sp
        raise KeyError(f"unknown section {section_key!r} in {project!r}")
    raise KeyError(f"unknown project: {project!r}")


# ----------------------------------------------------------------------------
# Render generated markdown regions
# ----------------------------------------------------------------------------

def _cell(value) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def field_for_header(header: str, used: set[str]) -> str:
    """Map a column header to an item field, so headers can be renamed freely."""
    h = str(header).strip().lower()
    if h in ("#", "id", "no", "no."):
        return "id"
    if h in ("p", "pri", "priority"):
        return "priority"
    if h == "status":
        return "status"
    if "blocked" in h:
        return "blocked_on"
    if "due" in h or "date" in h:
        return "due"
    if "action" in h or "next" in h:
        return "next"
    return "title" if "title" not in used else "next"


def render_status_table(data: dict) -> str:
    cols = data.get("columns") or ["#", "P", "Item", "Status", "The one next action"]
    fields, used = [], set()
    for c in cols:
        f = field_for_header(c, used)
        used.add(f)
        fields.append(f)

    lines = ["| " + " | ".join(str(c) for c in cols) + " |", "|" + "---|" * len(cols)]
    for item in data["items"]:
        row = []
        for f in fields:
            if f == "status":
                row.append(STATUS_EMOJI.get(item["status"], "🔵"))
            elif f == "priority":
                row.append("" if item.get("priority") is None else str(item["priority"]))
            elif f == "title":
                cell = _cell(item.get("title"))
                done, total = subtask_progress(item)
                row.append(f"{cell} · {done}/{total}" if total else cell)
            else:
                row.append(_cell(item.get(f)))
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def render_subtasks(data: dict) -> str:
    """Collapsible <details> block per item, with a nested GFM checklist."""
    blocks = []
    for item in data["items"]:
        subs = item.get("subtasks") or []
        if not subs:
            continue
        done, total = subtask_progress(item)
        title = str(item.get("title") or "").replace("**", "").strip()
        lines = [
            "<details>",
            f"<summary><strong>{item.get('id')} · {title}</strong> — {done}/{total} done</summary>",
            "",
        ]
        lines += [f"- [{'x' if s.get('done') else ' '}] {s['text']}" for s in subs]
        lines += ["", "</details>"]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) if blocks else "_No sub-tasks yet._"


def render_notes(data: dict) -> str:
    blocks = []
    for item in data["items"]:
        if not item.get("notes"):
            continue
        heading = re.sub(r"\*\*+", "**", f"**{item.get('id')}. {item.get('title')}**")
        entries = [f"- *{n.get('date','')}* — {str(n['text']).strip()}" for n in item["notes"]]
        blocks.append(heading + "\n" + "\n".join(entries))
    for n in data.get("project_notes") or []:
        blocks.append(f"**Project**\n- *{n.get('date','')}* — {str(n['text']).strip()}")
    return "\n\n".join(blocks) if blocks else "_No notes yet._"


def _replace_region(raw: str, start_tag: str, end_tag: str, body: str) -> str | None:
    i = raw.find(start_tag)
    if i == -1:
        return None
    j = raw.find(end_tag, i)
    if j == -1:
        return None
    open_end = raw.find("-->", i)
    if open_end == -1 or open_end > j:
        return None
    return raw[: open_end + 3] + "\n" + body + "\n" + raw[j:]


def write_file(path: Path, data: dict, touch_updated: bool = True) -> bool:
    """Rewrite ONLY the frontmatter and generated regions. True if changed."""
    raw = read_text(path)
    m = FM_RE.match(raw)
    if not m:
        raise ValueError(f"{path.name} has no frontmatter")

    payload = {k: v for k, v in data.items() if not k.startswith("_")}
    if touch_updated:
        payload["updated"] = dt.date.today().isoformat()
    new_raw = "---\n" + dump_frontmatter(payload) + "---\n" + raw[m.end():]

    replaced = _replace_region(new_raw, STATUS_START, STATUS_END, render_status_table(data))
    if replaced is not None:
        new_raw = replaced

    replaced = _replace_region(new_raw, SUBTASKS_START, SUBTASKS_END, render_subtasks(data))
    if replaced is not None:
        new_raw = replaced
    elif any(i.get("subtasks") for i in data["items"]):
        new_raw = new_raw.rstrip() + (
            "\n\n---\n\n## Sub-tasks\n\n"
            + SUBTASKS_START + " — generated from frontmatter; edits here are overwritten -->\n"
            + render_subtasks(data) + "\n" + SUBTASKS_END + "\n"
        )

    replaced = _replace_region(new_raw, NOTES_START, NOTES_END, render_notes(data))
    if replaced is not None:
        new_raw = replaced
    elif any(i.get("notes") for i in data["items"]) or data.get("project_notes"):
        new_raw = new_raw.rstrip() + (
            "\n\n---\n\n## Notes\n\n"
            + NOTES_START + " — generated from frontmatter; edits here are overwritten -->\n"
            + render_notes(data) + "\n" + NOTES_END + "\n"
        )

    if new_raw == raw:
        return False
    atomic_write(path, new_raw)
    return True


def sync_all() -> list[str]:
    """Idempotently regenerate every tracker's marker regions."""
    changed = []
    with WRITE_LOCK:
        for entry in discover():
            for path in ([entry["primary"]] if entry["primary"] else []) + entry["subs"]:
                data = parse_file(path)
                if not data:
                    continue
                try:
                    if write_file(path, data, touch_updated=False):
                        changed.append(path.name)
                except ValueError as exc:
                    print(f"  ! {path.name}: {exc}", file=sys.stderr)
    return changed


def atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# ----------------------------------------------------------------------------
# Mutations
# ----------------------------------------------------------------------------

def update_item(project: str, section: str, item_id, fields: dict) -> dict:
    with WRITE_LOCK:
        path = resolve_file(project, section or "")
        data = parse_file(path)
        if data is None:
            raise KeyError(f"could not read {path.name}")
        for item in data["items"]:
            if str(item.get("id")) == str(item_id):
                if "status" in fields:
                    st = str(fields["status"]).lower()
                    if st not in VALID_STATUS:
                        raise ValueError(f"bad status: {st}")
                    item["status"] = st
                if "priority" in fields:
                    raw = fields["priority"]
                    if raw in (None, ""):
                        item["priority"] = None
                    else:
                        p = coerce_priority(raw)
                        if p is None:
                            raise ValueError(f"bad priority: {raw} (use 1-5, or blank)")
                        item["priority"] = p
                for key in ("next", "blocked_on", "due", "title"):
                    if key in fields:
                        val = fields[key]
                        item[key] = (str(val).strip() or None) if val is not None else None
                write_file(path, data)
                bump()
                return item
        raise KeyError(f"unknown item {item_id} in {project}/{section or 'main'}")


def _find_item(data: dict, item_id):
    for item in data["items"]:
        if str(item.get("id")) == str(item_id):
            return item
    raise KeyError(f"unknown item {item_id}")


def _with_item(project: str, section: str, item_id):
    """Open the right tracker file and return (path, data, item)."""
    path = resolve_file(project, section or "")
    data = parse_file(path)
    if data is None:
        raise KeyError(f"could not read {path.name}")
    return path, data, _find_item(data, item_id)


def add_subtask(project: str, section: str, item_id, text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty sub-task")
    with WRITE_LOCK:
        path, data, item = _with_item(project, section, item_id)
        entry = {"text": text, "done": False}
        item.setdefault("subtasks", []).append(entry)
        write_file(path, data)
        bump()
        return entry


def set_subtask(project: str, section: str, item_id, index: int, done: bool) -> dict:
    with WRITE_LOCK:
        path, data, item = _with_item(project, section, item_id)
        subs = item.get("subtasks") or []
        if not (0 <= index < len(subs)):
            raise KeyError("sub-task not found")
        subs[index]["done"] = bool(done)
        write_file(path, data)
        bump()
        return subs[index]


def delete_subtask(project: str, section: str, item_id, index: int) -> None:
    with WRITE_LOCK:
        path, data, item = _with_item(project, section, item_id)
        subs = item.get("subtasks") or []
        if not (0 <= index < len(subs)):
            raise KeyError("sub-task not found")
        subs.pop(index)
        write_file(path, data)
        bump()


def add_note(project: str, section: str, item_id, text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty note")
    with WRITE_LOCK:
        path = resolve_file(project, section or "")
        data = parse_file(path)
        if data is None:
            raise KeyError(f"could not read {path.name}")
        entry = {"date": dt.date.today().isoformat(), "text": text}
        if item_id in (None, "", "project"):
            data.setdefault("project_notes", []).append(entry)
        else:
            for item in data["items"]:
                if str(item.get("id")) == str(item_id):
                    item.setdefault("notes", []).append(entry)
                    break
            else:
                raise KeyError(f"unknown item {item_id}")
        write_file(path, data)
        bump()
        return entry


def delete_note(project: str, section: str, item_id, index: int) -> None:
    with WRITE_LOCK:
        path = resolve_file(project, section or "")
        data = parse_file(path)
        if data is None:
            raise KeyError(f"could not read {path.name}")
        target = None
        if item_id in (None, "", "project"):
            target = data.get("project_notes") or []
        else:
            for item in data["items"]:
                if str(item.get("id")) == str(item_id):
                    target = item.get("notes") or []
                    break
        if target is None or not (0 <= index < len(target)):
            raise KeyError("note not found")
        target.pop(index)
        write_file(path, data)
        bump()


def bump() -> None:
    REVISION["n"] += 1
    REVISION["at"] = dt.datetime.now().isoformat(timespec="seconds")


# ----------------------------------------------------------------------------
# HTML
# ----------------------------------------------------------------------------

def render_html(mode: str) -> str:
    tpl = read_text(TEMPLATE)
    state = scan() if mode == "static" else {"projects": [], "generated": "", "revision": 0}
    return (tpl.replace("__MODE__", mode)
               .replace("__PORT__", str(RUNTIME["port"]))
               .replace('"__DATA__"', json.dumps(state, ensure_ascii=False, default=str)))


def write_snapshot() -> Path:
    atomic_write(SNAPSHOT, render_html("static"))
    return SNAPSHOT


# ----------------------------------------------------------------------------
# Server
# ----------------------------------------------------------------------------

def build_app():
    from flask import Flask, jsonify, request

    app = Flask(__name__)

    @app.get("/")
    def index():
        return render_html("live"), 200, {"Content-Type": "text/html; charset=utf-8"}

    @app.get("/api/state")
    def api_state():
        return jsonify(scan())

    @app.get("/api/revision")
    def api_revision():
        return jsonify(REVISION)

    @app.post("/api/item")
    def api_item():
        b = request.get_json(force=True) or {}
        try:
            item = update_item(b["project"], b.get("section", ""), b["id"], b)
        except (KeyError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        write_snapshot()
        return jsonify({"ok": True, "item": item, "revision": REVISION["n"]})

    @app.post("/api/subtask")
    def api_subtask_add():
        b = request.get_json(force=True) or {}
        try:
            entry = add_subtask(b["project"], b.get("section", ""), b.get("id"), b.get("text", ""))
        except (KeyError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        write_snapshot()
        return jsonify({"ok": True, "subtask": entry, "revision": REVISION["n"]})

    @app.post("/api/subtask/set")
    def api_subtask_set():
        b = request.get_json(force=True) or {}
        try:
            entry = set_subtask(b["project"], b.get("section", ""), b.get("id"),
                                int(b["index"]), bool(b.get("done")))
        except (KeyError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        write_snapshot()
        return jsonify({"ok": True, "subtask": entry, "revision": REVISION["n"]})

    @app.post("/api/subtask/delete")
    def api_subtask_delete():
        b = request.get_json(force=True) or {}
        try:
            delete_subtask(b["project"], b.get("section", ""), b.get("id"), int(b["index"]))
        except (KeyError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        write_snapshot()
        return jsonify({"ok": True, "revision": REVISION["n"]})

    @app.post("/api/note")
    def api_note():
        b = request.get_json(force=True) or {}
        try:
            entry = add_note(b["project"], b.get("section", ""), b.get("id"), b.get("text", ""))
        except (KeyError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        write_snapshot()
        return jsonify({"ok": True, "note": entry, "revision": REVISION["n"]})

    @app.post("/api/note/delete")
    def api_note_delete():
        b = request.get_json(force=True) or {}
        try:
            delete_note(b["project"], b.get("section", ""), b.get("id"), int(b["index"]))
        except (KeyError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        write_snapshot()
        return jsonify({"ok": True, "revision": REVISION["n"]})

    @app.post("/api/snapshot")
    def api_snapshot():
        return jsonify({"ok": True, "path": str(write_snapshot())})

    return app


def start_watcher() -> None:
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        print("  (watchdog not installed — file watching disabled)")
        return

    debounce = {"timer": None}

    def regenerate():
        try:
            write_snapshot()
            bump()
            print(f"  ~ change detected, snapshot refreshed (rev {REVISION['n']})")
        except Exception as exc:  # noqa: BLE001
            print(f"  ! snapshot failed: {exc}", file=sys.stderr)

    class Handler(FileSystemEventHandler):
        def on_any_event(self, event):
            if event.is_directory:
                return
            name = os.path.basename(str(event.src_path))
            if not name.endswith(".md") or name.startswith(".tmp-"):
                return
            if debounce["timer"]:
                debounce["timer"].cancel()
            debounce["timer"] = threading.Timer(0.6, regenerate)
            debounce["timer"].daemon = True
            debounce["timer"].start()

    observer = Observer()
    observer.schedule(Handler(), str(ROOT), recursive=True)
    observer.daemon = True
    observer.start()


def main() -> None:
    ap = argparse.ArgumentParser(description="ClaudeProjects status dashboard")
    ap.add_argument("--once", action="store_true", help="write dashboard.html and exit")
    ap.add_argument("--no-watch", action="store_true", help="disable the file watcher")
    ap.add_argument("--no-browser", action="store_true", help="do not open a browser")
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()
    RUNTIME["port"] = args.port

    state = scan()
    print(f"ClaudeProjects dashboard — root: {ROOT}")
    for p in state["projects"]:
        if p.get("missing"):
            extra = f" ({p['orphan_subs']} sub-tracker(s) ignored)" if p.get("orphan_subs") else ""
            print(f"  · {p['project']}: {p['message']}{extra}")
            continue
        counts = {}
        for s in p["sections"]:
            for it in s["items"]:
                counts[it["status"]] = counts.get(it["status"], 0) + 1
        total = sum(counts.values())
        secs = len(p["sections"]) - 1
        summary = ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
        print(f"  · {p['project']}: {total} items ({summary})"
              + (f", {secs} sub-tracker(s)" if secs else ""))
    if not state["projects"]:
        print(f"  ! No project folders found under {ROOT}")

    synced = sync_all()
    if synced:
        print(f"  ~ regenerated from frontmatter: {', '.join(synced)}")

    write_snapshot()
    print(f"  → snapshot: {SNAPSHOT}")

    if args.once:
        return

    if not args.no_watch:
        start_watcher()

    app = build_app()
    url = f"http://127.0.0.1:{args.port}/"
    print(f"  → live dashboard: {url}   (Ctrl-C to stop)")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    try:
        from waitress import serve
        serve(app, host="127.0.0.1", port=args.port, threads=4)
    except ImportError:
        app.run(host="127.0.0.1", port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
