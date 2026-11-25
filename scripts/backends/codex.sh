#!/bin/bash
# codex backend: `codex exec`, headless, one turn per call, on the user's own
# Codex login and ~/.codex/config.toml (model, effort, MCP servers).
#
#   codex.sh <read|write|full> <resume-id or ""> <prompt>
#
# Unlike zcode, codex runs commands inside an OS sandbox, so the mode is a real
# boundary rather than a permission prompt. See references/codex.md.
set -euo pipefail
MODE="${1:?mode}"; SID="${2-}"; PROMPT="${3:?prompt}"

command -v codex >/dev/null || { echo "codex CLI not found on PATH" >&2; exit 127; }

# write: the repo minus .git, no network, nothing outside the workspace.
# full: no sandbox -- acceptance needs the network, token caches in ~, and
# running local services.
case "$MODE" in
  read)  SANDBOX=read-only;;
  write) SANDBOX=workspace-write;;
  full)  SANDBOX=danger-full-access;;
  *) echo "unknown mode: $MODE" >&2; exit 64;;
esac

# `exec resume` accepts neither -s nor -C: the sandbox goes through -c for both
# forms, and the cwd is the one run-step.sh starts us in.
#
# Sub-agents are off: a spawned agent writes its own rollout, which watch.py and
# tripwire.py would never see.
args=(-c "sandbox_mode=\"$SANDBOX\"" --disable multi_agent)
[[ -n "${DELEGATE_MODEL:-}" ]] && args+=(-m "$DELEGATE_MODEL")
[[ -n "${DELEGATE_EFFORT:-}" ]] && args+=(-c "model_reasoning_effort=\"$DELEGATE_EFFORT\"")

# stdin is closed: with a prompt argument codex still reads stdin and appends it,
# and an open pipe would make a background step wait forever.
if [[ -n "$SID" ]]; then
  exec codex exec resume "${args[@]}" "$SID" "$PROMPT" </dev/null
fi
exec codex exec "${args[@]}" "$PROMPT" </dev/null
