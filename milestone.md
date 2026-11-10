# Milestones

每完成一个阶段就更新这里：这阶段**多加了什么**、为什么加、怎么验证。
完整设计见 [outline.md](outline.md)，踩过的坑见 [error.md](error.md)。
每个 milestone 在架构图上的位置见 [docs/architecture.md](docs/architecture.md)。

状态：⬜ 未开始　🟡 进行中　✅ 完成　🗄 封存

| # | 阶段 | 状态 |
|---|---|---|
| 0 | 规划：写 outline | ✅ |
| 1 | Telegram echo bot + 白名单 | 🗄 封存 |
| 2 | Remote Control 跑通（Ubuntu + Mac）+ preflight 检查 | 🟡 Ubuntu 完成，Mac 待测 |
| 3 | 安全层：settings + policy + hook | 🟡 代码完成，待手机实测 |
| 4 | Audit log + git checkpoint | ⬜ |
| 5 | 一直在线：tmux + systemd / launchd + 防休眠 | ⬜ |
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

## M3 安全层 🟡（2026-11-10 代码完成，待手机实测）
**目标**：远端 session 里，aggressive 行为直接拒绝；本地 session 不受影响。

**多加了什么**
- `hooks/policy.py`：判断规则（纯函数）。Bash 指令用 `bashlex` 拆开，逐段判断：
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

**规则细节和 outline 的差异**
- 读取工作目录外的档案：Ask（不是 Deny），机密档案仍是 Deny
- 写到 `/tmp` 视为可以（Ask），其他工作目录外的地方 Deny

**已知限制**
- 执行脚本（`python x.py`、`npm test`、`make`）时脚本内容无法分析，一律 Ask，要在手机上看清楚再批准
- 第一道防线 `permissions.deny` 的规则语法还没在实机上确认；就算写错，第二道 hook 仍会挡

**学到的概念**：Claude Code `permissions` 规则语法、`PreToolUse` hook 的输入 / 输出、`bashlex` 语法树、symlink 与路径检查、fail closed

**怎么用**：见 README「每次出门前」

**验证**
- [x] 单元测试全过（全部 165 个）
- [x] 用产生出来的 hook 指令实际跑：`ls` → allow、`touch x` → ask、`git push` → exit 2 拒绝
- [ ] 手机：`git push` / `sudo` / `rm -rf` / 读 `~/.ssh` → ⛔ 拒绝，手机上没有批准提示
- [ ] 手机：组合指令（`ls && rm -rf x`）→ 仍拒绝
- [ ] 手机：改工作目录里的档案 → 出现批准提示
- [ ] 电脑：一般的 `claude`（没带 `--settings`）→ 不受影响
- [ ] Mac：`render_settings.py` + preflight 通过

## M4 Audit log + git checkpoint ⬜
**目标**：知道远端做过什么，出问题时能还原。
**会多加**：`hooks/checkpoint.py`、`scripts/restore.sh`，`remote_guard.py` 写 audit log（JSONL）
**学到的概念**：`UserPromptSubmit` hook、`git stash create` / `git update-ref`（存快照但不动工作区）
**验证**
- [ ] 每个 prompt 前都有一个 checkpoint，`scripts/restore.sh` 列得出来、还原得回去
- [ ] audit log 记录每次工具调用：时间、工具、参数、允许 / 拒绝

## M5 一直在线 ⬜
**目标**：开机就自动启动远端 session，挂了会重启，电脑不会休眠。
**会多加**：`scripts/start-remote.sh`、`scripts/install.sh`、`platform_utils.py`、`deploy/remote-ctrl.service`、`deploy/com.remotectrl.agent.plist`
**Mac 注意**（细节见 outline.md「Mac 特别注意」）：launchd 的 PATH 很短要写绝对路径；必须用 LaunchAgent 才读得到钥匙圈；重开机后要先登录才会启动
**验证**
- [ ] 重启电脑后手机连得到 session（Ubuntu；Mac 为登录后）
- [ ] 手动 kill session → 自动重启
- [ ] macOS 闲置 30 分钟后仍在线

## M6 v0.2 ⬜
多项目多 session、健康检查与通知、log 轮替（细节见 outline.md）
