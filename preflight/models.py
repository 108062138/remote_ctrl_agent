"""检查结果的资料格式。每一项检查都回传一个 Check。"""

from __future__ import annotations

from dataclasses import dataclass

OK, WARN, FAIL = "ok", "warn", "fail"   # 通过 / 警告（可以启动）/ 失败（不能启动）
ICON = {OK: "✅", WARN: "⚠️ ", FAIL: "❌"}


@dataclass
class Check:
    name: str        # 检查项目，例如「tmux」
    status: str      # OK / WARN / FAIL
    detail: str      # 检查到的结果
    fix: str = ""    # 没通过时怎么修
