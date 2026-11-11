# 贡献 Claude Code：讨论纪录

> 2026-11-11 讨论，**尚未决定**要从哪一步开始。之后回来从「下一步」挑一项即可。
> 相关：进度见 [milestone.md](../milestone.md)，踩坑纪录见 [error.md](../error.md)。

## 目标
使用者的目标是**帮 Claude Code 做贡献**。remote_ctrl_agent 是目前累积的成果，可以当作贡献的素材。

## 前提（待查证）
- Claude Code 的**核心程式不是开源的**，没办法直接改原始码送 PR
- 以下贡献管道是依记忆整理，Anthropic 的方式可能已更新。**真正动手前要先查官方文件**（官方 GitHub repo 的 CONTRIBUTING、plugin 收录规则、资安回报方式）

## 三条贡献管道

### 1. 做成 plugin 分享（最适合这个专案）
- Claude Code 支援 plugin 和 plugin marketplace：可以用自己的 GitHub repo 当 marketplace，别人加入后就能安装
- 官方可能有收录 plugin 的地方（规则待查）
- 题目：「远端操作（Remote Control）时的安全层」：preflight、Allow / Ask / Deny hook、git 还原点、安全等级

### 2. 回报问题 / 提功能建议（成本最低、价值高）
这两天实际踩到、属于 Claude Code 本身可以改进的地方（都有重现步骤，见 error.md）：

| # | 建议 | 来源 |
|---|---|---|
| 1 | hook 收到的资料里加上「这是 Remote Control session」：现在只能靠自己设的环境变量（`REMOTE_CTRL_LEVEL`）分辨 | M3 / M5 设计 |
| 2 | 安全类 hook 超时可以选「拒绝」（fail closed）：现在超过 timeout 会被当成非阻挡错误，工具照样执行 | error.md：M4 code review #1 |
| 3 | Remote Control 内建「远端权限设定」：远端可以用 `claude -p`、`npx @anthropic-ai/claude-code` 开一个没有安全层的新 session | error.md：2026-11-11 两个 session 同时改 repo |

⚠️ **和安全绕过有关的问题要走 Anthropic 的资安回报管道私下回报，不要直接公开开 issue。**第 2、3 项写之前要先判断属于功能建议还是资安问题。

### 3. 文件 / 范例
- 「Remote Control + hook」的实战范例、踩过的坑（error.md 里有很多）本身就是教学素材

## 离「可以拿出去贡献」还差什么

| 差距 | 说明 |
|---|---|
| 英文文件 | README、注解、讯息目前都是中文；社群主要用英文。至少要英文 README，程式讯息最好能切换语言 |
| plugin 包装 | 依 plugin 格式放资料夹、`SKILL.md`、hook 设定（路径用 plugin 根目录变数） |
| hook 只在远端生效 | plugin 启用后 hook 对**所有** session 生效，hook 要先判断「没有 `REMOTE_CTRL_LEVEL` 就不做事」。判断写错：平常用 Claude 会被挡；漏了：远端没有安全层 |
| 不依赖 conda | hook 现在需要 conda env 里的 `bashlex`；别人装 plugin 就要能用（一起打包，或用不需另外安装的方式执行） |
| 每台电脑的设定 | `--trust`、白名单要留在本机（orgId 不能进 plugin），但安装步骤要更简单 |
| 安全说明诚实 | 清楚写出「Deny 层只能尽力挡」和两次 review 找到的已知限制 |
| 中立的命名 | 不能让人误会是 Anthropic 官方的东西 |

## 建议顺序（尚未决定）
1. **查清楚现在的贡献管道**：官方 repo 的 CONTRIBUTING、plugin 收录规则、资安回报方式（可以请 Claude 先查）
2. **写 2–3 个功能建议**：从上表 1–3 开始，error.md 有现成重现步骤（成本最低、最快能贡献）
3. **包装成 plugin + 英文 README**：当成新的 milestone（M7）

## 下一步（回来时挑一项）
- [ ] 查官方贡献管道，更新本文件的「前提」
- [ ] 判断功能建议 1–3 哪些是资安问题、哪些可以公开
- [ ] 写第一份功能建议草稿（建议从 #1「hook 分辨 Remote Control session」开始，最没有资安疑虑）
- [ ] 决定要不要做 M7「包装成 plugin」
