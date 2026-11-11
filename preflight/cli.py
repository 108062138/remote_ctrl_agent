"""指令列入口：

  python3 scripts/preflight.py                 跑全部检查（有 ❌ 时 exit 1）
  python3 scripts/preflight.py --json          输出 JSON（给之后的 start-remote.sh 用）
  python3 scripts/preflight.py --show-org      显示这台电脑登录的 email、orgId
  python3 scripts/preflight.py --trust ORG_ID  设定这台电脑唯一信任的账号
  python3 scripts/preflight.py --config        显示本机设定档的位置（可直接复制）和内容
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

import platform_utils as pu
from preflight import commands, config_store, runner
from preflight.models import FAIL, ICON, OK, WARN, Check


def print_report(checks: list[Check]) -> None:
    print(f"Remote Control preflight — {pu.platform_name()}\n")
    for c in checks:
        print(f"{ICON[c.status]} {c.name}：{c.detail}")
        if c.fix and c.status != OK:
            print(f"     → {c.fix}")
    fails = sum(c.status == FAIL for c in checks)
    warns = sum(c.status == WARN for c in checks)
    print()
    if fails:
        print(f"❌ {fails} 项未通过，不能启动远端 session。")
    else:
        print(f"✅ 可以启动远端 session（{warns} 项警告）。")


def read_current_account() -> tuple[dict | None, str | None]:
    """回传 (登录资料, 错误讯息)，给 --show-org 和 --trust 共用。"""
    claude = pu.find_tool("claude")
    if not claude:
        return None, "❌ 找不到 claude，请先安装 Claude Code 或检查 PATH。"
    status = commands.read_auth_status(claude)
    if status is None:
        return None, "❌ 读不到 claude auth status，请先执行 claude auth login。"
    if not status.get("orgId"):
        return None, "❌ 登录资料里没有 orgId，请先执行 claude auth login。"
    return status, None


def show_org() -> int:
    status, error = read_current_account()
    if error:
        print(error)
        return 1
    current = status["orgId"]
    print(f"登录 email：{status.get('email', '?')}")
    print(f"账号名称：{status.get('orgName', '?')}")
    print(f"方案：{status.get('subscriptionType') or status.get('planTier') or '?'}")
    print(f"orgId：{current}")
    print("\n确认 email 是你自己的，再执行：")
    print(f"  python3 scripts/preflight.py --trust {current}")
    return 0


def trust(org_id: str) -> int:
    """只有这台电脑目前登录的就是 org_id 时才写入，登错账号无法被设成可信。"""
    status, error = read_current_account()
    if error:
        print(error)
        return 1
    if status["orgId"] != org_id:
        print("❌ 这台电脑目前登录的账号和你给的 orgId 不同，没有写入。")
        print("   用 --show-org 看这台电脑登录的是哪个 email。")
        return 1
    cfg, cfg_error = config_store.read_local_config()
    if cfg_error:
        print(f"❌ {cfg_error}\n   为了不盖掉你的设定，没有写入。请先修正或删掉这个档案。")
        return 1
    cfg["trusted_org_id"] = org_id
    cfg.setdefault("work_dirs", [])
    path = config_store.save_local_config(cfg)
    print(f"✅ 已写入 {pu.shell_path(path)}")
    print(f"   之后要加白名单：nano {pu.shell_path(path)}（或用 --config 看内容）")
    return 0


def show_config() -> int:
    """印出本机设定档的位置（可以直接复制）和内容（orgId 只显示开头）。"""
    path = config_store.config_path()
    print(f"设定档：{pu.shell_path(path)}")
    print(f"编辑：  nano {pu.shell_path(path)}\n")
    cfg, err = config_store.read_local_config()
    if err:
        print(f"❌ {err}")
        return 1
    if not path.is_file():
        print("（还没有设定档，先执行 --trust）")
        return 0
    shown = dict(cfg)
    if isinstance(shown.get("trusted_org_id"), str):
        shown["trusted_org_id"] = shown["trusted_org_id"][:8] + "…"
    print(json.dumps(shown, indent=2, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Remote Control preflight check")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--show-org", action="store_true", help="显示这台电脑目前登录的账号")
    ap.add_argument("--trust", metavar="ORG_ID", help="设定这台电脑唯一信任的账号 orgId")
    ap.add_argument("--config", action="store_true", help="显示本机设定档的位置和内容")
    args = ap.parse_args(argv)

    if args.config:
        return show_config()
    if args.show_org:
        return show_org()
    if args.trust:
        return trust(args.trust)

    checks = runner.run_checks()
    if args.json:
        print(json.dumps([asdict(c) for c in checks], ensure_ascii=False, indent=2))
    else:
        print_report(checks)
    return 1 if any(c.status == FAIL for c in checks) else 0
