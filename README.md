# remote_ctrl_agent

人不在电脑前时，用手机的 **Claude app** 控制自己电脑（Ubuntu / Mac）上的 Claude Code（`claude --remote-control`）。
这个 repo 加了三样东西：启动前检查（preflight）、安全层（每个动作自动允许 / 问你 / 拒绝）、每台电脑的远端设定。

> 进度：安全层、还原点已在 Ubuntu 手机实测通过；一行指令启动 + 安全等级（M5）已完成，还没在手机上实测。
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
4. 设定允许远端操作的资料夹（白名单）。先看设定档在哪（会印出可以直接复制的编辑指令）：
   ```bash
   python3 scripts/preflight.py --config
   ```
   Mac 的路径有空白（`Application Support`），自己打指令时要加引号：`nano "$HOME/Library/Application Support/remote-ctrl/config.json"`。在设定档里加上：
   ```json
   "work_dirs": ["~/Desktop/learn_git"],
   "default_dir": "~/Desktop/learn_git",
   "default_level": "readonly"
   ```
   - `work_dirs`：只有这些资料夹（和里面的子资料夹）能远端操作
   - `default_dir` / `default_level`：不指定时用的预设值；开机自动启动也用这组
5. 跑检查：全部 ✅ 或只有 ⚠️ 就能用；有 ❌ 就照 `→` 后面的提示修
   ```bash
   python3 scripts/preflight.py
   ```
6. （可选）登录后自动启动：用预设资料夹和等级在背景启动
   ```bash
   scripts/install.sh               # 安装（下次登录生效）；scripts/install.sh --uninstall 移除
   ```

## 二、每次出门前

一行指令：选**安全等级**和**资料夹**，检查通过后在 tmux 背景启动（防休眠、挂了自动重启都包含在内）：

```bash
scripts/start-remote.sh --level readonly --dir ~/Desktop/learn_git
```

| 等级 | 手机能做什么 | 适合 |
|---|---|---|
| `readonly` | 只能看（读档、搜寻、`git status`）；其他一律拒绝，不会问你 | 出门只想看进度、问问题 |
| `normal` | 读档直接放行；改档、跑程式会问你；危险操作拒绝 | 需要远端改程式 |

- `--dir` 必须在 `work_dirs` 白名单里；`normal` 需要是 git repo 的根目录（才有还原点，git 指令也不会动到白名单外）
- 远端 session 用专用的 tmux（`tmux -L remote-ctrl`），不会和你自己的 tmux 混在一起
- 有未 commit 的修改时，会先自动存一个还原点再启动
- 不加 `--level` / `--dir` 就用 `config.json` 的预设值
- 新资料夹第一次启动前，先在那个资料夹手动跑一次 `claude`，按「信任」
- Mac 合盖仍会睡，要接电源 + 外接显示器

其他：
```bash
scripts/start-remote.sh --status                 # 列出正在跑的远端 session
scripts/start-remote.sh --stop --name Desktop_learn_git  # 停止（也可用 --dir；--all 全部停止）
# 在电脑上看它在做什么：--status 会印出 tmux 指令（离开：Ctrl+B 再按 D）
```

## 三、在手机上

1. 打开 Claude app，找名称是「上一层资料夹_资料夹名」的 session，例如 `Desktop_learn_git`、`test_area_learn_git`（`--status` 会列出名称和路径）
2. 像平常一样打字，例如 `list files in this folder`
3. 跳出 **允许 / 拒绝** 时，看清楚完整指令再按
4. 危险操作（`git push`、`sudo`、`rm -rf`、读 `.env` 等）会直接被拒绝，Claude 会看到 `[remote-guard]` 的原因

## 四、回到电脑前

```bash
scripts/start-remote.sh --stop --name Desktop_learn_git   # 结束远端 session
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
| 手机上看不到 session | `scripts/start-remote.sh --status` 确认还在跑；照 `--status` 印出的 tmux 指令接上去看错误讯息；电脑没睡着、有网络 |
| 正常指令被拒绝 | 看 `[remote-guard]` 后面的原因，请 Claude 改成简单的写法 |
| `readonly` 下什么都被拒绝 | 正常：readonly 只允许看。要改档就停掉，改用 `--level normal` 重新启动 |
| 「不在 work_dirs 白名单里」 | 把资料夹路径加进 `config.json` 的 `work_dirs` |

## 开发者

```bash
conda activate remote-ctrl-agent
python -m pytest -q
```
设计：[outline.md](outline.md) · 进度：[milestone.md](milestone.md) · 架构：[docs/architecture.md](docs/architecture.md)
