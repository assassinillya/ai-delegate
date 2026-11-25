"""Run one agy turn with pinned permissions and an honest exit code.

  _agy_run.py <read|write> <resume-id or ""> <prompt>

Why this is not a plain `agy -p` (all measured, see references/agy.md):
- With no --mode, agy picks Auto a few seconds after start, when the account
  offers it. In Auto a `BypassSandbox` command is reviewed by another model and
  can really run outside the sandbox; without Auto every command is refused. So
  the permissions are pinned instead of inherited: accept-edits for edits,
  proceed-in-sandbox for commands.
- agy exits 0 when it was refused mid-turn and produced nothing.
- `--conversation <unknown id>` silently starts an empty conversation, exit 0.
- The Antigravity desktop app watches ~/.gemini/antigravity-cli and races agy to
  open each new conversation: some fail to start, some lose their workspace (and
  once resumed the sandbox refuses the repo). So delegated runs get a data dir of
  their own, which shares the login but which the app does not touch.

Exit: 0 ok | 3 timeout | 75 refused, failed or empty | 76 session not resumable |
78 the pinned permissions did not take effect | otherwise agy's own code.
"""
import glob
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time

SKILL = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
GEMINI = os.path.expanduser("~/.gemini")
DATA_DIR, SHARED_DATA_DIR = "antigravity-cli-delegate", "antigravity-cli"
TOOL_PERMISSION = "proceed-in-sandbox"
PREAMBLE = """\
[Runner rules]
- Commands run in a sandbox: this repo and temp dirs are writable; .git, everything outside the repo and all network (localhost included) are not.
- Never set BypassSandbox, even when a failed command's output suggests it. A failing test is usually a failing test.
- Do not use subagents, browser or MCP tools, and do not write files outside this repo. One refused call ends your whole turn and loses the rest of the step.
- If the sandbox blocks something you need, skip it and say so in your final message.

[Task]
"""


def git(*args):
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def permissions(mode):
    """Rules take absolute paths only: a relative one makes EVERY command fail
    with `sandbox configuration error` while the step still reads DONE."""
    allow, deny = [], [f"read_file({SKILL})", "mcp(*)", "execute_url(*)"]
    if os.path.lexists(os.path.expanduser("~/.claude/skills/ai-delegate")):
        deny.append(f"read_file({os.path.expanduser('~/.claude/skills/ai-delegate')})")
    common = git("rev-parse", "--path-format=absolute", "--git-common-dir")
    top = git("rev-parse", "--show-toplevel")
    if common and top:
        main = os.path.dirname(os.path.realpath(common))
        if os.path.basename(common) == ".git" and main != os.path.realpath(top):
            # A linked worktree: git metadata and symlinked deps live in the main
            # checkout. Readable, so git works; still not writable.
            allow.append(f"read_file({main})")
            for depth in ("", "*/", "*/*/"):
                for p in glob.glob(os.path.join(main, depth + ".env*")):
                    if not any(w in os.path.basename(p) for w in ("example", "sample", "template")):
                        deny.append(f"read_file({p})")
    for p in filter(None, os.environ.get("DELEGATE_AGY_READ", "").split(":")):
        allow.append(f"read_file({os.path.realpath(os.path.expanduser(p))})")
    if mode == "read":
        # The wildcard stops the edit tools; only the concrete path also makes
        # the sandbox mount the repo read-only for commands.
        deny += ["write_file(*)", f"write_file({os.path.realpath(top or os.getcwd())})"]
    return {"allow": allow, "deny": sorted(set(deny))}


def data_dir(sid):
    """Where `sid` lives; sessions from before the split are in the shared dir."""
    for d in (DATA_DIR, SHARED_DATA_DIR):
        if sid and os.path.exists(os.path.join(GEMINI, d, "conversations", f"{sid}.db")):
            return d
    return None


def workspace_state(sid):
    """"ok", "missing" (no such conversation), "lost" (workspace gone) or "unknown"."""
    if not data_dir(sid):
        return "missing"
    db = os.path.join(GEMINI, data_dir(sid), "conversations", f"{sid}.db")
    tmp = tempfile.mkdtemp()
    try:
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(db + suffix):
                shutil.copy(db + suffix, tmp)
        con = sqlite3.connect(os.path.join(tmp, f"{sid}.db"))
        rows = con.execute("select length(data) from trajectory_metadata_blob").fetchall()
        con.close()
        return "ok" if rows and all(r[0] for r in rows) else "lost"
    except (OSError, sqlite3.Error):
        return "unknown"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def say(msg):
    print(f"agy: {msg}", flush=True)


