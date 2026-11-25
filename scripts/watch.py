#!/usr/bin/env python3
"""Live snapshot of what the delegated agent is doing right now.

Safe to run mid-run: every backend persists each tool call shortly after it
finishes (zcode: WAL sqlite; codex: the rollout jsonl; dsh: zstd-framed jsonl,
flushed every few seconds).

  watch.py                 # this step's session
  watch.py <sess_id>       # a specific session
  watch.py --reasoning 6   # show more of the model's own reasoning
  watch.py --sends         # only the acceptance skill's outbound messages
  watch.py --session-id    # just the id, to --resume with
  watch.py --backend zcode # read another backend than the current step's
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _session  # noqa: E402

SEND_HINT = "send-message.sh"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("session", nargs="?")
    ap.add_argument("--reasoning", type=int, default=3)
    ap.add_argument("--sends", action="store_true")
    ap.add_argument("--session-id", action="store_true")
    ap.add_argument("--backend")
    a = ap.parse_args()

    be = _session.backend(a.backend)
    sid = be.resolve_session(a.session)
    if a.session_id:
        print(sid)
        return
    head = be.session_head(sid)
    calls = be.tool_calls(sid)

    if a.sends:
        print(f"# outbound sends — {sid}")
        n = 0
        for c in calls:
            blob = _session.flat(c.get("input"), 4000)
            if SEND_HINT in blob:
                n += 1
                print(f"\n[{n}] status={c['status']} {_session.ago(c['started_at'])}")
                print(f"    {blob}")
        if not n:
            print("(none)")
        return

    tot, model, mode = be.usage_totals(sid)
    print(f"session : {sid}")
    print(f"title   : {(head['title'] or '')[:70]}")
    print(f"model   : {model or '?'}   mode: {mode or '?'}")
    print(f"updated : {_session.ago(head['time_updated'])}")
    print(f"tokens  : in={tot['input']} out={tot['output']} total={tot['total']}")

    td = be.todos(sid)
    if td:
        print("\ntodo")
        for t in td:
            mark = {"completed": "x", "in_progress": ">"}.get(t["status"], " ")
            print(f"  [{mark}] {t['content'][:80]}")

    print(f"\ntool calls ({len(calls)})")
    if not calls:
        print("  (none yet)")
    for i, c in enumerate(calls, 1):
        flags = []
        if c["destructive"] == 1:
            flags.append("DESTRUCTIVE")
        if c["read_only"] == 1:
            flags.append("ro")
        if c["exit_code"] not in (None, 0):
            flags.append(f"exit={c['exit_code']}")
        tag = (" " + " ".join(flags)) if flags else ""
        print(f"  {i:>2}. {c['tool_name']:<10} {c['status']:<9}{tag}")
        arg = _session.flat(c.get("input"), 180)
        if arg:
            print(f"      {arg}")
        if c["status"] == "error":
            err = _session.flat(c.get("error") or c.get("output"), 180)
            if err:
                print(f"      !! {err}")

    if a.reasoning:
        r = [t for k, t in be.parts(sid, ("reasoning",))]
        if r:
            print(f"\nreasoning (last {min(a.reasoning, len(r))} of {len(r)})")
            for t in r[-a.reasoning:]:
                print(f"  · {_session.flat(t, 300)}")

    say = [t for k, t in be.parts(sid, ("text",))]
    if say:
        print("\nlatest message")
        print(f"  {_session.flat(say[-1], 400)}")


if __name__ == "__main__":
    main()
