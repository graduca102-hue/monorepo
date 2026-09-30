"""Динамический запуск/остановка дочерних ботов, добавленных владельцем."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from . import storage
from .child_handlers import build_router as build_child_router

log = logging.getLogger("bot-farm.manager")


class BotManager:
    def __init__(self, default_reply_text: str) -> None:
        self.default_reply_text = default_reply_text
        self._children: dict[int, dict] = {}  # bot_id -> {"bot": Bot, "task": Task}

    def is_running(self, bot_id: int) -> bool:
        return bot_id in self._children

    def get_bot(self, bot_id: int) -> Bot | None:
        child = self._children.get(bot_id)
        return child["bot"] if child else None

    async def start_all(self) -> None:
        for row in await storage.list_bots(active_only=True):
            try:
                await self._spawn(row["id"], row["token"], row["username"])
            except Exception:
                log.exception("не смог поднять бота id=%s (@%s) при старте", row["id"], row["username"])

    async def add_from_token(self, token: str, reply_text: str = "") -> dict:
        """Проверяет токен через getMe, сохраняет и запускает бота. Возвращает запись bots."""
        probe = Bot(token)
        try:
            me = await probe.get_me()
        finally:
            await probe.session.close()

        existing = await storage.get_bot_by_token(token)
        bot_id = await storage.add_bot(token, me.id, me.username or "", reply_text)
        if existing and reply_text:
            await storage.set_reply_text(bot_id, reply_text)

        if not self.is_running(bot_id):
            await self._spawn(bot_id, token, me.username or "")
        return await storage.get_bot_by_token(token)

    async def _spawn(self, bot_id: int, token: str, username: str) -> None:
        bot = Bot(token, default=DefaultBotProperties(parse_mode=None))
        dp = Dispatcher()
        dp.include_router(build_child_router())
        dp["bot_id"] = bot_id
        dp["default_reply_text"] = self.default_reply_text

        async def _poll() -> None:
            try:
                try:
                    await bot.delete_webhook(drop_pending_updates=False)
                except Exception:
                    log.warning("не смог снять webhook у @%s (id=%s), пробую поллинг как есть", username, bot_id)
                await dp.start_polling(bot, handle_signals=False)
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception("дочерний бот @%s (id=%s) упал", username, bot_id)
            finally:
                await bot.session.close()

        task = asyncio.create_task(_poll(), name=f"child-bot-{bot_id}")
        self._children[bot_id] = {"bot": bot, "task": task}
        log.info("запущен дочерний бот @%s (id=%s)", username, bot_id)

    async def stop(self, bot_id: int) -> None:
        child = self._children.pop(bot_id, None)
        if not child:
            return
        child["task"].cancel()
        try:
            await child["task"]
        except asyncio.CancelledError:
            pass

    async def remove(self, bot_id: int) -> None:
        await self.stop(bot_id)
        await storage.set_active(bot_id, False)
        await storage.remove_bot(bot_id)

    async def stop_all(self) -> None:
        for bot_id in list(self._children):
            await self.stop(bot_id)
