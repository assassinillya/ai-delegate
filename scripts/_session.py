"""Backend-neutral access to the delegated agent's session.

Each backend `<name>` is a pair: `backends/<name>.sh` runs one step, and
`_<name>.py` reads that agent's own session store. A reader module exposes

  resolve_session(sid=None) -> str
  session_head(sid)         -> {id, title, directory, time_updated}
  tool_calls(sid)           -> [normalized call dicts, see below]
  parts(sid, kinds)         -> [(kind, text)], kinds from ("reasoning", "text")
  usage_totals(sid)         -> ({input, output, total, cost}, model, mode)
  todos(sid)                -> [{content, status, ...}]

A normalized tool call carries: tool_call_id, tool_name, status ("completed",
"failed" for a command that ran and exited non-zero, "error" when the tool
itself failed or was blocked, "running"), read_only, destructive, exit_code,
started_at (epoch ms), duration_ms, input, output, error, `writes` -- the file
paths it writes, or None -- and `scan`, the text the red lines are matched
against when the input itself would mislead (it carries file content), else
None to scan the input.
"""
import importlib
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_BACKEND = "codex"
LOG_DIR = os.environ.get("DELEGATE_LOG_DIR") or os.path.join(
    os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
    "ai-delegate", "logs",
)


def marker():
    """The current.env written by run-step.sh, so we scope to THIS step."""
    path = os.path.join(LOG_DIR, "current.env")
    out = {}
    if os.path.exists(path):
        for line in open(path):
            if "=" in line:
                k, v = line.rstrip("\n").split("=", 1)
                out[k] = v
    return out


def backend(name=None):
    """Explicit name wins; else the backend the current step ran on."""
    name = name or marker().get("BACKEND") or DEFAULT_BACKEND
    if not os.path.exists(os.path.join(HERE, "backends", f"{name}.sh")):
        raise SystemExit(f"unknown backend: {name}")
    return importlib.import_module(f"_{name}")


def flat(obj, limit=200):
    if obj is None:
        return ""
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    s = " ".join(s.split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def ago(ms):
    if not ms:
        return "-"
    return f"{max(0, int(time.time() * 1000) - int(ms)) // 1000}s ago"
