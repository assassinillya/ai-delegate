"""Session reader for the agy backend: the stream-json run-step.sh tees to its log.

Antigravity keeps conversations in its own sqlite/protobuf store, so the event
stream is the readable record. A log holds exactly one step; a resumed
conversation's earlier steps are in their own logs.
"""
import functools
import glob
import json
import os
import re
from datetime import datetime

from _session import LOG_DIR, marker

DENIED = re.compile(r"Operation not permitted|Read-only file system|Could not resolve host"
                    r"|Command failed to run")
EXIT_CODE = re.compile(r"Command exited with code (-?\d+)")
CREATED = re.compile(r"Created At: (\S+)")
WRITE_TOOL = re.compile(r"write|edit|replace|delete_file|create_file|move_file")
PATH_KEYS = ("Path", "TargetFile", "AbsolutePath", "FilePath", "file_path", "path")
# Calls the supervisor can't see into. A BypassSandbox request can't be spotted
# here (the stream omits that parameter); the runner's pinned permissions refuse it.
OFF_LIMITS = re.compile(r"subagent|browser|execute_browser|call_mcp_tool|generate_image")


def _log(sid=None):
    m = marker()
    if sid is None:
        return m.get("LOG")
    logs = sorted(glob.glob(os.path.join(LOG_DIR, "step-*.log")), reverse=True)
    for log in ([m["LOG"]] if m.get("LOG") else []) + logs:
        if os.path.exists(log) and sid in open(log, errors="replace").read():
            return log
    raise SystemExit(f"session not found: {sid}")


@functools.cache
def _events(log):
    out = []
    for line in open(log, errors="replace"):
        if not line.startswith("{"):
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue  # half-written last line mid-run
    return out


def resolve_session(sid=None):
    if sid:
        return sid
    log = _log()
    if log and os.path.exists(log):
        for d in _events(log):
            if d.get("conversation_id"):
                return d["conversation_id"]
    if marker().get("RESUMED"):
        return marker()["RESUMED"]
    raise SystemExit("no agy session in the current step log")


def session_head(sid):
    log = _log(sid)
    init = next((d["init"] for d in _events(log) if d.get("event") == "init"), {})
    return {"id": sid, "title": "", "directory": init.get("cwd"),
            "time_updated": int(os.path.getmtime(log) * 1000)}


def _updates(sid):
    for d in _events(_log(sid)):
        if d.get("event") == "step_update":
            yield d.get("step_update") or {}


def tool_calls(sid):
    """One entry per tool step; later updates of a step replace earlier ones."""
    steps = {}
    for u in _updates(sid):
        # "subagent" is invoke_subagent; "unknown" is what ask_question shows up as.
        if u.get("step_type") in ("tool", "subagent", "unknown"):
            steps[u.get("step_index")] = u
    return [_call(u) for _, u in sorted(steps.items())]


def _call(u):
    info = u.get("tool_info") or {}
    name = u.get("tool_name") or info.get("name") or u.get("step_type") or "?"
    params = info.get("parameters") or u.get("subagent_info") or {}
    output = info.get("output")
    text = output if isinstance(output, str) else json.dumps(output or "", ensure_ascii=False)
    if info.get("error"):
        text += " " + json.dumps(info["error"], ensure_ascii=False)
    writes = None
    if WRITE_TOOL.search(name):
        writes = [params[k] for k in PATH_KEYS if isinstance(params.get(k), str)] or None
    code = EXIT_CODE.search(text)
    code = int(code.group(1)) if code else None
    created = CREATED.search(text)
    denied = DENIED.search(text)
    state = u.get("state")
    if state not in ("DONE", "ERROR", "FAILED", "CANCELED", "CANCELLED"):
        status = "running"
    elif state != "DONE" or denied or (name == "run_command" and not output):
        status = "error"  # a command with no output record never ran (turn cut short)
    else:
        status = "failed" if code else "completed"
    return {
        "tool_call_id": u.get("step_index"), "tool_name": name, "status": status,
        "read_only": None,
        "destructive": 1 if OFF_LIMITS.search(name) else None,
        "exit_code": code, "started_at": _ms(created.group(1)) if created else None,
        "duration_ms": int((u.get("duration_seconds") or 0) * 1000) or None,
        "input": params, "output": text,
        "error": text[-400:] if status == "error" else None,
        "writes": writes, "scan": " ".join(writes) if writes else None,
    }


def _ms(ts):
    try:
        return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def parts(sid, kinds=("reasoning", "text")):
    if "text" not in kinds:
        return []
    texts = {}
    for u in _updates(sid):
        if u.get("step_type") == "agent_response" and u.get("text_delta"):
            texts.setdefault(u.get("step_index"), []).append(u["text_delta"])
    return [("text", "".join(t).strip()) for _, t in sorted(texts.items())]


def usage_totals(sid):
    tot = {"input": 0, "output": 0, "total": 0, "cost": 0.0}
    model = None
    for d in _events(_log(sid)):
        if d.get("event") == "init":
            model = (d.get("init") or {}).get("model")
    for u in _updates(sid):
        if u.get("state") == "DONE" and u.get("usage"):
            tot["input"] += u["usage"].get("input_tokens", 0)
            tot["output"] += u["usage"].get("output_tokens", 0)
            tot["total"] += u["usage"].get("total_tokens", 0)
    return tot, model, marker().get("MODE")


def todos(sid):
    return []
