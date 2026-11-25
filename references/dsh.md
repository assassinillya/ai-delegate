# dsh 后端（DeepSeek，默认最新发布的模型）

`--backend dsh`。启动脚本 `scripts/backends/dsh.sh` + 固定补丁层 `scripts/backends/dsh.patch.yml`，会话读取 `scripts/_dsh.py`。
**执行模型是 DeepSeek（ds）就必须走这个后端**——不要拿 codex/zcode 去挂 DeepSeek 模型。
以下都是 2026-10-19 在 DeepSeek Harness Desktop 0.2.1-rc.2（macOS arm64）上实测的。

## 前置

- 装 DeepSeek Harness Desktop：`https://download.deepseek.com/desktop/dsh-latest-macos-arm64.dmg`（之后应用内自动更新）。
  **不要再装 npm 的 `@deepseek-ai/dsh`**——那是旧的独立 CLI，和 Desktop 自带的命令会在 PATH 上打架。
- `dsh` 命令：Desktop 菜单 **Manage dsh Command… → Install** 会装到 `/usr/local/bin/dsh`。没装也行，
  `dsh.sh` 会直接用应用包里的 `/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh`；
  `DELEGATE_DSH=<path>` 可强制指定。
- 鉴权：Desktop 里登录 DeepSeek 账号（默认通道），或在 Desktop 的模型设置里存一个 API key（`api-key` 通道）。
  两者都落在 `~/.dsh/.credentials.yaml`（0600）。

## 调用方式

| 用途 | 命令（`dsh.sh` 拼出来的） |
|---|---|
| 新会话 | `DSH_PERMISSION_MODE=<sandbox> dsh --profile headless --patch dsh.patch.yml --patch route.patch.yml --json "<prompt>" </dev/null` |
| 续接 | 同上加 `--session-id <session-…>` |

- `--json` 每行一个事件，**第一行** `{"type":"session","sessionId":"session-…"}` 就是续接用的 id；最后一行 `{"type":"final","text":…}`。
  `run-step.sh` 照常 tee 进日志，`_dsh.py` 从日志里取 id。
- 失败时退出码非 0（实测缺凭据 → `rc=1`，stderr 一行 `dsh: MISSING_CREDENTIAL: …` / `ACCOUNT_SIGN_IN_REQUIRED: …`）。
- 环境里 `all_proxy` 是 SOCKS 时，每次 stderr 打一行 `all_proxy names a SOCKS proxy … connecting directly`，无害。
- 项目的 `AGENTS.md` 会自动加载（`Instructions from: AGENTS.md`）。
- 一次只回一句话的调用 ≈ 7k 输入 token，远小于 codex。

## 模型与通道

`route.patch.yml` 每次运行时生成在 `~/.cache/ai-delegate/dsh/`（不含密钥），覆盖 `agent-default-model`：

| 变量 | 默认 | 含义 |
|---|---|---|
| `DELEGATE_DSH_ROUTE` | `account` | `account` = Desktop 登录的 DeepSeek 账号（`deepseek-account`，和用户 Desktop 自己的默认一致）；`api-key` = 凭据库里的 `DEEPSEEK_API_KEY`（`deepseek-official`） |
| `DELEGATE_MODEL` | **必填，无默认** | 模型 id，按下面"选模型"每次委派前查 |

<a id="选模型"></a>
### 选模型：每次委派前查最新发布

规则：**默认用 DeepSeek 最新发布的模型，Pro / Flash 都可能，看发布日期。** 用户点名了型号就用点名的。

查法固定是用 Agent 工具起一个 `web-fetch` subagent。整页更新日志只在 subagent 自己的上下文里，主对话只收到它最后一行结论。prompt 照抄：

> 读 DeepSeek 官网 API 更新日志 https://api-docs.deepseek.com/zh-cn/updates （打不开就读 https://api-docs.deepseek.com/updates ）。
> 找出**发布日期最新**的那条**模型发布或模型更新**条目（跳过只涉及计费、工具、搜索 API 等非模型的条目），不分 Pro / Flash 系列。
> 只回答三行，不要别的：
> `date: <YYYY-MM-DD>`
> `model: <页面上的模型名>`
> `id: <页面上写的调用 model id，原样照抄>`
> 如果页面同时写了最低 DeepSeek Harness 版本要求，加一行 `harness: <版本>`。

