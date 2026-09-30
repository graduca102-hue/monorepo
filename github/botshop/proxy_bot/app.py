from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from pathlib import Path
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    MenuButtonCommands,
    Message,
    ReplyKeyboardMarkup,
)
from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[1]
MODULE_DIR = Path(__file__).resolve().parent

# Shared provider/payment settings stay in the main environment.  The second
# file contains only this standalone bot's identity and presentation settings.
load_dotenv(ROOT_DIR / ".env")
load_dotenv(MODULE_DIR / ".env", override=True)

import main as shop_main  # noqa: E402  (environment must be loaded first)
import database.data as database_store  # noqa: E402
import favorites_service as favorites_store  # noqa: E402
import keyboard as shop_keyboard  # noqa: E402
import maskify_service as proxy_store  # noqa: E402
from database.data import (  # noqa: E402
    add_user,
    get_loyalty_discount_info,
    get_main_bot_settings,
    get_user_by_referral_code,
    get_user_profile,
    save_user_start_data,
    set_user_language,
)
from i18n import normalize_language_code, tr  # noqa: E402
from keyboard import (  # noqa: E402
    ALL_PRODUCTS_REPLY_TEXT,
    GOODS_BUTTON_EMOJI_ID,
    LANGUAGE_BUTTON_EMOJI_ID,
    PROFILE_BUTTON_EMOJI_ID,
    PROFILE_ORDERS_BUTTON_EMOJI_ID,
    PROFILE_PROMOCODE_BUTTON_EMOJI_ID,
    REPLY_MENU_ACTIONS,
    REPLY_CATEGORY_BUTTON_EMOJI_ID,
    SCREEN_BANNERS,
    SUPPORT_BUTTON_EMOJI_ID,
    build_language_keyboard,
    build_language_menu_text,
    build_main_subscription_gate_keyboard,
    build_main_subscription_gate_text,
    build_profile_keyboard,
    build_profile_text,
    get_event_language_code,
    is_admin_user,
    is_user_subscribed_to_channel,
    render_screen,
)
from tracking import parse_start_tracking  # noqa: E402


logger = logging.getLogger(__name__)


def module_data_path(env_name: str, default_name: str) -> Path:
    configured = os.getenv(env_name, "").strip()
    path = Path(configured) if configured else MODULE_DIR / default_name
    return path if path.is_absolute() else MODULE_DIR / path


# Keep customer state separate from the main shop. Provider credentials and
# APIs still come from the shared root .env; only mutable bot data is isolated.
PROXY_ONLY_DATABASE_PATH = module_data_path("PROXY_ONLY_DATABASE_PATH", "data.db")
PROXY_ONLY_MASKIFY_DATABASE_PATH = module_data_path(
    "PROXY_ONLY_MASKIFY_DATABASE_PATH",
    "maskify_users.db",
)
database_store.DB_PATH = str(PROXY_ONLY_DATABASE_PATH)
favorites_store.DB_PATH = PROXY_ONLY_DATABASE_PATH
proxy_store.MASKIFY_DB_PATH = str(PROXY_ONLY_MASKIFY_DATABASE_PATH)

PROXY_ONLY_RESIDENTIAL_PRICE_USD = float(
    os.getenv("PROXY_ONLY_RESIDENTIAL_PRICE_USD", "0.6") or 0.6
)
# main.py is imported into this standalone process, so this changes only the
# proxy-only bot and leaves the separately running main shop untouched.
shop_main.PROXY_TEST_GB_PRICES["residential"] = PROXY_ONLY_RESIDENTIAL_PRICE_USD

BRAND_NAME = os.getenv("PROXY_ONLY_BOT_BRAND", "SOUS PROXY").strip() or "SOUS PROXY"
SUPPORT_URL = os.getenv(
    "PROXY_ONLY_SUPPORT_URL",
    "https://t.me/UniversallSupportBot?start=proxy",
).strip()
NEWS_URL = os.getenv(
    "PROXY_ONLY_NEWS_URL",
    "",
).strip()
DOCS_URL = os.getenv(
    "PROXY_ONLY_DOCS_URL",
    "https://sousmarketfranchize.shop/docs/",
).strip()
USAGE_URL = os.getenv(
    "PROXY_ONLY_USAGE_URL",
    "https://telegra.ph/Polzovatelskoe-soglashenie-08-22-52",
).strip()
PRIVACY_URL = os.getenv(
    "PROXY_ONLY_PRIVACY_URL",
    "https://telegra.ph/Politika-konfidencialnosti-08-22-78",
).strip()

