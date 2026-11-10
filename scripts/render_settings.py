#!/usr/bin/env python3
"""产生这台电脑的远端专用设定档 remote-settings.json。

为什么要「产生」：hook 要用 conda env 里的 python（才有 bashlex），
而这个路径在 Ubuntu 和 Mac 不一样，所以从 deploy/remote-settings.template.json 填进实际路径。

用法（要在 conda env 里跑）：
  conda activate remote-ctrl-agent
  python scripts/render_settings.py

输出到本机设定资料夹（和 config.json 同一个地方，不在 repo 里）。
"""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import platform_utils as pu  # noqa: E402
from preflight import config_store  # noqa: E402

TEMPLATE = ROOT / "deploy" / "remote-settings.template.json"
GUARD = ROOT / "hooks" / "remote_guard.py"
SETTINGS_NAME = "remote-settings.json"


def settings_path() -> Path:
    return pu.config_dir() / SETTINGS_NAME


def hook_command(python: str, guard: Path) -> str:
    return f"{shlex.quote(python)} {shlex.quote(str(guard))}"


def render(python: str) -> dict:
    text = TEMPLATE.read_text()
    data = json.loads(text)
    for group in data["hooks"]["PreToolUse"]:
        for h in group["hooks"]:
            h["command"] = h["command"].replace("{{HOOK_COMMAND}}", hook_command(python, GUARD))
    return data


def main() -> int:
    try:
        import bashlex  # noqa: F401
    except ImportError:
        print("❌ 这个 python 没有 bashlex。请先 conda activate remote-ctrl-agent 再执行。")
        return 1
    path = config_store.save_local_config(render(sys.executable), settings_path())
    print(f"✅ 已产生 {path}")
    print(f"   hook：{hook_command(sys.executable, GUARD)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
