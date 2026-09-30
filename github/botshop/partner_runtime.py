import asyncio
import contextlib
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramConflictError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
    TelegramUnauthorizedError,
)
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineQuery,
    Message,
    PreCheckoutQuery,
    ShippingQuery,
    Update,
)

from database.data import (
    deactivate_partner_bot,
    get_partner_bot_by_token,
    get_partner_owner_summary,
    list_active_partner_bots,
    register_partner_bot_user,
    upsert_partner_bot,
)
try:
    from miniapp import (
        configure_bot_menu_button,
        ensure_miniapp_commands,
        format_brand_name_from_username,
    )
except ModuleNotFoundError:
    async def configure_bot_menu_button(*args, **kwargs) -> None:
        return None

    async def ensure_miniapp_commands(*args, **kwargs) -> None:
        return None

    def format_brand_name_from_username(bot_username: str | None) -> str:
        return (bot_username or "").strip().lstrip("@").upper() or "BOT"


LOGGER = logging.getLogger(__name__)
DEFAULT_PARTNER_MARGIN_PERCENT = int(float(os.getenv("PARTNER_DEFAULT_MARGIN_PERCENT", "50") or 50))

_runtime = None


def set_partner_runtime(runtime: "PartnerBotsRuntime"):
    global _runtime
    _runtime = runtime


def get_partner_runtime() -> "PartnerBotsRuntime | None":
    return _runtime


