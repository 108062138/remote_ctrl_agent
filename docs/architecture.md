# 整体架构

图片版：[architecture.png](architecture.png)（由 `architecture.dot` 产生：`dot -Tpng -Gdpi=130 docs/architecture.dot -o docs/architecture.png`）

## ASCII 版
框里的 `[M?]` 就是对应的 milestone。（框内用英文，中文在终端机里宽度不一，框线会歪。）

```
  +--------------+  HTTPS   +-------------------------+
  | iPhone       |<-------->| Anthropic (relay)       |
  | Claude app   |          | account login + 2FA     |
  +--------------+          +------------+------------+
        ^                                |
        : [M1] Telegram bot (archived)   | computer only connects OUT, no open ports
                                         |
 ===================== Your computer: Ubuntu / macOS =====================
                                         |
  [M5] boot: systemd / launchd           |
        |                                |
        v                                |
  [M5] scripts/start-remote.sh           |
        |-- 1. run preflight --------> [M2] preflight/   checklist OK/WARN/FAIL
        |                                    |  <-> config.json (trusted orgId, work dirs)
        |                                    +-- any FAIL -> stop, do not start
        |-- 2. anti-sleep ----------> [M5] systemd-inhibit / caffeinate
        |-- 3. remove API-key env vars
        v                                |
  +----------------- tmux ---------------+--------------------------------+
  |  [M2] claude --remote-control  <-----+                                |
  |         ^                                                             |
  |         | loads   [M3] deploy/remote-settings.json (allow/ask/deny)   |
  |         |                                                             |
  |         |-- every prompt ----> [M4] UserPromptSubmit hook             |
  |         |                          checkpoint.py -> git snapshot      |
  |         |                                                             |
  |         +-- every tool call -> [M3] PreToolUse hook                   |
  |                                    remote_guard.py + policy/          |
  |                                    Allow / Ask (phone) / Deny         |
  |                                    +--> [M4] audit log                |
  +------------------------------------|----------------------------------+
                                       | only if allowed
                                       v
                         WORK_DIR (your git repo)  <-- [M4] scripts/restore.py

  [M0] outline.md / milestone.md / error.md        (planning docs)
  [M6] multi-project sessions, health check, log rotation  (on top of all above)
```

## 一个 prompt 从手机到电脑的路径
```
手机打字
  -> Anthropic
  -> 电脑上的 claude --remote-control                    [M2]
  -> checkpoint.py 先存 git 快照                          [M4]
  -> Claude 决定要用工具（例如改档、跑指令）
  -> remote_guard.py 判断                                 [M3]
       Allow -> 直接执行
       Ask   -> 手机跳出允许 / 拒绝
       Deny  -> 直接拒绝，手机批准也没用
  -> 写进 audit log                                       [M4]
  -> 结果传回手机
```

## Milestone 对照表

| Milestone | 在图上的位置 | 主要档案 | 状态 |
|---|---|---|---|
| M0 规划 | 图外的文件 | `outline.md`、`milestone.md`、`error.md`、`docs/` | ✅ |
| M1 Telegram | 手机旁边的虚线（已被 Claude app 取代） | `legacy/telegram/` | 🗄 封存 |
| M2 跑通 + preflight | `claude --remote-control`、`preflight/`、`config.json` | `preflight/`、`scripts/preflight.py`、`platform_utils.py` | 🟡 Ubuntu 完成，Mac 待测 |
| M3 安全层 | 每次工具调用都要经过的 hook | `deploy/remote-settings.template.json`、`scripts/render_settings.py`、`hooks/remote_guard.py`、`hooks/policy/` | ✅ Ubuntu 手机实测通过 |
| M4 记录与还原 | 每个 prompt 前的快照、audit log、restore | `hooks/checkpoint.py`、`hooks/gitsnap.py`、`hooks/audit.py`、`scripts/restore.py` | ✅ Ubuntu 手机实测通过 |
| M5 一直在线 + 安全等级 | 开机启动 → start-remote.sh（白名单、preflight、还原点）→ tmux、防休眠；hook 依等级收紧 | `scripts/start_remote.py`、`scripts/install_service.py`、`deploy/*.service`、`deploy/*.plist`、`hooks/policy/levels.py` | 🟡 代码完成，待 code review |
| M6 v0.2 | 整个电脑区块之上 | 多 session、健康检查、log 轮替 | ⬜ |

## preflight/ 里面（M2）

```
scripts/preflight.py         入口，只负责呼叫 cli.main()
        |
        v
preflight/cli.py             印报告、--show-org、--trust、--json
        |
        v
preflight/runner.py          依序收集资料、执行每一项检查
   |            |               |
   v            v               v
commands.py   config_store.py   checks_claude.py / checks_system.py
执行 claude、  读写 config.json   每个函数判断一项，回传 Check
conda 指令                          |
                                    v
                              models.py  Check = 名称 + ✅/⚠️/❌ + 说明 + 修法

platform_utils.py（专案根目录）Ubuntu / Mac 差异：设定档位置、找工具、移除 API key 变量
```
