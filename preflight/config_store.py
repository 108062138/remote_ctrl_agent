"""本机设定档：记录可信账号（trusted_org_id）和工作目录（work_dirs）。

位置（不在 repo 里，不会被 commit）：
  Ubuntu  ~/.config/remote-ctrl/config.json
  macOS   ~/Library/Application Support/remote-ctrl/config.json
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import platform_utils as pu

CONFIG_FILE = "config.json"


def config_path() -> Path:
    return pu.config_dir() / CONFIG_FILE


def read_local_config(path: Path | None = None) -> tuple[dict, str | None]:
    """回传 (设定, 错误讯息)。档案不存在不算错；内容坏掉才算错。"""
    path = path or config_path()
    if not path.is_file():
        return {}, None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        return {}, f"{path} 读不了或不是合法 JSON：{e}"
    if not isinstance(data, dict):
        return {}, f"{path} 的内容必须是 JSON 物件 {{...}}"
    return data, None


def load_local_config(path: Path | None = None) -> dict:
    return read_local_config(path)[0]


def save_local_config(cfg: dict, path: Path | None = None) -> Path:
    """写入设定档。档案一建立就是 600 权限（只有你能读写），写完再一次换上。"""
    path = path or config_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    os.chmod(tmp, 0o600)  # tmp 若原本就存在，权限可能不对
    os.replace(tmp, path)
    return path
