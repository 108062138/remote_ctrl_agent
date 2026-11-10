"""动作纪录（audit log）：远端 session 每次用工具、每个 prompt 都记一行 JSON。

位置：Ubuntu ~/.local/state/remote-ctrl/audit.jsonl，Mac ~/Library/Logs/remote-ctrl/audit.jsonl
档案权限 600，远端 session 不能修改（在安全层的受保护清单）。

写入失败不会影响判断（只是少一笔纪录），也不会让 hook 当掉。
不记录写入档案的内容，只记路径；指令太长会截断。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

LOG_NAME = "audit.jsonl"
MAX_FIELD = 500


def _short(value, limit: int = MAX_FIELD):
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"…（共 {len(value)} 字）"
    return value


def summarize_input(tool_name: str, tool_input: dict) -> dict:
    """只留下看得懂发生什么的栏位，不留档案内容。"""
    if not isinstance(tool_input, dict):
        return {}
    keep = ("command", "file_path", "notebook_path", "path", "pattern", "glob", "url", "query", "description")
    out = {k: _short(tool_input[k]) for k in keep if k in tool_input}
    if tool_name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        for k in ("content", "new_string", "old_string", "new_source", "edits"):
            if k in tool_input:
                v = tool_input[k]
                out[f"{k}_size"] = len(v) if isinstance(v, (str, list)) else None
    return out


def write(log_dir: Path, event: dict) -> None:
    """写一行纪录。任何错误都吞掉：纪录失败绝不能影响安全判断或挡住 prompt。"""
    try:
        log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = log_dir / LOG_NAME
        record = {"time": datetime.now().isoformat(timespec="seconds"), **event}
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8", errors="backslashreplace") as f:
            f.write(line)
    except Exception:
        pass


def read(log_dir: Path, tail: int = 50) -> list[dict]:
    path = log_dir / LOG_NAME
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-tail:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            out.append({"time": "?", "event": "broken", "raw": line[:200]})
    return out
