"""Universal notification gateway and Tmux event hook observer."""

import json
import os
import platform
import re
import subprocess
import sys
import time
from typing import Dict, Any, Optional
from .context import resolve_notification_context


def _tmux_option(option: str, default: str) -> str:
    try:
        val = subprocess.run(
            ["tmux", "show-option", "-gqv", option],
            capture_output=True,
            text=True,
            timeout=0.3
        ).stdout.strip()
        return val if val else default
    except Exception:
        return default


def handle_clear(pane_id: str) -> None:
    """Clear visual input alert and needs-input flag on a target pane."""
    if not pane_id:
        return
    try:
        subprocess.run(["tmux", "set", "-pu", "-t", pane_id, "pane-border-style"], capture_output=True, timeout=0.2)
        subprocess.run(["tmux", "set", "-pu", "-t", pane_id, "pane-active-border-style"], capture_output=True, timeout=0.2)
        subprocess.run(["tmux", "set", "-pu", "-t", pane_id, "@pane-needs-input"], capture_output=True, timeout=0.2)
    except Exception:
        pass


def handle_hook_title(pane_id: str) -> None:
    """Deprecated: Pane title sniffing removed in favor of native agent hooks."""
    pass



def _check_agy_waiting(conv_id: str) -> bool:
    """Check if AGY is currently waiting for background tasks or schedule timers."""
    if not conv_id:
        return False
    agy_tpath = os.path.expanduser(f"~/.gemini/antigravity-cli/brain/{conv_id}/.system_generated/logs/transcript.jsonl")
    if not os.path.exists(agy_tpath):
        return False
    try:
        with open(agy_tpath, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()[-80:]
        running_tasks = set()
        finished_tasks = set()
        pending_timer = False
        for l in reversed(lines):
            try:
                d = json.loads(l)
                t = d.get("type")
                c = str(d.get("content", ""))
                m_fin = re.findall(r'Task id\s*"([^"]+)"\s*finished', c)
                for tid in m_fin:
                    finished_tasks.add(tid.split("/")[-1])
                if d.get("status") == "RUNNING":
                    m_start = re.search(r'task id:\s*([^\s\n"]+)', c)
                    if m_start:
                        t_id = m_start.group(1).split("/")[-1]
                        if t_id not in finished_tasks:
                            running_tasks.add(t_id)
                if t == "PLANNER_RESPONSE":
                    tcs = [tc.get("name") for tc in d.get("tool_calls", [])]
                    if "schedule" in tcs and not pending_timer:
                        pending_timer = True
                if "Timer: " in c or "sender=schedule" in c:
                    pending_timer = False
                if t == "USER_INPUT":
                    break
            except Exception:
                pass
        return len(running_tasks) > 0 or pending_timer
    except Exception:
        return False


def _get_claude_last_turn_uuid(transcript_path: str) -> Optional[str]:
    """Extract uuid of the transcript's last user/assistant entry."""
    if not transcript_path or not os.path.isfile(transcript_path):
        return None
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()[-200:]
        for line in reversed(lines):
            try:
                d = json.loads(line)
                if d.get("type") in ("user", "assistant") and d.get("uuid"):
                    return str(d["uuid"])
            except Exception:
                continue
    except Exception:
        pass
    return None


def _idle_teammates(transcript_path: str) -> set:
    """Descriptions of idle in-process teammates. Stop payloads list a teammate as running until it shuts down."""
    names: Dict[str, str] = {}
    idle = set()
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if '"name":"Agent"' not in line and '"name":"SendMessage"' not in line and "idle_notification" not in line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                content = (d.get("message") or {}).get("content")
                if d.get("type") == "user" and isinstance(content, str):
                    # A teammate mails the lead an idle_notification each time its turn ends
                    idle.update(re.findall(r'"type":"idle_notification","from":"([^"]+)"', content))
                elif d.get("type") == "assistant" and isinstance(content, list):
                    # Spawning or messaging a teammate puts it back to work
                    for b in content:
                        if b.get("type") != "tool_use":
                            continue
                        inp = b.get("input") or {}
                        if b.get("name") == "Agent" and inp.get("name"):
                            names[inp.get("description", "")] = inp["name"]
                            idle.discard(inp["name"])
                        elif b.get("name") == "SendMessage":
                            idle.discard(inp.get("to"))
    except Exception:
        return set()
    return {desc for desc, name in names.items() if name in idle}


def _dispatch_claude_settle_daemon(
    tp: str,
    mark: str,
    wait_s: float,
    payload: Dict[str, Any],
    agent_name: str,
    message: str,
    target_pane: str,
    force: bool,
) -> None:
    """Spawn detached background daemon to re-dispatch notification after settle/hold wait."""
    self_cmd = sys.argv[0] if sys.argv and os.path.isfile(sys.argv[0]) else "tmux-agent-switcher"
    sub_payload = dict(payload)
    sub_payload["settled"] = True

    daemon_py = (
        "import json, os, subprocess, sys, time\n"
        f"time.sleep({wait_s})\n"
        "curr = None\n"
        "try:\n"
        f"    with open({repr(tp)}, 'r', encoding='utf-8', errors='ignore') as f:\n"
        "        lines = f.readlines()[-200:]\n"
        "    for l in reversed(lines):\n"
        "        try:\n"
        "            d = json.loads(l)\n"
        "            if d.get('type') in ('user', 'assistant') and d.get('uuid'):\n"
        "                curr = str(d['uuid'])\n"
        "                break\n"
        "        except Exception:\n"
        "            pass\n"
        "except Exception:\n"
        "    pass\n"
        f"if curr == {repr(mark)}:\n"
        f"    cmd = [{repr(self_cmd)}, 'notify']\n"
        + (f"    cmd.append({repr(agent_name)})\n" if agent_name else "")
        + (f"    cmd.append({repr(message)})\n" if message else "")
        + (f"    cmd.append({repr(target_pane)})\n" if target_pane else "")
        + ("    cmd.append('--force')\n" if force else "")
        + "    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        f"    p.communicate(input=json.dumps({repr(sub_payload)}).encode('utf-8'))\n"
    )
    try:
        subprocess.Popen(
            [sys.executable, "-c", daemon_py],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass


def _osc9(text: str, term: str) -> str:
    """OSC 9 desktop alert. A client that itself runs inside tmux needs it wrapped for passthrough, every inner ESC doubled."""
    # OSC 9 only: Ghostty and WezTerm also render OSC 777, so sending both shows two banners.
    clean = re.sub(r"[\x00-\x1f\x7f]", " ", text)  # agent text must not end the sequence or inject another
    seq = f"\x1b]9;{clean}\x1b\\"
    if term.startswith(("tmux", "screen")):
        seq = "\x1bPtmux;" + seq.replace("\x1b", "\x1b\x1b") + "\x1b\\"
    return seq


def handle_notify(
    agent_name: str = "",
    message: str = "",
    target_pane: str = "",
    force: bool = False,
    stdin_data: str = ""
) -> None:
    """Universal AI agent notification dispatcher."""
    payload: Dict[str, Any] = {}
    if stdin_data:
        try:
            payload = json.loads(stdin_data)
        except Exception:
            payload = {}

    # Headless SDK suppression
    if os.environ.get("CLAUDE_CODE_ENTRYPOINT", "").startswith("sdk-") and "hook_event_name" in payload:
        return

    # Claude Code Stop hold and settle suppression:
    # 1. Settle: Queued input or fast next-turn results start immediately after Stop,
    #    so settle AGENT_NOTIFY_SETTLE_S (default 3s) first to avoid alert during turn overlap.
    # 2. Hold: While background work is in flight (.background_tasks non-empty), Claude wakes itself
    #    when it lands, so hold AGENT_NOTIFY_HOLD_S (default 1800s) unless the reply asks a question (?).
    #    An idle teammate stays listed but never wakes Claude, so it doesn't hold.
    if not force and payload.get("hook_event_name") == "Stop" and not payload.get("settled"):
        bg_tasks = payload.get("background_tasks") or []
        is_mate = lambda t: isinstance(t, dict) and t.get("type") == "teammate"
        if any(map(is_mate, bg_tasks)):
            idle = _idle_teammates(payload.get("transcript_path", ""))
            bg_tasks = [t for t in bg_tasks if not is_mate(t) or t.get("description") not in idle]
        last_msg = payload.get("last_assistant_message") or ""
        last_para = re.split(r'\n\s*\n', last_msg.rstrip())[-1] if last_msg else ""
        asks_question = bool(re.search(r'\?(\s|$|[*_`)"”’\'])', last_para))

        held = False
        wait_s = float(os.environ.get("AGENT_NOTIFY_SETTLE_S", 3.0))
        if len(bg_tasks) > 0 and not asks_question:
            held = True
            wait_s = float(os.environ.get("AGENT_NOTIFY_HOLD_S", 1800.0))

        tp = payload.get("transcript_path", "")
        if tp and os.path.isfile(tp):
            mark = _get_claude_last_turn_uuid(tp)
            if mark and wait_s > 0:
                _dispatch_claude_settle_daemon(
                    tp, mark, wait_s, payload, agent_name, message, target_pane, force
                )
                return
        if held:
            return

    # Codex goal suppression
    transcript_path = payload.get("transcript_path", "")
    if transcript_path and os.path.isfile(transcript_path):
        try:
            with open(transcript_path, "r", encoding="utf-8", errors="ignore") as f:
                tail = f.readlines()[-30:]
            for line in reversed(tail):
                if "thread_goal_updated" in line:
                    d = json.loads(line)
                    if d.get("payload", {}).get("goal", {}).get("status") == "active":
                        return
                    break
        except Exception:
            pass

    # AGY background task / timer suppression
    conv_id = payload.get("conversationId", "")
    if conv_id:
        if payload.get("fullyIdle") is False:
            return
        if _check_agy_waiting(conv_id):
            return

    # Extract message details if empty
    if not message:
        if payload.get("tool_name") or payload.get("permission_request") or payload.get("permission"):
            perm = payload.get("permission")
            perm_name = perm.get("type") if isinstance(perm, dict) else perm
            tool_name = (
                payload.get("tool_name")
                or payload.get("permission_request", {}).get("tool")
                or perm_name
                or "Tool"
            )
            message = f"Approval needed: {tool_name}"
        elif payload.get("question") or payload.get("question_asked"):
            message = "Human input needed"
        elif payload.get("error") or payload.get("session_error") or payload.get("terminationReason") == "error":
            err = str(payload.get("error") or payload.get("session_error") or "Execution halted with error")
            message = err[:70]
        else:
            last_msg = payload.get("last_assistant_message", "")
            if not last_msg and conv_id:
                agy_tpath = os.path.expanduser(f"~/.gemini/antigravity-cli/brain/{conv_id}/.system_generated/logs/transcript.jsonl")
                if os.path.exists(agy_tpath):
                    try:
                        with open(agy_tpath, "r", encoding="utf-8", errors="ignore") as f:
                            for l in reversed(f.readlines()[-60:]):
                                d = json.loads(l)
                                if d.get("type") == "PLANNER_RESPONSE" and d.get("content"):
                                    last_msg = str(d["content"]).strip()
                                    break
                    except Exception:
                        pass
            if last_msg:
                clean_msg = re.sub(r"[\n\r]+", " ", last_msg).strip("#*` \t\r\n")[:70]
                message = clean_msg if clean_msg else "Task complete"
            else:
                message = "Task complete"

    # Detect agent name if empty
    if not agent_name:
        if payload.get("conversationId"):
            agent_name = "AGY"
        elif payload.get("turn_id") is not None or payload.get("type") == "agent-turn-complete":
            agent_name = "Codex"
        elif payload.get("transcript_path"):
            agent_name = "Claude Code"
        else:
            agent_name = "AI Agent"

    # Agent badges
    badge_map = {
        "codex": ("Codex", "🔵"),
        "claude": ("Claude Code", "🟠"),
        "claude code": ("Claude Code", "🟠"),
        "claudecode": ("Claude Code", "🟠"),
        "agy": ("AGY", "🟣"),
        "antigravity": ("AGY", "🟣"),
        "opencode": ("OpenCode", "🟢"),
        "cursor": ("Cursor", "⚡"),
    }
    canon_name, badge = badge_map.get(agent_name.lower(), (agent_name, "🤖"))

    # Resolve pane context
    ctx = resolve_notification_context(canon_name, payload, target_pane=target_pane)
    pane = ctx.get("pane", "")
    pane_location = ctx.get("location", "")
    is_focused = ctx.get("focused", False)
    pane_tty = ctx.get("pane_tty", "")
    client_tty = ctx.get("client_tty", "")

    host_name = platform.node().split(".")[0]
    notify_context = f"{host_name} · {pane_location}" if pane_location else host_name

    # Check configuration options
    mark_input = _tmux_option("@agent-switcher-mark-input", "on").lower()
    notify_mode = _tmux_option("@agent-switcher-notify", "none").lower()

    # Check if notification should be suppressed due to focus
    # When focused, suppress in-tmux yellow border, bell, and desktop banner unless forced
    if not force and is_focused:
        return

    # Apply In-Tmux visual mark (Layer 2)
    if mark_input == "on" and pane:
        try:
            subprocess.run(["tmux", "set", "-p", "-t", pane, "pane-border-style", "fg=yellow"], capture_output=True, timeout=0.2)
            subprocess.run(["tmux", "set", "-p", "-t", pane, "pane-active-border-style", "fg=yellow,bold"], capture_output=True, timeout=0.2)
            subprocess.run(["tmux", "set", "-p", "-t", pane, "@pane-needs-input", "1"], capture_output=True, timeout=0.2)
        except Exception:
            pass

    # Terminal bell
    if notify_mode in ("bell", "all") and pane_tty and os.path.exists(pane_tty):
        try:
            with open(pane_tty, "w") as pt:
                pt.write("\a")
        except Exception:
            pass

    # Check if system notification mode is enabled (Layer 3)
    if notify_mode in ("none", "off", "bell"):
        return

    # De-duplicate notifications on same pane within 3 seconds
    if pane and not force:
        try:
            last_epoch = subprocess.run(
                ["tmux", "show-option", "-pqv", "-t", pane, "@last-notify-epoch"],
                capture_output=True, text=True, timeout=0.2
            ).stdout.strip()
            now = time.time()
            if last_epoch and (now - float(last_epoch)) < 3.0:
                return
            subprocess.run(["tmux", "set", "-p", "-t", pane, "@last-notify-epoch", str(now)], capture_output=True, timeout=0.2)
        except Exception:
            pass

    safe_title = f"{badge} {canon_name}".replace('"', ' ').replace('\\', ' ')
    safe_msg = message.replace('"', ' ').replace('\\', ' ')
    safe_ctx = notify_context.replace('"', ' ').replace('\\', ' ')

    is_darwin = platform.system() == "Darwin"
    remote = ctx.get("remote_clients", [])

    # Local macOS notification, unless every attached client is remote
    sent_desktop = False
    if is_darwin and (ctx.get("local_client") or not remote) and notify_mode in ("desktop", "all"):
        script = f'display notification "{safe_msg}" with title "{safe_title}" subtitle "{safe_ctx}"'
        try:
            subprocess.Popen(["osascript", "-e", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            sent_desktop = True
        except Exception:
            pass

    # OSC 9 to every SSH client (the user may sit at either end), plus the local client in passthrough mode.
    # With no client resolved, the pane's own tty: its tmux forwards the wrapped alert if passthrough is allowed.
    targets = list(remote)
    if (notify_mode == "passthrough" or (notify_mode == "all" and not sent_desktop)) and client_tty not in [t for t, _ in remote]:
        targets.append((client_tty, ctx.get("client_term", "")) if client_tty else (pane_tty, "tmux"))
    for tty, term in targets:
        if tty and os.path.exists(tty):
            try:
                with open(tty, "w") as ct:
                    ct.write(_osc9(f"{safe_title} · {safe_ctx}: {safe_msg}", term))
            except Exception:
                pass

