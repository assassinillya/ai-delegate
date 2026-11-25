---
name: ai-delegate
description: 把一份实施计划交给本机的另一个编码 agent 去执行（默认 Codex CLI；可选 ZCode CLI 上的 glm-5.3；执行模型是 DeepSeek 时用 dsh，默认用 DeepSeek 最新发布的模型；反重力 / Antigravity 用 agy），Claude 全程做监督者：出计划、分步下发、读它的会话记录实时盯、越红线就中断回滚、然后按验收清单（项目有自己的验收 skill 就用它）做验收并在不通过时让它 debug 重验（上限 3 轮）。全局 skill，任何 git 仓库都能用。Use when 用户要求「让 codex / GLM / zcode / ds / DeepSeek / dsh / 反重力 / antigravity / agy / 别的模型去实施这个计划」、委派实施、监督另一个 agent 干活、或者要跑「实施→自查→验收→debug」这条流水线。
---

# 委派实施（ai-delegate）

角色分工：**你出计划、下发、监视、拦截、判验收**；**下层 agent 写代码**。你不替它写实现，它不替你判对错。

这是**全局 skill**，装在 `~/.claude/skills/ai-delegate/`，任何 git 仓库里都能用。脚本都在 `~/.claude/skills/ai-delegate/scripts/`，下面写作 `$S/`
（`S=~/.claude/skills/ai-delegate/scripts`）。脚本以当前目录所在的 git 仓库根为工作区，所以从目标仓库里调用。

故意不放 `~/.agents/skills/`：codex / zcode / dsh 都会读那个目录，监督者的红线清单不该被被监督的 agent 看到。

## 后端

| 后端 | 下层 | 何时用 | 细节 |
|---|---|---|---|
| `codex`（**默认**） | Codex CLI，用户的 ChatGPT 登录与 `~/.codex/config.toml` 里的模型 | 用户没指定时 | [references/codex.md](references/codex.md) |
| `zcode` | ZCode.app 自带 CLI，钉死 glm-5.3 | 用户点名 GLM / zcode | [references/zcode.md](references/zcode.md) |
| `dsh` | DeepSeek Harness Desktop 自带的 `dsh` headless，模型 = 开工前 subagent 从官网更新日志查到的最新发布模型（Pro / Flash 都可能）+ 思考强度固定 `max` | 用户点名 ds / DeepSeek / dsh | [references/dsh.md](references/dsh.md) |
| `agy` | Antigravity CLI（反重力），用户的订阅登录，默认 gemini-4-neon-high | 用户点名 antigravity / 反重力 | [references/agy.md](references/agy.md) |

用户点名哪个就用哪个：`--backend <name>`，或者整轮 `export DELEGATE_BACKEND=<name>`。codex、dsh、agy 换模型用 `DELEGATE_MODEL=<id>`；codex 和 agy 换 effort 用 `DELEGATE_EFFORT=<level>`。**dsh（DeepSeek 模型）思考强度一律 `max`**，脚本写死，不受 `DELEGATE_EFFORT` 影响。

