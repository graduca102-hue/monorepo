from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject


class PartnerBotContextMiddleware(BaseMiddleware):
    """Injects current partner-bot context into every update handler."""

    def __init__(self, registry: "PartnerBotEngine"):
        self.registry = registry

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        bot = data["bot"]
        partner_bot = self.registry.get_partner_by_token(bot.token)
        if partner_bot is None:
            raise RuntimeError("Partner bot is not registered in registry")

        data["partner_bot"] = partner_bot
        data["bot_id"] = partner_bot.id
        data["partner_owner_id"] = partner_bot.owner_id
        data["margin_percentage"] = partner_bot.margin_percentage
        return await handler(event, data)


from .engine import PartnerBotEngine
