# Error Log

记录开发中踩过的坑：错误、原因、怎么修、学到什么。
新的写在最上面。对应的阶段见 [milestone.md](milestone.md)。

## 模板（复制这段来用）
```markdown
### [YYYY-MM-DD] 简短标题
- **阶段**：M?
- **平台**：Ubuntu / macOS / 都有
- **现象**：看到了什么（错误讯息原文贴这里）
- **原因**：为什么会发生
- **修法**：改了什么（文件 / commit）
- **教训**：下次怎么避免
```

---

### [2026-11-11] Mac：设定档路径有空白，复制后指令坏掉
- **阶段**：M5（Mac 实测）
- **平台**：macOS
- **现象**：`--trust` 印出 `已写入 /Users/popo/Library/Application Support/remote-ctrl/config.json`，复制去 `cat` 变成两个路径：`cat: /Users/popo/Library/Application: No such file or directory`
- **原因**：Mac 的 `Application Support` 有空白，程式印路径时没加引号
- **修法**：新增 `platform_utils.shell_path()`，所有印给人看的路径都用 `shlex.quote` 加引号；新增 `preflight.py --config` 印出设定档位置、编辑指令和内容（orgId 只显示开头）；README 加上 Mac 要加引号的说明
- **教训**：印给使用者复制的路径一定要考虑空白；在 Ubuntu 上开发看不出来，要两个平台都测

### [2026-11-11] 两个 session 同时改 repo；远端可以读到 orgId、可以执行 claude
- **阶段**：M5
- **平台**：Ubuntu
- **现象**：
  1. 另一个 Claude session 在 `~/Desktop/test_area/remote_ctrl_agent`（另一份 clone）发现：远端可以 `cat ~/.config/remote-ctrl/config.json` 读到可信账号 orgId；也可以执行 `claude auth status`（显示 orgId）、`claude -p`（开一个没有安全层的新 session）
  2. 它修了程式（commit `6f41797`），并**手动**在 `~/.config/remote-ctrl/remote-settings.json` 加了 3 条 deny。但这个档案是 `render_settings.py` 产生的，重新产生就会被盖掉
  3. `npx @anthropic-ai/claude-code` 这类间接执行 claude 的写法仍然是 Ask
- **原因**：设定资料夹之前只列为「受保护」（不能改，但能读）；没想到 claude 指令本身就是绕过安全层的方法
- **修法**：
  1. 把 `6f41797` fast-forward 进开发用的这份（没有冲突）：设定资料夹改成「机密」（读写都挡）、禁止 `claude` 指令
  2. 手动加的规则改由 `render_settings.py` 自动产生（路径依平台决定）：设定资料夹 Read / Edit、产生设定的那份 repo Edit。`test_area` 那份不在任何工作资料夹里，安全层本来就挡写入，不需要写死
  3. `npx` / `bunx` / `pnpm dlx` / `npm exec` 执行 claude → Deny
  4. 测试共 322 个
- **教训**：
  - 两份 clone、两个 session 同时改会搞混：只在一份开发，另一份用 `git pull` 同步
  - 产生出来的档案不要手动改，要改产生它的模板或程式
  - 「能开新 session 的指令」本身就是绕过安全层的方法，要和 sudo 一样对待

