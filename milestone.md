# Milestones

每完成一个阶段就更新这里：这阶段**多加了什么**、为什么加、怎么验证。
完整设计见 [outline.md](outline.md)，踩过的坑见 [error.md](error.md)。
每个 milestone 在架构图上的位置见 [docs/architecture.md](docs/architecture.md)。

状态：⬜ 未开始　🟡 进行中　✅ 完成　🗄 封存

**每个 milestone 的固定流程**
1. 实作
2. 测试（单元测试 + 实际跑一次）
3. code review（`/code-review`，范围是这个 milestone 的档案）
4. 修正（挑要修的项目，修正记进 error.md）
5. 更新文件（milestone.md、README、架构图）
6. commit（一个 milestone 一个 commit，格式 `M<n>: ...`）

| # | 阶段 | 状态 |
|---|---|---|
| 0 | 规划：写 outline | ✅ |
| 1 | Telegram echo bot + 白名单 | 🗄 封存 |
| 2 | Remote Control 跑通（Ubuntu + Mac）+ preflight 检查 | 🟡 Ubuntu 完成，Mac 待测 |
| 3 | 安全层：settings + policy + hook | ✅ Ubuntu 手机实测通过（Mac 待测） |
| 4 | Audit log + git checkpoint | ✅ Ubuntu 手机实测通过（Mac 待测） |
| 5 | 一直在线 + 安全等级：启动脚本、systemd / launchd、防休眠 | ⬜ |
| 6 | v0.2 功能 | ⬜ |

> 原本的 M7「GUI 美化」取消：介面就是 Claude app。

---

## M0 规划 ✅（2026-11-10）
**多加了什么**
- `outline.md`：目标、架构、Tech Stack、Scope、项目结构、验证方式
- 跨平台：Ubuntu + macOS 都支持
- 远端安全：Allow / Ask / Deny 三层，Deny 层手机批准也无效

**方向调整（2026-11-10）**：Telegram bot → Claude app + `claude --remote-control`
- 官方账号登录、没有 bot token 要保管、权限确认是内建的
- 项目改成只做 Claude Code 外围的三件事：安全层、一直在线、记录与还原

---

## M1 Telegram echo bot + 白名单 🗄（2026-11-10 封存）
**状态**：代码完成、9 个单元测试通过，没有做手机实测；方向调整后不再开发。

**当时加了什么**
- conda env `remote-ctrl-agent`（Python 3.12），`environment.yml` + `requirements.txt`
- `config.py`：读 `.env`（`BOT_TOKEN`、`ALLOWED_USER_IDS`、`WORK_DIR`）
- `bot.py`：`/start` + 文字 echo，白名单检查；白名单为空时只回传对方 user id
- `.env.example`、`.gitignore`、`pytest.ini`、`tests/test_m1.py`

**之后的处理**（2026-11-10 已完成）
- 已移到 `legacy/telegram/`（`bot.py`、`config.py`、`.env.example`、`test_m1.py`），测试不再自动跑
- `requirements.txt` 已拿掉 Telegram 相关套件，只留 `bashlex`、`pytest`

**学到的概念**：BotFather、bot token、`python-telegram-bot` handler、long polling

---

## M2 Remote Control 跑通（Ubuntu + Mac）⬜
**目标**：确认官方功能在**两台**电脑上都能用，并把不确定的地方查清楚。只写一个检查脚本，不写其他代码。
**会多加**：`scripts/preflight.py`（检查项目见 outline.md「Remote Control 前置条件」，只用标准库，两个平台都能跑）

**preflight.py 已完成（2026-11-10）**
- 多加：`platform_utils.py`（平台差异、找工具路径、`sanitized_env()`）、`scripts/preflight.py`、`tests/test_preflight.py`（13 个测试）
- 规则（依你的回答）：
  - ❌ 擋住：没有 `--remote-control`、没登录 / 不是 claude.ai 订阅、登录已过期、**没设定可信账号或账号不一致**、缺 tmux、缺防休眠工具、工作目录不存在或不是 git repo
  - ⚠️ 警告：登录 < 7 天到期、侦测到 `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN`（启动时直接盖掉）、没设定工作目录、缺 conda env
  - 不读任何 `.env`，不印出 key 的值；登录检查用「移除 key 后的环境」跑，和实际启动时一致
