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
