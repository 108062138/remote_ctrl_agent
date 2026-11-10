"""总入口 decide()：依工具种类分派。Bash 交给 bash.py，其他工具在这里判断。"""

from __future__ import annotations

import os

from .bash import analyze_bash
from .core import ALLOW, ASK, DENY, Context, Decision, worst
from .paths import glob_may_name_secret, path_rule, recursive_read_rule

READ_TOOLS = {"Read": "file_path", "NotebookRead": "notebook_path", "LS": "path"}
WRITE_TOOLS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path",
               "NotebookEdit": "notebook_path"}
HARMLESS_TOOLS = {"TodoWrite", "TodoRead", "ExitPlanMode", "AskUserQuestion", "ToolSearch"}


def decide(tool_name: str, tool_input: dict, ctx: Context) -> Decision:
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    if tool_name == "Bash":
        command = tool_input.get("command")
        if not isinstance(command, str):
            return Decision(DENY, "Bash 没有指令内容")
        return analyze_bash(command, ctx)
    if tool_name in READ_TOOLS:
        path = tool_input.get(READ_TOOLS[tool_name])
        return path_rule(path if isinstance(path, str) and path else ".", ctx, "read")
    if tool_name == "Glob":
        return _judge_glob(tool_input, ctx)
    if tool_name == "Grep":
        return _judge_grep(tool_input, ctx)
    if tool_name in WRITE_TOOLS:
        path = tool_input.get(WRITE_TOOLS[tool_name])
        if not isinstance(path, str) or not path:
            return Decision(DENY, "看不到要写入的档案路径")
        return path_rule(path, ctx, "write")
    if tool_name in HARMLESS_TOOLS:
        return Decision(ALLOW, "不会动到档案的内部工具")
    if tool_name in ("WebFetch", "WebSearch"):
        return Decision(ASK, "连到外部网站")
    return Decision(ASK, f"未分类的工具 {tool_name}，请确认")


def _str(v) -> str:
    return v if isinstance(v, str) else ""


def _judge_glob(tool_input: dict, ctx: Context) -> Decision:
    """Glob 只列出档名，不读内容；但样式本身不能指向机密位置。"""
    path, pattern = _str(tool_input.get("path")), _str(tool_input.get("pattern"))
    words = [path or "."]
    if pattern:
        words.append(os.path.join(path, pattern) if path and not os.path.isabs(pattern) else pattern)
    return worst(path_rule(w, ctx, "read") for w in words)


def _judge_grep(tool_input: dict, ctx: Context) -> Decision:
    """Grep 工具（ripgrep）会读档案内容，预设略过隐藏档；指定 glob 时会盖过这个规则。"""
    path, glob_pat = _str(tool_input.get("path")) or ".", _str(tool_input.get("glob"))
    decisions = [path_rule(path, ctx, "read")]
    if glob_pat and glob_may_name_secret(glob_pat):
        decisions.append(Decision(DENY, f"Grep 的档名样式会对到机密档案：{glob_pat}"))
    positive = glob_pat if glob_pat and not glob_pat.startswith("!") else None
    decisions.append(recursive_read_rule(path, ctx, include_hidden=bool(positive), only=positive))
    return worst(decisions)