class PartnerBotsRuntime:
    def __init__(self, dispatcher: Dispatcher, source_bot: Bot):
        self.dispatcher = dispatcher
        self.source_bot = source_bot
        self.allowed_updates = dispatcher.resolve_used_update_types()
        self._bots_by_token: dict[str, Bot] = {}
        self._tasks_by_token: dict[str, asyncio.Task] = {}
        self._partner_ids_by_token: dict[str, int] = {}
        self._offsets_by_token: dict[str, int] = {}
        self._lock = asyncio.Lock()

    def get_partner_bot_for_token(self, bot_token: str) -> dict | None:
        return get_partner_bot_by_token(bot_token)

    def get_bot_by_partner_id(self, partner_id: int) -> Bot | None:
        normalized_partner_id = int(partner_id or 0)
        for bot_token, current_partner_id in self._partner_ids_by_token.items():
            if int(current_partner_id) == normalized_partner_id:
                return self._bots_by_token.get(bot_token)
        return None

    async def load_active_bots(self):
        for partner_bot in list_active_partner_bots():
            try:
                await self._ensure_bot_task(partner_bot)
            except TelegramUnauthorizedError:
                await self._deactivate_revoked_partner_bot(
                    int(partner_bot["id"]),
                    partner_bot["bot_token"],
                    partner_bot.get("bot_username"),
                )
            except Exception:
                LOGGER.exception(
                    "Failed to initialize partner bot partner_id=%s",
                    partner_bot.get("id"),
                )

    async def onboard_partner_bot(
        self,
        owner_id: int,
        bot_token: str,
        margin_percentage: int = DEFAULT_PARTNER_MARGIN_PERCENT,
    ) -> dict:
        if bot_token == self.source_bot.token:
            raise ValueError("Нельзя подключить основной токен бота как партнёрский.")

        existing_partner = get_partner_bot_by_token(bot_token)
        owner_summary = get_partner_owner_summary(int(owner_id))
        is_current_active_bot = bool(
            existing_partner
            and int(existing_partner.get("owner_id") or 0) == int(owner_id)
            and int(existing_partner.get("is_active") or 0) == 1
        )
        if int(owner_summary.get("total_bots") or 0) >= 2 and not is_current_active_bot:
            raise ValueError("Во франшизе можно создать не больше 2 ботов.")

        probe_bot = Bot(bot_token)
        try:
            me = await probe_bot.get_me()
        except TelegramUnauthorizedError as error:
            raise ValueError("BotFather токен недействителен или был отозван.") from error
        finally:
            await probe_bot.session.close()

        partner_bot = upsert_partner_bot(
            owner_id=owner_id,
            bot_token=bot_token,
            bot_username=me.username or f"bot_{me.id}",
            margin_percentage=margin_percentage,
            is_active=True,
        )
        await self._clone_source_bot_profile(bot_token)
        await self._ensure_bot_task(partner_bot)
        return partner_bot

    async def shutdown(self):
        async with self._lock:
            tasks = list(self._tasks_by_token.values())
            bots = list(self._bots_by_token.values())
            self._tasks_by_token.clear()
            self._bots_by_token.clear()
            self._partner_ids_by_token.clear()
            self._offsets_by_token.clear()

        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for bot in bots:
            await bot.session.close()

    async def set_bot_active(self, partner_bot: dict, active: bool) -> None:
        """Apply the cabinet status change to the live polling task."""
        bot_token = str(partner_bot.get("bot_token") or "")
        if not bot_token:
            return
        if active:
            refreshed = get_partner_bot_by_token(bot_token) or partner_bot
            await self._ensure_bot_task(refreshed)
            return

        async with self._lock:
            task = self._tasks_by_token.pop(bot_token, None)
            bot = self._bots_by_token.pop(bot_token, None)
            self._partner_ids_by_token.pop(bot_token, None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if bot is not None:
            await bot.session.close()

    async def _clone_source_bot_profile(self, partner_token: str):
        partner_bot = Bot(partner_token)
        try:
            source_description = await self.source_bot.get_my_description()
            source_short_description = await self.source_bot.get_my_short_description()
            source_commands = await self.source_bot.get_my_commands()

            if getattr(source_description, "description", None):
                await partner_bot.set_my_description(description=source_description.description)
            if getattr(source_short_description, "short_description", None):
                await partner_bot.set_my_short_description(
                    short_description=source_short_description.short_description
                )
            if source_commands:
                commands = [
                    BotCommand(command=command.command, description=command.description)
                    for command in source_commands
                ]
                await partner_bot.set_my_commands(commands=commands)
            await ensure_miniapp_commands(partner_bot)
            await configure_bot_menu_button(
                partner_bot,
                None,
                format_brand_name_from_username(None),
            )
        except TelegramBadRequest:
            LOGGER.exception("Failed to clone source bot profile to partner bot")
        finally:
            await partner_bot.session.close()

    async def _ensure_bot_task(self, partner_bot: dict):
        bot_token = partner_bot["bot_token"]
        async with self._lock:
            existing_task = self._tasks_by_token.get(bot_token)
            if existing_task and not existing_task.done():
                self._partner_ids_by_token[bot_token] = int(partner_bot["id"])
                return

            bot = Bot(bot_token)
            try:
                await bot.delete_webhook(drop_pending_updates=False)
            except TelegramUnauthorizedError:
                await bot.session.close()
                raise
            with contextlib.suppress(Exception):
                await ensure_miniapp_commands(bot)
                await configure_bot_menu_button(
                    bot,
                    partner_bot.get("bot_username"),
                    format_brand_name_from_username(partner_bot.get("bot_username")),
                )
            self._bots_by_token[bot_token] = bot
            self._partner_ids_by_token[bot_token] = int(partner_bot["id"])
            task = asyncio.create_task(
                self._poll_bot(bot_token),
                name=f"partner-bot-{partner_bot['id']}",
            )
            self._tasks_by_token[bot_token] = task

    async def _poll_bot(self, bot_token: str):
        bot = self._bots_by_token[bot_token]
        partner_id = self._partner_ids_by_token[bot_token]
        offset = self._offsets_by_token.get(bot_token, 0)
        retry_delay = 2.0

        while True:
            try:
                updates = await bot.get_updates(
                    offset=offset,
                    timeout=30,
                    allowed_updates=self.allowed_updates,
                )
                for update in updates:
                    offset = update.update_id + 1
                    self._offsets_by_token[bot_token] = offset
                    user_id = extract_user_id_from_update(update)
                    if user_id is not None:
                        register_partner_bot_user(partner_id, user_id)
                    await self.dispatcher.feed_update(bot, update)
                retry_delay = 2.0
            except asyncio.CancelledError:
                raise
            except TelegramUnauthorizedError:
                await self._deactivate_revoked_partner_bot(
                    partner_id,
                    bot_token,
                    get_partner_bot_by_token(bot_token).get("bot_username") if get_partner_bot_by_token(bot_token) else None,
                )
                return
            except TelegramRetryAfter as error:
                delay = max(float(error.retry_after), retry_delay) + min(partner_id % 5, 4) * 0.25
                LOGGER.warning(
                    "Partner bot polling throttled for partner_id=%s; retrying in %.2fs",
                    partner_id,
                    delay,
                )
                await asyncio.sleep(delay)
                retry_delay = min(max(retry_delay * 2, delay), 60.0)
            except (TelegramNetworkError, TelegramServerError) as error:
                delay = retry_delay + min(partner_id % 5, 4) * 0.4
                LOGGER.warning(
                    "Partner bot polling temporarily unavailable for partner_id=%s: %s; retrying in %.2fs",
                    partner_id,
                    type(error).__name__,
                    delay,
                )
                await asyncio.sleep(delay)
                retry_delay = min(retry_delay * 2, 30.0)
            except TelegramConflictError:
                # A partner can enable a webhook from an external panel while
                # this runtime is polling.  Clear it and retry immediately;
                # otherwise the loop would stay in a permanent conflict state.
                LOGGER.warning(
                    "Partner bot webhook detected for partner_id=%s; deleting webhook",
                    partner_id,
                )
                with contextlib.suppress(Exception):
                    await bot.delete_webhook(drop_pending_updates=False)
                await asyncio.sleep(2.0)
                retry_delay = 2.0
            except Exception:
                LOGGER.exception("Partner bot polling loop failed for partner_id=%s", partner_id)
                await asyncio.sleep(max(retry_delay, 5.0))
                retry_delay = min(retry_delay * 2, 30.0)

    async def _deactivate_revoked_partner_bot(
        self,
        partner_id: int,
        bot_token: str,
        bot_username: str | None = None,
    ) -> None:
        LOGGER.warning(
            "Deactivating revoked partner bot partner_id=%s bot_username=%s",
            partner_id,
            bot_username or "",
        )
        deactivate_partner_bot(partner_id)
        async with self._lock:
            task = self._tasks_by_token.pop(bot_token, None)
            bot = self._bots_by_token.pop(bot_token, None)
            self._partner_ids_by_token.pop(bot_token, None)
            self._offsets_by_token.pop(bot_token, None)

        if task is not None and task is not asyncio.current_task():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if bot is not None:
            with contextlib.suppress(Exception):
                await bot.session.close()


def extract_user_id_from_update(update: Update) -> int | None:
    if isinstance(update.message, Message) and update.message.from_user:
        return update.message.from_user.id
    if isinstance(update.callback_query, CallbackQuery) and update.callback_query.from_user:
        return update.callback_query.from_user.id
    if isinstance(update.inline_query, InlineQuery) and update.inline_query.from_user:
        return update.inline_query.from_user.id
    if isinstance(update.shipping_query, ShippingQuery) and update.shipping_query.from_user:
        return update.shipping_query.from_user.id
    if isinstance(update.pre_checkout_query, PreCheckoutQuery) and update.pre_checkout_query.from_user:
        return update.pre_checkout_query.from_user.id
    return None
