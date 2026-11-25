# ai-delegate

一个 Claude Code skill：让 Claude 当**监督者**，把实施计划一步步交给本机的另一个编码 agent 去写，自己负责出计划、盯过程、查 diff、拦危险动作和判验收。

> A Claude Code skill that turns Claude into a supervisor: it plans, dispatches bounded steps to another local coding agent, reviews every diff, trips on red lines, and judges acceptance. Docs are in Chinese.

## 支持的后端

| 后端 | 下层 agent | 隔离 |
|---|---|---|
| `codex`（默认） | Codex CLI | 系统沙箱：`.git` 只读、断网 |
| `agy` | Antigravity CLI（反重力） | 系统沙箱：`.git` 只读、断网，派活前自检 |
| `dsh` | DeepSeek Harness Desktop | 系统沙箱：`.git` 可写、网络通 |
| `zcode` | ZCode CLI（glm-5.3） | **没有沙箱**，只靠事后红线 |

## 安装

```bash
git clone https://github.com/assassinillya/ai-delegate ~/.claude/skills/ai-delegate
```

需要 macOS、`git`、`python3`、Claude Code，以及你要用的那个后端（自己装好并登录）。`dsh` 另需 `zstd`，`zcode` 另需 `node`。

## 用法

在任意 git 仓库里对 Claude Code 说：

> 写好计划，让反重力去实现

也可以单独用脚本：

```bash
S=~/.claude/skills/ai-delegate/scripts
$S/run-step.sh --backend agy "第 1 步：……"
$S/run-step.sh --backend agy --resume <会话 id> "只修这一处：……"
$S/watch.py       # 它做了什么
$S/tripwire.py    # 有没有越红线
```

## 怎么工作

1. 开工作分支，记下基线；没有 checkpoint，git 是唯一的撤销手段。
2. 一次只派一个有界的步骤，退出就是审查点。
3. 每步看工具调用、红线和真实 diff，过了由监督者提交；不过就 resume 纠正。
4. 自查之后按清单验收，最多 3 轮。

完整规矩见 [SKILL.md](SKILL.md)，各后端的实测细节见 [references/](references/)。

## 反重力后端的自检

agy 会在后台自动升级，权限行为可能跟着变。所以每次派活前会检查 agy 版本、设置和脚本，变了就重跑 14 项自检（约 4 分钟，消耗少量额度）：能不能写仓库、能不能写仓库外、断网、`BypassSandbox` 会不会被放行、resume、只读模式、工作树等。不过就不派。

自检会在家目录建一个 `.ai-delegate-selftest.*` 临时目录，结束后删除。

## 注意

- 只在 macOS 上实测过。文档里的版本号和行为是写下时的情况，各家 CLI 更新后可能变。
- 下层 agent 会真的执行命令。`zcode` 没有沙箱，`--mode full` 也会关掉沙箱，先确认你接受。
- 仓库里的 `.env` 下层 agent 能读到，红线只能事后发现；联网搜索也不受沙箱限制。
- `agy` 用了一个没写进官方文档的参数 `--app_data_dir`，用来避开桌面版抢会话数据库的问题。
- `zcode` 会从你本机的 ZCode 配置读取 API key，在 `~/.cache/ai-delegate/zcode/` 生成一份 0600 的临时配置；别把它提交或分享出去。
- 非官方项目，与上述各家厂商无关；请自行确认符合你所用服务的条款。

## License

[MIT](LICENSE)