BUY_PROXY_LABELS = {
    "ru": "Купить прокси",
    "uk": "Купити проксі",
    "en": "Buy proxy",
    "es": "Comprar proxy",
    "zh": "购买代理",
    "ja": "プロキシを購入",
}

DISCOUNT_LABELS = {
    "ru": "Скидки",
    "uk": "Знижки",
    "en": "Discounts",
    "es": "Descuentos",
    "zh": "折扣",
    "ja": "割引",
}

OLD_CATALOG_TEXTS = {
    text
    for text, action in REPLY_MENU_ACTIONS.items()
    if action in {"category", "all_products"}
}
OLD_CATALOG_TEXTS.add(ALL_PRODUCTS_REPLY_TEXT)
FRANCHISE_TEXTS = {
    text
    for text, action in REPLY_MENU_ACTIONS.items()
    if action == "partner"
}

PROXY_COMMANDS = {"start", "menu", "proxy", "catalog", "shop"}
PROFILE_COMMANDS = {"profile"}
HELP_COMMANDS = {"help", "support"}
FRANCHISE_COMMANDS = {"partner", "cabinet", "franchise"}

BLOCKED_CATALOG_CALLBACKS = {
    "magazine",
    "market_mail_menu",
    "market_messengers_menu",
    "market_messaging_social_menu",
    "market_games_menu",
    "market_social_menu",
}
BLOCKED_CATALOG_PREFIXES = (
    "market_",
)
BLOCKED_FRANCHISE_PREFIXES = (
    "partner_program",
    "partner_bot_",
    "partner_admin_",
    "partner_cabinet_",
    "partner_markup_",
    "admin_franchise",
    "admin_franchises",
    "admin_utm_category:franchises",
)


def buy_proxy_label(language_code: str | None) -> str:
    lang = normalize_language_code(language_code)
    return BUY_PROXY_LABELS.get(lang, BUY_PROXY_LABELS["en"])


def discount_label(language_code: str | None) -> str:
    lang = normalize_language_code(language_code)
    return DISCOUNT_LABELS.get(lang, DISCOUNT_LABELS["en"])


def build_proxy_only_reply_menu(user_id: int, language_code: str | None = None) -> ReplyKeyboardMarkup:
    lang = normalize_language_code(language_code or get_event_language_code_for_user(user_id))
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text=buy_proxy_label(lang),
                    icon_custom_emoji_id=REPLY_CATEGORY_BUTTON_EMOJI_ID,
                )
            ],
            [
                KeyboardButton(
                    text=tr(lang, "menu.profile"),
                    icon_custom_emoji_id=PROFILE_BUTTON_EMOJI_ID,
                ),
                KeyboardButton(
                    text=tr(lang, "menu.language"),
                    icon_custom_emoji_id=LANGUAGE_BUTTON_EMOJI_ID,
                ),
            ],
            [
                KeyboardButton(
                    text=discount_label(lang),
                    icon_custom_emoji_id=PROFILE_PROMOCODE_BUTTON_EMOJI_ID,
                )
            ],
            [
                KeyboardButton(
                    text=tr(lang, "reply.information"),
                    icon_custom_emoji_id=SUPPORT_BUTTON_EMOJI_ID,
                )
            ],
        ],
        is_persistent=True,
        resize_keyboard=True,
    )


def get_event_language_code_for_user(user_id: int) -> str:
    # resolve_language_code is intentionally kept in keyboard.py because it
    # also understands the shared user profile database.
    from keyboard import resolve_language_code

    return resolve_language_code(user_id=user_id)


def build_proxy_only_inline_menu(language_code: str | None = None) -> InlineKeyboardMarkup:
    lang = normalize_language_code(language_code)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=buy_proxy_label(lang),
                    callback_data="proxy_test:home",
                    icon_custom_emoji_id=shop_main.PROXY_MINIAPP_BUTTON_EMOJI_ID,
                    style="success",
                )
            ],
            [
                InlineKeyboardButton(
                    text=tr(lang, "menu.profile"),
                    callback_data="profile",
                    icon_custom_emoji_id=PROFILE_BUTTON_EMOJI_ID,
                )
            ],
            [
                InlineKeyboardButton(
                    text=tr(lang, "menu.language"),
                    callback_data="language_menu",
                    icon_custom_emoji_id=LANGUAGE_BUTTON_EMOJI_ID,
                )
            ],
            [
                InlineKeyboardButton(
                    text=tr(lang, "menu.support"),
                    url=SUPPORT_URL,
                    icon_custom_emoji_id=SUPPORT_BUTTON_EMOJI_ID,
                )
            ],
        ]
    )


