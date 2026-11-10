"""把所有检查串起来：先收集资料（执行指令、读设定档），再交给各个 check 函数判断。"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import platform_utils as pu
from preflight import commands, config_store
from preflight.checks_claude import (
    check_api_key_env,
    check_auth,
    check_claude_found,
    check_login_expiry,
    check_platform,
    check_remote_flag,
    check_trusted_account,
)
from preflight.checks_system import check_conda_env, check_remote_settings, check_tool, check_work_dirs
from preflight.models import FAIL, Check


def run_checks() -> list[Check]:
    # 1. 本机设定档
    cfg, cfg_error = config_store.read_local_config()
    checks = [check_platform(sys.platform), check_api_key_env(dict(os.environ))]
    if cfg_error:
        checks.append(Check("本机设定档", FAIL, cfg_error, "修正或删掉这个档案后重新 --trust"))

    # 2. Claude：版本、登录、可信账号（用移除 API key 后的环境跑）
    clean_env = pu.sanitized_env()
    claude = pu.find_tool("claude")
    checks.append(check_claude_found(claude))
    if claude:
        v_code, v_out, _ = commands.run([claude, "--version"], env=clean_env)
        h_code, h_out, _ = commands.run([claude, "--help"], env=clean_env)
        version = commands.first_word(v_out) if v_code == 0 else "?"
        checks.append(check_remote_flag(h_out, version, help_ok=h_code == 0))
        status = commands.read_auth_status(claude, clean_env)
        checks += [
            check_auth(status),
            check_login_expiry(status, datetime.now(timezone.utc)),
            check_trusted_account(status, cfg.get("trusted_org_id")),
        ]

    # 3. 电脑：工具、conda、工作目录
    checks.append(check_tool("tmux", pu.find_tool("tmux"),
                             "brew install tmux" if pu.IS_MAC else "sudo apt install tmux"))
    checks.append(check_tool(f"防休眠工具 {pu.anti_sleep_tool()}", pu.find_tool(pu.anti_sleep_tool()),
                             "macOS 内建，检查 PATH" if pu.IS_MAC else "需要 systemd"))
    checks.append(check_conda_env(commands.conda_env_names()))
    checks.append(check_remote_settings(pu.config_dir() / "remote-settings.json"))
    checks += check_work_dirs(cfg.get("work_dirs", []))
    return checks
