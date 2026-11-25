#!/bin/bash
# Dispatch ONE bounded step to the delegated agent and run it to completion.
#
# Launch via the Bash tool with run_in_background:true — the supervisor gets
# woken on exit, which is the real review checkpoint. Logs go outside the repo
# so they never pollute the git diff the step is reviewed against.
#
#   run-step.sh "<prompt>"                     # fresh session, codex, write mode
#   run-step.sh --resume <sess_id> "<prompt>"  # continue, full context kept
#   run-step.sh --mode full "<prompt>"         # read | write | full
#   run-step.sh --backend dsh "<prompt>"       # any backends/<name>.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
LOG_DIR="${DELEGATE_LOG_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/ai-delegate/logs}"

BACKEND="${DELEGATE_BACKEND:-codex}"
MODE="${DELEGATE_MODE:-write}"
SID=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --backend) BACKEND="${2:?--backend needs a name}"; shift 2;;
    --resume)  SID="${2:?--resume needs a session id}"; shift 2;;
    --mode)    MODE="${2:?--mode needs a value}"; shift 2;;
    --) shift; break;;
    -*) echo "unknown flag: $1" >&2; exit 64;;
    *) break;;
  esac
done
PROMPT="${1:-}"
[[ -n "$PROMPT" ]] || { echo 'usage: run-step.sh [--backend <name>] [--resume <sid>] [--mode read|write|full] "<prompt>"' >&2; exit 64; }
[[ -x "$HERE/backends/$BACKEND.sh" ]] || { echo "unknown backend: $BACKEND (have: $(cd "$HERE/backends" && ls *.sh | sed 's/\.sh$//' | xargs))" >&2; exit 64; }
case "$MODE" in read|write|full) ;; *) echo "unknown mode: $MODE (read|write|full)" >&2; exit 64;; esac

mkdir -p "$LOG_DIR"
STAMP="$(date '+%Y%m%d-%H%M%S')"
LOG="$LOG_DIR/step-$STAMP.log"
START_MS=$(python3 -c 'import time; print(int(time.time()*1000))')

# Marker the watch/tripwire scripts use to scope themselves to THIS step,
# instead of guessing at "the newest session".
cat > "$LOG_DIR/current.env" <<ENV
START_MS=$START_MS
LOG=$LOG
BACKEND=$BACKEND
MODE=$MODE
ROOT=$ROOT
RESUMED=${SID:-}
ENV

{
  echo "step   : $STAMP"
  echo "backend: $BACKEND"
  echo "mode   : $MODE"
  echo "cwd    : $ROOT"
  echo "resume : ${SID:-<new session>}"
  echo "log    : $LOG"
  echo "---"
} | tee "$LOG"

# cwd must be the project root or the agent won't see the project-scoped skills.
cd "$ROOT"
set +e
"$HERE/backends/$BACKEND.sh" "$MODE" "$SID" "$PROMPT" 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
SESSION="$(python3 "$HERE/watch.py" --session-id 2>/dev/null)"
set -e
echo "--- exit=$rc session=${SESSION:-?}" | tee -a "$LOG"
exit "$rc"
