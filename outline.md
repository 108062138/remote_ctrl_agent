# Object
人不在办公室时，用手机的 **Claude app** 控制 / 查看电脑上 Claude Code 的进度。
支持 **Ubuntu 和 macOS**，同一份设定两边都能用。
练手 side project。

相关文件：使用说明见 [README.md](README.md)，进度见 [milestone.md](milestone.md)，踩坑记录见 [error.md](error.md)，架构图见 [docs/architecture.md](docs/architecture.md)（PNG：[docs/architecture.png](docs/architecture.png)）。

> 2026-11-10 方向调整：原本用 Telegram bot，改用 Claude Code 内建的 Remote Control。
> 原因：官方账号登录（可开两步验证）、没有 bot token 会外泄、权限确认是内建的、要维护的代码少很多。
> Telegram 版 M1 的代码保留当练习（见 milestone.md），不再继续开发。

# Architecture
```
iPhone Claude app <-> Anthropic <-> claude --remote-control（Ubuntu / macOS，跑在 tmux 里）
                                          │
                                          ├── remote-settings.json：allow / ask / deny 规则
                                          └── hooks/remote_guard.py：PreToolUse 安全检查 + audit log
```
- 电脑只发出 HTTPS 连线，不需要公网 IP、不开端口
- 本项目不重写 Claude Code，只做三件事：**安全层**、**一直在线**、**记录与还原**

# Tech Stack
- Claude Code CLI（`claude --remote-control`，本机 2.1.322）+ 手机 Claude app
- Claude Code 设定：`--settings` 指定远端专用设定档，`permissions` + `hooks`
- Python 3.12（conda env `remote-ctrl-agent`）写 hook，`bashlex` 拆解 Bash 指令
- 背景运行：优先研究 Claude Code 内建的 `claude --bg`（背景 session，可 `attach` / `respawn`），不行再用 tmux
- 常驻：Ubuntu 用 systemd user service，macOS 用 launchd（LaunchAgent plist）

# Remote Control 前置条件（每台机器都要满足）
Ubuntu 和 Mac 是两台独立的机器，**各自**都要满足下面条件，由 `scripts/preflight.py` 自动检查（安装时跑一次、每次启动前再跑一次，不满足就停下并提示怎么修）。

| # | 条件 | 怎么检查 | 不满足时 |
|---|---|---|---|
| 1 | Claude Code 支持 Remote Control | `claude --help` 里有 `--remote-control`（看功能不看版本号） | `claude update` |
| 2 | 用 claude.ai 订阅登录，不是 API key | `claude auth status --json`：`loggedIn: true`、`authMethod: "claude.ai"`、`apiProvider: "firstParty"` | `claude auth login`；启动环境里不要有 `ANTHROPIC_API_KEY`，以免盖掉订阅登录 |
| 3 | 登录没快过期 | `loginExpiresAt` 距今 > 7 天 | 重新登录；或研究 `claude setup-token`（长期 token） |
| 4 | 只信任自己的账号（手机 + 电脑） | `orgId` 必须等于本机设定档里的可信 orgId；没设定也算失败 | `--show-org` 确认后 `--trust <orgId>` |
| 5 | 账号 / 组织没关闭 Remote Control | M2 实测：手机看得到 session | 若是 Team / Enterprise，请管理员开启 |
| 6 | 依赖工具都在 | `tmux`（两台都用，必须有）、`caffeinate`（Mac）/ `systemd-inhibit`（Ubuntu）、conda env（警告） | 依提示安装 |
| 7 | 不被 API key 盖掉 | 侦测 `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` 是否存在（不读值、不读 `.env`） | 启动时自动从环境移除 |

已知状态：
- Ubuntu：Claude Code 2.1.322，有 `--remote-control`，`authMethod: claude.ai`，Pro 方案，登录到期 2026-12-08 → 无人值守时要注意到期
- Mac：**还没检查**，M2 要跑一次 preflight

