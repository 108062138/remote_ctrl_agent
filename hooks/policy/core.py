"""判断结果（Decision）和判断时需要的路径资讯（Context）。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ALLOW, ASK, DENY = 0, 1, 2
LEVEL_NAME = {ALLOW: "allow", ASK: "ask", DENY: "deny"}


@dataclass(frozen=True)
class Decision:
    level: int
    reason: str

    @property
    def name(self) -> str:
        return LEVEL_NAME[self.level]


def worst(decisions, default: Decision | None = None) -> Decision:
    """多个判断取最严格的（Deny > Ask > Allow）。"""
    result = default or Decision(ALLOW, "只读操作")
    for d in decisions:
        if d.level > result.level:
            result = d
    return result


class Unclear(Exception):
    """看不懂（不认得的选项、包装）。由呼叫的人决定要 Ask 还是 Deny。"""


# 机密：不论读写一律 Deny
SENSITIVE_HOME_PATHS = (
    ".ssh", ".gnupg", ".aws", ".azure", ".config/gcloud", ".kube", ".docker",
    ".config/gh", ".password-store", ".local/share/keyrings", "Library/Keychains",
    ".claude/.credentials.json", ".claude.json", ".netrc", ".git-credentials",
    ".npmrc", ".pypirc",
)


def real(p) -> Path:
    return Path(os.path.realpath(os.path.expanduser(str(p))))


@dataclass
class Context:
    """判断时需要的路径。全部是 realpath（已解开 symlink）。"""
    work_dir: Path
    home: Path
    tool_root: Path
    config_dir: Path
    cwd: Path | None = None  # None = 不确定（例如 cd 到变数路径之后）
    sensitive_dirs: tuple = field(default=())
    protected: tuple = field(default=())
    scratch: tuple = field(default=())


def make_context(work_dir, *, cwd=None, home=None, tool_root=None, config_dir=None) -> Context:
    home = real(home or Path.home())
    work_dir = real(work_dir)
    tool_root = real(tool_root or Path(__file__).resolve().parents[2])
    config_dir = real(config_dir or home / ".config" / "remote-ctrl")
    sensitive = tuple(real(home / p) for p in SENSITIVE_HOME_PATHS)
    protected = tuple(real(p) for p in (
        # 安全层本身：不能被远端修改或关掉
        tool_root / "hooks", tool_root / "deploy", tool_root / "scripts",
        tool_root / "preflight", tool_root / "platform_utils.py", config_dir,
        home / ".claude" / "settings.json", home / ".claude" / "settings.local.json",
        home / ".claude" / "hooks",
        # shell 启动档：改了之后每次开 shell 都会执行
        home / ".bashrc", home / ".bash_profile", home / ".profile", home / ".zshrc",
        home / ".zprofile", home / ".zshenv",
        # 工作目录里能让程式「之后自动执行」的设定
        work_dir / ".claude", work_dir / ".mcp.json",
        work_dir / ".git" / "hooks", work_dir / ".git" / "config",
    ))
    scratch = tuple(sorted({real(p) for p in ("/tmp", "/var/tmp", os.environ.get("TMPDIR") or "/tmp")}))
    return Context(work_dir=work_dir, home=home, tool_root=tool_root, config_dir=config_dir,
                   cwd=real(cwd) if cwd else work_dir,
                   sensitive_dirs=sensitive, protected=protected, scratch=scratch)
