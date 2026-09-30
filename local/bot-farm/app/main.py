"""Точка входа: главный бот-управление + все запущенные дочерние боты."""
from __future__ import annotations

import asyncio
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from aiogram import Bot, Dispatcher

from . import storage
from .config import Config
from .main_handlers import router as main_router
from .manager import BotManager

_LOG_PATH = Path(__file__).resolve().parent.parent / "bot-farm.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(_LOG_PATH, maxBytes=5_000_000, backupCount=2, encoding="utf-8"),
    ],
)
log = logging.getLogger("bot-farm")


async def _run() -> None:
    cfg = Config.from_env()
    await storage.init_db()

    manager = BotManager(default_reply_text=cfg.default_reply_text)
    await manager.start_all()

    bot = Bot(cfg.bot_token)
    dp = Dispatcher()
    dp.include_router(main_router)
    dp["cfg"] = cfg
    dp["manager"] = manager

    me = await bot.get_me()
    log.info("главный бот @%s запущен, владелец=%s", me.username, cfg.owner_id)

    try:
        await dp.start_polling(bot)
    finally:
        await manager.stop_all()
        await bot.session.close()


def main() -> None:
    try:
        asyncio.run(_run())
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
