from __future__ import annotations

import asyncio
import hashlib
import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Update

from .models import OrderCreate, PartnerBot
from .repository import PartnerRepository

LOGGER = logging.getLogger(__name__)


class PartnerBotEngine:
    """
    One-process multibot engine.

    - One Dispatcher for every partner bot.
    - One shared Aiogram HTTP session for all Bot instances.
    - Dynamic webhook registration without restarting the Python process.
    """

    def __init__(
        self,
        dispatcher: Dispatcher,
        repository: PartnerRepository,
        public_base_url: str,
        webhook_path_prefix: str = "/webhooks/partners",
    ):
        self.dispatcher = dispatcher
        self.repository = repository
        self.public_base_url = public_base_url.rstrip("/")
        self.webhook_path_prefix = webhook_path_prefix.rstrip("/")
        self._session = AiohttpSession()
        self._bots_by_partner_id: dict[int, Bot] = {}
        self._partners_by_token: dict[str, PartnerBot] = {}
        self._lock = asyncio.Lock()

    def get_partner_by_token(self, bot_token: str) -> PartnerBot | None:
        return self._partners_by_token.get(bot_token)

    def get_bot(self, partner_id: int) -> Bot | None:
        return self._bots_by_partner_id.get(partner_id)

    def get_webhook_path(self, partner_id: int) -> str:
        return f"{self.webhook_path_prefix}/{partner_id}"

    def get_webhook_url(self, partner_id: int) -> str:
        return f"{self.public_base_url}{self.get_webhook_path(partner_id)}"

    @staticmethod
    def build_secret_token(partner: PartnerBot) -> str:
        raw = f"{partner.id}:{partner.bot_token}:{partner.owner_id}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def load_active_bots(self) -> None:
        partners = await self.repository.fetch_active_partner_bots()
        for partner in partners:
            await self._activate_partner_bot(partner)

    async def start_partner_bot_by_token(
        self,
        owner_id: int,
        bot_token: str,
        margin_percentage: int = 0,
    ) -> PartnerBot:
        """
        Public function for dynamic bot onboarding without restarting the app.
        """

        probe_bot = Bot(bot_token, session=self._session)
        me = await probe_bot.get_me()
        partner = await self.repository.upsert_partner_bot(
            owner_id=owner_id,
            bot_token=bot_token,
            bot_username=me.username or f"bot_{me.id}",
            margin_percentage=margin_percentage,
            is_active=True,
        )
        await self._activate_partner_bot(partner)
        return partner

    async def disable_partner_bot(self, partner_id: int) -> None:
        async with self._lock:
            bot = self._bots_by_partner_id.pop(partner_id, None)
            partner = None
            if bot is not None:
                partner = self._partners_by_token.pop(bot.token, None)
                try:
                    await bot.delete_webhook(drop_pending_updates=False)
                except TelegramBadRequest:
                    LOGGER.warning("Webhook was already removed for partner_id=%s", partner_id)

            await self.repository.set_partner_bot_active(partner_id, False)
            if partner:
                LOGGER.info("Partner bot disabled: @%s", partner.bot_username)

    async def _activate_partner_bot(self, partner: PartnerBot) -> None:
        async with self._lock:
            bot = Bot(partner.bot_token, session=self._session)
            secret_token = self.build_secret_token(partner)
            await bot.set_webhook(
                url=self.get_webhook_url(partner.id),
                secret_token=secret_token,
                allowed_updates=self.dispatcher.resolve_used_update_types(),
                drop_pending_updates=False,
            )
            self._bots_by_partner_id[partner.id] = bot
            self._partners_by_token[partner.bot_token] = partner
            LOGGER.info("Partner bot activated: @%s", partner.bot_username)

    async def close(self) -> None:
        async with self._lock:
            self._bots_by_partner_id.clear()
            self._partners_by_token.clear()
        await self._session.close()

    async def handle_webhook(self, request: web.Request) -> web.Response:
        partner_id = int(request.match_info["partner_id"])
        bot = self.get_bot(partner_id)
        if bot is None:
            raise web.HTTPNotFound(text="Unknown partner bot")

        partner = self.get_partner_by_token(bot.token)
        if partner is None:
            raise web.HTTPUnauthorized(text="Partner context not found")

        secret_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if secret_token != self.build_secret_token(partner):
            raise web.HTTPForbidden(text="Invalid secret token")

        payload = await request.json()
        update = Update.model_validate(payload, context={"bot": bot})
        await self.dispatcher.feed_update(
            bot,
            update,
            partners_repo=self.repository,
            partner_engine=self,
        )
        return web.Response(text="ok")

    async def create_partner_order(
        self,
        partner_bot: PartnerBot,
        user_id: int,
        product_id: int,
        purchase_price: Decimal,
    ) -> int:
        """
        Helper for your supplier purchase flow.

        purchase_price:
            Base закупочная цена от поставщика.
        final_price:
            Цена для покупателя с учетом margin_percentage текущего бота.
        partner_profit:
            Чистая прибыль франчайзи.
        """

        multiplier = Decimal(1) + (Decimal(partner_bot.margin_percentage) / Decimal(100))
        final_price = (purchase_price * multiplier).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        partner_profit = (final_price - purchase_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        return await self.repository.create_order(
            OrderCreate(
                bot_id=partner_bot.id,
                user_id=user_id,
                product_id=product_id,
                purchase_price=purchase_price,
                final_price=final_price,
                partner_profit=partner_profit,
            )
        )

    def register_routes(self, app: web.Application) -> None:
        app.router.add_post(f"{self.webhook_path_prefix}/{{partner_id:\\d+}}", self.handle_webhook)
