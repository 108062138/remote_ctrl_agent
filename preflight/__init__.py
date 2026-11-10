"""preflight：启动远端 session 前的检查清单。

阅读顺序（由上往下，每个档案只做一件事）：
  models.py         检查结果长什么样（Check：名称、✅/⚠️/❌、说明、修法）
  commands.py       执行外部指令（claude、conda）并解析输出
  config_store.py   读写本机设定档（可信账号、工作目录）
  checks_claude.py  和 Claude 有关的检查：版本、登录、可信账号、环境变量
  checks_system.py  和电脑有关的检查：tmux、防休眠、conda、工作目录
  runner.py         把所有检查串起来跑一遍
  cli.py            指令列：印报告、--show-org、--trust

入口：python3 scripts/preflight.py
只用标准库，Python 3.9+。不读任何 .env，不印出 key 的值。
"""
