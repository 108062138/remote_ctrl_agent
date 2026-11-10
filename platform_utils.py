"""Ubuntu / macOS differences live here. Standard library only (runs on macOS system python3 too)."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

# 安全等级（唯一的定义；hooks/policy/levels.py、启动脚本、安装脚本、preflight 都用这个）
LEVELS = ("readonly", "normal")
# 远端 session 专用的 tmux server（-L），和你自己的 tmux 分开：停掉服务不会影响你的 session
TMUX_SOCKET = "remote-ctrl"

# Env vars that would override the claude.ai subscription login, switch the provider,
# or send traffic to another endpoint. The remote session is always started without them.
# Their values are never read.
OVERRIDING_AUTH_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)


def platform_name() -> str:
    return "macOS" if IS_MAC else "Ubuntu/Linux" if IS_LINUX else sys.platform


def config_dir() -> Path:
    """Per-machine config, outside the repo so it is never committed."""
    if IS_MAC:
        return Path.home() / "Library" / "Application Support" / "remote-ctrl"
    # XDG spec: ignore XDG_CONFIG_HOME when it is empty or relative.
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    base = Path(xdg) if xdg and Path(xdg).is_absolute() else Path.home() / ".config"
    return base / "remote-ctrl"


def log_dir(env: dict[str, str] | None = None) -> Path:
    """动作纪录（audit log）放这里，不在 repo 里。远端 session 不能修改（在安全层的受保护清单）。

    环境变量 REMOTE_CTRL_LOG_DIR 可以改位置（hook 和 restore.py log 都用这个函式，位置一定一致）。
    """
    env = os.environ if env is None else env
    if env.get("REMOTE_CTRL_LOG_DIR"):
        return Path(env["REMOTE_CTRL_LOG_DIR"])
    if IS_MAC:
        return Path.home() / "Library" / "Logs" / "remote-ctrl"
    xdg = os.environ.get("XDG_STATE_HOME", "")
    base = Path(xdg) if xdg and Path(xdg).is_absolute() else Path.home() / ".local" / "state"
    return base / "remote-ctrl"


def extra_bin_dirs() -> list[Path]:
    """Places tools often live that a short PATH (launchd, systemd) may miss."""
    dirs = [Path.home() / ".local" / "bin"]
    if IS_MAC:
        dirs += [Path("/opt/homebrew/bin"), Path("/usr/local/bin")]  # Apple Silicon, Intel
    else:
        dirs += [Path("/usr/local/bin"), Path("/home/linuxbrew/.linuxbrew/bin")]
    return dirs


def find_tool(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for d in extra_bin_dirs():
        candidate = d / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def anti_sleep_tool() -> str:
    return "caffeinate" if IS_MAC else "systemd-inhibit"


def sanitized_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """Copy of the environment with API-key overrides removed, so claude uses the subscription login."""
    env = dict(os.environ if env is None else env)
    for var in OVERRIDING_AUTH_VARS:
        env.pop(var, None)
    return env


def in_whitelist(path, work_dirs) -> Path | None:
    """path（解开 symlink、..）在 work_dirs 里某个资料夹之内时，回传解开后的路径；否则 None。"""
    p = Path(path).expanduser().resolve()
    if not isinstance(work_dirs, list):
        return None
    for w in work_dirs:
        if isinstance(w, str) and w:
            base = Path(w).expanduser().resolve()
            if p == base or base in p.parents:
                return p
    return None
