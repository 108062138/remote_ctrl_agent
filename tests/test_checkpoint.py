"""M4 测试：git 还原点（不动档案、能还原、能反悔）+ 动作纪录。"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hooks"))

import audit  # noqa: E402
import gitsnap  # noqa: E402

GIT_ID = ["-c", "user.name=t", "-c", "user.email=t@t"]


def git(repo, *args):
    return subprocess.run(["git", *GIT_ID, "-C", str(repo), *args], capture_output=True, text=True,
                          check=True).stdout


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    (r / "a.txt").write_text("v1\n")
    (r / ".gitignore").write_text("ignored.log\n")
    git(r, "add", ".")
    git(r, "commit", "-q", "-m", "init")
    return r


def state(repo):
    """工作目录 + index + HEAD 的状态，用来确认「没被动到」。"""
    return (git(repo, "status", "--porcelain", "--untracked-files=all"), git(repo, "rev-parse", "HEAD"),
            git(repo, "diff", "--cached"), sorted(p.name for p in repo.iterdir()))


# --- 拍快照 ----------------------------------------------------------------------

def test_create_does_not_touch_anything(repo):
    (repo / "a.txt").write_text("changed\n")
    (repo / "new.txt").write_text("untracked\n")
    git(repo, "add", "a.txt")  # 部分暂存
    before = state(repo)
    cp = gitsnap.create(repo, "test")
    assert state(repo) == before
    files = set(git(repo, "ls-tree", "-r", "--name-only", cp.commit).split())
    assert files == {"a.txt", ".gitignore", "new.txt"}   # 含未追踪
    assert git(repo, "show", f"{cp.commit}:a.txt") == "changed\n"


def test_ignored_files_not_in_snapshot(repo):
    (repo / "ignored.log").write_text("x")
    cp = gitsnap.create(repo)
    assert "ignored.log" not in git(repo, "ls-tree", "-r", "--name-only", cp.commit)


def test_not_on_any_branch(repo):
    gitsnap.create(repo)
    assert git(repo, "branch", "--format=%(refname)").split() == ["refs/heads/main"]
    assert "remote-ctrl" not in git(repo, "log", "--oneline", "--branches")


def test_list_newest_first_and_find(repo):
    a = gitsnap.create(repo, "first")
    b = gitsnap.create(repo, "second")
    cps = gitsnap.list_checkpoints(repo)
    assert [c.id for c in cps] == [b.id, a.id]
    assert cps[0].label == "second"
    assert gitsnap.find(repo, b.id[:20]).commit == b.commit
    with pytest.raises(gitsnap.GitError):
        gitsnap.find(repo, "nope")


def test_prune_keeps_latest(repo):
    for i in range(5):
        gitsnap.create(repo, str(i))
    assert gitsnap.prune(repo, keep=2) == 3
    assert [c.label for c in gitsnap.list_checkpoints(repo)] == ["4", "3"]


def test_empty_repo_without_commits(tmp_path):
    r = tmp_path / "empty"
    r.mkdir()
    git(r, "init", "-q")
    (r / "x.txt").write_text("x")
    cp = gitsnap.create(r)
    assert "x.txt" in git(r, "ls-tree", "-r", "--name-only", cp.commit)


def test_not_a_repo(tmp_path):
    with pytest.raises(gitsnap.GitError):
        gitsnap.create(tmp_path)


def test_subdirectory_uses_repo_root(repo):
    (repo / "sub").mkdir()
    (repo / "sub" / "b.txt").write_text("b")
    cp = gitsnap.create(repo / "sub")
    assert "sub/b.txt" in git(repo, "ls-tree", "-r", "--name-only", cp.commit)


# --- 比较与还原 ------------------------------------------------------------------

def test_changes_and_restore(repo):
    (repo / "keep.txt").write_text("untracked but important\n")
    cp = gitsnap.create(repo, "before remote")
    # 远端做了：改档、删档、新增档
    (repo / "a.txt").write_text("broken\n")
    (repo / "keep.txt").unlink()
    (repo / "junk.txt").write_text("junk\n")
    (repo / "ignored.log").write_text("log")
    ch = gitsnap.changes_since(repo, cp)
    assert ch == {"added": ["junk.txt"], "deleted": ["keep.txt"], "modified": ["a.txt"]}

    before = gitsnap.restore(repo, cp)
    assert (repo / "a.txt").read_text() == "v1\n"
    assert (repo / "keep.txt").read_text() == "untracked but important\n"
    assert not (repo / "junk.txt").exists()
    assert (repo / "ignored.log").exists()            # .gitignore 的档案不动
    assert git(repo, "diff", "--cached") == ""        # index 回到 HEAD
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"

    # 反悔：还原到「还原前」的还原点
    gitsnap.restore(repo, gitsnap.find(repo, before.id))
    assert (repo / "a.txt").read_text() == "broken\n"
    assert (repo / "junk.txt").exists()


# --- hook ------------------------------------------------------------------------

def run_hook(script, payload, env_extra):
    env = {**os.environ, **env_extra}
    return subprocess.run([sys.executable, str(ROOT / "hooks" / script)], input=json.dumps(payload),
                          capture_output=True, text=True, env=env, timeout=60)


def test_checkpoint_hook(repo, tmp_path):
    logs = tmp_path / "logs"
    p = run_hook("checkpoint.py", {"prompt": "fix the bug", "cwd": str(repo), "session_id": "s1"},
                 {"REMOTE_CTRL_WORK_DIR": str(repo), "REMOTE_CTRL_LOG_DIR": str(logs)})
    assert p.returncode == 0 and p.stdout == ""       # 成功时不说话
    cps = gitsnap.list_checkpoints(repo)
    assert len(cps) == 1 and "fix the bug" in cps[0].label
    rec = audit.read(logs)[-1]
    assert rec["event"] == "prompt" and rec["checkpoint"] == cps[0].id


def test_checkpoint_hook_not_a_repo_does_not_block(tmp_path):
    logs = tmp_path / "logs"
    p = run_hook("checkpoint.py", {"prompt": "hi", "cwd": str(tmp_path)},
                 {"REMOTE_CTRL_WORK_DIR": str(tmp_path), "REMOTE_CTRL_LOG_DIR": str(logs)})
    assert p.returncode == 0                          # 不挡 prompt
    assert "没有建立 git 还原点" in p.stdout            # 但告诉 Claude
    assert audit.read(logs)[-1]["checkpoint"] is None


def test_guard_writes_audit_without_file_content(tmp_path):
    logs = tmp_path / "logs"
    secret_body = "TOP-SECRET-CONTENT"
    run_hook("remote_guard.py",
             {"tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "a.txt"), "content": secret_body},
              "cwd": str(tmp_path), "session_id": "s1"},
             {"REMOTE_CTRL_WORK_DIR": str(tmp_path), "REMOTE_CTRL_LOG_DIR": str(logs)})
    run_hook("remote_guard.py", {"tool_name": "Bash", "tool_input": {"command": "git push"}, "cwd": str(tmp_path)},
             {"REMOTE_CTRL_WORK_DIR": str(tmp_path), "REMOTE_CTRL_LOG_DIR": str(logs)})
    recs = audit.read(logs)
    assert [r["decision"] for r in recs] == ["ask", "deny"]
    assert recs[0]["input"]["content_size"] == len(secret_body)
    assert secret_body not in (logs / audit.LOG_NAME).read_text()
    assert oct((logs / audit.LOG_NAME).stat().st_mode & 0o777) == "0o600"


# --- 远端不能删还原点、不能改纪录 -----------------------------------------------------

def test_remote_cannot_touch_checkpoints_or_logs(repo, tmp_path):
    from hooks import policy
    logs = tmp_path / "logs"
    ctx = policy.make_context(repo, home=tmp_path / "home", tool_root=tmp_path / "tool",
                              config_dir=tmp_path / "cfg", log_dir=logs)
    for cmd in ("git update-ref -d refs/remote-ctrl/checkpoints/x", "git reflog expire --all",
                f"rm {logs}/audit.jsonl", "rm .git/refs/remote-ctrl/checkpoints/x",
                "git tag -d remote-ctrl", "GIT_INDEX_FILE=/tmp/i git add ."):
        assert policy.decide("Bash", {"command": cmd}, ctx).level == policy.DENY, cmd
    assert policy.decide("Bash", {"command": "git for-each-ref refs/remote-ctrl"}, ctx).level == policy.ALLOW


# --- restore.py 指令 ---------------------------------------------------------------

def run_restore(*args, cwd):
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "restore.py"), *args], cwd=cwd,
                          capture_output=True, text=True, timeout=60)


def test_restore_cli(repo):
    cp = gitsnap.create(repo, "prompt: refactor")
    (repo / "a.txt").write_text("broken\n")
    assert "refactor" in run_restore("list", cwd=repo).stdout
    out = run_restore("diff", cp.id[:15], cwd=repo).stdout
    assert "修改  a.txt" in out
    p = run_restore("restore", cp.id[:15], "--yes", cwd=repo)
    assert p.returncode == 0 and "已还原" in p.stdout
    assert (repo / "a.txt").read_text() == "v1\n"


# --- M4 code review 修正（每一条都有对应测试）--------------------------------------------

def test_review1_timeout_cannot_be_swallowed(tmp_path):
    """超时发生在 except OSError 里面也一样会拒绝（TimeoutError 是 OSError 的子类别）。"""
    import time
    script = (
        "import sys, time; sys.path.insert(0, %r)\n"
        "import remote_guard as g\n"
        "g.TIME_LIMIT = 1\n"
        "def slow(payload, env):\n"
        "    end = time.time() + 8\n"
        "    while time.time() < end:\n"
        "        try:\n"
        "            time.sleep(0.1)\n"
        "        except OSError:\n"
        "            pass\n"
        "g.handle = slow\n"
        "sys.exit(g.main())\n" % str(ROOT / "hooks")
    )
    start = time.monotonic()
    p = subprocess.run([sys.executable, "-c", script], input="{}", capture_output=True, text=True, timeout=30)
    assert p.returncode == 2 and "超过" in p.stderr
    assert time.monotonic() - start < 5


def test_review2_deadline(repo, tmp_path):
    import time
    with pytest.raises(gitsnap.GitError):
        gitsnap.create(repo, deadline=time.monotonic() - 1)
    logs = tmp_path / "logs"
    p = run_hook("checkpoint.py", {"prompt": "x", "cwd": str(repo)},
                 {"REMOTE_CTRL_WORK_DIR": str(repo), "REMOTE_CTRL_LOG_DIR": str(logs),
                  "REMOTE_CTRL_CHECKPOINT_BUDGET": "0"})
    assert p.returncode == 0 and "没有建立 git 还原点" in p.stdout
    assert "时间" in audit.read(logs)[-1]["error"]
    template = json.loads((ROOT / "deploy" / "remote-settings.template.json").read_text())
    import checkpoint
    assert template["hooks"]["UserPromptSubmit"][0]["hooks"][0]["timeout"] > checkpoint.BUDGET + 30


def test_review3_ref_names_are_utc(repo):
    import re
    from datetime import datetime, timezone
    cp = gitsnap.create(repo)
    assert re.fullmatch(r"\d{8}T\d{6}\.\d{6}Z", cp.id)
    assert cp.id.startswith(datetime.now(timezone.utc).strftime("%Y%m%d"))


def test_review4_restore_target_never_pruned(repo, monkeypatch):
    monkeypatch.setattr(gitsnap, "KEEP", 3)
    oldest = gitsnap.create(repo, "oldest")
    gitsnap.create(repo, "b")
    gitsnap.create(repo, "c")
    (repo / "a.txt").write_text("changed\n")
    gitsnap.restore(repo, oldest)
    assert oldest.id in [c.id for c in gitsnap.list_checkpoints(repo)]


def test_review5_ignored_file_overwrite_can_be_undone(repo):
    (repo / "config.local").write_text("old\n")
    git(repo, "add", "config.local")
    git(repo, "commit", "-q", "-m", "add config")
    cp = gitsnap.create(repo)
    # 之后：config.local 改成被 .gitignore 排除，并在本机修改
    git(repo, "rm", "-q", "--cached", "config.local")
    (repo / ".gitignore").write_text("ignored.log\nconfig.local\n")
    git(repo, "commit", "-q", "-am", "ignore config")
    (repo / "config.local").write_text("my local edits\n")
    before = gitsnap.restore(repo, cp)
    assert (repo / "config.local").read_text() == "old\n"
    gitsnap.restore(repo, gitsnap.find(repo, before.id))
    assert (repo / "config.local").read_text() == "my local edits\n"


def test_review6_non_utf8_content_does_not_crash(repo):
    (repo / "latin.txt").write_bytes(b"caf\xe9\n")
    cp = gitsnap.create(repo)
    (repo / "latin.txt").write_bytes(b"na\xefve \xff\n")
    p = run_restore("diff", cp.id, "--full", cwd=repo)
    assert p.returncode == 0 and "latin.txt" in p.stdout


def test_review7_audit_never_raises(tmp_path):
    audit.write(tmp_path / "logs", {"prompt": "\ud800 lone surrogate", "obj": object()})
    assert len(audit.read(tmp_path / "logs")) == 1
    # 安全层本身判断得了（WebSearch → ask），但纪录里有无法编码的字元：不能因此变成拒绝
    p = run_hook("remote_guard.py", {"tool_name": "WebSearch", "tool_input": {"query": "\ud800 x"},
                                     "cwd": str(tmp_path)},
                 {"REMOTE_CTRL_WORK_DIR": str(tmp_path), "REMOTE_CTRL_LOG_DIR": str(tmp_path / "logs")})
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert len(audit.read(tmp_path / "logs")) == 2


def test_review9_log_command_uses_same_dir(repo, tmp_path):
    logs = tmp_path / "custom-logs"
    run_hook("checkpoint.py", {"prompt": "hello from phone", "cwd": str(repo)},
             {"REMOTE_CTRL_WORK_DIR": str(repo), "REMOTE_CTRL_LOG_DIR": str(logs)})
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "restore.py"), "log"], cwd=repo,
                       capture_output=True, text=True, timeout=60,
                       env={**os.environ, "REMOTE_CTRL_LOG_DIR": str(logs)})
    assert "hello from phone" in p.stdout


def test_review10_restore_scans_worktree_once(repo, monkeypatch):
    cp = gitsnap.create(repo)
    (repo / "a.txt").write_text("changed\n")
    calls = []
    real = gitsnap.snapshot_tree
    monkeypatch.setattr(gitsnap, "snapshot_tree", lambda *a, **k: calls.append(1) or real(*a, **k))
    tree = gitsnap.snapshot_tree(repo)
    gitsnap.changes_since(repo, cp, tree)
    gitsnap.restore(repo, cp, tree)
    assert len(calls) == 1
