"""remote_guard.py 测试：用真的子程序跑 hook，确认 Claude Code 看到的输出格式。"""

import json
import os
import subprocess
import sys
from pathlib import Path

GUARD = Path(__file__).resolve().parent.parent / "hooks" / "remote_guard.py"


def run_guard(payload, work_dir, extra_env=None):
    env = {**os.environ, "REMOTE_CTRL_WORK_DIR": str(work_dir), **(extra_env or {})}
    data = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run([sys.executable, str(GUARD)], input=data, capture_output=True,
                          text=True, env=env, timeout=30)


def payload(tool, tool_input, cwd):
    return {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input, "cwd": str(cwd)}


def test_deny_exits_2_with_reason(tmp_path):
    p = run_guard(payload("Bash", {"command": "git push"}, tmp_path), tmp_path)
    assert p.returncode == 2
    assert "git push" in p.stderr and "remote-guard" in p.stderr


def test_ask_outputs_permission_decision(tmp_path):
    p = run_guard(payload("Bash", {"command": "touch x.txt"}, tmp_path), tmp_path)
    assert p.returncode == 0
    out = json.loads(p.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse" and out["permissionDecision"] == "ask"


def test_allow_outputs_permission_decision(tmp_path):
    p = run_guard(payload("Bash", {"command": "ls"}, tmp_path), tmp_path)
    assert p.returncode == 0
    assert json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_bad_input_fails_closed(tmp_path):
    for bad in ("not json", "[1, 2]"):
        p = run_guard(bad, tmp_path)
        assert p.returncode == 2 and "内部错误" in p.stderr


def test_work_dir_falls_back_to_project_dir(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "REMOTE_CTRL_WORK_DIR"}
    env["CLAUDE_PROJECT_DIR"] = str(tmp_path)
    p = subprocess.run([sys.executable, str(GUARD)],
                       input=json.dumps(payload("Write", {"file_path": str(tmp_path / "a.txt")}, tmp_path)),
                       capture_output=True, text=True, env=env, timeout=30)
    assert json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"] == "ask"
