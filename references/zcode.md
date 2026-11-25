# zcode 后端（glm-5.3）

`--backend zcode`。启动脚本 `scripts/backends/zcode.sh`，会话读取 `scripts/_zcode.py`。

## 现状与 provider

- 订阅过期或额度用完时，请求会返回 `1113 余额不足或无可用资源包,请充值`，退出码 1。
- 模型：`glm-5.3`，`reasoningLevel` headless 下默认就是 `max`，不用设。
- Provider：`GLM_PROVIDER_NAME=<name>`，默认 `glm` = 官方 ZCode / BigModel coding plan
  （`open.bigmodel.cn/api/anthropic`，模型 `GLM-5.3`）。取不到就退出 78；想自动回落到自己配的另一个 provider，
  设 `GLM_PROVIDER_FALLBACK=<providerName>`。
- 权限：`write` / `full` 都映射到 `yolo`，`read` 映射到 `plan`。zcode 没有沙箱。

## 已知行为（都实测过）

- **遇阻它会自己绕路，不会停下来问。** 实测 plan mode 拦了它的 `git branch`，它转头去读 `.git/HEAD`。
- **review/验收阶段别用 plan mode**（`--mode read`）——它连只读 git 都拦。
- **`build` 在 headless 下根本无法写文件**：Edit/Write 返回 `No permission client configured for Edit`，
  而 GLM 只会绕着失败继续试、不会报错退出（2026-09-23 实测空转 70 分钟、540 万 token、零产出）。
  所以写入一律走 `yolo`，前提是已在工作分支且有 baseline commit。
- yolo 下 `.git` 可写，它有可能自己提交。你审完照样按步提交，回滚以你的提交为准；它擅自 `--amend` / `reset --hard` 会被红线拦。

## 为什么 zcode CLI 不能直接用 node 拉起

ZCode 桌面版把完整 CLI 打包在 `/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs`（v0.16.5，`electron-node` runtime，本机 node v22 能跑），但它**没有**装进 PATH。

直接 `node zcode.cjs --prompt …` 会报：

```
无法定位 CLI ZCode Built-in Provider Config：
  /Applications/ZCode.app/Contents/Resources/glm/provider/zcode-builtin.json,
  /config/provider/zcode-builtin.json
```

原因在它自己的解析逻辑里：从入口目录只找两个候选位置——`<entry>/provider/` 和
`<entry>/../../../../../config/provider/`。后者是按开发仓库里 `apps/zcode-cli/packages/cli/dist/`
的深度写死的；装成 .app 之后入口在 `Resources/glm/`，往上五层直接出了根目录，于是落到
`/config/provider/...`。真实文件在 `Resources/config/provider/zcode-builtin.json`，两个候选都不命中。

## 绕过方式

同时提供两个环境变量。源码里是 **and** 关系（`if (!t && !r) return null; if (!t || !r) throw`），
只给一个不生效——`ZCODE_BUILTIN_PROVIDER_BUNDLED_CONFIG_FILE` 单独给也没用，那是另一条路径上的变量：

| 变量 | 值 |
|---|---|
| `ZCODE_BUILTIN_PROVIDER_CONFIG_FILE` | `~/.zcode/v2/runtime/provider/<platform>/<appVersion>/endpoint-<hash>/zcode-builtin.json` |
| `ZCODE_PERSONAL_PROVIDER_CONFIG_FILE` | `~/.zcode/v2/provider_config.json` |

`scripts/backends/zcode.sh` 里按 mtime 挑最新的那份 runtime 配置，免得桌面版升版本后路径写死失效。

## 怎么锁 glm-5.3

headless 模式**不认 slash command**——`--prompt "/model"` 会被当成普通话，它回你"这是客户端命令，
请用客户端的模型选择器"。也没有 `--model` 这个 flag（`--help` 里没有，bundle 里搜到的
`--model` 全是别的工具的 shell 补全数据）。

模型是从 personal provider config 的 `modelOrder` 头部解析出来的。如果 `modelOrder` 是
`[kimi-k3, glm-5.3, …]`，headless 默认跑的就是 **kimi-k3**，不是 GLM。

