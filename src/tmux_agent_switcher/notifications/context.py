"""Resolve notification context from live tmux panes and process trees."""

import collections
import glob
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
from typing import Dict, List, Optional, Any, Tuple


def _tmux_cmd(args: List[str], timeout: float = 0.5) -> str:
    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return res.stdout or ""
    except Exception:
        return ""


def list_context_panes() -> List[Dict[str, str]]:
    fmt = "#{pane_id}\t#{session_name}:#{window_index}.#{pane_index}\t#{pane_pid}\t#{pane_tty}\t#{pane_current_path}\t#{pane_title}"
    result = []
    for line in _tmux_cmd(["tmux", "list-panes", "-a", "-F", fmt]).splitlines():
        fields = line.split("\t", 5)
        if len(fields) == 6 and re.fullmatch(r"%\d+", fields[0]):
            result.append(dict(zip(("pane", "location", "pid", "pane_tty", "cwd", "title"), fields)))
    return result


def get_pane_processes(candidates: List[Dict[str, str]]) -> Dict[str, Tuple[str, str, str]]:
    ttys = sorted({p["pane_tty"].removeprefix("/dev/") for p in candidates if p.get("pane_tty")})
    if not ttys:
        return {}
    result = {}
    for line in _tmux_cmd(["ps", "-t", ",".join(ttys), "-o", "pid=,ppid=,tty=,command="]).splitlines():
        fields = line.strip().split(None, 3)
        if len(fields) == 4:
            result[fields[0]] = (fields[1], fields[2], fields[3])
    return result


def find_codex_clients(pane: Dict[str, str], procs: Dict[str, Tuple[str, str, str]]) -> List[List[str]]:
    children = collections.defaultdict(list)
    for pid, proc in procs.items():
        children[proc[0]].append(pid)
    found, stack, seen = [], [pane["pid"]], set()
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        stack.extend(children[pid])
        proc = procs.get(pid, ("", "", ""))
        if proc[1] != pane["pane_tty"].removeprefix("/dev/"):
            continue
        argv = proc[2].split()
        if argv and Path(argv[0]).name in ("node", "nodejs"):
            argv = argv[1:]
        if argv and Path(argv[0]).name == "codex" and not any(
            a in ("app-server", "exec-server", "sandbox", "exec", "review", "debug", "--help", "-h", "--version", "-V")
            for a in argv[1:]
        ):
            found.append(argv)
    return found


def get_codex_thread_metadata(session_id: str) -> Dict[str, Any]:
    root = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    databases = sorted(
        root.glob("state_*.sqlite"),
        key=lambda p: int(p.stem.split("_")[-1]) if p.stem.split("_")[-1].isdigit() else 0,
        reverse=True
    )
    for path in databases:
        try:
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.2) as db:
                db.row_factory = sqlite3.Row
                row = db.execute("SELECT * FROM threads WHERE id=?", (session_id,)).fetchone()
                if row:
                    row_dict = dict(row)
                    name = row_dict.get("name") or ""
                    if name and db.execute(
                        "SELECT count(*) FROM threads WHERE name=? AND cwd=? AND archived=0",
                        (name, row_dict["cwd"])
                    ).fetchone()[0] > 1:
                        row_dict["name"] = ""
                    return row_dict
        except Exception:
            continue
    return {}


def matches_codex_thread(pane: Dict[str, str], metadata: Dict[str, Any]) -> bool:
    name, cwd = metadata.get("name", ""), metadata.get("cwd", "")
    title = re.sub(r"^[✳*★☆✦✧⠀-⣿\s]+", "", pane.get("title", "")).strip()
    return bool(name and cwd and os.path.realpath(pane.get("cwd", "")) == os.path.realpath(cwd) and (title == name or title.startswith(name + " | ")))


def get_claude_sessions_meta() -> List[Dict[str, Any]]:
    sessions = []
    for f in glob.glob(os.path.expanduser("~/.claude*/sessions/*.json")):
        try:
            with open(f, "r", encoding="utf-8") as fp:
                sessions.append(json.load(fp))
        except Exception:
            pass
    return sessions


