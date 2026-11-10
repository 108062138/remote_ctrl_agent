# remote_ctrl_agent

人不在电脑前时，用手机的 **Claude app** 控制电脑（Ubuntu / macOS）上的 Claude Code。

> 目前进度：M3 安全层完成（待手机实测）。
> ⚠️ 安全层是「尽力挡」：冷门的 Bash 写法仍可能绕过（见 milestone.md M3 已知限制）。手机上的批准请看清楚完整指令，只在设定好的工作目录里用，出门前先 commit。
> 已经能用：启动前检查（preflight）、安全层（危险操作直接拒绝）、手动启动远端 session。
> 还没做：还原点（M4）、开机自动启动（M5）。**手机实测安全层之前，请先在测试资料夹里用。**
>
> 设计见 [outline.md](outline.md)、进度见 [milestone.md](milestone.md)、架构图见 [docs/architecture.md](docs/architecture.md)。

---

## 一、第一次设定（每台电脑各做一次）

### 1. 需要的东西
- 手机：Claude app，登录你的 Anthropic 账号（建议开启两步验证）
- 电脑：
  - Claude Code（支持 `--remote-control`；旧版先 `claude update`）
  - 用 claude.ai 订阅登录：`claude auth login`
  - tmux：Ubuntu `sudo apt install tmux`、Mac `brew install tmux`
  - conda

### 2. 建立环境
```bash
cd remote_ctrl_agent
conda env create -f environment.yml      # 建立 remote-ctrl-agent 环境
```

### 3. 设定可信账号
只信任你自己的账号。第一台电脑：
```bash
python3 scripts/preflight.py --show-org
```
会显示 email、方案、orgId。**确认 email 是你自己的**，再复制最后一行执行：
```bash
python3 scripts/preflight.py --trust <orgId>
```
第二台电脑（例如 Mac）：先 `claude auth login` 登录同一个账号，跑 `--show-org` 确认 orgId 和第一台**一模一样**，再用同一个 orgId 跑 `--trust`。登录的账号不一样时，`--trust` 会拒绝写入。

> 设定档存在本机（不在 repo 里）：Ubuntu `~/.config/remote-ctrl/config.json`，Mac `~/Library/Application Support/remote-ctrl/config.json`

### 4. 产生安全层设定
```bash
conda activate remote-ctrl-agent
python scripts/render_settings.py
```
会在设定资料夹产生 `remote-settings.json`（hook 要用这台电脑 conda env 的 python，所以每台电脑各产生一次；conda env 搬家或重建后要重跑）。

### 5. 跑一次检查
```bash
python3 scripts/preflight.py
```
- 全部 ✅ 或只有 ⚠️ → 可以用
- 有 ❌ → 照每一项下面 `→` 的提示修好再跑

---

## 二、每次出门前

### 1. 检查
```bash
python3 scripts/preflight.py
```
特别注意「登录有效期」：剩不到 7 天会 ⚠️，出门前先 `claude auth login` 重新登录。

### 2. 启动远端 session（目前是手动）
```bash
tmux new -s remote                        # 开一个 tmux，关掉终端机 session 也不会停
mkdir -p ~/rc-test && cd ~/rc-test        # 手机实测安全层之前，先用测试资料夹

# Ubuntu
env -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN REMOTE_CTRL_WORK_DIR="$PWD" \
  claude --remote-control test --settings ~/.config/remote-ctrl/remote-settings.json

# Mac
env -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN REMOTE_CTRL_WORK_DIR="$PWD" \
  claude --remote-control test --settings "$HOME/Library/Application Support/remote-ctrl/remote-settings.json"
```
- `--settings` 载入安全层；**不加就没有安全层**（M5 的启动脚本会自动带）
- `REMOTE_CTRL_WORK_DIR` 是允许修改的范围，写在这个资料夹以外会被拒绝
- 第一次在新资料夹启动时，电脑上会问是否信任这个资料夹，要在电脑前按同意
- 离开 tmux 但让它继续跑：按 `Ctrl+B` 再按 `D`
- 回来接上：`tmux attach -t remote`

### 3. 别让电脑睡着
- Ubuntu：设定 → 电源 → 关闭自动待机
- Mac：另开终端机跑 `caffeinate -i`（合盖仍会睡，要接电源 + 外接显示器）

> M5 完成后，第 2、3 步会变成开机自动做好。

---

## 三、在手机上用
1. 打开 Claude app，找到名称带 `test` 的 session（可能在 Code 区块）
2. 像平常一样打字下指令，例如 `list files in this folder`
3. Claude 要改档案或跑指令时，手机会跳出 **允许 / 拒绝**，看清楚再按
4. 危险操作（`git push`、`sudo`、`rm -rf`、读 `~/.ssh` 或 `.env`……）会直接被拒绝，Claude 会看到 `[remote-guard]` 的原因并换做法；完整清单见 [outline.md](outline.md) 的 Remote Safety Policy

---

## 四、回到电脑前
```bash
tmux attach -t remote     # 看刚才远端做了什么
# 在 claude 里按 Ctrl+C 两次结束，再 exit 离开 tmux
```

---

## 常见问题

| 状况 | 怎么办 |
|---|---|
| preflight 显示「可信账号 ❌」 | 用 `--show-org` 看这台电脑登录的 email；不是你的就 `claude auth logout` 再 `claude auth login` |
| preflight 显示「登录有效期 ❌ / ⚠️」 | `claude auth login` |
| preflight 显示「会盖掉订阅登录的环境变量 ⚠️」 | 不用处理，启动远端时会自动移除（目前手动启动时，请先 `unset ANTHROPIC_API_KEY`） |
| 手机上看不到 session | 确认电脑上的 `claude --remote-control` 还在跑（`tmux attach -t remote`），电脑没睡着、有网络 |
| 正常的指令被拒绝了 | 原因会写在 `[remote-guard]` 后面；看不懂的语法（例如 `[[ ]]`）会被拒绝，请 Claude 改写成简单的形式。规则在 `hooks/policy/` |
| 只读指令也跳出询问 | 只读指令用了白名单以外的选项（例如 `sort --compress-program`）就会问你，确认没问题再批准即可 |
| preflight 显示「安全层设定 ❌」 | `conda activate remote-ctrl-agent && python scripts/render_settings.py` |
| 遇到错误 | 把错误讯息记进 [error.md](error.md) |

## 开发者

```bash
conda activate remote-ctrl-agent
python -m pytest -q                       # 跑全部测试
```
程式结构见 [docs/architecture.md](docs/architecture.md) 最后一节。