### [2026-11-10] M5 code review：启动脚本与开机服务的 10 个问题
- **阶段**：M5
- **平台**：都有（3、4、8、9 主要是 Ubuntu）
- **现象 → 修法**：
  1. session 只用资料夹名称命名：`~/a/proj` 和 `~/b/proj` 撞名，第二个会被当成「已经在跑」→ 名称加上路径代码和等级 `rc-<名称>-<代码>-<等级>`；同资料夹不同等级要求先停掉
  2. 白名单里有一个非 git / 不存在的资料夹，preflight 就 ❌ 挡住所有启动 → 改成 ⚠️，启动时只检查要用的那个
  3. 服务启动的 tmux server 在服务的 cgroup 里，停服务会杀掉使用者自己的 tmux session → 远端用专用 server `tmux -L remote-ctrl`
  4. user service 的 `network-online.target` 没作用，开机时网络还没好就重启 5 次后放弃 → 启动脚本自己等网络（最多 5 分钟），重启前也等
  5. `--stop --boot` 用没解开的路径算名称，symlink 时停不到 → 和启动用同一个 `resolve_target`
  6. 白名单是 repo 子资料夹时，`git checkout` 等会动到白名单外的档案 → `normal` 必须是 repo 根目录
  7. 安装脚本的预设值检查比 preflight 弱，装好了开机才失败 → 直接用 preflight 的检查
  8. 路径有空白、`%`（systemd）或 `&`、`<`（plist）时服务档坏掉 → 引号 + 跳脱；`systemd-analyze verify` 通过
  9. `systemctl --user enable` 失败仍显示成功 → 失败就说失败、exit 1
  10. 安全等级清单写在 4 个地方 → 统一由 `platform_utils.LEVELS` 定义
- **原因**：只想到「一个资料夹、一个 session、网络正常、路径单纯」的情况；对 systemd user service 的 cgroup 和 target 不熟
- **教训**：
  - 识别用的名称要能唯一对应（路径代码），不能只用好看的名字
  - 背景服务产生的程序会被服务一起管理（cgroup）；共用资源（tmux server）要分开
  - 安装 / 设定类脚本要「失败就说失败」，不要只印警告
  - 同一个规则只写一次（等级清单、白名单判断）

### [2026-11-10] 手机实测发现：测试写进真的纪录、工作目录外的读取被放行
- **阶段**：M3 / M4 手机实测
- **平台**：Ubuntu
- **现象**：
  1. 真正的 audit log 里出现 `/tmp/pytest-...` 的纪录：是跑单元测试时 hook 写进去的
  2. 在 `rc-test` 的 session 里，`cd ~/Desktop/test_area/learn_git && ls -la && git status` 被 ✅ 放行；读工作目录外应该是 ❓
  3. `~/rc-test` 没有还原点：资料夹不是 git repo（`git init` 那步漏了）。这是预期行为，提醒机制正常
- **原因**：
  1. 测试没有把 `REMOTE_CTRL_LOG_DIR` 指到暂时资料夹
  2. 不带路径参数的 `ls`、`git status` 读的是「目前目录」，但安全层只检查参数里的路径，没检查目前目录在哪
- **修法**：
  1. 已修：`tests/conftest.py` 让每个测试把纪录写到暂时资料夹；已验证跑完测试真的纪录行数不变。之前那几笔测试纪录保留不删（纪录不该被事后修改）
  2. 未修（只影响读取，风险低）：之后让「不带路径的只读指令」也检查目前目录
- **教训**：会写到使用者真实档案的程式，测试一定要隔离（环境变量 + autouse fixture）

### [2026-11-10] M4 code review：还原点与纪录的 10 个 bug
- **阶段**：M4
- **平台**：都有
- **现象 → 修法**：
  1. hook 10 秒超时没生效：`TimeoutError` 是 `OSError` 的子类别，被读档的 `except OSError` 吞掉，可能拖过 Claude Code 30 秒上限而**完全略过安全检查** → 超时时直接 `os._exit(2)`，不用 raise
  2. 大 repo 拍还原点会被中途砍掉（hook 上限 60 秒 = 单一 git 指令上限）→ 所有 git 指令共用 45 秒期限，hook 上限改 120 秒，超时会提醒 Claude 并写纪录
  3. 还原点用当地时间命名排序，日光节约 / 改时钟会删错 → 改用 UTC
  4. 已有 50 个时还原到最旧的，它会被清掉 → 清理时保护还原目标
  5. 还原会盖掉 `.gitignore` 排除的档案却没备份 → 还原前强制把这些档案存进「还原前」快照
  6. 非 UTF-8 档案让 `diff --full` 当掉、git 超时跳 traceback → surrogateescape 解码、输出用替代字元、超时转成 GitError
  7. 纪录里有无法编码的字元时写入出错，变成误拒 → 纪录写入吞掉所有错误
  8. preflight 把 repo 的子资料夹判成「不是 git repo」还叫你 `git init` → 改用和还原点相同的 `git rev-parse --show-toplevel`
  9. `restore.py log` 没看 `REMOTE_CTRL_LOG_DIR` → 统一由 `platform_utils.log_dir()` 决定位置
  10. 还原、`diff --full` 扫描两次工作目录 → 只扫一次共用
