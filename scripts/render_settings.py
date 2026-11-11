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
CHECKPOINT = ROOT / "hooks" / "checkpoint.py"
SETTINGS_NAME = "remote-settings.json"
# 范本里的占位字 -> 要执行的 hook 程式
PLACEHOLDERS = {"{{HOOK_COMMAND}}": GUARD, "{{CHECKPOINT_COMMAND}}": CHECKPOINT}


def settings_path() -> Path:
    return pu.config_dir() / SETTINGS_NAME


def hook_command(python: str, script: Path) -> str:
    return f"{shlex.quote(python)} {shlex.quote(str(script))}"


def _rule_path(path: Path) -> str:
    """权限规则里的路径：在家目录下就写成 ~/...。"""
    home = Path.home().resolve()
    path = path.resolve()
    return "~/" + str(path.relative_to(home)) if home in path.parents else str(path)


def machine_deny_rules() -> list[str]:
    """和这台电脑有关的拒绝规则（路径依平台不同，不能写死在模板里）。

    - 本机设定资料夹（含可信账号 orgId）：远端不能读也不能改
    - 产生这份设定的 repo（安全层本身）：远端不能改
    """
    cfg = _rule_path(pu.config_dir())
    return [f"Read({cfg}/**)", f"Edit({cfg}/**)", f"Edit({_rule_path(ROOT)}/**)"]


def render(python: str) -> dict:
    data = json.loads(TEMPLATE.read_text())
    deny = data["permissions"]["deny"]
    deny.extend(r for r in machine_deny_rules() if r not in deny)
    for groups in data["hooks"].values():
        for group in groups:
            for h in group["hooks"]:
                for placeholder, script in PLACEHOLDERS.items():
                    h["command"] = h["command"].replace(placeholder, hook_command(python, script))
    return data


def main() -> int:
    try:
        import bashlex  # noqa: F401
    except ImportError:
        print("❌ 这个 python 没有 bashlex。请先 conda activate remote-ctrl-agent 再执行。")
        return 1
    path = config_store.save_local_config(render(sys.executable), settings_path())
    print(f"✅ 已产生 {path}")
    print(f"   安全层 hook：{hook_command(sys.executable, GUARD)}")
    print(f"   还原点 hook：{hook_command(sys.executable, CHECKPOINT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