def resolve_claude_pane(sid: str, lookup: Any, live: List[Dict[str, str]]) -> Optional[Dict[str, str]]:
    if not sid:
        return None
    tagged = []
    for line in _tmux_cmd(["tmux", "list-panes", "-a", "-F", "#{pane_id}\t#{@claude-session-id}"]).splitlines():
        fields = line.split("\t")
        if len(fields) == 2 and fields[1] == sid:
            pane = lookup(fields[0])
            if pane:
                tagged.append(pane)
    if len(tagged) == 1:
        return tagged[0]

    sessions = get_claude_sessions_meta()
    for s in sessions:
        if s.get("sessionId") == sid and s.get("tmux"):
            pane_id = s["tmux"].split(".")[-1] if "." in s["tmux"] else s["tmux"]
            pane = lookup(pane_id)
            if pane:
                return pane
        if s.get("sessionId") == sid:
            job_id = s.get("jobId")
            if job_id:
                for parent in sessions:
                    if parent.get("parkedJobId") == job_id and parent.get("tmux"):
                        pane_id = parent["tmux"].split(".")[-1] if "." in parent["tmux"] else parent["tmux"]
                        pane = lookup(pane_id)
                        if pane:
                            return pane

    for s in sessions:
        if (s.get("jobId") and sid.startswith(s["jobId"])) or (s.get("parkedJobId") and sid.startswith(s["parkedJobId"])):
            if s.get("tmux"):
                pane_id = s["tmux"].split(".")[-1] if "." in s["tmux"] else s["tmux"]
                pane = lookup(pane_id)
                if pane:
                    return pane
    return None


def resolve_agent_pane(
    agent: str,
    payload: Dict[str, Any],
    live: List[Dict[str, str]],
    procs: Dict[str, Any],
    metadata: Dict[str, Any],
    explicit: str = "",
    inherited: str = ""
) -> Optional[Dict[str, str]]:
    def lookup(target: str) -> Optional[Dict[str, str]]:
        return next((p for p in live if target in (p["pane"], p["location"])), None)

    if explicit:
        return lookup(explicit)

    agent_lower = agent.lower()
    if agent_lower == "codex":
        sid = payload.get("session_id") or payload.get("thread-id") or ""
        if not sid:
            return None
        named = []
        for pane in live:
            clients = find_codex_clients(pane, procs)
            if not clients:
                continue
            if matches_codex_thread(pane, metadata):
                named.append(pane)
        return named[0] if len(named) == 1 else None

    is_claude = agent_lower in ("claude", "claude code", "claudecode") or (
        not agent and "transcript_path" in payload and payload.get("hook_event_name")
    )
    if is_claude:
        sid = payload.get("session_id") or payload.get("sessionId") or ""
        pane = resolve_claude_pane(sid, lookup, live)
        if pane:
            return pane

    conversation = payload.get("conversationId")
    if conversation:
        tagged = []
        for line in _tmux_cmd(["tmux", "list-panes", "-a", "-F", "#{pane_id}\t#{@agy-session-id}"]).splitlines():
            fields = line.split("\t")
            if len(fields) == 2 and fields[1] == conversation:
                pane = lookup(fields[0])
                if pane:
                    tagged.append(pane)
        return tagged[0] if len(tagged) == 1 else None

    return lookup(inherited) if inherited else None


def resolve_notification_context(
    agent_name: str,
    payload: Dict[str, Any],
    target_pane: str = "",
    tag_session: bool = True
) -> Dict[str, Any]:
    """Resolve active target pane, terminal paths, and client focus."""
    sid = payload.get("session_id") or payload.get("sessionId") or payload.get("thread-id") or ""
    live = list_context_panes()
    metadata = get_codex_thread_metadata(sid) if sid and agent_name.lower() == "codex" else {}
    candidates = [p for p in live if target_pane in (p["pane"], p["location"]) or matches_codex_thread(p, metadata)]
    procs = get_pane_processes(candidates) if agent_name.lower() == "codex" else {}

    inherited = os.environ.get("TMUX_PANE", "")
    pane = resolve_agent_pane(agent_name, payload, live, procs, metadata, target_pane, inherited)

    # Inspect clients for focus state
    client_lines = _tmux_cmd(["tmux", "list-clients", "-F", "#{pane_id}\t#{client_tty}\t#{client_flags}\t#{session_name}"]).splitlines()
    clients = [c.split("\t") for c in client_lines if len(c.split("\t")) == 4]

    if not pane:
        return {"pane": "", "location": "", "focused": False, "pane_tty": "", "client_tty": ""}

    if sid and tag_session:
        if agent_name.lower() == "codex" and find_codex_clients(pane, procs):
            _tmux_cmd(["tmux", "set-option", "-p", "-t", pane["pane"], "@codex-session-id", sid])
        elif agent_name.lower() in ("claude", "claude code", "claudecode") or (not agent_name and "transcript_path" in payload):
            _tmux_cmd(["tmux", "set-option", "-p", "-t", pane["pane"], "@claude-session-id", sid])

    matching = [c for c in clients if c[0] == pane["pane"]]
    focused = [c for c in matching if "focused" in c[2].split(",")]
    session = pane["location"].rsplit(":", 1)[0]
    same_session = [c for c in clients if len(c) > 3 and c[3] == session]
    client = (focused or matching or (same_session if len(same_session) == 1 else []) or [None])[0]

    return {
        "pane": pane["pane"],
        "location": pane["location"],
        "focused": bool(focused),
        "pane_tty": pane.get("pane_tty", ""),
        "client_tty": client[1] if client else ""
    }
