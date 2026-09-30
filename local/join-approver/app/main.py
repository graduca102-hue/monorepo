"""Точка входа: long-polling бота-одобрятеля заявок."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher

from .config import Config
from .handlers import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("join-approver")


async def _run() -> None:
    cfg = Config.from_env()
    bot = Bot(cfg.bot_token)
    dp = Dispatcher()
    dp.include_router(router)

    me = await bot.get_me()
    log.info(
        "started as @%s | auto_approve=%s | chats=%s | delay=%ss",
        me.username,
        cfg.auto_approve,
        (sorted(cfg.allowed_chat_ids) or "ALL"),
        cfg.approve_delay,
    )

    # chat_join_request не входит в дефолтные allowed_updates — запрашиваем явно
    allowed = dp.resolve_used_update_types()
    if "chat_join_request" not in allowed:
        allowed.append("chat_join_request")

    try:
        await dp.start_polling(bot, cfg=cfg, allowed_updates=allowed)
    finally:
        await bot.session.close()


def main() -> None:
    try:
        asyncio.run(_run())
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
