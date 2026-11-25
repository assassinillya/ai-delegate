#!/bin/bash
# agy backend: Antigravity CLI, headless, one turn per call, on the user's own
# Antigravity subscription login.
#
#   agy.sh <read|write|full> <resume-id or ""> <prompt>
#
# Everything that decides permissions lives in ../_agy_run.py; read
# references/agy.md before touching any flag there.
set -euo pipefail
MODE="${1:?mode}"; SID="${2-}"; PROMPT="${3:?prompt}"

command -v agy >/dev/null || { echo "agy CLI not found on PATH" >&2; exit 127; }
case "$MODE" in
  read|write) ;;
  full) echo "agy backend has no 'full' mode: run acceptance yourself or use 'write'" >&2; exit 64;;
  *) echo "unknown mode: $MODE" >&2; exit 64;;
esac

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Permission behaviour moves with the agy version and the user's own settings,
# so it is re-proved on this machine whenever either one (or our flags) changes.
[[ -n "${DELEGATE_AGY_SKIP_SELFTEST:-}" ]] || "$HERE/agy-selftest.py" --if-stale >&2 || exit 78
exec python3 -u "$HERE/_agy_run.py" "$MODE" "$SID" "$PROMPT"
