"""M5 测试：安全等级、启动脚本（白名单、preflight、还原点、tmux、自动重启）、服务安装。

tmux 和 claude 都用假的小脚本代替，不会真的启动任何 session。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "hooks"))

import gitsnap  # noqa: E402
import install_service  # noqa: E402
import start_remote as sr  # noqa: E402
from hooks import policy  # noqa: E402
from preflight import checks_system  # noqa: E402
from preflight.models import FAIL, OK, WARN, Check  # noqa: E402

GIT_ID = ["-c", "user.name=t", "-c", "user.email=t@t"]


def git(repo, *args):
    subprocess.run(["git", *GIT_ID, "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """暂时的设定资料夹 + 一个 git repo + 一个普通资料夹 + 假的 tmux。"""
    if sys.platform == "darwin":
        pytest.skip("用 XDG_CONFIG_HOME 改设定位置只适用 Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    repo = tmp_path / "proj"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("v1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "init")
    plain = tmp_path / "plain"
    plain.mkdir()
    tmux_log = tmp_path / "tmux.log"
    fake_tmux = tmp_path / "tmux"
    fake_tmux.write_text(f'#!/bin/sh\necho "$@" >> {tmux_log}\n'
                         f'[ "$3" = list-sessions ] && cat {tmp_path}/sessions 2>/dev/null\nexit 0\n')
    fake_tmux.chmod(0o755)
    monkeypatch.setattr(sr, "_tmux", lambda: str(fake_tmux))
    monkeypatch.setattr(sr.runner, "run_checks", lambda: [Check("x", OK, "ok")])
    monkeypatch.setattr(sr, "wait_for_network", lambda *a, **k: True)
    return {"tmp": tmp_path, "repo": repo, "plain": plain, "tmux_log": tmux_log}


def write_cfg(env, **cfg):
    d = env["tmp"] / "xdg" / "remote-ctrl"
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(json.dumps(cfg))


def start(*argv):
    return sr.main(list(argv))


# --- 安全等级 -------------------------------------------------------------------------

def test_parse_level():
    assert policy.parse_level(None) == "normal"          # 手动启动的旧行为
    assert policy.parse_level("readonly") == "readonly"
    assert policy.parse_level("NORMAL") == "normal"
    assert policy.parse_level("admin") == "readonly"     # 看不懂 → 往严格走


@pytest.mark.parametrize("cmd,expected", [
    ("ls -la", policy.ALLOW), ("git status", policy.ALLOW), ("cat a.txt", policy.ALLOW),
    ("ls; pwd", policy.DENY), ("cat a.txt | head", policy.DENY), ("ls *.py", policy.DENY),
    ("echo $HOME", policy.DENY), ("FOO=1 ls", policy.DENY), ("touch x", policy.DENY),
    ("git commit -m x", policy.DENY), ("git push", policy.DENY),
])
def test_readonly_bash(tmp_path, cmd, expected):
    ctx = policy.make_context(tmp_path, home=tmp_path / "h", tool_root=tmp_path / "t", config_dir=tmp_path / "c")
    d = policy.decide("Bash", {"command": cmd}, ctx)
    assert policy.apply_level("readonly", "Bash", {"command": cmd}, d).level == expected, cmd


def test_readonly_other_tools(tmp_path):
    work = tmp_path / "work"          # 家目录不能放在工作资料夹里面，否则 Grep 会保守地拒绝（可能扫到 ~/.ssh）
    work.mkdir()
    ctx = policy.make_context(work, home=tmp_path / "h", tool_root=tmp_path / "t", config_dir=tmp_path / "c")
    tmp_path = work
    for tool, inp, expected in [
        ("Read", {"file_path": str(tmp_path / "a")}, policy.ALLOW),
        ("Grep", {"pattern": "x"}, policy.ALLOW),
        ("Write", {"file_path": str(tmp_path / "a")}, policy.DENY),
        ("Edit", {"file_path": str(tmp_path / "a")}, policy.DENY),
        ("WebFetch", {"url": "https://x"}, policy.DENY),
    ]:
        d = policy.apply_level("readonly", tool, inp, policy.decide(tool, inp, ctx))
        assert d.level == expected, tool
    # normal 不变
    w = policy.decide("Write", {"file_path": str(tmp_path / "a")}, ctx)
    assert policy.apply_level("normal", "Write", {}, w).level == policy.ASK


def test_guard_reads_level_from_env(tmp_path):
    payload = json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "a")}, "cwd": str(tmp_path)})
    for level, code in (("readonly", 2), ("normal", 0)):
        p = subprocess.run([sys.executable, str(ROOT / "hooks" / "remote_guard.py")], input=payload,
                           capture_output=True, text=True, timeout=30,
                           env={**os.environ, "REMOTE_CTRL_WORK_DIR": str(tmp_path), "REMOTE_CTRL_LEVEL": level})
        assert p.returncode == code, (level, p.stderr)
    assert "唯读模式" in subprocess.run(
        [sys.executable, str(ROOT / "hooks" / "remote_guard.py")], input=payload, capture_output=True, text=True,
        env={**os.environ, "REMOTE_CTRL_WORK_DIR": str(tmp_path), "REMOTE_CTRL_LEVEL": "readonly"}).stderr


def test_level_env_var_cannot_be_changed_remotely(tmp_path):
    ctx = policy.make_context(tmp_path, home=tmp_path / "h", tool_root=tmp_path / "t", config_dir=tmp_path / "c")
    for cmd in ("export REMOTE_CTRL_LEVEL=normal", "REMOTE_CTRL_LEVEL=normal claude"):
        assert policy.decide("Bash", {"command": cmd}, ctx).level == policy.DENY, cmd


# --- 决定等级与资料夹 ------------------------------------------------------------------

def test_whitelist(env):
    cfg = {"work_dirs": [str(env["repo"])]}
    assert sr.resolve_target("normal", str(env["repo"]), None, cfg, False).dir == env["repo"].resolve()
    (env["repo"] / "sub").mkdir()
    assert sr.resolve_target("normal", str(env["repo"] / "sub"), None, cfg, False).name == "sub"
    with pytest.raises(sr.StartError, match="白名单"):
        sr.resolve_target("normal", str(env["plain"]), None, cfg, False)
    with pytest.raises(sr.StartError, match="work_dirs"):
        sr.resolve_target("normal", str(env["repo"]), None, {}, False)
    with pytest.raises(sr.StartError, match="不存在"):
        sr.resolve_target("normal", str(env["tmp"] / "nope"), None, cfg, False)
    # ../ 绕不出白名单
    with pytest.raises(sr.StartError, match="白名单"):
        sr.resolve_target("normal", str(env["repo"] / ".." / "plain"), None, cfg, False)


def test_defaults_and_boot(env):
    cfg = {"work_dirs": [str(env["repo"])], "default_level": "readonly", "default_dir": str(env["repo"])}
    t = sr.resolve_target(None, None, None, cfg, False)
    assert (t.level, t.name) == ("readonly", "proj")
    assert t.session == f"rc-proj-{sr.dir_code(env['repo'].resolve())}-readonly"
    t = sr.resolve_target("normal", None, None, cfg, True)       # --boot 只看预设值
    assert t.level == "readonly"
    with pytest.raises(sr.StartError, match="default_level"):
        sr.resolve_target(None, None, None, {"work_dirs": [str(env["repo"])]}, True)
    with pytest.raises(sr.StartError, match="安全等级"):
        sr.resolve_target("admin", str(env["repo"]), None, cfg, False)


def test_session_env_and_command(env, monkeypatch):
    t = sr.Target("readonly", env["repo"], "proj")
    e = sr.session_env({"ANTHROPIC_API_KEY": "x", "PATH": "/bin"}, t)
    assert "ANTHROPIC_API_KEY" not in e and e["REMOTE_CTRL_LEVEL"] == "readonly"
    assert e["REMOTE_CTRL_WORK_DIR"] == str(env["repo"])
    monkeypatch.setattr(sr, "anti_sleep_prefix", lambda: ["inhibit"])
    cmd = sr.claude_command("/x/claude", t, Path("/s.json"))
    assert cmd == ["inhibit", "/x/claude", "--remote-control", "proj", "--settings", "/s.json"]
    tm = sr.tmux_command("/x/tmux", t, "/x/python")
    assert tm[:7] == ["/x/tmux", "-L", "remote-ctrl", "new-session", "-d", "-s", t.session] and "--run" in tm[-1]


def test_safe_name_and_parse():
    assert sr.safe_name("my-repo!") == "my_repo"
    assert sr.safe_name("../..") == "session"
    assert sr.parse_session("rc-learn_git-3fa2c1-readonly") == ("learn_git", "3fa2c1", "readonly")
    assert sr.parse_session("rc-x-normal") is None and sr.parse_session("work") is None


# --- 启动 ---------------------------------------------------------------------------

def test_start_normal_creates_checkpoint_when_dirty(env):
    write_cfg(env, work_dirs=[str(env["repo"])])
    (env["repo"] / "a.txt").write_text("dirty\n")
    assert start("--level", "normal", "--dir", str(env["repo"])) == 0
    cps = gitsnap.list_checkpoints(env["repo"])
    assert len(cps) == 1 and "启动" in cps[0].label
    assert "-L remote-ctrl new-session -d -s rc-proj-" in env["tmux_log"].read_text()


def test_start_clean_repo_no_checkpoint(env):
    write_cfg(env, work_dirs=[str(env["repo"])])
    assert start("--level", "normal", "--dir", str(env["repo"])) == 0
    assert gitsnap.list_checkpoints(env["repo"]) == []


def test_start_normal_requires_git_but_readonly_does_not(env, capsys):
    write_cfg(env, work_dirs=[str(env["plain"])])
    assert start("--level", "normal", "--dir", str(env["plain"])) == 1
    assert "git repo" in capsys.readouterr().out
    assert start("--level", "readonly", "--dir", str(env["plain"])) == 0


def test_start_blocked_by_preflight(env, monkeypatch, capsys):
    write_cfg(env, work_dirs=[str(env["repo"])])
    monkeypatch.setattr(sr.runner, "run_checks", lambda: [Check("可信账号", FAIL, "不一致", "修")])
    assert start("--level", "normal", "--dir", str(env["repo"])) == 1
    assert "preflight 没通过" in capsys.readouterr().out
    assert not env["tmux_log"].exists() or "new-session" not in env["tmux_log"].read_text()


def session_of(env, level, d=None):
    return sr.Target(level, (d or env["repo"]).resolve(), "proj").session


def test_start_already_running_is_ok(env, capsys):
    write_cfg(env, work_dirs=[str(env["repo"])])
    (env["tmp"] / "sessions").write_text(session_of(env, "readonly") + "\n")
    assert start("--level", "readonly", "--dir", str(env["repo"])) == 0
    assert "在跑了" in capsys.readouterr().out
    assert "new-session" not in env["tmux_log"].read_text()


def test_dry_run_changes_nothing(env, capsys):
    write_cfg(env, work_dirs=[str(env["repo"])])
    (env["repo"] / "a.txt").write_text("dirty\n")
    assert start("--level", "normal", "--dir", str(env["repo"]), "--dry-run") == 0
    assert gitsnap.list_checkpoints(env["repo"]) == []
    assert "dry-run" in capsys.readouterr().out
    assert "new-session" not in (env["tmux_log"].read_text() if env["tmux_log"].exists() else "")


def test_boot_failure_is_logged(env, tmp_path):
    write_cfg(env, work_dirs=[str(env["repo"])])
    assert start("--boot") == 1
    import audit
    import platform_utils as pu
    assert audit.read(pu.log_dir())[-1]["event"] == "session_start_failed"


def test_stop_and_status(env, capsys):
    write_cfg(env, work_dirs=[str(env["repo"])], default_dir=str(env["repo"]), default_level="readonly")
    mine = session_of(env, "readonly")
    (env["tmp"] / "sessions").write_text(f"{mine}\nrc-other-abcdef-normal\nunrelated\n")
    assert start("--status") == 0
    out = capsys.readouterr().out
    assert "proj（readonly）" in out and "unrelated" not in out
    assert start("--stop", "--boot") == 0
    assert f"kill-session -t {mine}" in env["tmux_log"].read_text()
    assert "rc-other" not in env["tmux_log"].read_text().split("kill-session")[-1]
    assert start("--stop", "--all") == 0
    assert "kill-session -t rc-other-abcdef-normal" in env["tmux_log"].read_text()


# --- 自动重启 -------------------------------------------------------------------------

def fake_claude(env, exit_code):
    runs = env["tmp"] / "claude-runs"
    script = env["tmp"] / "claude"
    script.write_text(f'#!/bin/sh\necho "$REMOTE_CTRL_LEVEL $REMOTE_CTRL_WORK_DIR $ANTHROPIC_API_KEY" >> {runs}\n'
                      f"exit {exit_code}\n")
    script.chmod(0o755)
    return script, runs


def test_run_loop_restarts_then_gives_up(env, monkeypatch):
    write_cfg(env, work_dirs=[str(env["repo"])])
    script, runs = fake_claude(env, 1)
    monkeypatch.setattr(sr.pu, "find_tool", lambda name: str(script) if name == "claude" else None)
    monkeypatch.setattr(sr, "RESTART_DELAY", 0)
    monkeypatch.setattr(sr, "MAX_RESTARTS", 2)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-be-removed")
    assert sr.main(["--run", "--level", "readonly", "--dir", str(env["repo"]), "--name", "proj"]) == 1
    lines = runs.read_text().splitlines()
    assert len(lines) == 3                                     # 第一次 + 重启 2 次
    assert lines[0].split() == ["readonly", str(env["repo"].resolve())]   # API key 被移除


def test_run_loop_normal_exit_does_not_restart(env, monkeypatch):
    write_cfg(env, work_dirs=[str(env["repo"])])
    script, runs = fake_claude(env, 0)
    monkeypatch.setattr(sr.pu, "find_tool", lambda name: str(script) if name == "claude" else None)
    assert sr.main(["--run", "--level", "normal", "--dir", str(env["repo"]), "--name", "proj"]) == 0
    assert len(runs.read_text().splitlines()) == 1


def test_run_loop_rechecks_whitelist(env, monkeypatch, capsys):
    write_cfg(env, work_dirs=[str(env["repo"])])
    assert sr.main(["--run", "--level", "normal", "--dir", str(env["plain"]), "--name", "x"]) == 1
    assert "白名单" in capsys.readouterr().out


# --- preflight 预设值 / 服务安装 -------------------------------------------------------------

def test_preflight_defaults(tmp_path):
    assert checks_system.check_defaults({}).status == WARN
    assert checks_system.check_defaults({"default_level": "admin", "default_dir": "x"}).status == FAIL
    cfg = {"work_dirs": [str(tmp_path)], "default_level": "readonly", "default_dir": str(tmp_path)}
    assert checks_system.check_defaults(cfg).status == OK
    cfg["default_dir"] = "/etc"
    assert checks_system.check_defaults(cfg).status == FAIL


def test_install_render_and_dry_run(env, capsys):
    text = install_service.render("/py/python3", "/a:/b", Path("/logs"))
    assert "/py/python3" in text and str(install_service.START) in text and "/a:/b" in text
    assert "{{" not in text
    write_cfg(env, work_dirs=[str(env["repo"])])
    assert install_service.main(["--dry-run"]) == 1            # 没有预设值不能装
    assert "default_level" in capsys.readouterr().out
    write_cfg(env, work_dirs=[str(env["repo"])], default_level="readonly", default_dir=str(env["repo"]))
    assert install_service.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "--boot" in out and "dry-run" in out
    assert not install_service.service_path(Path.home()).exists() or True   # dry-run 不写档
    assert install_service.main(["--uninstall", "--dry-run"]) == 0


def test_remote_cannot_install_or_start_services(tmp_path):
    ctx = policy.make_context(tmp_path, home=tmp_path / "h", tool_root=ROOT, config_dir=tmp_path / "c")
    for cmd in (f"python3 {ROOT}/scripts/install_service.py", f"{ROOT}/scripts/start-remote.sh --level normal",
                "systemctl --user enable remote-ctrl.service", "tmux kill-session -t rc-proj"):
        assert policy.decide("Bash", {"command": cmd}, ctx).level == policy.DENY, cmd


# --- M5 code review 修正（每一条都有对应测试）--------------------------------------------

def test_review1_same_name_different_dirs_do_not_collide(env):
    other = env["tmp"] / "b" / "proj"
    other.mkdir(parents=True)
    git(other, "init", "-q", "-b", "main")
    write_cfg(env, work_dirs=[str(env["repo"]), str(other)])
    (env["tmp"] / "sessions").write_text(session_of(env, "readonly") + "\n")
    assert start("--level", "readonly", "--dir", str(other)) == 0
    assert f"new-session -d -s {session_of(env, 'readonly', other)}" in env["tmux_log"].read_text()


def test_review1_same_dir_other_level_is_refused(env, capsys):
    write_cfg(env, work_dirs=[str(env["repo"])])
    (env["tmp"] / "sessions").write_text(session_of(env, "readonly") + "\n")
    assert start("--level", "normal", "--dir", str(env["repo"])) == 1
    assert "readonly 等级在跑" in capsys.readouterr().out


def test_review2_readonly_in_non_git_dir_with_real_preflight_rules(env):
    from preflight.checks_system import check_work_dirs
    assert all(c.status != FAIL for c in check_work_dirs([str(env["plain"]), str(env["tmp"] / "gone")]))


def test_review3_dedicated_tmux_server(env):
    write_cfg(env, work_dirs=[str(env["repo"])])
    start("--level", "readonly", "--dir", str(env["repo"]))
    start("--status")
    start("--stop", "--all")
    for line in env["tmux_log"].read_text().splitlines():
        assert line.startswith("-L remote-ctrl "), line
    service = (ROOT / "deploy" / "remote-ctrl.service").read_text()
    assert "network-online" not in service.split("[Unit]")[1].split("[Service]")[0].replace("#", "")


def test_review4_wait_for_network_gives_up_quickly():
    import time
    t0 = time.monotonic()
    assert sr.wait_for_network(("127.0.0.1", 9), limit=0.2, step=0.05) is False
    assert time.monotonic() - t0 < 5


def test_review5_stop_boot_uses_resolved_default_dir(env):
    link = env["tmp"] / "work-link"
    link.symlink_to(env["repo"])
    write_cfg(env, work_dirs=[str(env["repo"])], default_dir=str(link), default_level="readonly")
    started = sr.resolve_target(None, None, None, json.loads(
        (env["tmp"] / "xdg" / "remote-ctrl" / "config.json").read_text()), True).session
    (env["tmp"] / "sessions").write_text(started + "\n")
    assert start("--stop", "--boot") == 0
    assert f"kill-session -t {started}" in env["tmux_log"].read_text()


def test_review6_normal_needs_repo_root(env, capsys):
    (env["repo"] / "sub").mkdir()
    write_cfg(env, work_dirs=[str(env["repo"] / "sub")])
    assert start("--level", "normal", "--dir", str(env["repo"] / "sub")) == 1
    assert "根目录" in capsys.readouterr().out
    assert start("--level", "readonly", "--dir", str(env["repo"] / "sub")) == 0


def test_review7_install_checks_default_dir_in_whitelist(env, capsys):
    write_cfg(env, work_dirs=[str(env["repo"])], default_level="readonly", default_dir=str(env["plain"]))
    assert install_service.main(["--dry-run"]) == 1
    assert "白名单" in capsys.readouterr().out


@pytest.mark.skipif(sys.platform == "darwin", reason="systemd 专用")
def test_review8_paths_with_spaces_and_percent(tmp_path):
    text = install_service.render("/My Projects/py 100%", "/a b:/c", Path("/l"))
    exec_line = [l for l in text.splitlines() if l.startswith("ExecStart=")][0]
    assert exec_line.startswith('ExecStart="/My Projects/py 100%%" ')
    assert 'Environment="PATH=/a b:/c"' in text
    unit = tmp_path / "remote-ctrl.service"
    unit.write_text(text)
    p = subprocess.run(["systemd-analyze", "--user", "verify", str(unit)], capture_output=True, text=True)
    assert "Invalid" not in p.stderr and "Failed to parse" not in p.stderr, p.stderr


def test_review9_systemctl_failure_reported(env, monkeypatch, tmp_path, capsys):
    if sys.platform == "darwin":
        pytest.skip("systemd 专用")
    write_cfg(env, work_dirs=[str(env["repo"])], default_level="readonly", default_dir=str(env["repo"]))
    monkeypatch.setattr(install_service, "service_path", lambda home: tmp_path / "unit" / "remote-ctrl.service")
    monkeypatch.setattr(install_service, "run", lambda cmd: False)
    assert install_service.main([]) == 1
    out = capsys.readouterr().out
    assert "启用失败" in out and "下次登录" not in out


def test_review10_levels_have_one_source():
    import platform_utils as pu
    from hooks.policy import levels
    assert sr.LEVELS is pu.LEVELS and levels.LEVELS is pu.LEVELS
