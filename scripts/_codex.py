"""Session reader for the codex backend: Codex's rollout files.

`codex exec` appends every event of a thread, as it happens, to
$CODEX_HOME/sessions/YYYY/MM/DD/rollout-<stamp>-<thread id>.jsonl, so a running
step is readable mid-run. Tool calls come from the raw response items, not the
normalized `item_completed` events: the model drives its tools through a JS
`exec` wrapper (`tools.exec_command`, `tools.apply_patch`, ...), one wrapper can
run several commands, and on 2026-10-08 a `git commit` run inside a wrapper got
no CommandExecution event at all. The wrapper's code and output are complete.
Reasoning is encrypted and its summaries are off, so the only narration is the
agent's own messages.
"""
import functools
import glob
import json
import os
import re
from datetime import datetime

from _session import marker

SESSIONS = os.path.join(
    os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex"), "sessions")
# What a command sees when the sandbox, not the command, refused it. Flailing
# against these is the codex counterpart of zcode's tool errors.
DENIED = re.compile(r"Operation not permitted|Read-only file system|Could not resolve host")
EXIT_CODE = re.compile(r'"exit_code":\s*(-?\d+)')
PATCH = re.compile(r"\*\*\* Begin Patch.*?\*\*\* End Patch", re.S)
# Inside a wrapper's JS source a patch's newlines are the two characters `\n`.
PATCH_FILE = re.compile(r"\*\*\* (?:Add|Update|Delete) File: (.+?)(?=\\n|\n|$)"
                        r"|\*\*\* Move to: (.+?)(?=\\n|\n|$)")


def _file(sid):
    hits = glob.glob(os.path.join(SESSIONS, "**", f"rollout-*-{sid}.jsonl"), recursive=True)
    if not hits:
        raise SystemExit(f"session not found: {sid}")
    return max(hits, key=os.path.getmtime)


@functools.cache
def _events(sid):
    out = []
    for line in open(_file(sid)):
        try:
            out.append(json.loads(line))
        except ValueError:
            continue  # the last line can be half-written mid-run
    return out


def _items(sid):
    for d in _events(sid):
        p = d.get("payload") or {}
        if p.get("type") == "item_completed" and isinstance(p.get("item"), dict):
            yield p, p["item"]


def resolve_session(sid=None):
    """Explicit id wins; else the step's own session; else newest in cwd."""
    if sid:
        return sid
    m = marker()
    if m.get("RESUMED"):
        return m["RESUMED"]
    # run-step.sh tees codex's banner into the step log, and it names the thread.
    log = m.get("LOG")
    if log and os.path.exists(log):
        found = re.search(r"^session id: (\S+)", open(log, errors="replace").read(), re.M)
        if found:
            return found.group(1)
    root = os.path.realpath(m.get("ROOT") or os.getcwd())
    files = sorted(glob.glob(os.path.join(SESSIONS, "**", "rollout-*.jsonl"), recursive=True),
                   key=os.path.getmtime, reverse=True)
    for f in files[:200]:
        try:
            head = json.loads(open(f).readline())
        except ValueError:
            continue
        p = head.get("payload") or {}
        if head.get("type") == "session_meta" and os.path.realpath(p.get("cwd") or "") == root:
            return p["id"]
    raise SystemExit(f"no session found for {root}")


def session_head(sid):
    meta = next((d["payload"] for d in _events(sid) if d.get("type") == "session_meta"), {})
    title = next((c.get("text") for _, it in _items(sid) if it["type"] == "UserMessage"
                  for c in it.get("content") or [] if c.get("text")), "")
    return {
        "id": sid,
        "title": title,
        "directory": meta.get("cwd"),
        "time_updated": int(os.path.getmtime(_file(sid)) * 1000),
    }


def tool_calls(sid):
    """One entry per tool call the model made, with its exact input."""
    out, by_id = [], {}
    for d in _events(sid):
        p = d.get("payload") or {}
        if d.get("type") != "response_item":
            continue
        kind = p.get("type")
        if kind in ("custom_tool_call", "function_call"):
            call = _call(p, _ms(d.get("timestamp")))
            by_id[p.get("call_id")] = call
            out.append(call)
        elif kind in ("custom_tool_call_output", "function_call_output"):
            call = by_id.get(p.get("call_id"))
            if call:
                _finish(call, p.get("output"), _ms(d.get("timestamp")))
    return out


def _call(p, started):
    name = p.get("name") or "?"
    if p.get("namespace"):
        name = f"{p['namespace']}.{name}"
    raw = p.get("input") if p.get("type") == "custom_tool_call" else p.get("arguments")
    raw = raw or ""
    try:
        inp = json.loads(raw) if p.get("type") == "function_call" else raw
    except ValueError:
        inp = raw
    # Patch bodies are file CONTENT, not commands: keep only their headers, so a
    # doc that mentions `launchctl` reads as a write, not as running it.
    writes = [a or b for a, b in PATCH_FILE.findall(raw)] or None
    if isinstance(inp, str):
        inp = PATCH.sub(lambda m: "*** patch: " + ", ".join(
            a or b for a, b in PATCH_FILE.findall(m.group(0))), inp)
    return {
        "tool_call_id": p.get("call_id"), "tool_name": name, "status": "running",
        "read_only": None, "destructive": None, "exit_code": None,
        "started_at": started, "duration_ms": None,
        "input": inp, "output": None, "error": None, "writes": writes, "scan": None,
    }


def _finish(call, output, done):
    if isinstance(output, list):
        output = "".join(o.get("text") or "" for o in output if isinstance(o, dict))
    output = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
    call["output"] = output
    if call["started_at"] and done:
        call["duration_ms"] = done - call["started_at"]
    codes = [int(c) for c in EXIT_CODE.findall(output)]
    call["exit_code"] = next((c for c in codes if c), 0) if codes else None
    denied = DENIED.search(output)
    if output.startswith("Script failed") or denied:
        call["status"] = "error"
        call["error"] = output[max(0, denied.start() - 120):denied.end()] if denied else output
    elif call["exit_code"]:
        # A command that ran and exited non-zero (a failing test, grep with no
        # match) is ordinary work, not an error.
        call["status"] = "failed"
    else:
        call["status"] = "completed"


def _ms(ts):
    try:
        return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000)
    except (AttributeError, ValueError):
        return None


def parts(sid, kinds=("reasoning", "text")):
    out = []
    for _, it in _items(sid):
        if it["type"] == "Reasoning" and "reasoning" in kinds:
            for s in it.get("summary_text") or []:
                txt = (s if isinstance(s, str) else (s or {}).get("text") or "").strip()
                if txt:
                    out.append(("reasoning", txt))
        elif it["type"] == "AgentMessage" and "text" in kinds:
            txt = "".join(c.get("text") or "" for c in it.get("content") or []).strip()
            if txt:
                out.append(("text", txt))
    return out


def usage_totals(sid):
    tot = {"input": 0, "output": 0, "total": 0, "cost": 0.0}
    model = mode = None
    for d in _events(sid):
        p = d.get("payload") or {}
        if d.get("type") == "turn_context":
            model = p.get("model") or model
            if p.get("effort"):
                model = f"{model} ({p['effort']})"
            mode = (p.get("sandbox_policy") or {}).get("type") or mode
        elif p.get("type") == "token_count":
            # Cumulative for the whole thread, so the last one is the total.
            u = (p.get("info") or {}).get("total_token_usage") or {}
            tot.update(input=u.get("input_tokens", 0), output=u.get("output_tokens", 0),
                       total=u.get("total_tokens", 0))
    return tot, model, mode


def todos(sid):
    return []