- 可信账号存在本机设定档（不在 repo 里，权限 600）：Ubuntu `~/.config/remote-ctrl/config.json`、Mac `~/Library/Application Support/remote-ctrl/config.json`
  - `--trust <orgId>` 只有在这台电脑**目前登录的就是这个 orgId** 时才会写入，所以 Mac 登错账号时无法被设成可信
- Ubuntu 实测：可信账号已设定，全部通过（只剩「工作目录」⚠️，之后再加）
- code review 后修了 8 个问题（细节见 error.md），测试增加到 32 个
  - 会被移除的环境变量扩充为 7 个，包含 `CLAUDE_CODE_OAUTH_TOKEN`（`claude setup-token` 的长期 token）。若之后决定用长期 token 解决登录到期，要回来调整这条规则
- `preflight.py` 拆成 `preflight/` 资料夹（models、commands、config_store、checks_claude、checks_system、runner、cli），入口仍是 `python3 scripts/preflight.py`，测试 35 个全过
- Mac：还没跑

**设定可信账号（你来做）**
```bash
python3 scripts/preflight.py --show-org          # Ubuntu：印出 orgId，确认是你自己的账号
python3 scripts/preflight.py --trust <orgId>     # Ubuntu 写入
# Mac：claude auth login 后，用同一个 orgId 跑 --trust；账号不同会拒绝写入
python3 scripts/preflight.py                     # 两台都要全部没有 ❌
```

**步骤**
1. 手机 Claude app 登录同一个 Anthropic 账号，开启两步验证
2. 两台电脑各跑 `python scripts/preflight.py`，全部 ✅ 才继续（Mac 若缺 `--remote-control` 先 `claude update`）
3. 两台电脑各执行 `claude --remote-control test`
4. 手机上找到两个 session，下 prompt、看进度、批准一次权限请求

**已知（2026-11-10，Ubuntu）**
- Claude Code 2.1.322，`claude --help` 有 `--remote-control` 和 `--remote-control-session-name-prefix`（默认 hostname）
- `claude auth status --json`：`authMethod: claude.ai`、Pro 方案、登录到期 2026-12-08
- CLI 内建背景 session：`claude --bg`、`attach`、`logs`、`stop`、`respawn`

**要查清楚的问题**（答案记在这里）
- [ ] Mac 上的 Claude Code 版本？有没有 `--remote-control`？
- [ ] Remote Control 一定要订阅登录吗？用 `claude setup-token` 的长期 token 能用吗？（解决登录到期问题）
- [ ] 能不能从手机开新的 session，还是只能接已经开着的？
- [ ] 电脑端 session 关掉 / 断网后，手机上会怎样？能自动重连吗？
- [ ] `--remote-control` 搭配 `--settings` 时，hooks 会正常执行吗？
- [ ] `claude --bg --remote-control` 能一起用吗？能的话就不需要 tmux
- [ ] Mac：从 launchd 启动时读得到钥匙圈里的登录吗？

**验证**
- [x] Ubuntu：preflight 全部 ✅（2026-11-10）
- [x] Ubuntu：手机 Claude app 连上 `claude --remote-control test`，测试正常（2026-11-10，在空的 `~/rc-test` 里测，因为安全层还没做）
- [ ] Mac：preflight 全部 ✅
- [ ] Mac：手机测试正常
- [ ] 手机上两台的 session 名称分得出来

## M3 安全层 ✅（2026-11-10 手机实测通过；Mac 待测）
**目标**：远端 session 里，aggressive 行为直接拒绝；本地 session 不受影响。

**多加了什么**
- `hooks/policy.py`（review 后拆成 `hooks/policy/`）：判断规则（纯函数）。Bash 指令用 `bashlex` 拆开，逐段判断：
  - 组合指令（`&&`、`;`、`|`）、`$(...)`、`<(...)`、`bash -c '...'`、`find -exec`、`xargs`、回圈、subshell 都会拆进去看
  - 去掉 `env`、`time`、`timeout`、`nice` 等包装，用绝对路径（`/usr/bin/sudo`）也认得
  - 追踪 `cd`，之后的相对路径用新目录判断；路径会解开 symlink；万用字元会实际展开检查
  - 指令名称是变数、看不懂的语法（例如 `[[ ]]`）、背景执行（`&`）、定义函数 → 一律拒绝