## Mac 特别注意
- **PATH**：launchd 启动时 PATH 很短，找不到 `claude`、`tmux`（可能在 `~/.local/bin`、`/opt/homebrew/bin`（Apple Silicon）或 `/usr/local/bin`（Intel））。启动脚本要写绝对路径或自己设 PATH
- **钥匙圈**：claude.ai 登录存在 macOS 钥匙圈里。必须用 LaunchAgent（跑在用户登录的 session），不能用 LaunchDaemon 或 SSH 进去启动，否则读不到登录
- **重开机后要先登录**：Mac 重开机停在登录画面时，LaunchAgent 不会跑。开了 FileVault 就不能自动登录 → 远端期间避免重开机，或接受「重开后要回电脑前登录一次」
- **休眠**：`caffeinate -i` 只防闲置休眠，合盖仍会睡；要合盖运行需接电源 + 外接显示器
- 不要用 `--bare` 模式启动：它不读 OAuth 和钥匙圈，订阅登录会失效
- session 名称前缀默认是 hostname，两台机器在手机上分得出来；启动脚本仍明确设 `--remote-control-session-name-prefix`（如 `ubuntu-`、`mac-`）

# Remote 与 Local 分开
- 安全规则**只在远端 session 生效**：启动脚本用 `claude --remote-control <name> --settings deploy/remote-settings.json`，并设环境变量 `REMOTE_CTRL=1`
- 平常坐在电脑前用 Claude Code 不受影响

# Cross-platform（Ubuntu + macOS）
- hook 只用纯 Python + `pathlib`，不写死路径
- 平台差异集中在 `scripts/` 和 `platform_utils.py`：

| 功能 | Ubuntu | macOS |
|---|---|---|
| 开机常驻 | systemd user service | launchd LaunchAgent |
| 防止休眠 | `systemd-inhibit` | `caffeinate -i` |
| tmux 安装 | `apt install tmux` | `brew install tmux` |
| 默认 log 目录 | `~/.local/state/remote-ctrl/` | `~/Library/Logs/remote-ctrl/` |

- 注意：macOS 合盖会休眠，`caffeinate` 只能防闲置休眠；要合盖运行需接电源 + 外接显示器，或在系统设置里关闭休眠

# Scope
## MVP（v0.1）
- 手机 Claude app 连上电脑的 Remote Control session，能下 prompt、看进度、批准 / 拒绝权限请求
- Remote Safety Policy：aggressive 操作直接拒绝（见 Security）
- Audit log：每次工具调用都记录
- 每次 prompt 前自动建 git checkpoint，回到电脑前可以还原
- 开机自动启动 + 防休眠，session 挂了自动重启

## v0.2
- 多个项目各自一个 Remote Control session（用名称区分）
- 健康检查：session 断线、或 Claude 登录快过期（`loginExpiresAt` < 7 天）时通知
- log 轮替，避免 audit log 无限变大

## Later
- 任务完成时推送通知（`Notification` / `Stop` hook）
- 多台机器管理

# Security
- 依赖 Anthropic 账号安全：开启两步验证
- 所有工具调用写进 audit log（`hooks/remote_guard.py`）

## Remote Safety Policy（远端时禁止 aggressive 行为）
原则：人不在电脑前，出错了没法马上补救，所以远端只做**可逆、范围小**的操作。
两道防线：
1. `deploy/remote-settings.json` 的 `permissions.allow / ask / deny`（Claude Code 内建，简单规则）
2. `hooks/remote_guard.py`（`PreToolUse` hook，处理 settings 规则写不出来的情况：组合指令、路径沙箱）。拒绝时回传原因给 Claude

| 层级 | 行为 | 例子 |
|---|---|---|
| ✅ Allow（自动放行） | 只读操作 | `Read`、`Glob`、`Grep`、`ls`、`cat`、`git status/diff/log` |
| ❓ Ask（手机上批准） | 可逆的修改 | `Edit`/`Write`（限 WORK_DIR 内）、跑测试、`git add/commit`、安装 dev 依赖 |
| ⛔ Deny（直接拒绝，手机批准也没用） | 不可逆 / 影响范围大 | 见下方清单 |

