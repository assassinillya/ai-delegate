# agy 后端（Antigravity / 反重力）

`--backend agy`。`backends/agy.sh` → 自检 → `_agy_run.py`（权限、退出码都在这里），会话读取 `_agy.py`。
2026-11-25 在 agy 1.5.16 / 1.5.17 上实测。agy **会在后台自动升级**，所以行为以自检为准，不以这份文档为准。

## 自检（每次先过这关）

`agy.sh` 每步先跑 `agy-selftest.py --if-stale`：agy 版本、它的设置、或我们的脚本变了就重跑（约 4 分钟，便宜模型），没变 0.1 秒。
14 项，看磁盘结果和真实工具调用，不看模型自述。不过就**退出 78、什么都不派**。手动跑：`$S/agy-selftest.py`。

- 第一步可能多等 4 分钟，是它在自检，不是卡住。
- 78 时别绕过（`DELEGATE_AGY_SKIP_SELFTEST` 只给自检自己用），看哪项 FAIL，修好再派；修不好就报给用户。
- 改 `_agy_run.py` 的任何参数后必须跑自检。**试过的参数和派活用的参数必须是同一份代码**——2026-11-24 撞墙就是试的是一套、派的是另一套。

## 退出码

| 码 | 意思 | 监督者做什么 |
|---|---|---|
| 0 | 正常结束 | 照常三件套审查 |
| 3 | 超时（`DELEGATE_AGY_TIMEOUT`，默认 `60m`；后台命令不退也算） | 看 diff，`--resume` 收尾，别再起长驻命令 |
| 75 | 有动作被拒、失败或空回复，**这一轮在那里就断了** | `watch.py` 看最后的 error，`--resume` 说"跳过它，继续剩下的" |
| 76 | 会话不能续（不存在，或丢了工作区），**什么都没跑** | 开新会话，给它计划文件和已提交到哪一步 |
| 78 | 自检没过，或权限没生效 | 停下，别派 |
| 1 等 | agy 自己报错（坏模型名、额度、登录） | 看日志；额度/登录问题报给用户 |

**不要把 `run-step.sh` 接管道**（`| tail -5`）：退出码会变成 tail 的 0。输出已经进日志，要短就看日志。

## 权限：为什么是这一套

固定参数：`--sandbox --mode accept-edits --settings '{"toolPermission":"proceed-in-sandbox",…}'`。两个缺一不可：

| 配法 | 实测 |
|---|---|
| 只有 `accept-edits` | 任何命令都被拒，整轮断掉、零产出，agy 退出码还是 0（**2026-11-24 第 1 步撞的墙**） |
| 不带 `--mode`（11-24 的"修好"） | 能跑，但实际是 **Auto 模式**：启动约 3 秒后才异步打开，账号不给就变回全拒；而且 `BypassSandbox` 由另一个模型审，**真的放行出沙箱**（实测写进了家目录） |
| `--mode auto` | 打印"账号不可用"，实际又进了 Auto。这条提示不可信 |
| `--mode plan` | 配 `--disable-slash-commands` 不生效，命令照样被拒 |
| 两个一起（现在用的） | 改文件、沙箱内命令都放行；出沙箱一律拒；不依赖 Auto |

- `--settings` 里的 deny 只能写**绝对路径**：写 `.env` 这种相对路径，会让所有命令 `sandbox configuration error`，状态却是 DONE。
- 命中 deny 规则是软拒绝，这一轮继续；需要"询问"的动作（出沙箱、仓库外读写）在无界面下会**断掉整轮**。
- 每步提示词前自动加一段 Runner rules（不许 BypassSandbox、不许子代理等）。必须有：测试一失败，agy 的输出就会建议它用 `BypassSandbox` 重跑。
- 绝不用 `--dangerously-skip-permissions`。

| mode | 实测 |
|---|---|
| `write` | 仓库和临时目录可写；`.git` 只读（提交不了）；仓库外不可写；断网，**连 localhost 也连不上**（能监听，不能连库） |
| `read` | 命令能跑，仓库只读 |
| `full` | 没有（退出 64）。要联网、连库、起服务的验收由监督者自己跑 |

- 独立工作树：自动放行**只读**主仓库（`.git`、软链的 `.venv` / `node_modules` 在那），主仓库的 `.env*` 除外。否则 git 报 `not a git repository`，用读文件工具碰一下软链就断一轮。
- 还要读别处（比如另一个仓库）：`DELEGATE_AGY_READ=/abs/a:/abs/b`，别让它自己去碰。
- 仓库自己的 `.env` 它读得到（测试要用），靠 `tripwire.py` 事后拦。
- `search_web` 不受沙箱管，拦不住。

## 会话

- 委派用独立数据目录 `~/.gemini/antigravity-cli-delegate`（隐藏参数 `--app_data_dir`，登录共用）。
  原因：**桌面版 Antigravity 盯着 `~/.gemini/antigravity-cli`，新会话一建它就抢开数据库**，95 个里丢了 8 个工作区，还有启动失败（`failed to start conversation`）。
  丢了工作区的会话一 resume，沙箱里连仓库都写不了。隔离后 86 个丢 0 个。
- 启动失败会自动重试两次（那时什么都还没跑）；仍丢工作区的，步骤结尾打 `WARNING`，resume 时 76 拒绝。
- `--conversation` 给错 id，agy 只警告一句就新开空会话、退出 0。`_agy_run.py` 会挡成 76。
- 11-25 之前的会话在旧目录，resume 时自动去那里找。

## 模型

默认 `gemini-4-neon-high`；`DELEGATE_MODEL=<id>` 换（`agy models` 列出）。
`DELEGATE_EFFORT` 和带强度后缀的 id 冲突、直接报错：只给 effort 时自动用 `gemini-4-neon`，自己给模型就用不带后缀的 id。

## 监视

- 记录就是步骤日志里的 stream-json，每份日志只含一步。
- 事件流**不带 `BypassSandbox` 参数**，看不出来它请求过；靠权限挡，表现为 75。
- 子代理显示为 `invoke_subagent`、判红线（里面干什么看不到，但同样出不了沙箱）；`unknown` 是它调了 `ask_question`，无界面下自动跳过，说明它有疑问没人答。
- `run_command error` 且没输出 = 这条没跑过（同一批里前面有动作被拒）。
- 它的 skill 列表里有 `ai-delegate`（如果 `~/.gemini/config/skills.json` 指向了 `~/.claude/skills`），内容已被 deny，命中会触发红线。
- 中途终止会连沙箱里的命令一起杀掉。

## 写计划时顺手写上

- 测试用 `.venv/bin/python -m pytest`，不用 `uv run`（uv 缓存在沙箱里只读）。
- 要连库、连本地服务、联网的检查不要派给它，写明"由监督者跑"。
- 沙箱里会固定失败的测试先列出来，免得它去修。
- 没有 `rg` 时它会报 127，让它用 `grep`。
