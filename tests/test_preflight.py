"""preflight 测试：只测判断逻辑，不需要真的 claude 或网络。"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import platform_utils as pu
from preflight import checks_claude as cc
from preflight import checks_system as cs
from preflight import cli, commands, config_store
from preflight.models import FAIL, OK, WARN

PROJECT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 11, 10, tzinfo=timezone.utc)
GOOD = {
    "loggedIn": True,
    "authMethod": "claude.ai",
    "apiProvider": "firstParty",
    "subscriptionType": "pro",
    "orgId": "org-aaaa-1111",
    "loginExpiresAt": "2026-12-08T04:55:01.092Z",
}


# --- checks_claude ---------------------------------------------------------------

def test_platform():
    assert cc.check_platform("linux").status == OK
    assert cc.check_platform("darwin").status == OK
    assert cc.check_platform("win32").status == FAIL


def test_remote_flag():
    assert cc.check_remote_flag("  --remote-control [name]  ...", "2.1.322").status == OK
    assert cc.check_remote_flag("  --resume ...", "1.0.0").status == FAIL


def test_remote_flag_when_help_fails():
    c = cc.check_remote_flag("", "?", help_ok=False)
    assert c.status == FAIL and "--help" in c.detail


def test_auth_ok():
    assert cc.check_auth(GOOD).status == OK


def test_auth_failures():
    assert cc.check_auth(None).status == FAIL
    assert cc.check_auth({**GOOD, "loggedIn": False}).status == FAIL
    assert cc.check_auth({**GOOD, "authMethod": "api_key"}).status == FAIL
    assert cc.check_auth({**GOOD, "apiProvider": "bedrock"}).status == FAIL


def test_login_expiry():
    assert cc.check_login_expiry(GOOD, NOW).status == OK
    soon = (NOW + timedelta(days=3)).isoformat()
    assert cc.check_login_expiry({**GOOD, "loginExpiresAt": soon}, NOW).status == WARN
    past = (NOW - timedelta(days=1)).isoformat()
    assert cc.check_login_expiry({**GOOD, "loginExpiresAt": past}, NOW).status == FAIL
    assert cc.check_login_expiry({}, NOW).status == WARN


def test_login_expiry_naive_and_odd_values():
    naive = (NOW + timedelta(days=30)).replace(tzinfo=None).isoformat()
    assert cc.check_login_expiry({"loginExpiresAt": naive}, NOW).status == OK
    assert cc.check_login_expiry({"loginExpiresAt": 1765169701092}, NOW).status == WARN
    assert cc.check_login_expiry({"loginExpiresAt": "not a date"}, NOW).status == WARN


def test_trusted_account_is_strict():
    assert cc.check_trusted_account(GOOD, "org-aaaa-1111").status == OK
    assert cc.check_trusted_account(GOOD, None).status == FAIL             # 没设定 -> 挡
    assert cc.check_trusted_account(GOOD, "org-bbbb-2222").status == FAIL  # 别的账号 -> 挡
    assert cc.check_trusted_account({**GOOD, "orgId": None}, "org-aaaa-1111").status == FAIL


def test_api_key_env_warns_without_leaking_value():
    secret = "sk-ant-SECRET-VALUE"
    c = cc.check_api_key_env({"ANTHROPIC_API_KEY": secret})
    assert c.status == WARN
    assert secret not in c.detail + c.fix
    assert cc.check_api_key_env({}).status == OK


def test_more_override_vars():
    for var in ("CLAUDE_CODE_USE_BEDROCK", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_OAUTH_TOKEN"):
        assert cc.check_api_key_env({var: "1"}).status == WARN
        assert var not in pu.sanitized_env({var: "1", "PATH": "/bin"})


# --- checks_system ---------------------------------------------------------------

def test_tool_required():
    assert cs.check_tool("tmux", "/usr/bin/tmux", "").status == OK
    assert cs.check_tool("tmux", None, "install").status == FAIL


def test_work_dirs(tmp_path):
    assert cs.check_work_dirs([])[0].status == WARN
    assert cs.check_work_dirs([str(tmp_path / "missing")])[0].status == FAIL
    assert cs.check_work_dirs([str(tmp_path)])[0].status == FAIL  # 不是 git repo
    import subprocess
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert cs.check_work_dirs([str(tmp_path)])[0].status == OK
    (tmp_path / "sub").mkdir()
    sub = cs.check_work_dirs([str(tmp_path / "sub")])[0]          # repo 的子资料夹也算（M4 review #8）
    assert sub.status == OK and "git repo" in sub.detail


def test_work_dirs_wrong_type():
    assert cs.check_work_dirs("~/proj")[0].status == FAIL
    assert len(cs.check_work_dirs("~/proj")) == 1
    assert cs.check_work_dirs([1, 2])[0].status == FAIL


def test_remote_settings(tmp_path):
    import json
    path = tmp_path / "remote-settings.json"
    assert cs.check_remote_settings(path).status == FAIL            # 还没产生
    path.write_text("{broken")
    assert cs.check_remote_settings(path).status == FAIL            # 坏掉
    guard = tmp_path / "remote_guard.py"
    guard.write_text("")

    def write(python, with_checkpoint=True):
        hooks = {"PreToolUse": [{"hooks": [{"command": f"{python} {guard}"}]}]}
        if with_checkpoint:
            hooks["UserPromptSubmit"] = [{"hooks": [{"command": f"{python} {guard}"}]}]
        path.write_text(json.dumps({"hooks": hooks}))

    write(tmp_path / "no-python")
    assert cs.check_remote_settings(path).status == FAIL            # python 不存在
    write(sys.executable, with_checkpoint=False)
    assert cs.check_remote_settings(path).status == FAIL            # 缺还原点 hook（M4 前产生的旧设定）
    write(sys.executable)
    assert cs.check_remote_settings(path).status == OK


def test_conda_env():
    assert cs.check_conda_env(None).status == WARN
    assert cs.check_conda_env(["base", "remote-ctrl-agent"]).status == OK
    assert cs.check_conda_env(["base"]).status == WARN


# --- platform_utils ---------------------------------------------------------------

def test_sanitized_env_removes_overrides_only():
    env = {"ANTHROPIC_API_KEY": "x", "ANTHROPIC_AUTH_TOKEN": "y", "PATH": "/bin", "HOME": "/h"}
    clean = pu.sanitized_env(env)
    assert "ANTHROPIC_API_KEY" not in clean and "ANTHROPIC_AUTH_TOKEN" not in clean
    assert clean["PATH"] == "/bin" and clean["HOME"] == "/h"
    assert env["ANTHROPIC_API_KEY"] == "x"  # 原本的不被改动


def test_config_dir_ignores_bad_xdg(monkeypatch):
    if pu.IS_MAC:
        return
    for bad in ("", "relative/dir"):
        monkeypatch.setenv("XDG_CONFIG_HOME", bad)
        assert pu.config_dir() == Path.home() / ".config" / "remote-ctrl"
    monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/xdg")
    assert pu.config_dir() == Path("/tmp/xdg/remote-ctrl")


# --- commands ---------------------------------------------------------------------

def test_parse_json_object_ignores_trailing_text():
    assert commands.parse_json_object('{"a": 1}\nWarning: update available') == {"a": 1}
    assert commands.parse_json_object('notice\n{"a": 1}') == {"a": 1}
    assert commands.parse_json_object("no json here") is None
    assert commands.parse_json_object("[1, 2]") is None


def test_run_keeps_stdout_and_stderr_apart():
    code, out, err = commands.run([sys.executable, "-c",
                                   "import sys; print('{\"ok\": true}'); print('warn', file=sys.stderr)"])
    assert code == 0 and commands.parse_json_object(out) == {"ok": True} and "warn" in err


def test_first_word():
    assert commands.first_word("") == "?"
    assert commands.first_word("2.1.322 (Claude Code)") == "2.1.322"


# --- config_store -----------------------------------------------------------------

def test_config_roundtrip(tmp_path):
    path = tmp_path / "remote-ctrl" / "config.json"
    assert config_store.load_local_config(path) == {}
    config_store.save_local_config({"trusted_org_id": "org-1", "work_dirs": []}, path)
    assert config_store.load_local_config(path)["trusted_org_id"] == "org-1"
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_broken_config(tmp_path):
    path = tmp_path / "config.json"
    for content in ("[]", "null", "{broken"):
        path.write_text(content)
        cfg, err = config_store.read_local_config(path)
        assert cfg == {} and err


def test_save_is_private_from_the_start(tmp_path):
    folder = tmp_path / "rc"
    folder.mkdir()
    (folder / "config.json.tmp").write_text("old")
    (folder / "config.json.tmp").chmod(0o644)
    path = config_store.save_local_config({"x": 1}, folder / "config.json")
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert not (folder / "config.json.tmp").exists()


# --- cli --------------------------------------------------------------------------

def test_trust_messages(monkeypatch, capsys):
    monkeypatch.setattr(pu, "find_tool", lambda name: None)
    assert cli.main(["--trust", "org-1"]) == 1
    assert "找不到 claude" in capsys.readouterr().out

    monkeypatch.setattr(pu, "find_tool", lambda name: "/bin/claude")
    monkeypatch.setattr(commands, "read_auth_status", lambda claude, env=None: None)
    assert cli.main(["--trust", "org-1"]) == 1
    assert "读不到 claude auth status" in capsys.readouterr().out

    monkeypatch.setattr(commands, "read_auth_status", lambda claude, env=None: GOOD)
    assert cli.main(["--trust", "org-other"]) == 1
    assert "不同" in capsys.readouterr().out


def test_trust_writes_when_account_matches(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    monkeypatch.setattr(config_store, "config_path", lambda: path)
    monkeypatch.setattr(pu, "find_tool", lambda name: "/bin/claude")
    monkeypatch.setattr(commands, "read_auth_status", lambda claude, env=None: GOOD)
    assert cli.main(["--trust", GOOD["orgId"]]) == 0
    assert config_store.load_local_config(path)["trusted_org_id"] == GOOD["orgId"]


def test_trust_refuses_to_overwrite_broken_config(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{broken")
    monkeypatch.setattr(config_store, "config_path", lambda: path)
    monkeypatch.setattr(pu, "find_tool", lambda name: "/bin/claude")
    monkeypatch.setattr(commands, "read_auth_status", lambda claude, env=None: GOOD)
    assert cli.main(["--trust", GOOD["orgId"]]) == 1
    assert path.read_text() == "{broken"


def test_preflight_never_reads_dotenv():
    sources = [PROJECT / "platform_utils.py", *sorted((PROJECT / "preflight").glob("*.py"))]
    src = "".join(p.read_text() for p in sources)
    assert "dotenv" not in src and '".env"' not in src
