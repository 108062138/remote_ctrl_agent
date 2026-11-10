"""M1: Telegram echo bot with a user-id whitelist.

Run:  conda activate remote-ctrl-agent && python bot.py
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from config import Config, ConfigError, load_config

log = logging.getLogger("remote-ctrl")


def is_allowed(user_id: int | None, allowed: frozenset[int]) -> bool:
    return user_id is not None and user_id in allowed


def build_app(cfg: Config) -> Application:
    app = Application.builder().token(cfg.bot_token).build()

    async def guard(update: Update) -> bool:
        """Return True if the sender may use the bot. Strangers get no reply."""
        user = update.effective_user
        uid = user.id if user else None
        if is_allowed(uid, cfg.allowed_user_ids):
            return True
        if not cfg.allowed_user_ids and update.effective_message:
            # Setup mode: no whitelist yet, so tell the sender their id.
            await update.effective_message.reply_text(
                f"白名单还是空的。你的 user id 是 {uid}\n"
                f"把它填到 .env 的 ALLOWED_USER_IDS，然后重启 bot。"
            )
        log.warning("忽略非白名单用户 id=%s username=%s", uid, user.username if user else None)
        return False

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await guard(update):
            return
        await update.effective_message.reply_text(
            "remote-ctrl-agent 已上线（M1 echo 模式）。发任何文字我会原样回传。"
        )

    async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await guard(update):
            return
        await update.effective_message.reply_text(update.effective_message.text)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
    return app


def main() -> None:
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx logs the URL, which contains the token
    try:
        cfg = load_config()
    except ConfigError as e:
        raise SystemExit(f"设定错误：{e}")

    if not cfg.allowed_user_ids:
        log.warning("ALLOWED_USER_IDS 是空的：bot 只会回传对方的 user id，不会执行其他操作")
    log.info("启动 bot，白名单 %d 人，WORK_DIR=%s", len(cfg.allowed_user_ids), cfg.work_dir)
    build_app(cfg).run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
