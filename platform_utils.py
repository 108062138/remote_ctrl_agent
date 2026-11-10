"""Ubuntu / macOS differences live here. Standard library only (runs on macOS system python3 too)."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

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
