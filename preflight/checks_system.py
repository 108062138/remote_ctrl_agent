"""和电脑有关的检查：工具、conda 环境、工作目录。"""

from __future__ import annotations

from pathlib import Path

from preflight import config_store
from preflight.models import FAIL, OK, WARN, Check

CONDA_ENV = "remote-ctrl-agent"


def check_tool(label: str, path: str | None, fix: str) -> Check:
    """必备工具（tmux、防休眠工具）：找不到就 ❌。"""
    if path:
        return Check(label, OK, path)
    return Check(label, FAIL, "找不到", fix)


def check_conda_env(env_names: list[str] | None) -> Check:
    """conda 环境：M3 的 hook 才需要，所以只警告。"""
    name = f"conda env {CONDA_ENV}"
    if env_names is None:
        return Check(name, WARN, "找不到 conda", "安装 conda 后：conda env create -f environment.yml")
    if CONDA_ENV in env_names:
        return Check(name, OK, "存在")
    return Check(name, WARN, "不存在（M3 的 hook 需要）", "conda env create -f environment.yml")


def check_remote_settings(path: Path) -> Check:
    """M3 安全层：远端专用设定档要存在，里面 hook 用的 python 也要存在。"""
    import json
    import shlex

    name = "安全层设定（remote-settings.json）"
    fix = "conda activate remote-ctrl-agent && python scripts/render_settings.py"
    if not path.is_file():
        return Check(name, FAIL, "还没产生", fix)
    try:
        data = json.loads(path.read_text())
        command = data["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        python, guard = shlex.split(command)[:2]
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return Check(name, FAIL, "内容坏掉，找不到 hook 设定", fix)
    for label, p in (("hook 用的 python", python), ("remote_guard.py", guard)):
        if not Path(p).is_file():
            return Check(name, FAIL, f"{label} 不存在：{p}", fix)
    return Check(name, OK, str(path))


def check_work_dirs(dirs: object) -> list[Check]:
    """每个工作目录都要存在，而且是 git repo（M4 的还原点要用 git）。"""
    if not dirs:
        return [Check("工作目录", WARN, "还没设定 work_dirs",
                      f"之后在 {config_store.config_path()} 加上 \"work_dirs\": [...]")]
    if not isinstance(dirs, list) or not all(isinstance(d, str) for d in dirs):
        return [Check("工作目录", FAIL, "work_dirs 必须是字串清单", '例如 "work_dirs": ["~/proj"]')]
    checks = []
    for d in dirs:
        p = Path(d).expanduser()
        if not p.is_dir():
            checks.append(Check(f"工作目录 {d}", FAIL, "不存在", "修正 config.json 里的路径"))
        elif not (p / ".git").exists():
            checks.append(Check(f"工作目录 {d}", FAIL, "不是 git repo（还原点需要 git）", f"cd {d} && git init"))
        else:
            checks.append(Check(f"工作目录 {d}", OK, "存在且是 git repo"))
    return checks