`backends/zcode.sh` 的做法：每次运行时从用户真实配置**复制**一份，把 `glm-5.3` 排到
`modelOrder`/`personalModelIds` 首位、把首选 provider 排到 `providerRules` 首位，写到
`~/.cache/ai-delegate/zcode/provider_config.glm53.json`（0600），再用
`ZCODE_PERSONAL_PROVIDER_CONFIG_FILE` 指过去。

两个要点：

- **不动用户全局配置**，桌面版照常用它自己的顺序。
- 这份复制件**带 API key**，所以放 cache 目录且 0600，**绝不能进仓库**。每次运行重新生成，
  用户改了自己的配置它会跟上。

验证方式：`--prompt "只回答一行：你当前的模型 id 是什么"` → `<providerId>/glm-5.3`。

## reasoning level

`message.data` 里记着 `"modelSelection": {"modelId": "glm-5.3", "options": {"reasoningLevel": "max"}}`
——headless 下默认就是 `max`，不需要额外设置。所谓 "glm5.3 max" 指的是这个 reasoning level，
不是一个独立的 model id；`glm-5.3-max` 这个 model id 不存在。

可用的 glm 型号：官方 builtin 配置里是 `glm-5.1` / `glm-5.2`；`glm-5.3` 只来自 personal
provider。

## 会话库（监视用）

`~/.zcode/cli/db/db.sqlite`，WAL 模式，**跑到一半就能读**：

| 表 | 用途 |
|---|---|
| `session` | `id` / `directory` / `title` / `time_created`，按 cwd 找当前会话 |
| `tool_usage` | 每次工具调用的 `status` / `read_only` / `destructive` / `exit_code` / `duration_ms` |
| `part` | `type=tool` 带 `callID` + `tool` + `state.input`（精确参数）+ `state.output`；`type=reasoning` 是模型自己的思考原文 |
| `message` | 每轮的 `modelId` / `mode` / `tokens` / `cost` |
| `todo` | 它自己的任务清单（`content` / `status` / `position`） |

`part.callID` 就是 `tool_usage.tool_call_id`，两表靠它 join。实测工具行在调用后约 1 秒落库。
`scripts/_zcode.py` 以 `mode=ro` 打开，监督者不往 agent 的库里写。

## 其他坑

- **skill 按 cwd 解析。** 必须在项目根目录起 zcode，项目的 skill（例如项目自己的验收 skill）才出现在它的 skill 列表里
  （`zcode skills list` 在别的目录跑就看不到）。
- **`--max-turns` 是假的。** `--help` 里列了，实际解析器不认，传了直接打印 help 退出。
- zcode 共享 `~/.agents/skills/`，但**不读** `~/.claude/skills/`。


## 官方订阅（`glm`）怎么接进 headless

2026-09-23 查清：headless 解析模型**只看** personal provider config 的 `providerRules`，
且只认 `access.type: api-key`。builtin 里那些 `zhipu-account`（OAuth 账号态）条目，
无论账号是否登录，都只会得到 `Model creation failed` —— 在一次**成功的 Z.AI OAuth 登录之后**
（`credentials.json` 确实写入了 `oauth:zai:access_token`）复测，结果不变。所以 `zcode login`
这条路对 headless 无效。

真正可用的钥匙在 `~/.zcode/v2/config.json`：

| 键 | baseURL | apiKey |
|---|---|---|
| `builtin:bigmodel-coding-plan` | `https://open.bigmodel.cn/api/anthropic` | 明文 |
| `builtin:bigmodel-start-plan` | `https://zcode.z.ai/api/v1/zcode-plan/anthropic` | 明文（仅 GLM-5.3-Flash） |

`backends/zcode.sh` 在 `GLM_PROVIDER_NAME=glm`（默认）时，从前者合成一条 `providerName: glm` 的
personal 条目（`anthropic-messages` + `GLM-5.3`）写进 0600 的 pinned config。
key 只在运行时从用户自己的配置里读，不落仓库。

另注：`coding-plan-cache.json` 里能看到各 plan 的 `available` / `coding_plan_not_entitled` 状态。
