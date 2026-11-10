"""Load settings from .env (project root) into a Config object."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent


class ConfigError(Exception):
    """Raised when a required setting is missing or invalid."""


@dataclass(frozen=True)
class Config:
    bot_token: str
    allowed_user_ids: frozenset[int]
    work_dir: Path


def parse_user_ids(raw: str) -> frozenset[int]:
    """Parse "123, 456" into {123, 456}. Empty string gives an empty set."""
    ids = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            raise ConfigError(f"ALLOWED_USER_IDS 里有不是数字的值：{part!r}") from None
    return frozenset(ids)


def load_config(env_file: Path | None = None) -> Config:
    load_dotenv(env_file or PROJECT_ROOT / ".env")

    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise ConfigError("缺少 BOT_TOKEN：复制 .env.example 为 .env 并填入 BotFather 给的 token")

    allowed = parse_user_ids(os.getenv("ALLOWED_USER_IDS", ""))

    work_dir = Path(os.getenv("WORK_DIR", str(PROJECT_ROOT))).expanduser().resolve()
    if not work_dir.is_dir():
        raise ConfigError(f"WORK_DIR 不存在：{work_dir}")

    return Config(bot_token=token, allowed_user_ids=allowed, work_dir=work_dir)
