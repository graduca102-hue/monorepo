"""Хэндлеры для каждого управляемого бота: копит аудиторию, отвечает заданным текстом."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import Message

from . import storage

log = logging.getLogger("bot-farm.child")


async def on_any_message(msg: Message, bot_id: int, default_reply_text: str) -> None:
    user = msg.from_user
    if user is None:
        return
    await storage.record_audience(bot_id, user.id, user.username, user.full_name)
    text = await storage.get_reply_text(bot_id, default_reply_text)
    if not text:
        return
    try:
        await msg.answer(text)
    except (TelegramForbiddenError, TelegramBadRequest):
        pass  # пользователь заблокировал бота / деактивирован — не критично


def build_router() -> Router:
    """Router нельзя переиспользовать между Dispatcher'ами — каждому дочернему боту своя копия."""
    router = Router()
    router.message.register(on_any_message, F.chat.type == "private")
    return router