- `hooks/remote_guard.py`：接到 Claude Code 的 `PreToolUse` hook。Deny → exit 2；Ask / Allow → 输出 `permissionDecision`；程式出错时一律拒绝
- `deploy/remote-settings.template.json` + `scripts/render_settings.py`：产生这台电脑的远端专用设定（第一道防线是 Claude Code 内建的 `permissions.deny`，第二道是 hook；也关掉了 bypass 模式）
- preflight 多一项「安全层设定」检查：设定档不存在或 hook 的 python 不在 → ❌
- 受保护（远端不能改）：安全层本身（`hooks/`、`deploy/`、`scripts/`、`preflight/`、本机设定资料夹）、`~/.claude/settings*.json`、工作目录里的 `.claude/`、`.mcp.json`、`.git/hooks`、`.git/config`
- `tests/test_policy.py`、`tests/test_remote_guard.py`：130 个测试，包含 24 种绕过手法
- 整理：Telegram 档案移到 `legacy/telegram/`，`requirements.txt` 只留 `bashlex`、`pytest`

**code review 后的修正（2026-11-10）**
- review 找到 10 类绕过，最严重的会被自动放行（细节见 error.md）
- `hooks/policy.py` 拆成 `hooks/policy/` 资料夹，阅读顺序见 `hooks/policy/__init__.py`
- **Allow 层改成严格白名单**：只读指令的每个选项都认得才放行，不认得 → Ask
- hook 超过 10 秒自己拒绝（避免 Claude Code 等到 30 秒后略过 hook）
- 测试共 241 个（安全层 205 个），每一条 review 发现都有对应测试

**规则细节和 outline 的差异**
- 读取工作目录外的档案：Ask（不是 Deny），机密档案仍是 Deny
- 写到 `/tmp` 视为可以（Ask），其他工作目录外的地方 Deny

**第 2 次 code review 与收尾决定（2026-11-10）**
- 又找到 10 个绕过（细节见 error.md），其中 5 个会被自动放行
- **决定不再逐条补洞**：Bash 的写法太多，静态分析补不完。改用「出门前先设定安全等级 + 作用范围」限制风险（放进 M5 启动脚本，见 outline.md「安全等级与作用范围」）
- 这 10 项记为已知限制，不修

**已知限制**
- **Deny 层是「尽力挡」，不是保证**：刻意用冷门的 Bash 写法仍可能绕过（例如 `printf -v` 设定变数、bash 特有的 `[^x]` 万用字元、Mac 上大小写不同的档名）。所以：
  - 手机上的批准要看清楚完整指令再按
  - 只在设定好的工作目录里用，出门前先 commit
- 执行脚本（`python x.py`、`npm test`、`make`）时脚本内容无法分析，一律 Ask
- 第一道防线 `permissions.deny` 的规则语法还没在实机上确认；就算写错，第二道 hook 仍会挡

**学到的概念**：Claude Code `permissions` 规则语法、`PreToolUse` hook 的输入 / 输出、`bashlex` 语法树、symlink 与路径检查、fail closed

**怎么用**：见 README「每次出门前」

**验证**
- [x] 单元测试全过（全部 241 个）
- [x] code review（第 1 次）：10 类绕过已修
- [x] code review（第 2 次）：又找到 10 个，记为已知限制，改用安全等级 + 作用范围（M5）
- [x] 用产生出来的 hook 指令实际跑：`ls` → allow、`touch x` → ask、`git push` → exit 2 拒绝
- [x] 手机（2026-11-10）：`git push` / `sudo` / `rm -rf` / 读 `~/.ssh` → ⛔ 拒绝，手机上没有批准提示（确认远端 session 有执行 hook）
- [x] 手机：组合指令（`ls && rm -rf a.txt`）→ 仍拒绝
- [x] 手机：改工作目录里的档案（Write / Edit / git commit）→ 出现批准提示
- [ ] 电脑：一般的 `claude`（没带 `--settings`）→ 不受影响
- [ ] Mac：`render_settings.py` + preflight 通过

## M4 Audit log + git checkpoint ✅（2026-11-10 手机实测通过；Mac 待测）
**目标**：知道远端做过什么，出问题时能还原。