- **原因**：只测了「正常情况」；没想到例外的继承关系（TimeoutError ⊂ OSError）、多个时限之间的关系、时间与编码这类环境差异
- **教训**：
  - 安全相关的超时要用「一定会结束」的方式（`os._exit`），不要用可能被吞掉的例外
  - 外层时限一定要大于内层所有步骤加起来
  - 排序、命名用 UTC；读外部输出要处理编码
  - 每个 review 发现都补一个测试（这次新增 10 个，全部 264 个）

### [2026-11-10] M3 第 2 次 code review：修完又找到 10 个绕过 → 改策略
- **阶段**：M3
- **平台**：都有（第 8 项只在 macOS）
- **现象**：第 1 次修正后再 review，又找到 10 个（reviewer 依程式逻辑推出来，未实际执行）：
  - 会被**自动放行**：
    1. `printf -v BASH_ENV ...; export BASH_ENV; bash -c true`：用 `printf -v` 设定变数，没有 `=` 所以没被检查
    2. `echo .env | xargs -I{} sh -c 'cat {}'`：xargs 的 `{}` 没被当成未知参数
    3. `cat id_rs[^x]`、`cat .en[[:alpha:]]`：Python 的 fnmatch 看不懂 bash 的 `[^x]`、`[[:alpha:]]`
    4. `GLOBIGNORE=x; cat *`：让 `*` 也对到隐藏档
    5. macOS 档名不分大小写：`cat .ENV`、`rg --iglob '.ENV'`
  - 该 Deny 却只 Ask：条件式 `cd`（`true || cd sub`）、超时的 `TimeoutError` 被 `OSError` 处理吞掉、`git {push,x}` / `/bin/r[m]` 藏住指令名称、`curl ... | bash /dev/stdin`
- **原因**：用静态分析判断「一条 Bash 指令会做什么」，本质上补不完：bash 的展开、变数、条件执行、平台差异太多，每补一个洞就有新写法
- **修法**：**不修**，记为已知限制（见 milestone.md M3）。改用「出门前先设定安全等级 + 作用范围」降低风险，放进 M5 启动脚本
- **教训**：安全设计要先想「出事时的影响范围有多大」，而不是只想「能不能挡住每一种写法」。限制范围（工作目录、只读等级、git 还原点）比追求完美的规则更可靠

### [2026-11-10] M3 code review：安全层有 10 类绕过
- **阶段**：M3
- **平台**：都有
- **现象**：code review 实际跑过的绕过手法，最严重的会被**自动放行**（手机不会问）：
  1. `sed "1e rm -rf ~" README.md`：sed 的 `e` 指令会执行程式，却被当成只读
  2. `uniq payload .git/hooks/pre-commit`、`sort --compress-program=...`、`git diff --output=~/.bashrc`：「只读」指令其实能写档或执行程式
  3. `cat .{e,x}nv`：大括号展开读到 `.env`
  4. `grep -r KEY .`：递回搜寻把 `.env` 内容印出来
  5. `ls /**/nomatch*`：万用字元扫整个硬碟，hook 超过 30 秒后 Claude Code 略过它（等于没安全层）
  - 另外有一些该 Deny 却只变成 Ask：`timeout -s KILL 5 rm -rf ~`、`cd -P ~`、`bash -ec "..."`、`curl x | python3 -u`、`bun publish`、`curl -XPOST`、`rsync --del`；还有 `rm *.log` 被误判成 Deny