拿到之后：

- `export DELEGATE_MODEL=<id>`，整份计划用同一个；三行原样抄进计划文件头部。
- 有 `harness:` 就和 `dsh --version` 比一下，不够就让用户在 Desktop 里检查更新，先别跑。
- subagent 报不出 id 或者官网打不开 → 停下来问用户，**不要退回到某个写死的型号**。`dsh.sh` 故意没有默认模型，没 `DELEGATE_MODEL` 就 `exit 64`。
- 跑完第一步看 `watch.py` 的 `model` 行，确认真实请求的 `provider/model` 就是这个 id。

参考（2026-10-19 查到的，**不要拿它当默认**）：最新是 DeepSeek-V4.1-Flash-1017（2026-10-17），id `deepseek-flash`，建议 Harness ≥ 0.2.1-rc.1；
`deepseek-v4-pro`（2026-08-13）仍在服务；`deepseek-v4-flash` / `deepseek-v4-flash-vision-exp` 已下线，暂时转到 V4.1 Flash。

- **思考强度固定 `max`**，不管 Pro 还是 Flash（ds 模型一律 max）。`dsh.sh` 写死，**不读** `DELEGATE_EFFORT`——那个变量是给 codex 的，
  整轮 export 过也不会影响 dsh。官网 2026-08-13 起 V4-Pro / Flash 的思考模式支持 low / high / max，实测 `deepseek-flash` 和 `deepseek-v4-pro` 带 `max` 都能跑。
- **"V4.1 Flash 1017" 的 model id 就是 `deepseek-flash`。** 目录名是 `DeepSeek-V4.1-Flash`，`1017` 只是 Desktop 选择器里
  `dsh-client-ui-deepseek-model-label-1017` 加的临时角标，不是独立 id。另一个内置型号是 `deepseek-v4-pro`。
- 核对：`watch.py` 的 `model` 行打印 `request/header` 里真实发出去的 `provider/model (effort)`。
  **别问模型自己是谁**——实测它会自称 Anthropic 的 Claude，那是模型幻觉，请求确实打到了 `deepseek-flash`。
- 某个通道不可用（账号登出/过期 → `ACCOUNT_SIGN_IN_REQUIRED`，API key 缺失 → `MISSING_CREDENTIAL`）：报给用户，
  **不要自己切通道**——两条计费不同，选哪条是用户的事。

## 补丁层 `dsh.patch.yml` 为什么这样写

`headless` profile 出厂就把几样东西关了或开着，不改就跑不通或不安全：

| 条目 | 改成 | 原因 |
|---|---|---|
| `credentials` | 打开 | headless 出厂**关掉了凭据库**，只认启动环境里 export 的 `DEEPSEEK_API_KEY`（实测 Desktop 存了 key 也报 `MISSING_CREDENTIAL`）。打开后 key/账号 token 只在 dsh 进程内读取，**不进 agent 工具进程的环境变量**（实测 `env` 里只有 `DSH_*`）。 |
| `authorization` + `deepseek-account` | 打开（仅 `account` 通道，在 route 补丁里） | 账号插件依赖 authorization 服务；只开账号插件会 `pending (waiting for service: authorization)`。 |
| `session-log-deepseek` | `enabled: false` | 默认开着：每次官方 API 请求**额外**带上完整会话事件日志（含工具输出、文件内容）作为旁路字段上传。仓库和客户数据不该去那里。 |
| `tool-subagent*` / `tool-workflow` | 关 | 子 agent / workflow 写自己的会话文件，`watch.py`/`tripwire.py` 看不到（同 codex 的 `--disable multi_agent`）。 |
| `tool-goal` / `goal-round-driver` | 关 | goal 会让会话自动续跑多轮，越过你按步审查的边界。 |

