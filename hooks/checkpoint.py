#!/usr/bin/env python3
"""UserPromptSubmit hook：手机每送出一个 prompt，Claude 开始动作之前，先拍一个 git 还原点。

- 工作目录：REMOTE_CTRL_WORK_DIR > CLAUDE_PROJECT_DIR > hook 收到的 cwd
- 不是 git repo、或拍快照失败：不挡 prompt（不然人在外面会完全没办法用），
  但会告诉 Claude「这次没有还原点」，并写进 audit log
- 成功：不输出任何东西（不占 Claude 的上下文），只写 audit log
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))


BUDGET = 45  # 秒：拍还原点的总时间上限；settings 里 hook 的上限是 120 秒，留时间给提醒和纪录


def handle(payload: dict, env: dict[str, str]) -> str:
    """回传要给 Claude 看的文字（空字串 = 不说话）。拆出来方便测试。"""
    import time

    import audit
    import gitsnap
    import platform_utils as pu

    cwd = payload.get("cwd") or os.getcwd()
    work_dir = Path(env.get("REMOTE_CTRL_WORK_DIR") or env.get("CLAUDE_PROJECT_DIR") or cwd)
    prompt = payload.get("prompt") if isinstance(payload.get("prompt"), str) else ""
    event = {"event": "prompt", "session": payload.get("session_id"), "work_dir": str(work_dir),
             "prompt": audit._short(prompt)}
    try:
        budget = float(env.get("REMOTE_CTRL_CHECKPOINT_BUDGET") or BUDGET)
        cp = gitsnap.create(work_dir, f"prompt: {prompt}", deadline=time.monotonic() + budget)
        event.update(checkpoint=cp.id, commit=cp.commit[:12])
        message = ""
    except Exception as e:
        event.update(checkpoint=None, error=f"{type(e).__name__}: {e}")
        message = (f"[remote-ctrl] 注意：这次没有建立 git 还原点（{e}）。"
                   "修改档案前请先提醒使用者，因为出错时无法用 restore 还原。")
    audit.write(pu.log_dir(env), event)
    return message


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            payload = {}
        message = handle(payload, dict(os.environ))
    except Exception as e:  # 这个 hook 不挡 prompt
        message = f"[remote-ctrl] 注意：还原点 hook 出错（{type(e).__name__}），这次没有还原点。"
    if message:
        print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
