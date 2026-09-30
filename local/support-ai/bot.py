"""Standalone Telegram support bot powered by SupportAgent.

Run:
    python bot.py

Reads .env in this folder. Every non-command message from a client goes to the
LLM. If the LLM asks for escalation, the bot posts the operator line to
OPERATOR_CHAT_ID and forwards the original message.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from dotenv import load_dotenv

from ai_agent import SupportAgent, format_operator_line

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("support-ai.bot")

BOT_TOKEN = os.environ["BOT_TOKEN"]
OPERATOR_CHAT_ID = int(os.environ["OPERATOR_CHAT_ID"])

WELCOME = (
    "Здравствуйте. Я бот поддержки SOUS MARKET. "
    "Опишите проблему одним сообщением. "
    "Для заказов пришлите номер из «Мой профиль → 🧾 Заказы»."
)


async def main() -> None:
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()
    agent = SupportAgent()

    @dp.message(CommandStart())
    async def on_start(message: Message) -> None:
        agent.reset(message.from_user.id)
        await message.answer(WELCOME)

    @dp.message(Command("reset"))
    async def on_reset(message: Message) -> None:
        agent.reset(message.from_user.id)
        await message.answer("Контекст очищен. Опишите проблему заново.")

    @dp.message(F.text)
    async def on_text(message: Message) -> None:
        user = message.from_user
        try:
            await bot.send_chat_action(message.chat.id, "typing")
            reply = await agent.reply(user_id=user.id, text=message.text)
        except Exception:
            log.exception("agent.reply failed, user_id=%s", user.id)
            await message.answer(
                "Технический сбой. Передаю оператору. Ответ придёт в этот чат."
            )
            try:
                await bot.send_message(
                    OPERATOR_CHAT_ID,
                    f"[ERR] user {user.id} (@{user.username or '—'}): agent crash",
                )
                await message.forward(OPERATOR_CHAT_ID)
            except Exception:
                log.exception("failed to notify operator")
            return

        await message.answer(reply.text)

        if reply.escalation is not None:
            line = format_operator_line(user_id=user.id, escalation=reply.escalation)
            username = f"@{user.username}" if user.username else "—"
            header = f"[escalate] {username}\n{line}"
            try:
                await bot.send_message(OPERATOR_CHAT_ID, header)
                await message.forward(OPERATOR_CHAT_ID)
            except Exception:
                log.exception("failed to escalate, user_id=%s", user.id)

    @dp.message(~F.text)
    async def on_non_text(message: Message) -> None:
        user = message.from_user
        username = f"@{user.username}" if user.username else "—"
        await message.answer(
            "Прикрепления передаю оператору. Ответ придёт в этот чат."
        )
        try:
            await bot.send_message(
                OPERATOR_CHAT_ID,
                f"[attachment] user {user.id} ({username})",
            )
            await message.forward(OPERATOR_CHAT_ID)
        except Exception:
            log.exception("failed to forward attachment, user_id=%s", user.id)

    log.info("support-ai bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
