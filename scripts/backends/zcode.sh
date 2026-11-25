#!/bin/bash
# zcode backend: ZCode.app's bundled CLI, headless, pinned to glm-5.3.
#
#   zcode.sh <read|write|full> <resume-id or ""> <prompt>
#
# The bundled CLI can't locate its own provider config when started directly by
# node (it walks up 5 levels from the entry, which only lines up in the dev
# repo), so both provider paths are handed to it explicitly. See
# references/zcode.md.
set -euo pipefail
MODE="${1:?mode}"; SID="${2-}"; PROMPT="${3:?prompt}"

# build 在 headless 下无法写文件：Edit/Write 全部返回
# "No permission client configured for Edit"，而 GLM 不会停下来报错，只会绕着失败
# 继续试（2026-09-23 实测空转 70 分钟、540 万 token、零产出）。所以写入走 yolo，
# 前提是已在工作分支且有 baseline commit。zcode 没有沙箱，write 和 full 是一回事。
case "$MODE" in
  read)       ZMODE=plan;;
  write|full) ZMODE=yolo;;
  *) echo "unknown mode: $MODE" >&2; exit 64;;
esac

ENTRY="${GLM_ZCODE_ENTRY:-/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs}"
V2="$HOME/.zcode/v2"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/ai-delegate/zcode"
PINNED="$CACHE/provider_config.glm53.json"

[[ -f "$ENTRY" ]] || { echo "zcode entry not found: $ENTRY" >&2; exit 127; }

# Builtin provider config: newest one the desktop app has materialised.
BUILTIN=$(find "$V2/runtime/provider" -name zcode-builtin.json -type f 2>/dev/null \
  | xargs -I{} stat -f '%m %N' {} 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2- || true)
[[ -n "$BUILTIN" ]] || BUILTIN="/Applications/ZCode.app/Contents/Resources/config/provider/zcode-builtin.json"
[[ -f "$BUILTIN" ]] || { echo "builtin provider config not found" >&2; exit 78; }

# Personal provider config: regenerate a glm-5.3-first copy from the user's real
# one on every run so it tracks their edits. It carries API keys, so it lives in
# the cache dir at 0600 and never inside the repo.
SRC="$V2/provider_config.json"
[[ -f "$SRC" ]] || { echo "personal provider config not found: $SRC" >&2; exit 78; }
mkdir -p "$CACHE" && chmod 700 "$CACHE" "$(dirname "$CACHE")"
umask 077
GLM_PROVIDER_NAME="${GLM_PROVIDER_NAME:-glm}" python3 - "$SRC" "$PINNED" "$V2/config.json" <<'ZPY'
import json, os, sys
src, dst, cli_cfg = sys.argv[1], sys.argv[2], sys.argv[3]
want = os.environ.get("GLM_PROVIDER_NAME") or ""
d = json.load(open(src))
try:
    rules = d["config"]["providerConfigRules"]["providerRules"]
except (KeyError, TypeError):
    sys.exit(f"unexpected provider config shape: {src}")

# `glm` = the user's official ZCode/BigModel coding plan. It is NOT in the
# personal provider config: the desktop app keeps it in ~/.zcode/v2/config.json
# under `builtin:bigmodel-coding-plan`, and that entry carries the plan's API key
# in PLAINTEXT (the copy in credentials.json is enc:v1: and unusable here).
# Headless resolves models only from personal providerRules with an api-key
# access type; builtin/OAuth (`zhipu-account`) entries yield "Model creation
# failed" however the account is logged in -- verified 2026-09-23 immediately
# after a successful Z.AI OAuth login. So synthesize a personal entry from that
# plaintext key, and fall back loudly if it is ever absent.
if want == "glm":
    try:
        opts = json.load(open(cli_cfg))["provider"]["builtin:bigmodel-coding-plan"]["options"]
        key = opts["apiKey"]
        if not isinstance(key, str) or len(key) < 20:
            raise KeyError("apiKey")
        rules[:] = [{
            "providerId": "official-glm",
            "providerName": "glm",
            "enabled": True,
            "config": {
                "group": "standard-personal",
                "access": {"type": "api-key", "apiKey": key},
                "api": {"type": "anthropic-messages", "baseUrl": opts["baseURL"]},
                "modelOrder": ["GLM-5.3"],
                "personalModelIds": ["GLM-5.3"],
            },
        }]
        json.dump(d, open(dst, "w"), ensure_ascii=False, indent=2)
        raise SystemExit(0)
    except (KeyError, TypeError, FileNotFoundError, json.JSONDecodeError) as exc:
        fallback = os.environ.get("GLM_PROVIDER_FALLBACK")
        if not fallback:
            print(f"glm (official BigModel coding plan) unavailable ({exc!r}); "
                  "set GLM_PROVIDER_FALLBACK to use another provider", file=sys.stderr)
            raise SystemExit(78)
        print(f"glm (official BigModel coding plan) unavailable ({exc!r}); "
              f"falling back to provider {fallback}", file=sys.stderr)
        want = fallback

for r in rules:
    c = r["config"]
    for k in ("modelOrder", "personalModelIds"):
        if k in c:
            c[k] = sorted(c[k], key=lambda m: 0 if m == "glm-5.3" else 1)
# Preferred provider first, so model resolution lands on a known-good endpoint.
rules.sort(key=lambda r: 0 if r.get("providerName") == want else 1)
json.dump(d, open(dst, "w"), ensure_ascii=False, indent=2)
ZPY
chmod 600 "$PINNED"

export ZCODE_BUILTIN_PROVIDER_CONFIG_FILE="$BUILTIN"
export ZCODE_PERSONAL_PROVIDER_CONFIG_FILE="$PINNED"
args=(--prompt "$PROMPT" --mode "$ZMODE" --no-color)
[[ -n "$SID" ]] && args=(--resume "$SID" "${args[@]}")
exec node "$ENTRY" "${args[@]}"
