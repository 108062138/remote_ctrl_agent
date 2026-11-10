# remote_ctrl_agent

人不在电脑前时，用手机的 **Claude app** 控制自己电脑（Ubuntu / Mac）上的 Claude Code（`claude --remote-control`）。
这个 repo 加了三样东西：启动前检查（preflight）、安全层（每个动作自动允许 / 问你 / 拒绝）、每台电脑的远端设定。

> 进度：安全层、还原点已完成，还没在手机上实测，**先在测试资料夹里用**。开机自动启动还在做。
>
> ⚠️ 安全层只是「尽力挡」，冷门的 Bash 写法仍可能绕过。所以：手机上看清楚完整指令再批准；只在选好的工作资料夹里用；出门前先 commit。

## 一、第一次设定（每台电脑做一次）

先装好：手机 Claude app（建议账号开两步验证）、电脑上的 Claude Code（旧版先 `claude update`，再 `claude auth login`）、tmux（Ubuntu `sudo apt install tmux`，Mac `brew install tmux`）、conda。

1. 建立环境
   ```bash
   cd remote_ctrl_agent
   conda env create -f environment.yml
   ```
2. 设定可信账号（orgId 是你的账号 ID，不用自己找）
   ```bash
   python3 scripts/preflight.py --show-org
   ```
   确认显示的 email 是你自己的，再复制它最后印出的那行指令执行：
   ```bash
   python3 scripts/preflight.py --trust <orgId>
   ```
   第二台电脑要登录同一个账号，orgId 会一样；账号不同时 `--trust` 会拒绝。
3. 产生安全层设定（conda env 重建、或更新这个 repo 后要重跑）
   ```bash
   conda activate remote-ctrl-agent
   python scripts/render_settings.py
   ```
4. 跑检查：全部 ✅ 或只有 ⚠️ 就能用；有 ❌ 就照 `→` 后面的提示修
   ```bash
   python3 scripts/preflight.py
   ```

## 二、每次出门前

1. 检查（登录剩不到 7 天会 ⚠️，先 `claude auth login`）
   ```bash
   python3 scripts/preflight.py
   ```
2. 在 tmux 里启动远端 session
   ```bash
   tmux new -s remote
   mkdir -p ~/rc-test && cd ~/rc-test        # 手机实测安全层之前，先用测试资料夹

   # Ubuntu
   env -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN REMOTE_CTRL_WORK_DIR="$PWD" \
     claude --remote-control test --settings ~/.config/remote-ctrl/remote-settings.json

   # Mac
   env -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN REMOTE_CTRL_WORK_DIR="$PWD" \
     claude --remote-control test --settings "$HOME/Library/Application Support/remote-ctrl/remote-settings.json"
   ```
   - 一定要带 `--settings`，不带就没有安全层
   - Claude 只能改当前资料夹（`REMOTE_CTRL_WORK_DIR`）里的东西
   - 新资料夹第一次启动时，要在电脑上按「信任」
   - 资料夹要是 git repo 才有还原点（`~/rc-test` 先 `git init`）
3. 离开 tmux（让它继续跑）：按 `Ctrl+B`，再按 `D`
4. 别让电脑睡着
   - Ubuntu：设定 → 电源 → 关闭自动待机
   - Mac：另开终端机跑 `caffeinate -i`（合盖仍会睡）

## 三、在手机上

1. 打开 Claude app，找名称带 `test` 的 session（可能在 Code 区块）
2. 像平常一样打字，例如 `list files in this folder`
3. 跳出 **允许 / 拒绝** 时，看清楚完整指令再按
4. 危险操作（`git push`、`sudo`、`rm -rf`、读 `.env` 等）会直接被拒绝，Claude 会看到 `[remote-guard]` 的原因

## 四、回到电脑前

```bash
tmux attach -t remote     # 看远端做了什么；按 Ctrl+C 两次结束 claude，再 exit
git diff                  # 检查改了哪些档案
```

手机每送出一个 prompt，都会自动存一个**还原点**（整个资料夹的快照，不会动到你的档案或分支）。在工作资料夹里执行（`scripts/` 换成这个 repo 的路径）：

```bash
python3 scripts/restore.py log                 # 看远端做过的每个动作（✅ 放行 ❓ 问你 ⛔ 拒绝）
python3 scripts/restore.py list                # 列出还原点
python3 scripts/restore.py diff <id>           # 这个还原点之后改了哪些档案（加 --full 看内容）
python3 scripts/restore.py restore <id>        # 还原（会先把现在存成新还原点，可以反悔）
```
`<id>` 打开头几个字就好。资料夹不是 git repo 时不会有还原点，Claude 会被提醒。

## 常见问题

| 状况 | 怎么办 |
|---|---|
| preflight「可信账号 ❌」 | `--show-org` 看登录的 email；不是你的就 `claude auth logout` 再 `claude auth login` |
| preflight「登录有效期 ❌ / ⚠️」 | `claude auth login` |
| preflight「安全层设定 ❌」（含「缺少 checkpoint.py」） | `conda activate remote-ctrl-agent && python scripts/render_settings.py` |
| 手机上看不到 session | `tmux attach -t remote` 确认 claude 还在跑；电脑没睡着、有网络 |
| 正常指令被拒绝 | 看 `[remote-guard]` 后面的原因，请 Claude 改成简单的写法 |

## 开发者

```bash
conda activate remote-ctrl-agent
python -m pytest -q
```
设计：[outline.md](outline.md) · 进度：[milestone.md](milestone.md) · 架构：[docs/architecture.md](docs/architecture.md)
