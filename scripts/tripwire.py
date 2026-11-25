#!/usr/bin/env python3
"""Red lines. Exit 1 means stop the agent now; exit 0 means let it keep going.

Meant to be run between steps, and via Monitor during a long step. It reads
only — killing and rolling back is the supervisor's call, not this script's.

  tripwire.py                      # this step's session
  tripwire.py <sess_id> --budget 60
  tripwire.py --backend zcode     # another backend than the current step's
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _session  # noqa: E402

# Commands and paths the agent has no business touching while implementing a plan.
# Network calls are deliberately NOT blocked: acceptance often
# has to reach an external API.
DENY = [
    (r"\bgit\s+push\b", "git push"),
    (r"\bgit\s+reset\s+--hard\b", "git reset --hard"),
    (r"\bgit\s+clean\s+-[a-z]*f", "git clean -f"),
    (r"\bgit\s+commit\b.*--amend\b", "git commit --amend"),
    (r"\bgit\s+(checkout|switch)\s+(main|master)\b", "switching off the work branch"),
    (r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f|\brm\s+-[a-zA-Z]*f[a-zA-Z]*r", "rm -rf"),
    (r"\blaunchctl\b", "launchctl (touches the running service)"),
    (r"\bdeploy[\w-]*\.sh\b", "deploy script"),
    (r"\b(ssh|scp)\s+[^ ]+@", "remote shell/copy"),
    (r"provider_config\.json", "zcode provider config (holds API keys)"),
    # The zcode desktop config holds a plaintext apiKey.
    (r"\.zcode/v2/config\.json", "config holding app secrets / API keys"),
    (r"\.pem\b|\.p12\b|id_rsa|\bcredentials\b", "secret material"),
    (r"""(^|[\s/'"])\.env(rc)?($|[\s.'"/])""", ".env"),
    (r"\.claude/skills/ai-delegate", "the supervisor's own skill"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("session", nargs="?")
    ap.add_argument("--budget", type=int, default=50, help="max tool calls per step")
    ap.add_argument("--errors", type=int, default=3, help="consecutive errors allowed")
    ap.add_argument("--max-sends", type=int, default=0, help="0 = do not check")
    ap.add_argument("--backend")
    a = ap.parse_args()

    allow = os.environ.get("DELEGATE_ALLOW")
    allow_re = re.compile(allow) if allow else None
    deny = list(DENY)
    if os.environ.get("DELEGATE_DENY_EXTRA"):
        deny.append((os.environ["DELEGATE_DENY_EXTRA"], "DELEGATE_DENY_EXTRA"))

    be = _session.backend(a.backend)
    sid = be.resolve_session(a.session)
    root = os.path.normpath(_session.marker().get("ROOT") or os.getcwd())
    calls = be.tool_calls(sid)
    # A resumed session carries every earlier step's calls. Without an explicit
    # session id, judge only THIS step: on 2026-09-24 a 22-call step tripped the
    # 50-call budget because the 30 calls of the step before it were counted.
    step_start = int(_session.marker().get("START_MS") or 0)
    if not a.session and step_start:
        calls = [c for c in calls if not c.get("started_at") or c["started_at"] >= step_start]

    trips, notes = [], []
    sends = 0
    streak = 0
    worst_streak = 0

    for i, c in enumerate(calls, 1):
        blob = _session.flat(c.get("input"), 8000)
        where = f"call #{i} {c['tool_name']}"

        if c["destructive"] == 1:
            trips.append(f"{where}: flagged destructive by the agent — {blob[:120]}")

        if "send-message.sh" in blob:
            sends += 1

        # Command patterns describe things the agent might RUN, so a write is
        # judged by its target PATH, never its content (the backend hands that
        # over as `scan`, or strips the content from the input): on 2026-09-23 an incident
        # report containing the word "launchctl" tripped the wire while Bash had
        # invoked launchctl zero times. Writing docs must not look like running
        # commands — but writing INTO a protected path must still trip.
        writes = c.get("writes")
        scanned = blob if c.get("scan") is None else c["scan"]
        if scanned and not (allow_re and allow_re.search(scanned)):
            for pat, label in deny:
                if re.search(pat, scanned, re.I):
                    trips.append(f"{where}: {label} — {scanned[:120]}")
                    break

        # A write landing outside the repo is almost always the agent improvising.
        for fp in writes or []:
            abs_fp = os.path.abspath(fp) if fp.startswith("/") else ""
            # A scratch helper under a temp dir is ordinary tooling (2026-09-23:
            # an acceptance run wrote a poll script to /tmp). Product files
            # landing outside the repo are still the real signal.
            scratch = abs_fp.startswith(("/tmp/", "/private/tmp/", "/var/folders/"))
            if abs_fp and not scratch and not abs_fp.startswith(root + os.sep):
                trips.append(f"{where}: writes outside the repo — {fp}")

        if c["status"] == "error":
            streak += 1
            worst_streak = max(worst_streak, streak)
        else:
            streak = 0

    if streak >= a.errors:
        trips.append(f"{streak} tool errors in a row — it is flailing, not progressing")
    elif worst_streak >= a.errors:
        notes.append(f"recovered from a run of {worst_streak} consecutive errors")

    if len(calls) > a.budget:
        trips.append(f"{len(calls)} tool calls > budget {a.budget} — step is too big or it is lost")

    if a.max_sends and sends > a.max_sends:
        trips.append(f"{sends} outbound sends > cap {a.max_sends}")

    print(f"session : {sid}")
    print(f"calls   : {len(calls)}   sends: {sends}")
    for n in notes:
        print(f"note    : {n}")
    if not trips:
        print("verdict : clean")
        return 0
    print(f"verdict : TRIPPED ({len(trips)})")
    for t in trips:
        print(f"  !! {t}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
