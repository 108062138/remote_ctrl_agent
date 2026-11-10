#!/usr/bin/env python3
"""回到电脑前用：看远端做了什么、必要时还原。

  python3 scripts/restore.py list   [--dir 工作目录]          列出还原点（新的在前）
  python3 scripts/restore.py diff   <id> [--dir ...] [--full]  这个还原点之后改了哪些档案
  python3 scripts/restore.py restore <id> [--dir ...] [--yes]  还原（会先把现在存成新还原点，可以反悔）
  python3 scripts/restore.py log    [--tail 30]               看动作纪录（audit log）

<id> 打开头几个字就好（例如 20261110-1830）。--dir 预设是目前所在的资料夹。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hooks"))

import audit  # noqa: E402
import gitsnap  # noqa: E402
import platform_utils as pu  # noqa: E402

ICON = {"allow": "✅", "ask": "❓", "deny": "⛔"}


def cmd_list(args) -> int:
    cps = gitsnap.list_checkpoints(args.dir)
    if not cps:
        print("还没有还原点。远端 session 每收到一个 prompt 会自动建立一个。")
        return 0
    print(f"{len(cps)} 个还原点（新的在前）：\n")
    for c in cps:
        label = c.label.replace("\n", " ")
        print(f"  {c.id}  {c.created}  {label[:60]}")
    print("\n看改了什么：python3 scripts/restore.py diff <id>")
    return 0


def cmd_diff(args) -> int:
    cp = gitsnap.find(args.dir, args.id)
    tree = gitsnap.snapshot_tree(args.dir)   # 只扫描一次工作目录，下面共用
    ch = gitsnap.changes_since(args.dir, cp, tree)
    print(f"还原点 {cp.id}（{cp.created}）之后的变化：")
    labels = {"added": "新增", "modified": "修改", "deleted": "删除"}
    if not any(ch.values()):
        print("  没有变化")
    for key, label in labels.items():
        for name in ch[key]:
            print(f"  {label}  {name}")
    if args.full:
        print()
        print(gitsnap.diff_text(args.dir, cp, tree))
    return 0


def cmd_restore(args) -> int:
    cp = gitsnap.find(args.dir, args.id)
    tree = gitsnap.snapshot_tree(args.dir)   # 只扫描一次工作目录，下面共用
    ch = gitsnap.changes_since(args.dir, cp, tree)
    total = sum(len(v) for v in ch.values())
    print(f"要把 {gitsnap.repo_root(args.dir)} 还原到 {cp.id}（{cp.created}）")
    print(f"会影响 {total} 个档案："
          f"还原 {len(ch['modified']) + len(ch['deleted'])} 个、删除新增的 {len(ch['added'])} 个。")
    if total == 0:
        print("没有变化，不用还原。")
        return 0
    if not args.yes:
        answer = input("确定吗？现在的状态会先存成新还原点。[y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("取消。")
            return 1
    before = gitsnap.restore(args.dir, cp, tree)
    print(f"✅ 已还原。想反悔：python3 scripts/restore.py restore {before.id}")
    return 0


def cmd_log(args) -> int:
    records = audit.read(pu.log_dir(), args.tail)
    if not records:
        print(f"还没有纪录（{pu.log_dir() / audit.LOG_NAME}）")
        return 0
    for r in records:
        t = r.get("time", "?").replace("T", " ")
        if r.get("event") == "prompt":
            cp = r.get("checkpoint") or f"没有还原点：{r.get('error', '')}"
            print(f"{t}  💬 prompt：{str(r.get('prompt', ''))[:60]}  [{cp}]")
        elif r.get("event") == "tool":
            inp = r.get("input") or {}
            what = inp.get("command") or inp.get("file_path") or inp.get("path") or inp.get("pattern") or ""
            print(f"{t}  {ICON.get(r.get('decision'), '?')} {r.get('tool')}  {str(what)[:70]}")
            if r.get("decision") == "deny":
                print(f"{' ' * 22}↳ {r.get('reason', '')}")
        else:
            print(f"{t}  {r}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="看远端做了什么、必要时还原")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("list", "diff", "restore"):
        p = sub.add_parser(name)
        if name != "list":
            p.add_argument("id")
        p.add_argument("--dir", type=Path, default=Path.cwd())
        if name == "diff":
            p.add_argument("--full", action="store_true", help="印出完整的 diff")
        if name == "restore":
            p.add_argument("--yes", action="store_true", help="不问直接还原")
    p = sub.add_parser("log")
    p.add_argument("--tail", type=int, default=30)
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):   # 怪异编码的档名 / 内容：显示成替代字元，不当掉
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    try:
        return {"list": cmd_list, "diff": cmd_diff, "restore": cmd_restore, "log": cmd_log}[args.cmd](args)
    except (gitsnap.GitError, OSError) as e:
        print(f"❌ {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
