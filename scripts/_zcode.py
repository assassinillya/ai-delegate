"""Session reader for the zcode backend: ZCode's own session store.

ZCode persists live session state to a sqlite db in WAL mode, so a running
session is fully readable while it runs: `tool_usage` carries per-call status
and the destructive/read-only flags, `part` carries each tool call's exact
arguments plus the model's own reasoning text.
"""
import functools
import json
import os
import sqlite3

from _session import marker

DB = os.path.expanduser("~/.zcode/cli/db/db.sqlite")
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


@functools.cache
def _con():
    if not os.path.exists(DB):
        raise SystemExit(f"session db not found: {DB}")
    # Read-only: never let a supervisor write into the agent's own store.
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def resolve_session(sid=None):
    """Explicit id wins; else the step's own session; else newest in cwd."""
    if sid:
        return sid
    con = _con()
    m = marker()
    if m.get("RESUMED"):
        return m["RESUMED"]
    root = m.get("ROOT") or os.getcwd()
    start = int(m.get("START_MS") or 0)
    row = con.execute(
        "select id from session where directory = ? and time_created >= ?"
        " order by time_created desc limit 1",
        (root, start),
    ).fetchone()
    if row:
        return row["id"]
    row = con.execute(
        "select id from session where directory = ? order by time_created desc limit 1",
        (root,),
    ).fetchone()
    if not row:
        raise SystemExit(f"no session found for {root}")
    return row["id"]


def session_head(sid):
    con = _con()
    row = con.execute(
        "select id, title, directory, task_type, time_created, time_updated"
        " from session where id = ?",
        (sid,),
    ).fetchone()
    if not row:
        raise SystemExit(f"session not found: {sid}")
    return dict(row)


def tool_calls(sid):
    """Tool calls with their arguments, joined from tool_usage + part."""
    con = _con()
    usage = con.execute(
        "select tool_call_id, tool_name, status, read_only, destructive,"
        " exit_code, started_at, duration_ms from tool_usage"
        " where session_id = ? order by started_at",
        (sid,),
    ).fetchall()
    args = {}
    for (data,) in con.execute(
        "select data from part where session_id = ? order by sequence", (sid,)
    ):
        try:
            d = json.loads(data)
        except Exception:
            continue
        if d.get("type") == "tool":
            st = d.get("state") or {}
            key = d.get("callID")
            args[key] = {
                "tool": d.get("tool"),
                "input": st.get("input"),
                "status": st.get("status"),
                "output": st.get("output"),
                "error": st.get("error"),
            }
    out = []
    for u in usage:
        a = args.get(u["tool_call_id"], {})
        out.append({
            **dict(u),
            "input": a.get("input"),
            "output": a.get("output"),
            "error": a.get("error"),
            "writes": _writes(u["tool_name"], a.get("input")),
            "scan": _scan(u["tool_name"], a.get("input")),
        })
    # Tool calls whose usage row hasn't landed yet still show up via part.
    seen = {u["tool_call_id"] for u in usage}
    for key, a in args.items():
        if key not in seen:
            out.append({
                "tool_call_id": key,
                "tool_name": a.get("tool") or "?",
                "status": a.get("status") or "pending",
                "read_only": None, "destructive": None, "exit_code": None,
                "started_at": None, "duration_ms": None,
                "input": a.get("input"),
                "output": a.get("output"),
                "error": a.get("error"),
                "writes": _writes(a.get("tool"), a.get("input")),
                "scan": _scan(a.get("tool"), a.get("input")),
            })
    return out


def _writes(tool, inp):
    if tool not in WRITE_TOOLS:
        return None
    return [inp.get("file_path") or ""] if isinstance(inp, dict) else [""]


def _scan(tool, inp):
    # A write tool's input is the file's content; only its target path counts.
    return " ".join(_writes(tool, inp)) if tool in WRITE_TOOLS else None


def parts(sid, kinds=("reasoning", "text")):
    con = _con()
    out = []
    for (data,) in con.execute(
        "select data from part where session_id = ? order by sequence", (sid,)
    ):
        try:
            d = json.loads(data)
        except Exception:
            continue
        if d.get("type") in kinds:
            txt = (d.get("text") or "").strip()
            if txt:
                out.append((d["type"], txt))
    return out


def usage_totals(sid):
    con = _con()
    rows = con.execute(
        "select data from message where session_id = ? order by sequence", (sid,)
    ).fetchall()
    tot = {"input": 0, "output": 0, "total": 0, "cost": 0.0}
    model = mode = None
    for (data,) in rows:
        try:
            d = json.loads(data)
        except Exception:
            continue
        model = d.get("modelId") or model
        mode = d.get("mode") or mode
        sel = d.get("modelSelection") or {}
        if sel.get("modelId"):
            model = sel["modelId"]
        t = d.get("tokens") or {}
        for k in ("input", "output", "total"):
            tot[k] += int(t.get(k) or 0)
        tot["cost"] += float(d.get("cost") or 0)
    return tot, model, mode


def todos(sid):
    con = _con()
    try:
        rows = con.execute(
            "select content, status, priority, position from todo"
            " where session_id = ? order by position", (sid,)
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in rows]

