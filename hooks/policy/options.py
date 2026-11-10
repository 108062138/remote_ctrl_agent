"""解析指令选项。

支持的写法：-la（合并）、-n 5、-n5、--max-depth 3、--max-depth=3、-5（数字）、--（之后都是参数）。
不认得的选项丢 Unclear，由呼叫的人决定 Ask 还是 Deny。
"""

from __future__ import annotations

from dataclasses import dataclass

from .core import Unclear


@dataclass(frozen=True)
class OptSpec:
    letters: str = ""                     # 不带值的单字母选项
    short_val: str = ""                   # 带值的单字母选项（-n 5 或 -n5）
    longs: frozenset = frozenset()        # 不带值的长选项
    long_val: frozenset = frozenset()     # 带值的长选项（--x v 或 --x=v）
    long_optval: frozenset = frozenset()  # 值可有可无，只认 --x=v（例如 --color）
    numeric: bool = False                 # 接受 -5 这种
    any_flags: bool = False               # 任何选项都接受（ps、uname）


def spec(letters="", short_val="", longs=(), long_val=(), long_optval=(), numeric=False, any_flags=False):
    return OptSpec(letters, short_val, frozenset(longs), frozenset(long_val), frozenset(long_optval),
                   numeric, any_flags)


def parse_one(args: list[str], i: int, s: OptSpec) -> tuple[list[tuple[str, str | None]], int]:
    """解析 args[i] 这一个选项，回传 ([(名称, 值)], 下一个位置)。"""
    a = args[i]
    if s.any_flags:
        return [(a, None)], i + 1
    if a.startswith("--"):
        name, eq, val = a.partition("=")
        if name in s.long_val:
            if eq:
                return [(name, val)], i + 1
            if i + 1 >= len(args):
                raise Unclear(f"{name} 缺少值")
            return [(name, args[i + 1])], i + 2
        if name in s.long_optval:
            return [(name, val if eq else None)], i + 1
        if name in s.longs and not eq:
            return [(name, None)], i + 1
        raise Unclear(f"不认得的选项 {a}")
    if s.numeric and a[1:].isdigit():
        return [(a, None)], i + 1
    out = []
    j = 1
    while j < len(a):
        ch = a[j]
        if ch in s.short_val:
            val = a[j + 1:]
            if val:
                out.append(("-" + ch, val))
                return out, i + 1
            if i + 1 >= len(args):
                raise Unclear(f"-{ch} 缺少值")
            out.append(("-" + ch, args[i + 1]))
            return out, i + 2
        if ch in s.letters:
            out.append(("-" + ch, None))
            j += 1
            continue
        raise Unclear(f"不认得的选项 {a}")
    return out, i + 1


def is_option(a: str) -> bool:
    return a.startswith("-") and a != "-"


def scan(args: list[str], s: OptSpec) -> tuple[list[tuple[str, str | None]], list[str]]:
    """整串都解析：选项可以出现在参数后面（GNU 风格）。回传 (选项, 位置参数)。"""
    opts, pos = [], []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            pos.extend(args[i + 1:])
            break
        if is_option(a):
            got, i = parse_one(args, i, s)
            opts.extend(got)
        else:
            pos.append(a)
            i += 1
    return opts, pos


def leading(args: list[str], s: OptSpec) -> tuple[list[tuple[str, str | None]], list[str]]:
    """只解析开头的选项，遇到第一个参数就停（包装指令用：后面是真正要执行的指令）。"""
    opts = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            return opts, args[i + 1:]
        if not is_option(a):
            break
        got, i = parse_one(args, i, s)
        opts.extend(got)
    return opts, args[i:]