**执行模型是 DeepSeek（ds）就必须用 `dsh` 后端，模型默认用 DeepSeek 最新发布的那个**——不管是 Pro 还是 Flash，按发布日期最新的来。
用户没点名型号时，每次委派开工前（第 0 步里）用 Agent 工具起一个 `web-fetch` subagent 查官网更新日志，prompt 照抄
[references/dsh.md](references/dsh.md#选模型) 里的那段——让它在自己的上下文里读整页、只回一个 id，日志全文不进主对话。
然后整轮 `export DELEGATE_MODEL=<它报的 id>`，同一份计划的所有步骤（含 resume、验收）都用这一个；把"查询日期 + 最新条目 + id"写进计划文件头部。
`dsh.sh` 没有默认模型，不给 `DELEGATE_MODEL` 直接退出——查不到（官网打不开、subagent 报不出 id）就停下来问用户，别自己猜。
dsh 默认走 Desktop 登录的 DeepSeek 账号；`DELEGATE_DSH_ROUTE=api-key` 改走 API key——两条计费不同，用户没说就别换。

**加新后端** = 加两个文件：`scripts/backends/<name>.sh`（参数 `<read|write|full> <resume-id 或空> <prompt>`，在 cwd 里跑一步、退出码即结果）和 `scripts/_<name>.py`（读它的会话记录，接口见 `_session.py` 顶部）。其他脚本不用动。
**接完必须经 `run-step.sh` 实测再派活**：跑命令、改文件、写仓库外、提交、resume、故意做一个会被拒的动作，逐项看磁盘结果和退出码。
裸跑 CLI 试通不算——2026-11-24 的 agy 就是裸跑试了一套参数、脚本里写了另一套，第一步零产出还退出 0。

## 为什么是分步 resume，而不是一个大 prompt

一次调用从起到退之间你只能看，插不了话，唯一的中途干预是 kill，代价是整个 turn 白扔。但每个后端都能带完整上下文续接（codex/zcode 用 `--resume`，dsh 用 `--session-id`，agy 用 `--conversation`，`run-step.sh --resume` 统一处理；都已验证）。

所以节奏是：**下发一个有界的步骤 → 等它退出 → 你查 → 放行下一步 或 resume 喂纠正**。
"中断" 只留给真正越线的动作，那时才 kill + git 回滚。

## 权限档位（`--mode`）

| mode | codex | zcode | dsh | agy | 用在 |
|---|---|---|---|---|---|
| `read` | `read-only` 沙箱 | `plan`（连只读 git 都拦，基本别用） | `read-only` 沙箱 | 仓库只读，命令能跑 | 让它只读调研 |
| `write`（默认） | `workspace-write`：仓库可写，**`.git` 只读、断网、仓库外不可写** | `yolo` | `workspace-write`：仓库和 `/tmp` 可写，**`.git` 可写、网络通**，其他仓库外路径不可写 | 仓库和临时目录可写，`.git` 只读，断网（含 localhost） | 实施、自查、debug |
| `full` | 无沙箱 | `yolo` | `danger-full-access` | **没有**，验收由你跑 | 验收（要联网、写仓库外的 token 缓存、碰正在跑的本地服务） |

codex、dsh、agy 的沙箱是操作系统级的硬边界（agy 的由每次派活前的自检保证，见 [references/agy.md](references/agy.md)）（dsh 在 headless 下的提权申请会当场被拒）；zcode 没有沙箱，全靠下面的红线事后拦。

## 0. 前置（每次委派必做，不许跳）

```bash
git rev-parse --abbrev-ref HEAD                     # 不能在 main/master 上干
git status --short                                  # 已跟踪文件必须干净；未跟踪的本地文件保持未跟踪
git switch -c <backend>/<slug>                      # 开工作分支
git rev-parse HEAD                                  # 记下 BASELINE，写进计划文件，回滚靠它
mkdir -p .ai-delegate/plans                         # 计划目录；只在本地忽略，不改项目的 .gitignore：
grep -qxF '.ai-delegate/' "$(git rev-parse --git-path info/exclude)" 2>/dev/null \
  || echo '.ai-delegate/' >> "$(git rev-parse --git-path info/exclude)"
```

主 checkout 有别的 agent 或人正在改（有未提交改动、分支被切走）就别在里面开分支，用 `git worktree add ../<repo>-<slug> -b <backend>/<slug>` 另开一个工作树，在那里委派。

后端是 `dsh` 且用户没点名型号：这里先起 `web-fetch` subagent 查 DeepSeek 最新发布的模型，`export DELEGATE_MODEL=<id>`（见上面"后端"一节）。

**不要 `git add -A` 做基线提交**：它会把本来未跟踪的本地文件（备份、配置）带进分支，合并前只能 rebase 掉。已跟踪的改动先按路径单独提交，然后直接用 HEAD 当基线。

headless 下没有 checkpoint 可用——**git 是唯一可靠的撤销**。

## 1. 出计划（你写）

写到 `.ai-delegate/plans/YYYY-MM-DD-<slug>.md`（第 0 步已在本地排除，不进 git；在仓库内，各后端都能直读）。仓库已经有自己的计划目录约定就用它的。每步必须满足：

- **有界**：一步一个关注点，预期 10-25 次工具调用。超出就是切得太粗。
- **可验证**：写清"做完怎么算对"——跑哪条命令、看哪个文件的哪个字段。
- **不含实现**：给约束和验收口径，不给代码。它会照抄，然后你失去了独立的判断基准。

文件头部固定带：`BASELINE=<sha>`、`分支`、`后端`（dsh 再加模型查询的 `date` / `model` / `id` 三行）、`验收项清单`（每项写清用什么证据判定）、一个空的 `## 执行记录` 段（你逐步往里追加）。

## 2. 分步下发 + 监视

```bash
$S/run-step.sh "第 N 步：…（把计划文件路径告诉它，要求只做这一步）"
$S/run-step.sh --resume <sess_id> "上一步有问题：…。只修这个，别动其他。"
$S/run-step.sh --backend zcode "…"            # 用户点名 GLM 时
$S/run-step.sh --backend dsh "…"              # 用户点名 ds / DeepSeek 时
$S/run-step.sh --backend agy "…"              # 用户点名反重力时；第一步可能先自检约 4 分钟
```

**用 Bash 工具的 `run_in_background: true` 起它**——它退出时你会被自动唤醒，这就是 review 检查点。退出时最后一行是 `--- exit=<rc> session=<sess_id>`，后续 resume 用这个 id。
**别把它接管道**（`| tail`），退出码会被吃掉。`rc≠0`、或 `rc=0` 但 diff 是空的，都按"没做成"处理，先看日志尾部再决定 resume 还是重派。agy 的 75 / 76 / 78 见 [references/agy.md](references/agy.md#退出码)。
**不要 sleep 轮询。** 需要在 turn 内盯的只有一种情况：长 turn 里它开始做危险动作。那用 Monitor 盯：

```bash
$S/tripwire.py >/dev/null 2>&1; echo "TRIP=$?"   # TRIP=1 就叫你
```

每步退出后固定做三件事，缺一不可：

```bash
$S/watch.py                      # 它调了什么工具、参数是什么、它自己怎么说
$S/tripwire.py; echo "exit=$?"   # 红线
git diff --stat && git diff      # 它到底改了什么（别信它的自述）
```

三件事过了，**由你提交这一步**（提交信息照项目的 `AGENTS.md` / `CLAUDE.md` / `git log` 风格；codex 和 agy 在 `write` 下 `.git` 只读，本来也提交不了；zcode 和 dsh 能自己提交，回滚照样以你的提交为准）。这样每步都有干净的回滚点。

`watch.py` / `tripwire.py` 跑到一半也能读，按当前步骤的后端自动选读法（`--backend` 可强制）。

## 3. 让它自查

进验收之前，先 resume 一轮自查：

> 先不要进验收。回头 review 你这几步的产出：和 `.ai-delegate/plans/<file>` 的验收项逐条对，跑一遍构建/测试，把你自己发现的问题列出来并修掉。修完只汇报"改了什么、为什么"。

自查后你再跑一遍第 2 步那三件事。**它说"没问题"不算数，看 diff 和命令输出。**

## 4. 验收（上限 3 轮）

验收按计划文件的验收项清单走，每条都要"通过/不通过 + 证据"（命令输出、API 返回、日志原文）。怎么验看项目：

- **项目有自己的验收 skill**（在仓库的 `.agents/skills/` 或 `.zcode/skills/` 里）就让下层 agent 用它，并遵守那个 skill 自己的规矩。
- **没有**就让它逐条跑验收项里写的命令，把原始输出贴出来。你自己再抽几条复跑，不只看它的结论。

通用规矩：

- **轮次上限 3。** 3 轮不过就停下来叫用户，不许继续硬试。
- **需要构建、重启本地服务、部署的事你来做**，在下发验收之前做完（`launchctl`、部署脚本、`ssh` 对下层 agent 是红线）。项目的 `AGENTS.md` 写了"修完要重启并确认新版本已加载"之类的要求，照做。
- **验收必须在项目根目录起**，否则项目里的验收 skill 不在它的 skill 列表里（skill 按 cwd 解析；codex 和 dsh 经 `.agents/skills/`，zcode 经 `.zcode/skills/`）。
- **headless 不认 slash command**，得在 prompt 里用自然语言点名 skill。
- 验收有对外副作用（发消息、写线上数据）时**每轮留痕**：每轮结束立刻 `$S/watch.py --sends`（或看 `watch.py` 的工具调用），把实际发出去的内容抄进计划文件的 `## 执行记录`，跑完一并汇报给用户。

后端是 `agy` 时没有 `full`：你自己逐条跑验收项，不通过的用 `write` resume 喂给它修。其他后端：

```bash
$S/run-step.sh --mode full --resume <sess_id> \
  "现在做验收：按 .ai-delegate/plans/<file> 的验收项清单逐条验（项目有验收 skill 就使用它）。
   每条验收项给出'通过/不通过 + 证据（命令输出 / API 返回 / 日志原文）'。
   不要为了让验收通过去改验收脚本、测试或放宽验收口径。"
```

不通过 → resume 喂 debug（回到 `write`；带上你自己从 diff/日志看出来的线索，别只转述它的结论）→ 再验。计数到 3。

### 例：会真实外发的验收

如果项目的验收 skill 会**以用户本人身份真的发消息**、写日历或文档，发出去是撤不回的。
验收前先重启相关服务并确认新构建已加载；prompt 里要求它先跑那个 skill 的预检脚本；`watch.py --sends` 按 `send-message.sh` 统计外发（脚本名不同就改 `watch.py` 的 `SEND_HINT`）。

## 5. 收尾

验收通过后：跑构建/测试、`git diff $BASELINE` 终审、把 `## 执行记录` 补全、汇报给用户。**不要自己 push**，也不要合回 main——交付是给用户看分支和记录。

## 红线与升级

`tripwire.py` 命中（exit 1）→ **立刻** TaskStop 掉那个后台任务，`git reset --hard <上一个通过的步骤提交>`（或只 checkout 被污染的文件），然后 resume 讲清它越了哪条线。它盯这些：

agent 自己标 `destructive` 的调用（zcode）· `git push` / `reset --hard` / `clean -f` / `--amend` / 切回 main · `rm -rf` · `launchctl` · deploy 脚本 · `ssh`/`scp` · provider_config.json 与其他密钥材料 · `.env` · 改本 skill 自己 · 往仓库外写文件 · 连续 3 次工具报错（codex / dsh / agy 指被沙箱拦下或工具本身失败，命令正常跑完但退出码非 0 不算）· 单步工具调用超 50 次

红线按**命令文本和写入路径**匹配，不看写进文件的内容；但它自己写的校验命令里带了关键词也会命中——看一眼是不是这种误报。
网络调用故意**不**拦——验收经常要打外部 API。
误报用 `DELEGATE_ALLOW=<regex>` 压，加自己的红线用 `DELEGATE_DENY_EXTRA=<regex>`。

**什么时候停下来叫用户**（别自己硬扛）：

- 连续 2 步纠正后仍无实质进展
- 验收 3 轮不通过
- 红线命中且不是显然的手滑
- 它要改的东西超出计划范围，而你判断这个扩张可能是对的——范围是用户的决定，不是你的
- 后端本身不可用（额度/订阅/登录问题、agy 自检不过）——报给用户，不要自己换后端

## 通用已知行为

- **遇阻它会自己绕路，不会停下来问**（GLM 实测，codex 也按这个假设）。读 `watch.py` 里它的说明和工具调用，绕路本身就是跑偏信号。
- 日志在 `~/.cache/ai-delegate/logs/`（`DELEGATE_LOG_DIR` 可改），故意放在仓库外——不然会污染你用来 review 的 git diff。
- 各后端自己的坑（模型锁定、沙箱细节、会话记录格式）见各自的 reference。