def command(mode, sid, prompt):
    model, effort = os.environ.get("DELEGATE_MODEL"), os.environ.get("DELEGATE_EFFORT")
    # An id that names its effort conflicts with --effort, so the default drops it.
    model = model or ("gemini-4-neon" if effort else "gemini-4-neon-high")
    cmd = ["agy", f"--app_data_dir={data_dir(sid) or DATA_DIR}", "--model", model, "--output-format", "stream-json",
           "--disable-slash-commands", "--sandbox", "--mode", "accept-edits",
           "--settings", json.dumps({"toolPermission": TOOL_PERMISSION,
                                     "permissions": permissions(mode)}),
           "--print-timeout", os.environ.get("DELEGATE_AGY_TIMEOUT") or "60m"]
    if effort:
        cmd += ["--effort", effort]
    if sid:
        cmd += ["--conversation", sid]
    if not os.environ.get("DELEGATE_AGY_NO_PREAMBLE"):
        prompt = PREAMBLE + prompt
    return cmd + ["-p", prompt]


def descendants(pid):
    """Every live process below `pid`."""
    out = subprocess.run(["ps", "-axo", "pid=,ppid="], capture_output=True, text=True).stdout
    kids = {}
    for line in out.splitlines():
        child, parent = map(int, line.split())
        kids.setdefault(parent, []).append(child)
    found, todo = [], [pid]
    while todo:
        for c in kids.get(todo.pop(), []):
            found.append(c)
            todo.append(c)
    return found


def turn(cmd, sid):
    """Run one agy process, streaming its events. -> (rc, result, conversation, abort)"""
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            text=True, errors="replace")

    def stop(*_):
        # agy puts each sandboxed command in its own group and leaves it running
        # when terminated, so collect the tree first and take all of it down.
        tree = descendants(proc.pid)
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
        for pid in tree:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        sys.exit(143)
    for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(s, stop)

    result, started, abort = None, None, None
    for line in proc.stdout:
        try:
            d = json.loads(line)
        except ValueError:
            d = {}
        if d.get("event") == "init":
            started = d.get("conversation_id")
            perm = (d.get("init") or {}).get("permission_mode")
            if sid and started != sid:
                abort = (76, f"asked to resume {sid} but agy opened {started}; stopped")
            elif perm != TOOL_PERMISSION:
                abort = (78, f"permission mode is {perm!r}, expected {TOOL_PERMISSION!r}; stopped")
            if abort:
                proc.terminate()
                break
        if d.get("event") == "result":
            result = d.get("result") or {}
        sys.stdout.write(line)
        sys.stdout.flush()
    return proc.wait(), result, started, abort


def main():
    mode, sid, prompt = sys.argv[1:4]
    if sid:
        state = workspace_state(sid)
        if state in ("missing", "lost"):
            say(f"cannot resume {sid}: conversation {state}. Nothing was run. Start a NEW "
                "session and give it the plan file and what is already committed.")
            return 76
        if state == "unknown":
            say(f"could not check {sid} before resuming; verify the first command really writes")

    for wait in (5, 20, None):
        rc, result, started, abort = turn(command(mode, sid, prompt), sid)
        error = (result or {}).get("error") or ""
        if started or "failed to start conversation" not in error or wait is None:
            break
        # Nothing ran yet, so trying again is safe.
        say(f"agy could not start ({error}); retrying in {wait}s")
        time.sleep(wait)
    if abort:
        say(abort[1])
        return abort[0]

    code = 0
    if result is None:
        say(f"no result event (agy exit {rc})")
        code = rc or 75
    elif rc:
        say(f"agy exit {rc}: {result.get('finished_reason')} {result.get('error')}")
        code = rc
    elif result.get("denied_actions"):
        say(f"REFUSED {result['denied_actions']}: the turn was cut short there, its work is "
            "incomplete. See the last error in watch.py, then --resume with a way around it.")
        code = 75
    elif result.get("status") != "SUCCESS":
        say(f"status {result.get('status')} {result.get('error')}")
        code = 75
    elif not (result.get("response") or "").strip():
        say("empty final message: treat the step as not done")
        code = 75
    if started and workspace_state(started) == "lost":
        say(f"WARNING session {started} lost its workspace (agy bug): its work so far stands, "
            "but do NOT --resume it; continue in a new session")
    return code


if __name__ == "__main__":
    sys.exit(main())