`session-telemetry-otel` 默认 `FEEDBACK_ONLY`（只在用户主动反馈时上传），没动。

## 沙箱（`--mode`）

`DSH_PERMISSION_MODE`，操作系统级沙箱：

| mode | 值 | 实测 |
|---|---|---|
| `read` | `read-only` | 只读 |
| `write` | `workspace-write` | 仓库内可写；**`.git` 可写**（它自己能 `git commit`，和 codex 不同）；**网络通**（`curl` 通）；`$HOME/.cache` 写入 → `Operation not permitted` / `FS_SANDBOX_DENIED`；`/tmp` 可写 |
| `full` | `danger-full-access` | 无限制，approval `never` |

- **提权申请在 headless 下当场被拒**，不会挂住：沙箱拒绝后会提示它带 `sandbox_permissions` + `justification` 重试，
  重试返回 `sandbox escalation to "danger-full-access" requires approval, but no approval channel is available`。
- `.git` 可写意味着它可能自己提交。你审完照样按步提交，回滚以你的提交为准；`--amend` / `reset --hard` 有红线拦。

## skill 发现

从仓库根起，它能看到仓库 `.agents/skills/` 里的 skill（例如项目自己的验收 skill）和 `~/.agents/skills/` 里的 skill；
有 `skill` 工具按名加载。**不读 `.claude/skills/` 和 `~/.claude/skills/`**（实测）。

## 会话记录（监视用）

`~/.dsh/sessions/<cwd 把 / 换成 ->/<session-id>/session.v4.jsonl.zstd`，例如仓库根
`/Users/me/code/my-repo` 对应 `--Users-me-code-my-repo--`。zstd 帧流，**边跑边追加**，跑到一半 `zstd -dc` 就能读（实测运行中每 5 秒取样一次，行数持续增长、解压不报错）
（需要 `zstd` 命令）。每条事件带 `seq` 和 `time`（epoch ms）。

| 事件 | 用途 |
|---|---|
| `session` | 第一行；`id` / `cwd` |
| `sandbox/mode`、`permission/preset` | 本次的沙箱档位 |
| `request/header` | `header.config` = 真实的 `provider` / `model` / `reasoningEffort`；`header.tools` = 它能用的工具 |
| `assistant/message` | `message.content[]` 里 `reasoning`（**思考原文，明文**）/ `text` / `tool-call`；`usage` 是本次模型调用的 token |
| `tool/call` | `callId` / `name` / `arguments`（JSON 字符串）——**工具调用的权威记录** |
| `tool/result` | `message.toolCallId` / `content[].text` / `isError`；沙箱拒绝带 `error.code: FS_SANDBOX_DENIED` |
| `session/title` | 会话标题 |

`_dsh.py` 的归一化：

- **写入路径**取 `write` / `edit` 的 `file_path`；文件内容不进红线扫描（`scan` 只含工具名 + 路径），这样文档里写到 `launchctl` 不会误报。
  bash 里的写入只能靠命令文本匹配，和 codex 一样。
- **状态**：`isError: true`，或输出里有 `Operation not permitted` / `Read-only file system` / `[sandbox: ` → `error`（计入"连续 3 次报错"）；
  bash 结果结尾 `[exit code: N]` 且 N≠0 → `failed`（正常工作，不计入）；否则 `completed`。
- 没有 `destructive` 标记，这条红线只对 zcode 生效。
- todo 取最后一次 `todo_write` 的 `todos`。

## 已知行为

- 工具：`bash`（每次新 shell，`workdir` 换目录）、`read` / `write` / `edit` / `glob` / `grep`、`todo_write`、`skill`、
  `web_search` / `web_fetch`、后台 job 三件套（`job_list` / `job_output` / `job_kill`）。`bash` 可 `run_in_background`——
  后台 job 在这一步退出后不会再被看到，`watch.py` 里看到 `run_in_background` 要留意。
- 沙箱拒绝后它会照提示去申请提权。实测时 prompt 里写了"失败不要绕路"，它被拒后如实报告了；没写的时候会不会绕路还没测过，按"遇阻会自己绕路"的通用假设盯。
