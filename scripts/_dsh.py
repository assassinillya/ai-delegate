"""Session reader for the dsh backend: DeepSeek Harness session logs.

dsh appends every event of a session to
$DSH_HOME/sessions/<cwd with / as ->/<session id>/session.v4.jsonl.zstd as a
stream of zstd frames, flushed every few seconds, so `zstd -dc` reads a
running step. Each event carries `seq` and `time` (epoch ms). Tool calls are
`tool/call` (JSON-string arguments) answered by `tool/result` with `isError`
and, for sandbox denials, `error.code`; bash results end in `[exit code: N]`
when the command exits non-zero. Reasoning is plaintext.
"""
import functools
import glob
import json
import os
import re
import subprocess

from _session import marker

SESSIONS = os.path.join(
    os.environ.get("DSH_HOME") or os.path.expanduser("~/.dsh"), "sessions")
WRITE_TOOLS = {"write", "edit"}
EXIT_CODE = re.compile(r"\[exit code: (-?\d+)\]\s*$")
# A bash command the sandbox refused still "completes" with its own exit code;
# flailing against these is the dsh counterpart of zcode's tool errors.
DENIED = re.compile(r"Operation not permitted|Read-only file system|\[sandbox: ")


def _file(sid):
    hits = glob.glob(os.path.join(SESSIONS, "*", sid, "session.v*.jsonl.zstd"))
    if not hits:
        raise SystemExit(f"session not found: {sid}")
    return max(hits, key=os.path.getmtime)


@functools.cache
def _events(sid):
    raw = subprocess.run(["zstd", "-dcq", _file(sid)], capture_output=True).stdout
    out = []
    for line in raw.decode(errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue  # the last frame can be half-written mid-run
    return out


def resolve_session(sid=None):
    """Explicit id wins; else the step's own session; else newest in cwd."""
    if sid:
        return sid
    m = marker()
    if m.get("RESUMED"):
        return m["RESUMED"]
    # dsh.sh runs with --json, whose first event names the session.
    log = m.get("LOG")
    if log and os.path.exists(log):
        found = re.search(r'^\{"type":"session","sessionId":"([^"]+)"',
                          open(log, errors="replace").read(), re.M)
        if found:
            return found.group(1)
    root = os.path.realpath(m.get("ROOT") or os.getcwd())
    folder = os.path.join(SESSIONS, "-" + root.replace("/", "-") + "--")
    files = glob.glob(os.path.join(folder, "*", "session.v*.jsonl.zstd"))
    if not files:
        raise SystemExit(f"no session found for {root}")
    return os.path.basename(os.path.dirname(max(files, key=os.path.getmtime)))


def session_head(sid):
    ev = _events(sid)
    head = next((d for d in ev if d.get("type") == "session"), {})
    title = next((d["data"].get("title") for d in reversed(ev)
                  if d.get("type") == "session/title"), "")
    return {
        "id": sid,
        "title": title,
        "directory": head.get("cwd"),
        "time_updated": int(os.path.getmtime(_file(sid)) * 1000),
    }


def tool_calls(sid):
    """One entry per tool call the model made, with its exact input."""
    out, by_id = [], {}
    for d in _events(sid):
        kind, data = d.get("type"), d.get("data") or {}
        if kind == "tool/call":
            call = _call(data, d.get("time"))
            by_id[data.get("callId")] = call
            out.append(call)
        elif kind == "tool/result":
            msg = data.get("message") or {}
            call = by_id.get(msg.get("toolCallId"))
            if call:
                _finish(call, msg, data.get("error"), d.get("time"))
    return out


def _call(data, started):
    name = data.get("name") or "?"
    try:
        inp = json.loads(data.get("arguments") or "{}")
    except ValueError:
        inp = data.get("arguments")
    writes = scan = None
    if name in WRITE_TOOLS and isinstance(inp, dict):
        # File content is not a command: judge the write by its target path,
        # so a doc that mentions `launchctl` reads as a write, not as running it.
        writes = [inp.get("file_path")] if inp.get("file_path") else None
        scan = " ".join(filter(None, [name, inp.get("file_path"),
                                      inp.get("sandbox_permissions")]))
    return {
        "tool_call_id": data.get("callId"), "tool_name": name, "status": "running",
        "read_only": None, "destructive": None, "exit_code": None,
        "started_at": started, "duration_ms": None,
        "input": inp, "output": None, "error": None, "writes": writes, "scan": scan,
    }


def _finish(call, msg, err, done):
    output = "".join(c.get("text") or "" for c in msg.get("content") or []
                     if isinstance(c, dict))
    call["output"] = output
    if call["started_at"] and done:
        call["duration_ms"] = done - call["started_at"]
    code = EXIT_CODE.search(output)
    call["exit_code"] = int(code.group(1)) if code else 0
    denied = DENIED.search(output)
    if msg.get("isError") or denied:
        call["status"] = "error"
        call["error"] = (err or {}).get("code") or output[:300]
    elif call["exit_code"]:
        # A command that ran and exited non-zero (a failing test, grep with no
        # match) is ordinary work, not an error.
        call["status"] = "failed"
    else:
        call["status"] = "completed"


def parts(sid, kinds=("reasoning", "text")):
    out = []
    for d in _events(sid):
        if d.get("type") != "assistant/message":
            continue
        for c in (d["data"].get("message") or {}).get("content") or []:
            txt = (c.get("text") or "").strip()
            if txt and c.get("type") in kinds:
                out.append((c["type"], txt))
    return out


def usage_totals(sid):
    tot = {"input": 0, "output": 0, "total": 0, "cost": 0.0}
    model = mode = None
    for d in _events(sid):
        kind, data = d.get("type"), d.get("data") or {}
        if kind == "request/header":
            cfg = data.get("header", {}).get("config") or {}
            model = f"{cfg.get('provider')}/{cfg.get('model')}"
            if cfg.get("reasoningEffort"):
                model += f" ({cfg['reasoningEffort']})"
        elif kind == "sandbox/mode":
            mode = data.get("mode") or mode
        elif kind == "assistant/message":
            # Per model call; cache reads count as input.
            u = data.get("usage") or {}
            tot["input"] += u.get("inputTokens", 0) + u.get("cacheReadTokens", 0)
            tot["output"] += u.get("outputTokens", 0)
            tot["total"] += u.get("totalTokens", 0)
    return tot, model, mode


def todos(sid):
    latest = None
    for d in _events(sid):
        if d.get("type") == "tool/call" and (d.get("data") or {}).get("name") == "todo_write":
            try:
                latest = json.loads(d["data"].get("arguments") or "{}").get("todos")
            except ValueError:
                continue
    return [t for t in latest or [] if isinstance(t, dict) and t.get("content")]