async def render_proxy_only_main(
    target: Message,
    user_id: int,
    *,
    language_code: str | None = None,
    refresh_reply_keyboard: bool = False,
) -> Message:
    lang = normalize_language_code(language_code or get_event_language_code_for_user(user_id))
    inline_menu = build_proxy_only_inline_menu(lang)
    if not refresh_reply_keyboard:
        return await render_screen(
            target,
            "\u2063",
            reply_markup=inline_menu,
            banner="proxy",
        )

    anchor = await target.answer(
        "\u2063",
        reply_markup=build_proxy_only_reply_menu(user_id, lang),
    )
    banner_path = SCREEN_BANNERS["proxy"]
    if banner_path.exists():
        screen = await anchor.answer_photo(
            photo=FSInputFile(str(banner_path)),
            caption="\u2063",
            reply_markup=inline_menu,
        )
    else:
        screen = await anchor.answer("\u2063", reply_markup=inline_menu)
    if target.message_id != anchor.message_id:
        with contextlib.suppress(Exception):
            await target.delete()
    return screen


def extract_command_name(message: Message) -> str:
    text = str(message.text or "").strip()
    if not text.startswith("/"):
        return ""
    command = text[1:].split(maxsplit=1)[0].split("@", 1)[0]
    return command.casefold()


def extract_start_payload(message: Message) -> str | None:
    text = str(message.text or "").strip()
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) == 2 and parts[1].strip() else None


def extract_customer_referrer_id(payload: str | None, user_id: int) -> int | None:
    value = str(payload or "").strip()
    if value.startswith("r_"):
        referral_code = value[2:].strip()
    else:
        return None
    referrer = get_user_by_referral_code(referral_code) if referral_code else None
    if not referrer or int(referrer["user_id"]) == int(user_id):
        return None
    return int(referrer["user_id"])


async def show_subscription_gate_if_needed(message: Message, user_id: int, lang: str) -> bool:
    if is_admin_user(user_id):
        return False
    settings = get_main_bot_settings()
    channel_id = str(settings.get("subscription_channel_id") or "").strip()
    if int(settings.get("subscription_enabled") or 0) != 1 or not channel_id:
        return False
    if await is_user_subscribed_to_channel(message.bot, channel_id, user_id) is not False:
        return False
    await message.answer(
        "\u2063",
        reply_markup=build_proxy_only_reply_menu(user_id, lang),
    )
    await message.answer(
        build_main_subscription_gate_text(settings, lang),
        reply_markup=build_main_subscription_gate_keyboard(settings, lang),
        disable_web_page_preview=True,
    )
    return True


async def handle_start(message: Message, state: FSMContext | None) -> None:
    if state is not None:
        await state.clear()
    payload = extract_start_payload(message)
    add_user(
        message.from_user.id,
        referred_by=extract_customer_referrer_id(payload, message.from_user.id),
        language_code=message.from_user.language_code,
    )
    _, utm_data = parse_start_tracking(payload or "")
    save_user_start_data(message.from_user.id, payload, utm_data)
    lang = get_event_language_code(message)
    if await show_subscription_gate_if_needed(message, message.from_user.id, lang):
        return
    await render_proxy_only_main(
        message,
        message.from_user.id,
        language_code=lang,
        refresh_reply_keyboard=True,
    )


async def handle_profile(message: Message, state: FSMContext | None) -> None:
    if state is not None:
        await state.clear()
    add_user(message.from_user.id, language_code=message.from_user.language_code)
    profile = get_user_profile(message.from_user.id)
    lang = get_event_language_code(message, profile)
    await render_screen(
        message,
        build_profile_text(profile, lang),
        reply_markup=build_profile_keyboard(message.from_user.id, message.bot, language_code=lang),
        banner="profile",
        parse_mode="HTML",
    )


