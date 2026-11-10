"""执行外部指令（claude、conda）并把输出解析成 Python 资料。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import platform_utils as pu


def run(cmd: list[str], env: dict[str, str] | None = None) -> tuple[int, str, str]:
    """执行指令，回传 (return code, stdout, stderr)。

    stdout 和 stderr 分开，避免警告讯息混进 JSON。
    """
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)
        return p.returncode, p.stdout, p.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, "", str(e)


def parse_json_object(text: str) -> dict | None:
    """取出文字里第一个 JSON 物件 {...}，前后多出来的文字都忽略。"""
    start = text.find("{")
    if start < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text, start)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def first_word(text: str) -> str:
    """'2.1.322 (Claude Code)' -> '2.1.322'"""
    words = text.split()
    return words[0] if words else "?"


def read_auth_status(claude: str, env: dict[str, str] | None = None) -> dict | None:
    """读 `claude auth status --json`。

    用移除了 API key 等变量的环境来跑，结果和远端 session 实际启动时一致。
    """
    code, out, _ = run([claude, "auth", "status", "--json"], env=env or pu.sanitized_env())
    if code != 0:
        return None
    return parse_json_object(out)


def conda_env_names() -> list[str] | None:
    """列出 conda 环境名称；找不到 conda 时回传 None。"""
    conda = pu.find_tool("conda")
    if not conda:
        for base in ("anaconda3", "miniconda3", "miniforge3"):
            candidate = Path.home() / base / "bin" / "conda"
            if candidate.is_file():
                conda = str(candidate)
                break
    if not conda:
        return None
    code, out, _ = run([conda, "env", "list", "--json"])
    if code != 0:
        return None
    envs = (parse_json_object(out) or {}).get("envs")
    if not isinstance(envs, list):
        return None
    return [Path(e).name for e in envs if isinstance(e, str)]
