"""M1 tests: config parsing, whitelist, echo handler (no network)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot import build_app, is_allowed
from config import Config, ConfigError, load_config, parse_user_ids

FAKE_TOKEN = "123456:TEST-token"


# --- config ---------------------------------------------------------------

def test_parse_user_ids():
    assert parse_user_ids("") == frozenset()
    assert parse_user_ids(" 1, 22 ,,333 ") == frozenset({1, 22, 333})


def test_parse_user_ids_rejects_text():
    with pytest.raises(ConfigError):
        parse_user_ids("123,abc")


def test_load_config_requires_token(tmp_path, monkeypatch):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    env = tmp_path / ".env"
    env.write_text("BOT_TOKEN=\n")
    with pytest.raises(ConfigError):
        load_config(env)


def test_load_config_ok(tmp_path, monkeypatch):
    for k in ("BOT_TOKEN", "ALLOWED_USER_IDS", "WORK_DIR"):
        monkeypatch.delenv(k, raising=False)
    env = tmp_path / ".env"
    env.write_text(f"BOT_TOKEN={FAKE_TOKEN}\nALLOWED_USER_IDS=42\nWORK_DIR={tmp_path}\n")
    cfg = load_config(env)
    assert cfg.bot_token == FAKE_TOKEN
    assert cfg.allowed_user_ids == {42}
    assert cfg.work_dir == tmp_path.resolve()


# --- whitelist --------------------------------------------------------------

def test_is_allowed():
    allowed = frozenset({42})
    assert is_allowed(42, allowed)
    assert not is_allowed(7, allowed)
    assert not is_allowed(None, allowed)
    assert not is_allowed(42, frozenset())


# --- handlers ---------------------------------------------------------------

def make_update(user_id: int, text: str):
    msg = SimpleNamespace(text=text, reply_text=AsyncMock())
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id, username="u"),
        effective_message=msg,
    ), msg


def get_handler(app, kind: str):
    for h in app.handlers[0]:
        if type(h).__name__ == kind:
            return h.callback
    raise LookupError(kind)


def make_app(allowed):
    cfg = Config(bot_token=FAKE_TOKEN, allowed_user_ids=frozenset(allowed), work_dir=None)
    return build_app(cfg)


async def test_echo_allowed_user():
    echo = get_handler(make_app({42}), "MessageHandler")
    update, msg = make_update(42, "hello")
    await echo(update, None)
    msg.reply_text.assert_awaited_once_with("hello")


async def test_echo_stranger_gets_no_reply():
    echo = get_handler(make_app({42}), "MessageHandler")
    update, msg = make_update(7, "hello")
    await echo(update, None)
    msg.reply_text.assert_not_awaited()


async def test_setup_mode_tells_user_id():
    echo = get_handler(make_app(set()), "MessageHandler")
    update, msg = make_update(7, "hello")
    await echo(update, None)
    reply = msg.reply_text.await_args.args[0]
    assert "7" in reply and "ALLOWED_USER_IDS" in reply


async def test_start_allowed_user():
    start = get_handler(make_app({42}), "CommandHandler")
    update, msg = make_update(42, "/start")
    await start(update, None)
    msg.reply_text.assert_awaited_once()