async def handle_information(message: Message, state: FSMContext | None) -> None:
    if state is not None:
        await state.clear()
    lang = get_event_language_code(message)
    rows = []
    policy_row = []
    if USAGE_URL:
        policy_row.append(
            InlineKeyboardButton(
                text="Политика использования",
                url=USAGE_URL,
                icon_custom_emoji_id=PROFILE_ORDERS_BUTTON_EMOJI_ID,
            )
        )
    if PRIVACY_URL:
        policy_row.append(
            InlineKeyboardButton(
                text="Конфиденциальность",
                url=PRIVACY_URL,
                icon_custom_emoji_id=PROFILE_ORDERS_BUTTON_EMOJI_ID,
            )
        )
    if policy_row:
        rows.append(policy_row)
    if NEWS_URL:
        rows.append([
            InlineKeyboardButton(
                text="Новостной канал",
                url=NEWS_URL,
                icon_custom_emoji_id=SUPPORT_BUTTON_EMOJI_ID,
            )
        ])
    if DOCS_URL:
        rows.append([
            InlineKeyboardButton(
                text="Документация",
                url=DOCS_URL,
                icon_custom_emoji_id=GOODS_BUTTON_EMOJI_ID,
            )
        ])
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=tr(lang, "menu.support"),
                    url=SUPPORT_URL,
                    icon_custom_emoji_id=SUPPORT_BUTTON_EMOJI_ID,
                )
            ],
            [InlineKeyboardButton(text=tr(lang, "common.back"), callback_data="back_main")],
        ]
    )
    await render_screen(
        message,
        f"ℹ️ <b>{BRAND_NAME}</b>\n\nПокупка и автоматическая выдача прокси.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )


async def handle_discounts(message: Message, state: FSMContext | None) -> None:
    if state is not None:
        await state.clear()
    lang = get_event_language_code(message)
    info = get_loyalty_discount_info(message.from_user.id)
    await render_screen(
        message,
        "💸 <b>Скидки</b>\n\n"
        f"Сумма пополнений: <b>{info['topups_total']:.2f}$</b>\n\n"
        "<blockquote><b>Пополнения ≥ 250$</b>\nСкидка на всё: <b>3%</b></blockquote>\n"
        "<blockquote><b>Пополнения ≥ 500$</b>\nСкидка на всё: <b>5%</b></blockquote>\n"
        "<blockquote><b>Пополнения ≥ 1000$</b>\nСкидка на всё: <b>7%</b></blockquote>\n"
        f"Ваша скидка: <b>{info['discount_percent']:g}%</b>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=tr(lang, "common.back"), callback_data="back_main")]
            ]
        ),
        parse_mode="HTML",
    )


def is_blocked_callback(callback_data: str | None) -> bool:
    value = str(callback_data or "")
    if value.startswith("partner_proxy_"):
        return False
    return value in BLOCKED_CATALOG_CALLBACKS or value.startswith(
        BLOCKED_CATALOG_PREFIXES + BLOCKED_FRANCHISE_PREFIXES
    )


def remove_blocked_buttons(markup: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            allowed_row
            for row in markup.inline_keyboard
            if (allowed_row := [button for button in row if not is_blocked_callback(button.callback_data)])
        ]
    )


class ProxyOnlyMessageMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        state = data.get("state")
        command = extract_command_name(event)
        text = str(event.text or "").strip()
        lang = get_event_language_code(event)

        if command == "start":
            await handle_start(event, state)
            return None
        if command in PROXY_COMMANDS or text in OLD_CATALOG_TEXTS or text in BUY_PROXY_LABELS.values():
            if state is not None:
                await state.clear()
            add_user(event.from_user.id, language_code=event.from_user.language_code)
            await shop_main.show_proxy_test_home(event, event.from_user.id)
            return None
        if command in PROFILE_COMMANDS:
            await handle_profile(event, state)
            return None
        if command in HELP_COMMANDS:
            await event.answer(
                "👇",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text=tr(lang, "menu.support"), url=SUPPORT_URL)]]
                ),
            )
            return None
        if command in FRANCHISE_COMMANDS or text in FRANCHISE_TEXTS:
            if state is not None:
                await state.clear()
            await render_proxy_only_main(event, event.from_user.id, language_code=lang)
            return None
        if text in DISCOUNT_LABELS.values():
            await handle_discounts(event, state)
            return None
        if REPLY_MENU_ACTIONS.get(text) == "information":
            await handle_information(event, state)
            return None
        return await handler(event, data)


class ProxyOnlyCallbackMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[CallbackQuery, dict[str, Any]], Awaitable[Any]],
        event: CallbackQuery,
        data: dict[str, Any],
    ) -> Any:
        value = str(event.data or "")
        state = data.get("state")
        lang = get_event_language_code(event)

        if value == "back_main":
            await event.answer()
            if state is not None:
                await state.clear()
            if event.message:
                await render_proxy_only_main(event.message, event.from_user.id, language_code=lang)
            return None

        if value.startswith("language_set:"):
            if state is not None:
                await state.clear()
            language_code = normalize_language_code(value.split(":", 1)[1])
            set_user_language(event.from_user.id, language_code)
            await event.answer(tr(language_code, "language.updated"))
            if event.message:
                await render_proxy_only_main(
                    event.message,
                    event.from_user.id,
                    language_code=language_code,
                    refresh_reply_keyboard=True,
                )
            return None

        if value == "main_subscription_check":
            settings = get_main_bot_settings()
            channel_id = str(settings.get("subscription_channel_id") or "").strip()
            subscribed = (
                not channel_id
                or is_admin_user(event.from_user.id)
                or await is_user_subscribed_to_channel(event.bot, channel_id, event.from_user.id) is not False
            )
            if not subscribed:
                await event.answer(tr(lang, "subscription.prompt"), show_alert=True)
                return None
            await event.answer()
            if state is not None:
                await state.clear()
            if event.message:
                await render_proxy_only_main(
                    event.message,
                    event.from_user.id,
                    language_code=lang,
                    refresh_reply_keyboard=True,
                )
            return None

        if is_blocked_callback(value):
            await event.answer("В этом боте доступна только покупка прокси.", show_alert=True)
            if state is not None:
                await state.clear()
            if event.message:
                await render_proxy_only_main(event.message, event.from_user.id, language_code=lang)
            return None
        return await handler(event, data)


def configure_dispatcher() -> None:
    # The shared proxy handlers already know all services and callbacks. Wrap
    # their keyboards in this process so every proxy button gets a premium icon
    # without changing the main shop process.
    for builder_name in (
        "build_proxy_test_menu_keyboard",
        "build_proxy_pool_keyboard",
        "build_choices_keyboard",
        "build_partner_proxy_service_keyboard",
    ):
        original_builder = getattr(shop_main, builder_name)

        def premium_builder(*args: Any, _builder=original_builder, **kwargs: Any) -> InlineKeyboardMarkup:
            markup = _builder(*args, **kwargs)
            return InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        button
                        if button.icon_custom_emoji_id
                        else button.model_copy(
                            update={"icon_custom_emoji_id": shop_main.PROXY_MINIAPP_BUTTON_EMOJI_ID}
                        )
                        for button in row
                    ]
                    for row in markup.inline_keyboard
                ]
            )

        setattr(shop_main, builder_name, premium_builder)

    # Franchise management and franchise UTM statistics must not appear even
    # to the administrator of this independent proxy bot.
    for builder_name in ("admin_panel", "build_admin_utm_menu_keyboard"):
        original_builder = getattr(shop_keyboard, builder_name)

        def proxy_admin_builder(*args: Any, _builder=original_builder, **kwargs: Any) -> InlineKeyboardMarkup:
            return remove_blocked_buttons(_builder(*args, **kwargs))

        setattr(shop_keyboard, builder_name, proxy_admin_builder)
    shop_main.dp.message.outer_middleware(ProxyOnlyMessageMiddleware())
    shop_main.dp.callback_query.outer_middleware(ProxyOnlyCallbackMiddleware())


async def run() -> None:
    token = os.getenv("PROXY_ONLY_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("PROXY_ONLY_BOT_TOKEN is not configured")

    shop_main.create_db()
    configure_dispatcher()
    bot = Bot(token=token)
    await bot.delete_webhook(drop_pending_updates=False)
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Открыть меню"),
            BotCommand(command="proxy", description="Купить прокси"),
            BotCommand(command="profile", description="Профиль"),
            BotCommand(command="help", description="Поддержка"),
        ]
    )
    # Never inherit an old Web App menu button: this bot must work in every
    # Telegram client as an ordinary command/reply-keyboard bot.
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    logger.info("Proxy-only bot started as @%s", (await bot.get_me()).username)
    try:
        await shop_main.dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