**Deny 清单**
- 删除：`rm -rf`、`find -delete`、删除 WORK_DIR 以外的任何文件
- 提权与系统：`sudo`、`su`、`chmod -R`/`chown`、改 `/etc`、`systemctl`/`launchctl`、关机/重启
- Git：`git push`（尤其 `--force`）、`git reset --hard`、`git clean -fd`、`git checkout -- .`、删分支、改 remote
- 网络与外泄：`curl | sh`、上传文件到外部、`scp`/`rsync` 到远端、读取 `~/.ssh`、`.env`、钥匙圈 / 密码管理器
- 进程与资源：`kill -9`、fork bomb、长时间常驻的后台进程、磁盘格式化（`dd`、`mkfs`、`diskutil erase`）
- 其他：发布套件（`npm publish`、`twine upload`）、部署、数据库 `DROP`/`TRUNCATE`
- 关掉安全层：修改 `remote-settings.json`、`hooks/`、`~/.claude/settings.json`

**额外防护**
- 路径沙箱：写入 / 删除必须在 WORK_DIR 内（或 `/tmp`），读取 WORK_DIR 外要 Ask，机密档案一律 Deny（解析 symlink、展开万用字元后再检查）
- Bash 拆解：用 `bashlex` 拆开 `&&`、`;`、`|`、`$(...)` 逐段检查，任一段命中 Deny 就整条拒绝；拆不开的指令一律拒绝
- git checkpoint：`UserPromptSubmit` hook 用 `git stash create` + `git update-ref` 存快照，**不改动工作区**
- 不提供能解除 Deny 的模式；要做 Deny 层的事请回电脑前操作

# Project Layout
```
remote_ctrl_agent/
├── hooks/
│   ├── remote_guard.py       # PreToolUse：Allow / Ask / Deny 判断 + audit log
│   ├── policy.py             # Deny 清单、Bash 拆解、路径沙箱（纯函数，方便测试）
│   └── checkpoint.py         # UserPromptSubmit：git 快照
├── deploy/
│   ├── remote-settings.template.json # 远端专用设定范本（render_settings.py 填入路径后放到本机设定资料夹）
│   ├── remote-ctrl.service       # Ubuntu systemd
│   └── com.remotectrl.agent.plist # macOS launchd
├── preflight/                # 启动前检查清单（拆成多个小档案，阅读顺序见 __init__.py）
├── docs/                     # 架构图（architecture.md / .png / .dot）
├── scripts/
│   ├── preflight.py          # preflight 入口：python3 scripts/preflight.py
│   ├── render_settings.py    # 产生这台电脑的 remote-settings.json
│   ├── start-remote.sh       # 跑 preflight → 背景启动 claude --remote-control + 防休眠
│   ├── install.sh            # 判断平台，安装对应 service
│   └── restore.sh            # 列出 / 还原 git checkpoint
├── platform_utils.py
├── tests/                    # policy、hook、checkpoint 的单元测试
├── legacy/telegram/          # 封存的 Telegram 版 M1（bot.py、config.py、test_m1.py）
├── environment.yml
└── requirements.txt
```

# Milestones
详见 [milestone.md](milestone.md)：
1. （封存）Telegram echo bot
2. Remote Control 手动跑通，确认手机能用
3. 安全层：remote-settings.json + policy.py + remote_guard.py + 测试
4. Audit log + git checkpoint + 还原脚本
5. 一直在线：tmux 启动脚本 + systemd / launchd + 防休眠（Ubuntu + macOS）
6. v0.2 功能

> 原本的「GUI 美化」阶段取消：介面就是 Claude app，不需要自己做。

# Verification
- `scripts/preflight.py` 在 Ubuntu 和 Mac 都全部 ✅
- 手机 Claude app 看得到两台电脑的 session（名称分得出来），下 "list files in this repo" → 收到结果
- 让 Claude 改一个文件 → 手机上出现批准提示，拒绝后文件不变、允许后有改动
- 叫 Claude `git push`、`sudo apt install`、`rm -rf build/`、读 `~/.ssh/id_rsa` → 全部被拒绝，没有批准提示
- 组合指令绕过（`ls && rm -rf x`、`echo $(sudo whoami)`）→ 仍被拒绝
- 在电脑前用一般的 `claude`（非远端）→ 不受安全层影响
- 重启电脑后 session 自动上线，手机连得到
- 以上测试在 Ubuntu 和 macOS 各跑一次；macOS 额外确认闲置 30 分钟后仍在线
