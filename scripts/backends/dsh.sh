#!/bin/bash
# dsh backend: DeepSeek Harness `headless` profile, one turn per call, on
# DeepSeek's newest released model. Which one that is changes with every
# release, so there is no default: the supervisor looks it up in the official
# changelog before each delegation and passes DELEGATE_MODEL (references/dsh.md).
#
#   dsh.sh <read|write|full> <resume-id or ""> <prompt>
#
# Like codex, dsh runs tools inside an OS sandbox, so the mode is a real
# boundary. Unlike codex, workspace-write leaves .git and the network open.
# See references/dsh.md.
set -euo pipefail
MODE="${1:?mode}"; SID="${2-}"; PROMPT="${3:?prompt}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Desktop's "Manage dsh Command… → Install" puts dsh on PATH; without it, the
# launcher bundled in the app works the same.
DSH="${DELEGATE_DSH:-$(command -v dsh || true)}"
[[ -n "$DSH" ]] || DSH="/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh"
[[ -x "$DSH" ]] || { echo "dsh not found (install DeepSeek Harness Desktop, or set DELEGATE_DSH)" >&2; exit 127; }

# Escalation requests (`sandbox_permissions`) are refused on the spot in
# headless: "requires approval, but no approval channel is available".
case "$MODE" in
  read)  export DSH_PERMISSION_MODE=read-only;;
  write) export DSH_PERMISSION_MODE=workspace-write;;
  full)  export DSH_PERMISSION_MODE=danger-full-access;;
  *) echo "unknown mode: $MODE" >&2; exit 64;;
esac

# account = the DeepSeek login the Desktop app holds (its own default route);
# api-key = DEEPSEEK_API_KEY from the same credential store. headless disables
# the account platform and its authorization service, so the account route
# turns both back on.
ROUTE="${DELEGATE_DSH_ROUTE:-account}"
case "$ROUTE" in
  account) PROVIDER=deepseek-account;;
  api-key) PROVIDER=deepseek-official;;
  *) echo "unknown DELEGATE_DSH_ROUTE: $ROUTE (account|api-key)" >&2; exit 64;;
esac

MODEL="${DELEGATE_MODEL:-}"
[[ -n "$MODEL" ]] || { echo "DELEGATE_MODEL is required for dsh: look up DeepSeek's newest released model first (references/dsh.md#选模型)" >&2; exit 64; }

CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/ai-delegate/dsh"
mkdir -p "$CACHE"
ROUTE_PATCH="$CACHE/route.patch.yml"
{
  cat <<YML
- id: agent-default-model
  name: '@deepseek-ai/dsh-agent-default-model'
  config:
    provider: $PROVIDER
    model: $MODEL
    reasoningEffort: max
YML
  if [[ "$ROUTE" == account ]]; then cat <<'YML'
- id: authorization
  name: '@deepseek-ai/dsh-authorization'
  disabled: false
- id: deepseek-account
  name: '@deepseek-ai/dsh-deepseek-account-platform'
  disabled: false
YML
  fi
} > "$ROUTE_PATCH"

# --json: one event per line, the first naming the session id that _dsh.py and
# a later --session-id resume use. stdin is closed so `-`-style stdin reading
# can never hold a background step open.
args=(--profile headless --patch "$HERE/dsh.patch.yml" --patch "$ROUTE_PATCH" --json)
[[ -n "$SID" ]] && args+=(--session-id "$SID")
exec "$DSH" "${args[@]}" "$PROMPT" </dev/null