- **原因**：Allow 层是「黑名单思维」：只读指令只要没命中危险规则就放行，但很多只读指令带特殊选项就能写档或执行程式。选项解析也太简单（不懂 `-s KILL` 这种带值的选项、`-ec` 合并、`--opt=value`）
- **修法**：
  - `hooks/policy.py` 拆成 `hooks/policy/` 资料夹（core、paths、options、bash、commands、tools）
  - **Allow 层改成严格白名单**：只读指令的每个选项都要在清单里才放行，不认得就 Ask。以后漏掉的指令最多多问一次，不会悄悄放行
  - 正规的选项解析（`options.py`）：合并、带值、`--x=v`、`-oFILE`
  - sed 脚本解析器：找出 `e`、`w`、`r` 指令
  - 展开 `{a,b}`、`{a..z}`；万用字元和资料夹扫描都有数量上限；hook 自己 10 秒超时就拒绝
  - 递回读取前先检查资料夹里有没有机密档（会看 rg 是否略过隐藏档、`-g` 样式）
  - 危险环境变量（`PAGER`、`LD_PRELOAD`、`PATH`……）、参数里藏指令（`watch 'git push'`）、subshell 的 cd
  - 测试从 130 个增加到 205 个（全部 241 个），每一条 review 发现都有对应测试
- **教训**：安全规则的「放行」要用白名单，不能用黑名单；黑名单只适合当「一定拒绝」的那层

### [2026-11-10] 安全层被万用字元绕过：`cat .env*`
- **阶段**：M3
- **平台**：都有
- **现象**：测试 `cat .env*` 被判成 Allow（只读），应该是 Deny
- **原因**：机密档名只比对字面上的 `.env`，`.env*` 不符合；路径解析又把万用字元前面的资料夹（目前目录）当成目标
- **修法**：`policy.py` 新增 `_glob_may_hit_sensitive()`：实际展开万用字元检查每个结果，并把 `.e*` 这类可能对到 `.env` 的写法直接视为机密。补了 5 个万用字元测试
- **教训**：安全规则要用「攻击者会怎么写」来测，不是只测正常写法

### [2026-11-10] code review：preflight.py 的 8 个问题
- **阶段**：M2
- **平台**：都有
- **现象**：code review（medium）找到 10 个问题，修了前 8 个：
  1. stdout 和 stderr 接在一起再解析 JSON，Claude 多印一行警告就误判成没登录
  2. 到期时间没时区或不是字串时，整个 preflight 当掉
  3. 只移除 2 个环境变量，`ANTHROPIC_BASE_URL`、`CLAUDE_CODE_USE_BEDROCK` 等漏掉
  4. 设定档内容是 `[]` 或 `work_dirs` 写成字串时会当掉或乱报
  5. `XDG_CONFIG_HOME` 是空字串时，设定档写到目前目录（可能进 repo）
  6. `--trust` 读不到登录资料时，错说成「账号不同」
  7. 设定档先写入再改权限，中间有一瞬间别人读得到
  8. `claude --version` 失败时，把错误讯息第一个字当版本号
- **原因**：只测了正常情况，没测「外部指令输出不如预期」和「设定档被改坏」
- **修法**：`run()` 分开回传 stdout / stderr，`parse_json_object()` 只取第一个 JSON 物件；时间与设定档格式都先检查型别；环境变量清单扩充为 7 个；XDG 空值 / 相对路径时退回 `~/.config`；`--trust` 分开说明原因，设定档坏掉时拒绝覆盖；用 `os.open(..., 0o600)` + `os.replace` 写档；检查指令 return code。新增 10 个测试，共 32 个全过
- **教训**：读外部指令输出时，stdout / stderr 分开、型别先检查；写含敏感资料的档案，一开始就用正确权限建立
- **顺带**：#9（环境变量重复复制、`import os` 位置）在修 #8 时一起改掉了
- **未修**：#10 剩不到 1 天显示「剩 0 天」

### [2026-11-10] pytest-asyncio 出现 PytestDeprecationWarning
- **阶段**：M1
- **平台**：Ubuntu
- **现象**：`The configuration option "asyncio_default_fixture_loop_scope" is unset.`
- **原因**：新版 pytest-asyncio 要求明确设定 async fixture 的 event loop 范围
- **修法**：`pytest.ini` 加上 `asyncio_default_fixture_loop_scope = function`
- **教训**：用 `asyncio_mode = auto` 时一起设定 loop scope
