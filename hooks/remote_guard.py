#!/usr/bin/env python3
"""PreToolUse hook：Claude 每次要用工具前，Claude Code 会先执行这个程式。

流程：
  stdin 收到 JSON（工具名称、参数、目前目录）
    -> policy.decide() 判断
    -> Deny：错误讯息写到 stderr，exit 2（Claude Code 会挡下，并把原因告诉 Claude）
       Ask ：stdout 输出 permissionDecision = "ask"（手机上跳出允许 / 拒绝）
       Allow：stdout 输出 permissionDecision = "allow"（直接放行）

只在远端专用设定（remote-settings.json）里注册，所以平常在电脑前用 claude 不受影响。
程式本身出错时一律拒绝（fail closed）。

工作目录的决定顺序：环境变量 REMOTE_CTRL_WORK_DIR > CLAUDE_PROJECT_DIR > hook 收到的 cwd。
安全等级：环境变量 REMOTE_CTRL_LEVEL（readonly / normal，见 policy/levels.py）。
"""

from __future__ import annotations

import json
import os
import signal
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

EXIT_BLOCK = 2
TIME_LIMIT = 10  # 秒；settings 里 Claude Code 的上限是 30 秒


def build_output(decision) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision.name,
            "permissionDecisionReason": f"[remote-guard] {decision.reason}",
        }
    }


def handle(payload: dict, env: dict[str, str]) -> tuple[int, str, str]:
    """回传 (exit code, stdout, stderr)。拆出来方便测试。"""
    import audit
    import platform_utils as pu
    import policy

    cwd = payload.get("cwd") or os.getcwd()
    work_dir = env.get("REMOTE_CTRL_WORK_DIR") or env.get("CLAUDE_PROJECT_DIR") or cwd
    log_dir = pu.log_dir(env)
    ctx = policy.make_context(work_dir, cwd=cwd, config_dir=pu.config_dir(), log_dir=log_dir)
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    level = policy.parse_level(env.get("REMOTE_CTRL_LEVEL"))
    decision = policy.apply_level(level, tool_name, tool_input, policy.decide(tool_name, tool_input, ctx))
    audit.write(log_dir, {"event": "tool", "session": payload.get("session_id"), "tool": tool_name,
                          "input": audit.summarize_input(tool_name, tool_input),
                          "decision": decision.name, "reason": decision.reason, "cwd": cwd, "level": level})
    if decision.level == policy.DENY:
        return EXIT_BLOCK, "", f"⛔ [remote-guard] 已拒绝：{decision.reason}\n这是远端模式的安全规则，请换个做法，或等使用者回到电脑前处理。"
    return 0, json.dumps(build_output(decision), ensure_ascii=False), ""


def _on_timeout(signum, frame):
    # 不能用 raise：TimeoutError 是 OSError 的子类别，会被读档的 except OSError 吞掉。
    # 直接结束程式，确保一定是「拒绝」。
    os.write(2, f"⛔ [remote-guard] 判断超过 {TIME_LIMIT} 秒，为了安全先拒绝。\n".encode())
    os._exit(EXIT_BLOCK)


def main() -> int:
    # Claude Code 等太久会略过 hook（等于没有安全层），所以自己先设上限：超时就拒绝
    if hasattr(signal, "SIGALRM"):
        signal.signal(signal.SIGALRM, _on_timeout)
        signal.alarm(TIME_LIMIT)
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            raise ValueError("hook 输入不是 JSON 物件")
        code, out, err = handle(payload, dict(os.environ))
    except Exception as e:  # fail closed
        code, out, err = EXIT_BLOCK, "", f"⛔ [remote-guard] 内部错误，为了安全先拒绝：{type(e).__name__}: {e}"
    if out:
        print(out)
    if err:
        print(err, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
