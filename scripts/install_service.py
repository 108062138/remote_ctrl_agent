#!/usr/bin/env python3
"""安装 / 移除「登录后自动启动远端 session」的服务。

  python3 scripts/install_service.py              安装并启用（下次登录时生效，不会马上启动）
  python3 scripts/install_service.py --dry-run    只显示会写入的内容
  python3 scripts/install_service.py --uninstall  停用并移除

Ubuntu：~/.config/systemd/user/remote-ctrl.service（systemctl --user，不需要 sudo）
Mac   ：~/Library/LaunchAgents/com.remotectrl.agent.plist

开机启动用 config.json 的 default_level 和 default_dir，所以安装前要先设定好。
只用标准库。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import platform_utils as pu  # noqa: E402
from preflight import config_store  # noqa: E402

START = ROOT / "scripts" / "start_remote.py"
UNIT_NAME = "remote-ctrl.service"
PLIST_NAME = "com.remotectrl.agent.plist"


def service_path(home: Path) -> Path:
    if pu.IS_MAC:
        return home / "Library" / "LaunchAgents" / PLIST_NAME
    return home / ".config" / "systemd" / "user" / UNIT_NAME


def service_path_env() -> str:
    """服务的 PATH：开机时 PATH 很短，把会用到的工具所在的资料夹都放进去。"""
    dirs = []
    for tool in ("claude", "tmux", "git", pu.anti_sleep_tool(), "python3"):
        found = pu.find_tool(tool)
        if found:
            d = str(Path(found).parent)
            if d not in dirs:
                dirs.append(d)
    for d in ("/usr/local/bin", "/usr/bin", "/bin"):
        if d not in dirs:
            dirs.append(d)
    return ":".join(dirs)


def _systemd_escape(value: str) -> str:
    """放在 systemd 双引号里的值：\\ 和 " 要跳脱，% 要写成 %%。"""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")


def render(python: str, path_env: str, log_dir: Path) -> str:
    """填入路径。路径有空白、%、&、< 等字元也不会坏掉（systemd 用引号跳脱，plist 用 XML 跳脱）。"""
    from xml.sax.saxutils import escape

    template = ROOT / "deploy" / (PLIST_NAME if pu.IS_MAC else UNIT_NAME)
    quote = escape if pu.IS_MAC else _systemd_escape
    text = template.read_text()
    for key, value in {"{{PYTHON}}": python, "{{SCRIPT}}": str(START), "{{PATH}}": path_env,
                       "{{LOG_DIR}}": str(log_dir)}.items():
        text = text.replace(key, quote(value))
    return text


def check_defaults(cfg: dict) -> str | None:
    """开机启动需要预设值。和 preflight 用同一个检查（等级、资料夹存在、在白名单里）。"""
    from preflight.checks_system import check_defaults as preflight_check
    from preflight.models import OK

    c = preflight_check(cfg)
    if c.status != OK:
        return f"{c.detail}（{c.fix}）" if c.fix else c.detail
    return None


def run(cmd: list[str]) -> bool:
    print("  $ " + " ".join(cmd))
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        print(f"    ❌ {p.stderr.strip() or '失败'}")
        return False
    return True


def install(args) -> int:
    cfg, err = config_store.read_local_config()
    problem = err or check_defaults(cfg)
    if problem:
        print(f"❌ {problem}\n   设定档：{pu.shell_path(config_store.config_path())}")
        return 1
    content = render(sys.executable, service_path_env(), pu.log_dir())
    path = service_path(Path.home())
    if args.dry_run:
        print(f"（dry-run）会写入 {pu.shell_path(path)}：\n")
        print(content)
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    pu.log_dir().mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(content)
    print(f"✅ 已写入 {pu.shell_path(path)}")
    if pu.IS_MAC:
        print("下次登录 Mac 时会自动启动。想现在就启动：")
        print(f"  launchctl bootstrap gui/{os.getuid()} {pu.shell_path(path)}")
    else:
        ok = run(["systemctl", "--user", "daemon-reload"]) and run(["systemctl", "--user", "enable", UNIT_NAME])
        if not ok:
            print(f"❌ 服务档已写入，但启用失败（例如 SSH 连线里没有 user systemd）。请在桌面登录的终端机重跑，\n"
                  f"   或执行：systemctl --user daemon-reload && systemctl --user enable {UNIT_NAME}")
            return 1
        print("下次登录 Ubuntu 时会自动启动（登出就会停；锁屏没关系）。想现在就启动：")
        print(f"  systemctl --user start {UNIT_NAME}")
    print(f"开机用：{cfg['default_level']} 等级、资料夹 {cfg['default_dir']}")
    return 0


def uninstall(args) -> int:
    path = service_path(Path.home())
    if args.dry_run:
        print(f"（dry-run）会停用并删除 {pu.shell_path(path)}")
        return 0
    if pu.IS_MAC:
        run(["launchctl", "bootout", f"gui/{os.getuid()}", str(path)])
    else:
        run(["systemctl", "--user", "disable", "--now", UNIT_NAME])
    if path.exists():
        path.unlink()
        print(f"✅ 已删除 {pu.shell_path(path)}")
    if not pu.IS_MAC:
        run(["systemctl", "--user", "daemon-reload"])
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="安装 / 移除自动启动远端 session 的服务")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    return uninstall(args) if args.uninstall else install(args)


if __name__ == "__main__":
    sys.exit(main())
