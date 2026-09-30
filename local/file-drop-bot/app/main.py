"""Точка входа: long-polling одного бота."""
from __future__ import annotations

import asyncio
import logging
from logging.handlers import RotatingFileHandler

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from .config import BASE_DIR, Config
from .handlers import build_router
from .storage import Storage

log = logging.getLogger(__name__)


def setup_logging() -> None:
    handler = RotatingFileHandler(
        BASE_DIR / "file-drop-bot.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[handler, logging.StreamHandler()],
    )


async def run() -> None:
    setup_logging()
    cfg = Config.from_env()
    store = Storage(cfg.db_path)
    await store.open()

    bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(build_router(cfg, store))

    me = await bot.me()
    log.info("start as @%s (owner=%s, lines=%s)", me.username, cfg.owner_id, cfg.lines_per_user)
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot)
    finally:
        await store.close()
        await bot.session.close()


def main() -> None:
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
