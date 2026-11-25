# codex 后端（默认）

`--backend codex`，或不指定。启动脚本 `scripts/backends/codex.sh`，会话读取 `scripts/_codex.py`。
以下都是 2026-10-08 在 codex-cli 0.161.0 上实测的。

## 调用方式

| 用途 | 命令 |
|---|---|
| 新会话 | `codex exec -c sandbox_mode="<sandbox>" --disable multi_agent "<prompt>" </dev/null` |
| 续接 | `codex exec resume -c sandbox_mode="<sandbox>" --disable multi_agent <thread_id> "<prompt>" </dev/null` |

- 登录用的是用户自己的 ChatGPT 账号（`codex login status` → `Logged in using ChatGPT`），走订阅额度，不需要 API key。
- 模型、reasoning effort、MCP server、插件全部继承 `~/.codex/config.toml`。
  要换模型用 `DELEGATE_MODEL=<id>`（传给 `-m`），换 effort 用 `DELEGATE_EFFORT=<low|medium|high|xhigh|max>`（传给 `-c model_reasoning_effort`）。
- **`exec resume` 不认 `-s` 也不认 `-C`**，所以沙箱统一走 `-c sandbox_mode=…`，cwd 由 `run-step.sh` 切到项目根。
- **stdin 必须关掉。** 给了 prompt 参数它照样读 stdin 拼到后面（日志里那行 `Reading additional input from stdin...` 是正常的）；
  后台起的时候 stdin 如果是开着的管道，它会一直等。
- 输出头部有一段 banner，其中 `session id: <uuid>` 就是 thread id，`resume` 用它。`run-step.sh` 把输出 tee 进日志，
  `_codex.py` 从日志里取这个 id。
- **子 agent 关掉了**（`--disable multi_agent`）：它 `spawn_agent` 出来的子 agent 写的是另一个 rollout 文件，`watch.py`/`tripwire.py` 看不到。
- 项目的 `AGENTS.md` 它会自动读。

## 沙箱（`--mode`）

| mode | sandbox_mode | 实测 |
|---|---|---|
| `read` | `read-only` | `touch` → `Operation not permitted` |
| `write` | `workspace-write` | 仓库内可写；`git commit` → `.git/index.lock: Operation not permitted`；仓库外 `touch` → `Operation not permitted`；`curl` → `Could not resolve host`（断网） |
| `full` | `danger-full-access` | 无限制 |

- `write` 下要联网可以加 `-c sandbox_workspace_write.network_access=true`（实测 `curl` 通了），
  但要写仓库外的东西（例如某些 CLI 刷 token 写家目录）仍会被拦——所以验收直接用 `full`。
- `exec` 模式下 `approval_policy` 固定是 `never`：被沙箱拦就是失败，不会弹审批。

## skill 发现

- 从仓库根起，它能看到仓库的 `.agents/skills/`（例如项目自己的验收 skill），
  外加 `~/.agents/skills/`、`~/.codex/skills/` 和插件自带的 skill。
- **不读 `.claude/skills/` 和 `~/.claude/skills/`**——本 skill 的红线清单不会被它自动加载（实测它列出的 skill 里没有 `ai-delegate`）。
- 如果装了会接管流程的插件（例如 `superpowers`），codex 有时会先去读它的说明再干活。
  无害，但如果它开始按那套流程自己写计划/拆任务，就是偏离了你下发的步骤，resume 拉回来。

## 会话记录（监视用）

`~/.codex/sessions/YYYY/MM/DD/rollout-<时间>-<thread_id>.jsonl`，边跑边追加，跑到一半就能读（最后一行可能写了一半，跳过即可）。

| 记录 | 用途 |
|---|---|
| `session_meta` | 第一行；`payload.id` / `payload.cwd` |
| `turn_context` | 每轮的 `model` / `effort` / `sandbox_policy.type` |
| `response_item` · `custom_tool_call`（`name=exec`） | **工具调用的权威记录**：`input` 是一段 JS，里面调 `tools.exec_command({cmd})`、`tools.apply_patch("*** Begin Patch…")` 等，一段可能连跑好几条命令 |
| `response_item` · `function_call` | MCP 等其他工具，`arguments` 是 JSON 字符串 |
| `…_output`（同 `call_id`） | 输出；以 `Script completed` / `Script failed` 开头，每条命令的结果是一段带 `"exit_code"` 的 JSON |
| `event_msg` · `item_completed` | 规范化的 `CommandExecution` / `FileChange` / `AgentMessage` / `Reasoning` 等 |
| `event_msg` · `token_count` | `info.total_token_usage` 是整个 thread 的累计值 |

**工具调用必须读 `custom_tool_call`，不能读 `item_completed`。** 实测一段 JS 里连跑 `apply_patch` + `python3 hello.py` + `git commit`，
只有前两条产生了 `CommandExecution`/`FileChange` 事件，`git commit` 那条完全没有——`codex exec --json` 的事件流也一样漏。
所以 `_codex.py` 以 JS 包装调用为单位：一次包装 = 一条工具调用。

`_codex.py` 的归一化：

- **写入路径**从补丁头 `*** Add/Update/Delete File:` / `*** Move to:` 解析；补丁正文从输入里剥掉，只留 `*** patch: <路径>`，
  这样文件内容里的关键词不会触发红线，同一段 JS 里的命令照样被扫。
- **状态**：输出里有 `Operation not permitted` / `Read-only file system` / `Could not resolve host`，或以 `Script failed` 开头 → `error`
  （被沙箱拦或工具本身失败，计入"连续 3 次报错"）；有非 0 的 `exit_code` → `failed`（测试没过、grep 没匹配，正常工作，不计入）；否则 `completed`。
- 没有 `destructive` 标记（codex 不提供），这条红线只对 zcode 生效。
- **看不到思考过程**：`reasoning` 是加密的，summary 为空。`watch.py` 只能显示它发的说明文字（`AgentMessage`）。
- 没有 todo 表。

## 其他

- 一个只回一句话的调用也要 ~40k 输入 token（系统提示 + AGENTS.md + 工具定义），大部分命中缓存。
- 额度是 ChatGPT 订阅的 5 小时 / 周窗口，`token_count.rate_limits` 里有 `used_percent`。