**多加了什么**
- `hooks/gitsnap.py`：还原点的核心。用**暂时的 index** 把整个工作目录（含未追踪档、排除 `.gitignore`）做成 commit，存到 `refs/remote-ctrl/checkpoints/<时间>`；工作目录、repo 的 index、分支、HEAD 都不变。最多保留 50 个
  - 原本规划用 `git stash create`，但它不含未追踪的新档案，所以改用暂时 index
- `hooks/checkpoint.py`：`UserPromptSubmit` hook，手机每送一个 prompt 就拍一个还原点。失败时**不挡 prompt**（不然人在外面会完全不能用），但会提醒 Claude「这次没有还原点」
- `hooks/audit.py`：动作纪录（JSONL，权限 600）。记录每个 prompt 和每次工具调用（工具、参数、允许 / 询问 / 拒绝、原因）；**不记录写入的档案内容**，只记长度
  - 位置：Ubuntu `~/.local/state/remote-ctrl/audit.jsonl`、Mac `~/Library/Logs/remote-ctrl/audit.jsonl`
- `scripts/restore.py`：回到电脑前用。`log`（动作纪录）、`list`、`diff`、`restore`。还原前会先把现在存成新还原点，可以反悔
- 安全层加保护：远端不能删还原点（`refs/remote-ctrl`、`git update-ref`、`GIT_INDEX_FILE`）、不能改动作纪录
- `remote-settings` 多一个 `UserPromptSubmit` hook；preflight 检查两个 hook 都在（**M4 之前产生的设定要重跑 `render_settings.py`**，这台 Ubuntu 已重跑）
- `tests/test_checkpoint.py`：14 个测试（不动档案、含未追踪、排除 ignore、空 repo、子资料夹、还原与反悔、hook、纪录不含内容、远端删不掉）。全部 255 个

**学到的概念**：`UserPromptSubmit` hook、`GIT_INDEX_FILE` 暂时 index、`write-tree` / `commit-tree` / `update-ref`、不在分支上的 commit

**code review 后的修正（2026-11-10）**：10 个 bug 全修（细节见 error.md），重点：
- 安全层的 10 秒超时之前没真的生效，现在一定会拒绝（这也修掉 M3 第 2 次 review 的同一项）
- 还原点：共用时间期限、UTC 命名、还原目标不会被清掉、`.gitignore` 档案还原前会备份
- `restore.py`：怪异编码不当掉、`log` 看正确的位置、只扫描一次
- preflight：repo 的子资料夹也算 git repo
- 测试共 264 个；这台 Ubuntu 已重跑 `render_settings.py`（hook 时限改 120 秒）

**验证**
- [x] 单元测试全过（全部 264 个）
- [x] 实际跑：hook 建立还原点 → 改坏档案、新增档案 → `restore.py diff` 看得到 → `restore` 还原成功
- [x] code review：10 个全修，每个都有测试
- [x] 手机（2026-11-10，`test_area/learn_git`）：每个 prompt 都有还原点；`restore.py log` 看得到每个动作；不是 git repo 时正确提醒「没有还原点」
- [ ] Mac：重跑 `render_settings.py`，preflight 通过

## M5 一直在线 + 安全等级 ⬜
**目标**：开机就自动启动远端 session，挂了会重启，电脑不会休眠；启动前先设定这次的安全等级和作用范围。
**会多加**：`scripts/start-remote.sh`、`scripts/install.sh`、`platform_utils.py`、`deploy/remote-ctrl.service`、`deploy/com.remotectrl.agent.plist`
**安全等级 + 作用范围**（M3 第 2 次 review 后的决定，设计见 outline.md）
- 启动时必须指定 `--level`（`readonly` / `normal`）和 `--dir`（必须在 `config.json` 的 `work_dirs` 里）
- `readonly`：只放行确定只读的操作，其他一律拒绝（不会跳出询问），适合「出门只想看进度」
- `normal`：现在的 Allow / Ask / Deny
- 等级和范围写进环境变量交给 hook，远端改不了
**Mac 注意**（细节见 outline.md「Mac 特别注意」）：launchd 的 PATH 很短要写绝对路径；必须用 LaunchAgent 才读得到钥匙圈；重开机后要先登录才会启动
**验证**
- [ ] 重启电脑后手机连得到 session（Ubuntu；Mac 为登录后）
- [ ] 手动 kill session → 自动重启
- [ ] macOS 闲置 30 分钟后仍在线

## M6 v0.2 ⬜
多项目多 session、健康检查与通知、log 轮替（细节见 outline.md）
