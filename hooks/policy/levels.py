"""安全等级：在一般规则（Allow / Ask / Deny）之上，再收紧一层。

  normal    一般规则不变
  readonly  只放行「确定只读」的操作，其他一律拒绝（手机上不会跳出询问）
            - 原本要询问的（改档、执行程式、上网）→ 拒绝
            - Bash 只放行「单纯的单一指令」：不能有 ; & | $ ` ( ) { } [ ] < > * ? ! \\ 和换行，
              也不能设定变数。因为复杂的 Bash 写法可能绕过规则（见 error.md M3 第 2 次 review）

等级由启动脚本放进环境变量 REMOTE_CTRL_LEVEL，远端 session 改不了。
没设定时是 normal（手动启动时的旧行为）；值看不懂时当成 readonly（往严格的方向）。
"""

from __future__ import annotations

import re

import platform_utils as pu

from .core import ASK, DENY, Decision

NORMAL, READONLY = "normal", "readonly"
LEVELS = pu.LEVELS
SHELL_SPECIAL = re.compile(r"[;&|$`(){}\[\]<>*?!\\\n\r]")
ASSIGNMENT = re.compile(r"^\s*[A-Za-z_][A-Za-z0-9_]*\+?=")


def parse_level(value: str | None) -> str:
    if not value:
        return NORMAL
    value = value.strip().lower()
    return value if value in LEVELS else READONLY


def is_simple_bash(command: str) -> bool:
    return bool(command.strip()) and not SHELL_SPECIAL.search(command) and not ASSIGNMENT.match(command)


def apply_level(level: str, tool_name: str, tool_input: dict, decision: Decision) -> Decision:
    if level != READONLY or decision.level == DENY:
        return decision
    if decision.level == ASK:
        return Decision(DENY, f"唯读模式（readonly）不允许：{decision.reason}")
    if tool_name == "Bash":
        command = tool_input.get("command") if isinstance(tool_input, dict) else None
        if not isinstance(command, str) or not is_simple_bash(command):
            return Decision(DENY, "唯读模式只允许单纯的单一指令（不能有 ; | & $ * 等符号），请拆开或改用 Read / Grep")
    return decision
