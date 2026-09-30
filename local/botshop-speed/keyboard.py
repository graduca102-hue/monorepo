import asyncio
import contextlib
import html
from html.parser import HTMLParser
import logging
import math
import os
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import quote

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    CopyTextButton,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    WebAppInfo,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv
from vproxy_service import adjust_maskify_personal_user_traffic, get_maskify_user_remaining_gb
from favorites_service import list_favorite_product_ids, set_product_favorite
from partner_texts import partner_text_for
from partner_media import partner_banner_path

from database.data import (
    activate_promo_code,
    add_user,
    adjust_user_balance,
    create_sales_log_event,
    create_promo_code,
    delete_promo_code,
    get_all_franchises_stats,
    generate_api_key,
    get_api_account,
    list_pending_api_applications,
    review_api_application,
    submit_api_application,
    count_partner_bot_users,
    charge_user_balance_for_order,
    complete_market_order,
    complete_topup_payment,
    credit_order_to_user_balance,
    complete_order,
    count_user_orders,
    count_user_orders_any_status,
    create_topup,
    create_order,
    get_count,
    get_crm_stats,
    get_order,
    get_orders_stats,
    get_partner_bot_available_balance,
    get_partner_bot_by_id,
    get_partner_bot_by_token,
    get_main_bot_settings,
    get_main_bot_pricing_value,
    update_main_bot_pricing_value,
    get_partner_owner_summary,
    PARTNER_OWNER_REFERRAL_PERCENT,
    get_partner_program_stats,
    get_partner_bot_stats,
    get_partner_top_buyers,
    get_partner_bot_utm_stats,
    list_partner_bot_utm_links,
    get_topup,
    get_user_language,
    get_utm_stats,
    get_user_profile,
    get_loyalty_discount_info,
    get_money_referral_admin_report,
    get_money_referral_stats,
    give_all,
    list_active_partner_bots,
    list_pending_market_delivery_orders,
    list_pending_proxy_delivery_orders,
    list_recent_promo_codes,
    list_user_orders,
    list_user_orders_any_status,
    list_partner_bots_by_owner,
    list_partner_bot_user_ids,
    list_partner_referral_whitelist,
    mark_order_failed,
    mark_order_delivery_pending,
    mark_order_paid,
    mark_order_paid_from_invoice,
    mark_sales_log_event_failed,
    mark_sales_log_event_sent,
    record_partner_withdrawal,
    record_partner_referral_withdrawal,
    remove_partner_referral_whitelist,
    upsert_partner_bot_utm_link,
    upsert_partner_referral_whitelist,
    update_partner_referral_settings,
    update_partner_bot_margin,
    update_partner_franchise_setting,
    update_partner_bot_markup,
    update_partner_subscription_settings,
    update_main_bot_subscription_settings,
    update_order_supplier_data,
    update_topup_invoice,
    update_order_invoice,
    set_user_language,
)
from i18n import (
    DEFAULT_LANGUAGE,
    get_language_button_label,
    get_supported_language_codes,
    get_localized_market_category_label,
    localize_market_text,
    normalize_language_code,
    resolve_localized_text,
    tr,
)
from partner_runtime import get_partner_runtime
from services import (
    create_lolz_invoice,
    create_heleket_invoice,
    create_crystalpay_invoice,
    create_xrocket_transfer,
    MarketProviderError,
    get_xrocket_available_payment_currencies,
    get_heleket_payment,
    get_lolz_invoice,
    get_lolz_paid_invoice_fallback,
    HeleketError,
    CrystalPayError,
    is_heleket_invoice_paid,
    get_crystalpay_invoice,
    is_crystalpay_invoice_paid,
    is_lolz_invoice_paid,
    LolzError,
    PROXY_PROVIDER_CREATE_ATTEMPTS,
    PROXY_PROVIDER_CREATE_RETRY_DELAY_SECONDS,
    PROXY_PROVIDER_DETAILS_POLL_ATTEMPTS,
    PROXY_PROVIDER_DETAILS_POLL_INTERVAL_SECONDS,
    ProxyProviderError,
    PROXY_GENERIC_ERROR_CODE,
    XRocketError,
    create_market_order,
    create_proxy_provider_order,
    create_xrocket_invoice,
    download_text_file,
    filter_working_proxy_details,
    format_proxy_delivery,
    get_country_button_label,
    get_market_categories,
    get_market_category_by_id,
    get_market_order,
    get_market_order_file_url,
    get_market_product,
    get_market_products,
    get_proxy_category_by_id,
    get_proxy_provider_order,
    get_quality_label,
    get_static_proxy_categories,
    get_cached_market_rub_per_usdt,
    get_xrocket_invoice,
    is_market_order_creation_rejected,
    is_market_order_ready,
    is_market_email_category,
    is_market_category_available,
    is_xrocket_invoice_paid,
    market_goods_enabled,
    market_goods_notice,
)


load_dotenv()
logger = logging.getLogger(__name__)

REFERRAL_PERCENT = 50
MIN_PARTNER_REFERRAL_PERCENT = 10.0
MAX_PARTNER_REFERRAL_PERCENT = 100.0
XROCKET_PAYMENT_ASSET = os.getenv("XROCKET_PAYMENT_ASSET", "USDT")
PARTNER_MIN_WITHDRAW_AMOUNT = float(os.getenv("PARTNER_MIN_WITHDRAW_AMOUNT", "5"))
# Payment providers reject invoices below ~1 RUB. Keep top-ups at or above a
# small floor so the requested amount and the credited amount stay in sync
# (a top-up credits the amount the user asked for, not what the invoice was
# bumped to).
MIN_TOPUP_USD = float(os.getenv("MIN_TOPUP_USD", "0.10") or 0.10)
PARTNER_DEFAULT_MARGIN_PERCENT = int(float(os.getenv("PARTNER_DEFAULT_MARGIN_PERCENT", "50") or 50))
PROXY_PRICE_USD = 0.6
# Historical hard-coded defaults; live values are admin-editable and resolved at
# call time by market_markup_percent() / proxy_base_markup_percent() below.
PROXY_BASE_MARKUP_PERCENT_DEFAULT = 15
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
API_BASE_URL = f"{os.getenv('MINIAPP_BASE_URL', 'https://sousmarketfranchize.shop').rstrip('/')}/api/v1"
API_DOCS_URL = os.getenv("API_DOCS_URL", "https://sousmarketfranchize.shop/docs/#api").strip()
SALES_LOG_CHANNEL_ID = int(os.getenv("SALES_LOG_CHANNEL_ID", "-1003983969799") or -1003983969799)
SALES_LOG_HEADER = os.getenv("SALES_LOG_HEADER", "🏷 [LOG] SOUS FRAN").strip() or "🏷 [LOG] SOUS FRAN"
SHOP_MAIN_BRAND_NAME = os.getenv("MINIAPP_MAIN_BRAND_NAME", "SOUS MARKET").strip() or "SOUS MARKET"
# Per-owner franchise contact links.  These are intentionally scoped to the
# owner so the main bot and other franchisees keep their existing contacts.
FRANCHISE_OWNER_LINKS = {
    "1553147495": {
        "support": "https://t.me/afftraffsupport",
        "news": "https://t.me/+hV1vaHLU86pjZGIy",
    },
}
ORDERS_PAGE_SIZE = 5
MARKET_MARKUP_PERCENT_DEFAULT = float(os.getenv("MARKET_MARKUP_PERCENT", "15"))


def market_markup_percent() -> float:
    """Goods markup for the SOUS MARKET storefront (percent over supplier price)."""
    return get_main_bot_pricing_value("market_markup_percent", MARKET_MARKUP_PERCENT_DEFAULT)


def proxy_base_markup_percent() -> float:
    """Proxy-category markup for the storefront (percent over provider price)."""
    return get_main_bot_pricing_value("proxy_markup_percent", PROXY_BASE_MARKUP_PERCENT_DEFAULT)
MARKET_PRODUCTS_PAGE_SIZE = int(os.getenv("MARKET_PRODUCTS_PAGE_SIZE", "6"))
MARKET_ORDER_POLL_ATTEMPTS = int(os.getenv("MARKET_ORDER_POLL_ATTEMPTS", "300"))
MARKET_ORDER_POLL_INTERVAL_SECONDS = max(
    0.25,
    float(os.getenv("MARKET_ORDER_POLL_INTERVAL_SECONDS", "0.25") or 0.25),
)
XROCKET_CURRENCIES_PAGE_SIZE = 8
BANNERS_DIR = Path(__file__).resolve().parent / "assets" / "banners"
ORDER_FULFILLMENT_LOCKS: dict[int, asyncio.Lock] = {}
MARKET_ORDER_DELIVERY_TASKS: dict[int, asyncio.Task] = {}
PROXY_ORDER_DELIVERY_TASKS: dict[int, asyncio.Task] = {}
REPLY_ANCHOR_MESSAGE_IDS: dict[tuple[int, int], int] = {}
SCREEN_BANNERS = {
    "main_menu": BANNERS_DIR / "main_menu.jpg",
    "market": BANNERS_DIR / "market.jpg",
    "profile": BANNERS_DIR / "profile.jpg",
    "referral": BANNERS_DIR / "referral.jpg",
    "proxy": BANNERS_DIR / "proxy.png",
}


def resolve_screen_banner(bot: "Bot | None", banner_key: str | None) -> Path | None:
    """Custom partner-bot menu image for `banner_key`, else the shared default.

    Partner bots can replace each screen image in the SousPartners cabinet.
    When the update belongs to the main bot (or the partner set no image) the
    built-in `assets/banners` file is used, so behaviour is unchanged for
    everyone who does not touch that screen.
    """
    if not banner_key:
        return None
    if bot is not None:
        try:
            partner_bot = get_current_partner_bot(bot)
        except Exception:
            partner_bot = None
        if partner_bot is not None:
            custom = partner_banner_path(partner_bot, banner_key)
            if custom is not None:
                return custom
    default_path = SCREEN_BANNERS.get(banner_key)
    if default_path is not None and default_path.exists():
        return default_path
    return None


PROFILE_BUTTON_EMOJI_ID = "6032994772321309200"
PROXY_MINIAPP_BUTTON_EMOJI_ID = "5776233299424843260"
MONOCHROME_BUTTON_EMOJI_ID = "5778570255555105942"
PROXY_MONOCHROME_EMOJI_ID = "5776233299424843260"
MONOCHROME_MAIL_EMOJI_ID = "6030784887093464891"
MONOCHROME_SMS_EMOJI_ID = "5904248647972820334"
MONOCHROME_CATALOG_EMOJI_ID = "6028346797368203073"
VPN_BUTTON_EMOJI_ID = MONOCHROME_BUTTON_EMOJI_ID
MESSENGERS_BUTTON_EMOJI_ID = "5775870512127283512"
SOCIAL_BUTTON_EMOJI_ID = "6032994772321309200"
GAMES_BUTTON_EMOJI_ID = "6028338546736107668"
GOODS_BUTTON_EMOJI_ID = "5920332557466997677"
PARTNER_BOT_BUTTON_EMOJI_ID = "6030400221232501136"
PROFILE_PROMOCODE_BUTTON_EMOJI_ID = "5890883384057533697"
PROFILE_REFERRAL_BUTTON_EMOJI_ID = "6028171274939797252"
PROFILE_TOPUP_BUTTON_EMOJI_ID = "5769126056262898415"
PROFILE_ORDERS_BUTTON_EMOJI_ID = "5879814368572478751"
REFERRAL_INTRO_EMOJI_ID = "6028171274939797252"
REFERRAL_INVITED_EMOJI_ID = "6032609071373226027"
REFERRAL_EARNED_EMOJI_ID = "6037083366438737901"
REFERRAL_SHARE_BUTTON_EMOJI_ID = "6039451237743595514"
REFERRAL_COPY_BUTTON_EMOJI_ID = "6034969813032374911"
BACK_BUTTON_EMOJI_ID = "6039539366177541657"
LANGUAGE_BUTTON_EMOJI_ID = "5769403725898584391"
SUPPORT_BUTTON_EMOJI_ID = "6028346797368283073"
PROMOCODE_PROMPT_EMOJI_ID = "5890883384057533697"
REPLY_CATEGORY_BUTTON_EMOJI_ID = "6039630677182254664"
PARTNER_BASE_MARKUP_EMOJI_ID = "5938539885907415367"
PARTNER_PROFIT_EMOJI_ID = "6037083366438737901"
PARTNER_AVAILABLE_EMOJI_ID = "5920515922505765329"
PARTNER_WITHDRAWN_EMOJI_ID = "5769126056262898415"
PARTNER_STATS_EMOJI_ID = "5767288287001580715"
PROFILE_TITLE_EMOJI_ID = "5458412776152141722"
# The previous custom emoji document was removed by Telegram and caused
# DOCUMENT_INVALID for the entire profile message. Reuse the valid line icon.
PROFILE_FIRST_LINE_EMOJI_ID = "5458759367128026242"
PROFILE_LINE_EMOJI_ID = "5458759367128026242"
MARKET_CATEGORY_EMOJI_ID = REPLY_CATEGORY_BUTTON_EMOJI_ID
MARKET_PRODUCT_EMOJI_ID = GOODS_BUTTON_EMOJI_ID
MARKET_PRICE_EMOJI_ID = PARTNER_AVAILABLE_EMOJI_ID
MARKET_STOCK_EMOJI_ID = PROFILE_ORDERS_BUTTON_EMOJI_ID
MARKET_ATTRIBUTES_EMOJI_ID = PROMOCODE_PROMPT_EMOJI_ID
MARKET_DESCRIPTION_EMOJI_ID = PROFILE_LINE_EMOJI_ID
MARKET_QUANTITY_EMOJI_ID = PROFILE_FIRST_LINE_EMOJI_ID
# Telegram premium emoji matching the recognizable service marks selected for
# the mail catalogue (Gmail M, Rambler slash, Yandex Я, Mail.ru @, Outlook O,
# Proton M, Mail.com m and a neutral mail mark for iCloud).
MAIL_SERVICE_EMOJI_IDS = {
    39: "5375365468905313930",   # Gmail
    58: "5375209991089197355",   # Rambler
    54: "5375568487714429232",   # Yandex
    50: "5375511668622504238",   # Mail.ru
    63: "5373122228961452414",   # Outlook / Hotmail
    166: "5375295615557213960",  # Proton Mail
    66: "5373041350432301957",   # Mail.com
    149: "5371511566501744562",  # iCloud / mail
    65: "5375231521760252499",   # GMX
}
PAYMENT_BUTTON_EMOJI_ID = PROFILE_TOPUP_BUTTON_EMOJI_ID
PAYMENT_CHECK_EMOJI_ID = PROFILE_ORDERS_BUTTON_EMOJI_ID
SUBSCRIPTION_BUTTON_EMOJI_ID = SUPPORT_BUTTON_EMOJI_ID


def payment_button_text(value: str) -> str:
    """Premium buttons already have an icon; remove the duplicated Unicode icon."""
    return re.sub(r"^[^\w\d]+\s*", "", str(value), count=1, flags=re.UNICODE)


# Public payment label; internal provider remains crystalpay.
CRYPTOBOT_PROVIDER_LABEL = "Cryptobot"
PAYMENT_XROCKET_EMOJI_ID = "5341788140434637169"
PAYMENT_LOLZ_EMOJI_ID = "5388960547930678866"
PAYMENT_HELEKET_EMOJI_ID = "5328161038133133296"
PAYMENT_BALANCE_EMOJI_ID = "5388898833545597646"

user = Router()
ALL_PRODUCTS_REPLY_TEXT = "Ассортимент всех товаров"
# The catalogue is opened directly from the reply keyboard as a Telegram Web App.
# Keep the URL configurable so deployments can point at a different catalogue host.
ALL_PRODUCTS_SITE_URL = (
    os.getenv("ALL_PRODUCTS_SITE_URL")
    or f"{os.getenv('MINIAPP_BASE_URL', 'https://sousmarketfranchize.shop').rstrip('/')}/assortment/"
)
MARKET_WEBAPP_URL = (
    os.getenv("MARKET_WEBAPP_URL")
    or f"{os.getenv('MINIAPP_BASE_URL', 'https://sousmarketfranchize.shop').rstrip('/')}/market/"
)


def is_catalog_preview_callback(data: str | None) -> bool:
    value = str(data or "")
    return (
        value in {"magazine", "market_mail_menu", "market_messengers_menu", "market_social_menu", "market_games_menu"}
        or value.startswith(
            ("market_favorites", "market_category:", "market_products:", "market_product:")
        )
    )

REPLY_MENU_ACTIONS: dict[str, str] = {}
for _reply_language in get_supported_language_codes():
    REPLY_MENU_ACTIONS.update(
        {
            tr(_reply_language, "reply.category"): "category",
            tr(_reply_language, "menu.profile"): "profile",
            tr(_reply_language, "menu.language"): "language",
            tr(_reply_language, "menu.partner_program"): "partner",
            tr(_reply_language, "reply.information"): "information",
            "Скидки": "discounts",
            ALL_PRODUCTS_REPLY_TEXT: "all_products",
        }
    )
REPLY_MENU_ACTIONS["Создать своего бота"] = "partner"


class ProxyPurchaseState(StatesGroup):
    waiting_quantity = State()
    waiting_topup_amount = State()
    waiting_api_topup_amount = State()
    waiting_account_quantity = State()


class AdminState(StatesGroup):
    waiting_broadcast_message = State()
    waiting_broadcast_buttons = State()
    waiting_traffic_adjustment = State()
    waiting_franchises_broadcast_message = State()
    waiting_franchises_broadcast_buttons = State()
    waiting_franchise_setting_value = State()
    waiting_balance_adjustment = State()
    waiting_api_balance_adjustment = State()
    waiting_main_subscription_channel_id = State()
    waiting_main_subscription_channel_confirm = State()
    waiting_main_subscription_channel_url = State()
    waiting_promo_balance_data = State()
    waiting_promo_discount_data = State()
    waiting_promo_delete_code = State()
    waiting_pricing_value = State()


class PartnerBotState(StatesGroup):
    waiting_bot_token = State()
    waiting_margin_value = State()
    waiting_markup_value = State()


class PartnerAdminState(StatesGroup):
    waiting_broadcast_message = State()
    waiting_broadcast_buttons = State()
    waiting_margin_value = State()
    waiting_subscription_channel_id = State()
    waiting_subscription_channel_url = State()
    waiting_referral_percent = State()
    waiting_referral_whitelist_add = State()
    waiting_referral_whitelist_remove = State()
    waiting_utm_link_data = State()


class PartnerCabinetState(StatesGroup):
    waiting_broadcast_message = State()
    waiting_broadcast_buttons = State()
    waiting_subscription_channel_id = State()
    waiting_subscription_channel_confirm = State()
    waiting_subscription_channel_url = State()
    waiting_referral_percent = State()
    waiting_referral_whitelist_add = State()
    waiting_referral_whitelist_remove = State()
    waiting_utm_link_data = State()


class ProfileState(StatesGroup):
    waiting_promo_code = State()


def resolve_language_code(
    *,
    language_code: str | None = None,
    user_id: int | None = None,
    profile: dict | None = None,
) -> str:
    if language_code:
        return normalize_language_code(language_code)
    if profile and profile.get("language_code"):
        return normalize_language_code(profile.get("language_code"))
    if user_id is not None:
        return get_user_language(user_id)
    return DEFAULT_LANGUAGE


def get_event_language_code(event: Message | CallbackQuery, profile: dict | None = None) -> str:
    user_id = event.from_user.id if getattr(event, "from_user", None) else None
    if profile and profile.get("language_code"):
        return resolve_language_code(profile=profile)
    if user_id is not None:
        return get_user_language(user_id)
    telegram_language_code = getattr(getattr(event, "from_user", None), "language_code", None)
    return resolve_language_code(language_code=telegram_language_code)


def text_key(language_code: str | None, key: str, **kwargs) -> str:
    return tr(resolve_language_code(language_code=language_code), key, **kwargs)


class LanguageSyncMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        user_obj = data.get("event_from_user")
        if user_obj is not None:
            add_user(int(user_obj.id), language_code=getattr(user_obj, "language_code", None))
            data["ui_language"] = get_user_language(int(user_obj.id))
        return await handler(event, data)


class PartnerSubscriptionMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        bot = data.get("bot")
        if bot is None:
            return await handler(event, data)

        partner_bot = get_current_partner_bot(bot)
        if partner_bot is None or int(partner_bot.get("subscription_enabled") or 0) != 1:
            return await handler(event, data)

        user_obj = data.get("event_from_user")
        if user_obj is None:
            return await handler(event, data)

        if int(partner_bot.get("owner_id") or 0) == int(user_obj.id):
            return await handler(event, data)

        channel_id = (partner_bot.get("subscription_channel_id") or "").strip()
        if not channel_id:
            return await handler(event, data)

        if isinstance(event, CallbackQuery) and (
            event.data == "partner_subscription_check" or is_catalog_preview_callback(event.data)
        ):
            return await handler(event, data)

        if isinstance(event, Message) and (
            (event.text or "").startswith("/start") or (event.text or "") == ALL_PRODUCTS_REPLY_TEXT
        ):
            return await handler(event, data)

        # Acknowledge the button immediately; the membership check may need a
        # Telegram API round-trip and must not leave the client spinner active.
        if isinstance(event, CallbackQuery):
            with contextlib.suppress(Exception):
                await event.answer()
        subscription_status = await is_user_subscribed_to_channel(bot, channel_id, user_obj.id)
        if subscription_status is not False:
            return await handler(event, data)
        lang = get_user_language(int(user_obj.id))

        if isinstance(event, CallbackQuery):
            await event.answer(
                tr(lang, "generic.subscription_required", channel=get_partner_subscription_channel_label(partner_bot)),
                show_alert=True,
            )
            if event.message:
                await render_screen(
                    event.message,
                    build_partner_subscription_gate_text(partner_bot, lang),
                    reply_markup=build_partner_subscription_gate_keyboard(partner_bot, lang),
                    disable_web_page_preview=True,
                )
            return None

        if isinstance(event, Message):
            await event.answer(
                "\u2063",
                reply_markup=build_subscription_reply_menu(),
            )
            await event.answer(
                build_partner_subscription_gate_text(partner_bot, lang),
                reply_markup=build_partner_subscription_gate_keyboard(partner_bot, lang),
                disable_web_page_preview=True,
            )
            return None

        return await handler(event, data)


class MainSubscriptionMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        bot = data.get("bot")
        if bot is None or get_current_partner_bot(bot) is not None:
            return await handler(event, data)

        settings = get_main_bot_settings()
        if int(settings.get("subscription_enabled") or 0) != 1:
            return await handler(event, data)

        user_obj = data.get("event_from_user")
        if user_obj is None or is_admin_user(int(user_obj.id)):
            return await handler(event, data)

        channel_id = (settings.get("subscription_channel_id") or "").strip()
        if not channel_id:
            return await handler(event, data)

        if isinstance(event, CallbackQuery) and (
            event.data == "main_subscription_check" or is_catalog_preview_callback(event.data)
        ):
            return await handler(event, data)

        if isinstance(event, Message) and (
            (event.text or "").startswith("/start") or (event.text or "") == ALL_PRODUCTS_REPLY_TEXT
        ):
            return await handler(event, data)

        if isinstance(event, CallbackQuery):
            with contextlib.suppress(Exception):
                await event.answer()
        subscription_status = await is_user_subscribed_to_channel(bot, channel_id, user_obj.id)
        if subscription_status is not False:
            return await handler(event, data)
        lang = get_user_language(int(user_obj.id))

        if isinstance(event, CallbackQuery):
            await event.answer(
                tr(lang, "generic.subscription_required", channel=get_main_subscription_channel_label(settings)),
                show_alert=True,
            )
            if event.message:
                await render_screen(
                    event.message,
                    build_main_subscription_gate_text(settings, lang),
                    reply_markup=build_main_subscription_gate_keyboard(settings, lang),
                    disable_web_page_preview=True,
                )
            return None

        if isinstance(event, Message):
            await event.answer(
                "\u2063",
                reply_markup=build_subscription_reply_menu(),
            )
            await event.answer(
                build_main_subscription_gate_text(settings, lang),
                reply_markup=build_main_subscription_gate_keyboard(settings, lang),
                disable_web_page_preview=True,
            )
            return None

        return await handler(event, data)


user.message.middleware(LanguageSyncMiddleware())
user.callback_query.middleware(LanguageSyncMiddleware())
user.message.middleware(MainSubscriptionMiddleware())
user.callback_query.middleware(MainSubscriptionMiddleware())
user.message.middleware(PartnerSubscriptionMiddleware())
user.callback_query.middleware(PartnerSubscriptionMiddleware())


def main_menu(
    bot: Bot | None = None,
    *,
    user_id: int | None = None,
    language_code: str | None = None,
) -> InlineKeyboardMarkup:
    lang = resolve_language_code(language_code=language_code, user_id=user_id)
    current_partner = get_current_partner_bot(bot)
    rows = [list(row) for row in build_product_sections_keyboard(
        bot,
        user_id=user_id,
        language_code=lang,
    ).inline_keyboard]
    partner_row: list[InlineKeyboardButton] = []
    if current_partner is not None and str(current_partner.get("bot_username") or "").casefold() != "souspartnersbot":
        if int(current_partner.get("franchise_hide_create", 0) or 0) == 0:
            if build_partner_referral_link(current_partner):
                partner_row = [
                    InlineKeyboardButton(
                        text="Создать своего бота",
                        callback_data="partner_bot_invite",
                        icon_custom_emoji_id=PARTNER_BOT_BUTTON_EMOJI_ID,
                    )
                ]
    else:
        partner_row = [
            InlineKeyboardButton(
                text=tr(lang, "menu.partner_program"),
                callback_data="partner_program",
                icon_custom_emoji_id=PARTNER_BOT_BUTTON_EMOJI_ID,
            )
        ]
    rows.extend(
        [
            partner_row,
            [
                InlineKeyboardButton(
                    text=tr(lang, "menu.profile"),
                    callback_data="profile",
                    icon_custom_emoji_id=PROFILE_BUTTON_EMOJI_ID,
                )
            ],
            [InlineKeyboardButton(text=tr(lang, "menu.language"), callback_data="language_menu")],
        ]
    )
    # Support button: SOUS support on the main bot; on a franchise bot only the
    # partner's own link (or a per-owner override). Empty -> no button.
    if current_partner is not None:
        _fr_links = FRANCHISE_OWNER_LINKS.get(str(current_partner.get("owner_id") or ""), {})
        support_url = (current_partner.get("franchise_support_url") or _fr_links.get("support") or "").strip()
    else:
        support_url = "https://t.me/UniversallSupportBot?start=market"
    if support_url:
        rows.append([InlineKeyboardButton(text=tr(lang, "menu.support"), url=support_url)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_reply_menu(
    *,
    user_id: int | None = None,
    language_code: str | None = None,
    bot: Bot | None = None,
) -> ReplyKeyboardMarkup:
    lang = resolve_language_code(language_code=language_code, user_id=user_id)
    current_partner = get_current_partner_bot(bot)
    partner_button_text = tr(lang, "menu.partner_program")
    if (
        current_partner is not None
        and str(current_partner.get("bot_username") or "").casefold() != "souspartnersbot"
        and int(current_partner.get("franchise_hide_create", 0) or 0) == 0
    ):
        partner_button_text = "Создать своего бота"
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text=tr(lang, "reply.category"),
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
                    text=partner_button_text,
                    icon_custom_emoji_id=PARTNER_BOT_BUTTON_EMOJI_ID,
                ),
                KeyboardButton(text="Скидки"),
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


def build_subscription_reply_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text=ALL_PRODUCTS_REPLY_TEXT,
                    web_app=WebAppInfo(url=ALL_PRODUCTS_SITE_URL),
                    icon_custom_emoji_id=GOODS_BUTTON_EMOJI_ID,
                )
            ]
        ],
        is_persistent=True,
        resize_keyboard=True,
    )


def build_product_sections_keyboard(
    bot: Bot | None = None,
    *,
    user_id: int | None = None,
    language_code: str | None = None,
) -> InlineKeyboardMarkup:
    from miniapp import build_email_url, build_miniapp_url, build_sms_url

    lang = resolve_language_code(language_code=language_code, user_id=user_id)
    partner_bot = get_current_partner_bot(bot)
    bot_username = str(partner_bot.get("bot_username") or "").strip() if partner_bot else None
    miniapp_url = build_miniapp_url(bot_username)
    email_url = build_email_url(bot_username)
    sms_url = build_sms_url(bot_username)

    def _btn(text_key: str) -> str:
        return partner_text_for(partner_bot, text_key)

    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=_btn("btn_favorites"), callback_data="market_favorites")],
        [
            InlineKeyboardButton(
                text=_btn("btn_proxy"),
                callback_data="proxy_test:home",
                icon_custom_emoji_id=PROXY_MONOCHROME_EMOJI_ID,
                style="success",
            )
        ],
        [
            InlineKeyboardButton(text=_btn("btn_vpn"), callback_data="market_category:75", icon_custom_emoji_id=VPN_BUTTON_EMOJI_ID),
            InlineKeyboardButton(text=_btn("btn_mail"), callback_data="market_mail_menu", icon_custom_emoji_id=MONOCHROME_MAIL_EMOJI_ID),
        ],
        [
            InlineKeyboardButton(
                text=_btn("btn_messengers"),
                callback_data="market_messaging_social_menu",
                icon_custom_emoji_id=MESSENGERS_BUTTON_EMOJI_ID,
            ),
        ],
        [InlineKeyboardButton(text=_btn("btn_games"), callback_data="market_games_menu", icon_custom_emoji_id=GAMES_BUTTON_EMOJI_ID)],
    ]

    temporary_service_row: list[InlineKeyboardButton] = []
    if email_url:
        temporary_service_row.append(
            InlineKeyboardButton(
                text=_btn("btn_temp_mail"),
                web_app=WebAppInfo(url=email_url),
                icon_custom_emoji_id=MONOCHROME_MAIL_EMOJI_ID,
            )
        )
    if sms_url:
        temporary_service_row.append(
            InlineKeyboardButton(
                text=_btn("btn_temp_sms"),
                web_app=WebAppInfo(url=sms_url),
                icon_custom_emoji_id=MONOCHROME_SMS_EMOJI_ID,
            )
        )
    if temporary_service_row:
        rows.append(temporary_service_row)
    if miniapp_url:
        rows.append(
            [
                InlineKeyboardButton(
                    text=_btn("btn_catalog"),
                    web_app=WebAppInfo(url=miniapp_url),
                    icon_custom_emoji_id=MONOCHROME_CATALOG_EMOJI_ID,
                )
            ]
        )
    rows = [row for row in rows if row]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_welcome_text(language_code: str | None = None) -> str:
    # Telegram requires non-empty text/captions; this character is invisible.
    return "\u2063"


def build_language_menu_text(language_code: str | None = None) -> str:
    lang = resolve_language_code(language_code=language_code)
    return (
        f"{tr(lang, 'language.title')}\n\n"
        f"{tr(lang, 'language.current', language=get_language_button_label(lang))}"
    )


def build_language_keyboard(current_language_code: str | None = None) -> InlineKeyboardMarkup:
    current = resolve_language_code(language_code=current_language_code)
    rows = []
    for language_code in get_supported_language_codes():
        label = get_language_button_label(language_code)
        if language_code == current:
            label = f"✅ {label}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"language_set:{language_code}")])
    rows.append([InlineKeyboardButton(text=tr(current, "common.back"), callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _safe_delete_message(message: Message) -> None:
    with contextlib.suppress(Exception):
        await message.delete()


def _message_has_media(message: Message) -> bool:
    return bool(getattr(message, "photo", None) or getattr(message, "document", None))


def _strip_button_prefix(text: str, prefixes: tuple[str, ...]) -> str:
    value = str(text or "").strip()
    for prefix in prefixes:
        if value.startswith(prefix):
            return value[len(prefix):].lstrip()
    return value


def decorate_common_keyboard(
    reply_markup: InlineKeyboardMarkup | ReplyKeyboardMarkup | None,
) -> InlineKeyboardMarkup | ReplyKeyboardMarkup | None:
    if reply_markup is None or not isinstance(reply_markup, InlineKeyboardMarkup):
        return reply_markup

    rows: list[list[InlineKeyboardButton]] = []
    for row in reply_markup.inline_keyboard:
        decorated_row: list[InlineKeyboardButton] = []
        for button in row:
            updates: dict[str, Any] = {}
            text = str(button.text or "")
            normalized_text = _strip_button_prefix(
                text,
                ("◀️", "◀", "⬅️", "⬅", "🗣️", "🗣", "🌐", "✉️", "✉", "🆘"),
            )
            if text.startswith(("◀️", "◀", "⬅️", "⬅")):
                # Telegram rejects inline buttons whose text becomes empty.
                # Keep icon-only pagination arrows unchanged; decorate buttons
                # only when a real label remains after stripping the prefix.
                if normalized_text:
                    updates = {"text": normalized_text, "icon_custom_emoji_id": BACK_BUTTON_EMOJI_ID}
            elif button.callback_data == "language_menu":
                updates = {"text": normalized_text, "icon_custom_emoji_id": LANGUAGE_BUTTON_EMOJI_ID}
            elif str(button.url or "").rstrip("/") == "https://t.me/UniversallSupportBot?start=market":
                updates = {"text": normalized_text, "icon_custom_emoji_id": SUPPORT_BUTTON_EMOJI_ID}
            elif str(button.callback_data or "").startswith("market_product:"):
                updates = {"icon_custom_emoji_id": MARKET_PRODUCT_EMOJI_ID}
            elif str(button.callback_data or "").startswith("market_products:"):
                updates = {"icon_custom_emoji_id": MARKET_CATEGORY_EMOJI_ID}
            elif str(button.callback_data or "").startswith("market_qty:"):
                updates = {"icon_custom_emoji_id": MARKET_QUANTITY_EMOJI_ID}
            elif str(button.callback_data or "").startswith("market_qty_custom:"):
                updates = {"icon_custom_emoji_id": MARKET_QUANTITY_EMOJI_ID}
            elif str(button.callback_data or "").startswith("market_favorite"):
                updates = {"icon_custom_emoji_id": PROFILE_REFERRAL_BUTTON_EMOJI_ID}
            if updates.get("icon_custom_emoji_id") and button.icon_custom_emoji_id:
                # A service-specific logo is more useful than the generic
                # catalogue icon added by this common decorator.
                updates["icon_custom_emoji_id"] = button.icon_custom_emoji_id
            decorated_row.append(button.model_copy(update=updates) if updates else button)
        rows.append(decorated_row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def render_screen(
    target_message: Message,
    text: str,
    reply_markup: InlineKeyboardMarkup | ReplyKeyboardMarkup | None = None,
    *,
    banner: str | None = None,
    parse_mode: str | None = None,
    disable_web_page_preview: bool = False,
) -> Message:
    decorated_markup = decorate_common_keyboard(reply_markup)
    if decorated_markup is not None:
        reply_markup = decorated_markup
    is_reply_keyboard = isinstance(reply_markup, ReplyKeyboardMarkup)
    banner_path = resolve_screen_banner(getattr(target_message, "bot", None), banner or "")
    if banner_path is not None and banner_path.exists():
        if getattr(target_message, "photo", None) and not is_reply_keyboard:
            try:
                return await target_message.edit_media(
                    media=InputMediaPhoto(
                        media=FSInputFile(str(banner_path)),
                        caption=text,
                        parse_mode=parse_mode,
                    ),
                    reply_markup=reply_markup,
                )
            except TelegramBadRequest as error:
                if "message is not modified" in str(error).lower():
                    return target_message
                # Telegram cannot always replace media in-place (for example,
                # messages sent as documents). Recreate the screen below.
                if not any(
                    marker in str(error).lower()
                    for marker in ("document_invalid", "media_invalid", "wrong type of the web page content")
                ):
                    raise

        sent_message = await target_message.answer_photo(
            photo=FSInputFile(str(banner_path)),
            caption=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
        await _safe_delete_message(target_message)
        return sent_message

    target_is_bot_message = bool(getattr(getattr(target_message, "from_user", None), "is_bot", False))
    if _message_has_media(target_message) or is_reply_keyboard or not target_is_bot_message:
        sent_message = await target_message.answer(
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview,
        )
        await _safe_delete_message(target_message)
        return sent_message

    try:
        return await target_message.edit_text(
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview,
        )
    except TelegramBadRequest as error:
        error_text = str(error).lower()
        if "message is not modified" in error_text:
            return target_message
        if "message to edit not found" in error_text or "message can't be edited" in error_text:
            # Fulfillment continues in the background and sends the terminal
            # result as a new message. A deleted/stale progress message must
            # never abort the order worker.
            return None
        raise


async def render_main_menu_screen(
    target_message: Message,
    user_id: int,
    bot: Bot | None = None,
    *,
    language_code: str | None = None,
    refresh_reply_keyboard: bool = False,
) -> Message:
    lang = resolve_language_code(language_code=language_code, user_id=user_id)
    bot_client = bot or target_message.bot
    inline_menu = build_product_sections_keyboard(
        bot_client,
        user_id=user_id,
        language_code=lang,
    )
    current_partner = get_current_partner_bot(bot_client)
    if (
        current_partner is not None
        and str(current_partner.get("bot_username") or "").casefold() != "souspartnersbot"
        and int(current_partner.get("franchise_hide_create", 0) or 0) == 0
    ):
        if build_partner_referral_link(current_partner):
            inline_menu = InlineKeyboardMarkup(
                inline_keyboard=[
                    *inline_menu.inline_keyboard,
                    [
                        InlineKeyboardButton(
                            text="Создать своего бота",
                            callback_data="partner_bot_invite",
                            icon_custom_emoji_id=PARTNER_BOT_BUTTON_EMOJI_ID,
                        )
                    ],
                ]
            )
    inline_menu = decorate_common_keyboard(inline_menu)
    if not refresh_reply_keyboard:
        return await render_screen(
            target_message,
            build_welcome_text(lang),
            reply_markup=inline_menu,
            banner="main_menu",
        )

    anchor_key = (int(bot_client.id), int(target_message.chat.id))
    previous_anchor_id = REPLY_ANCHOR_MESSAGE_IDS.pop(anchor_key, None)
    if previous_anchor_id and previous_anchor_id != target_message.message_id:
        with contextlib.suppress(Exception):
            await bot_client.delete_message(target_message.chat.id, previous_anchor_id)
    # Telegram cannot attach Reply and inline keyboards to one message. Install
    # the persistent Reply keyboard first, then send a separate visible screen
    # after it. The short anchor must stay in chat or Telegram hides the Reply.
    keyboard_message = await target_message.answer(
        tr(lang, "menu.anchor"),
        reply_markup=build_reply_menu(user_id=user_id, language_code=lang, bot=bot_client),
    )
    REPLY_ANCHOR_MESSAGE_IDS[anchor_key] = int(keyboard_message.message_id)
    banner_path = resolve_screen_banner(bot_client, "main_menu") or SCREEN_BANNERS["main_menu"]
    screen_message = await keyboard_message.answer_photo(
        photo=FSInputFile(str(banner_path)),
        caption=build_welcome_text(lang),
        reply_markup=inline_menu,
    )
    await _safe_delete_message(target_message)
    return screen_message


async def safe_edit_text(
    target_message: Message | None,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = None,
    disable_web_page_preview: bool = False,
) -> Message | None:
    if target_message is None:
        return None

    reply_markup = decorate_common_keyboard(reply_markup)
    try:
        return await target_message.edit_text(
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview,
        )
    except TelegramBadRequest as error:
        if "message is not modified" in str(error).lower():
            return target_message
        raise


def truncate_text(value: str, limit: int = 64) -> str:
    cleaned = re.sub(r"\s+", " ", (value or "").strip())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def premium_emoji(custom_emoji_id: str, fallback_emoji: str) -> str:
    return f'<tg-emoji emoji-id="{custom_emoji_id}">{fallback_emoji}</tg-emoji>'


def convert_rub_to_usdt(amount_rub: float, margin_percentage: int | None = None) -> float:
    rate = get_cached_market_rub_per_usdt()
    base_amount = float(amount_rub or 0) / rate
    effective_margin = market_markup_percent() if margin_percentage is None else margin_percentage
    return calculate_sale_price_from_base_usdt(base_amount, int(effective_margin))


def format_rub_and_usdt(amount_rub: float, margin_percentage: int | None = None) -> str:
    amount_usdt = convert_rub_to_usdt(amount_rub, margin_percentage)
    return f"{amount_usdt:.2f} $"


class _MarketDescriptionParser(HTMLParser):
    """Turn supplier HTML into readable Telegram paragraphs and lists."""

    BLOCK_TAGS = {"p", "div", "section", "article", "ul", "ol"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag == "br":
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n• ")
        elif tag in self.BLOCK_TAGS:
            self.parts.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self.BLOCK_TAGS | {"li"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def clean_html_text(value: str) -> str:
    parser = _MarketDescriptionParser()
    try:
        parser.feed(value or "")
        text = "".join(parser.parts)
    except Exception:
        text = re.sub(r"<[^>]+>", " ", value or "")
    # Some providers double-encode entities, so unescape twice. In particular
    # this removes visible "&nbsp;" leftovers from product descriptions.
    text = html.unescape(html.unescape(text)).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    paragraphs: list[str] = []
    blank = False
    for line in lines:
        if not line:
            if paragraphs and not blank:
                paragraphs.append("")
            blank = True
            continue
        paragraphs.append(line)
        blank = False
    return "\n".join(paragraphs).strip()


def truncate_multiline_text(value: str, limit: int) -> str:
    cleaned = (value or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def strip_supplier_decorations(value: str) -> str:
    """Remove supplier arrows and decorative emoji without touching text."""
    result: list[str] = []
    skip_joined = False
    for char in str(value or ""):
        codepoint = ord(char)
        category = unicodedata.category(char)
        is_emoji_symbol = category == "So" or 0x1F000 <= codepoint <= 0x1FAFF
        is_emoji_modifier = (
            0xFE00 <= codepoint <= 0xFE0F
            or 0x1F3FB <= codepoint <= 0x1F3FF
            or codepoint == 0x200D
            or codepoint == 0x20E3
        )
        if is_emoji_symbol or is_emoji_modifier:
            skip_joined = True
            continue
        if char in "►▶▷▸➤➜➡→★☆✓✔✅❌":
            continue
        if skip_joined and category.startswith("M"):
            continue
        skip_joined = False
        result.append(char)
    cleaned = re.sub(r"[ \t]{2,}", " ", "".join(result)).strip(" |—–-\n\t")
    # A few upstream emoji are already replaced by a literal question mark
    # before reaching us. Remove it only when it is a decorative prefix.
    cleaned = re.sub(r"^\?+\s+", "", cleaned)
    return cleaned


def is_account_order(order: dict) -> bool:
    return order.get("proxy_kind") == "account"


def get_current_partner_bot(bot: Bot | None) -> dict | None:
    if bot is None:
        return None
    return get_partner_bot_by_token(bot.token)


def get_owned_current_partner_bot(bot: Bot | None, user_id: int) -> dict | None:
    partner_bot = get_current_partner_bot(bot)
    if partner_bot is None:
        return None
    return partner_bot if int(partner_bot.get("owner_id") or 0) == int(user_id) else None


def get_owned_partner_bot_by_id(partner_id: int, owner_id: int) -> dict | None:
    partner_bot = get_partner_bot_by_id(partner_id)
    if partner_bot is None:
        return None
    return partner_bot if int(partner_bot.get("owner_id") or 0) == int(owner_id) else None


def is_entity_owned_by_user(entity: dict | None, user_id: int) -> bool:
    if entity is None:
        return False
    return int(entity.get("user_id") or 0) == int(user_id)


def get_subscription_channel_label(
    channel_id: str | None,
    channel_url: str | None,
    channel_title: str | None = None,
    channel_username: str | None = None,
) -> str:
    normalized_channel_id = (channel_id or "").strip()
    normalized_channel_url = (channel_url or "").strip()
    normalized_channel_title = (channel_title or "").strip()
    normalized_channel_username = (channel_username or "").strip().lstrip("@")
    if normalized_channel_username:
        return f"@{normalized_channel_username}"
    if normalized_channel_title:
        return normalized_channel_title
    if normalized_channel_id.startswith("@"):
        return normalized_channel_id
    if (
        normalized_channel_url.startswith("https://t.me/+")
        or normalized_channel_url.startswith("http://t.me/+")
        or normalized_channel_url.startswith("https://t.me/joinchat/")
        or normalized_channel_url.startswith("http://t.me/joinchat/")
    ):
        return normalized_channel_url
    if normalized_channel_url.startswith("https://t.me/") or normalized_channel_url.startswith("http://t.me/"):
        slug = normalized_channel_url.rstrip("/").rsplit("/", 1)[-1].strip()
        if slug:
            return f"@{slug}"
    if normalized_channel_url:
        return normalized_channel_url
    if normalized_channel_id:
        return normalized_channel_id
    return "указанный канал"


def get_partner_subscription_channel_label(partner_bot: dict) -> str:
    return get_subscription_channel_label(
        partner_bot.get("subscription_channel_id"),
        partner_bot.get("subscription_channel_url"),
        partner_bot.get("subscription_channel_title"),
        partner_bot.get("subscription_channel_username"),
    )


def get_main_subscription_channel_label(settings: dict) -> str:
    return get_subscription_channel_label(
        settings.get("subscription_channel_id"),
        settings.get("subscription_channel_url"),
        settings.get("subscription_channel_title"),
        settings.get("subscription_channel_username"),
    )


# Mandatory subscription is checked on every user action. Successful results
# must not be cached: a user may leave the channel immediately after passing
# the gate and should lose access on the very next message or button press.
_SUBSCRIPTION_SUCCESS_TTL_SECONDS = 5 * 60
_subscription_success_cache: dict[tuple[int, str, int], float] = {}


async def is_user_subscribed_to_channel(bot: Bot, channel_id: str | None, user_id: int) -> bool | None:
    normalized_channel_id = (channel_id or "").strip()
    if not normalized_channel_id:
        return True

    cache_key = (int(bot.id), normalized_channel_id, int(user_id))
    now = time.monotonic()
    if _SUBSCRIPTION_SUCCESS_TTL_SECONDS > 0 and _subscription_success_cache.get(cache_key, 0.0) > now:
        return True

    async def fetch_status() -> bool | None:
        try:
            member = await bot.get_chat_member(chat_id=normalized_channel_id, user_id=user_id)
        except Exception as error:
            logger.warning(
                "Failed to verify channel subscription: channel_id=%s user_id=%s error=%s",
                normalized_channel_id,
                user_id,
                error,
            )
            return None
        member_status = getattr(member, "status", "")
        normalized_status = str(getattr(member_status, "value", member_status)).lower()
        return normalized_status not in {"left", "kicked"}

    first_status = await fetch_status()
    if first_status is True:
        if _SUBSCRIPTION_SUCCESS_TTL_SECONDS > 0:
            _subscription_success_cache[cache_key] = now + _SUBSCRIPTION_SUCCESS_TTL_SECONDS
        return True
    if first_status is None:
        return None

    # A single negative Bot API response must not randomly lock an already
    # subscribed user out. Confirm it once before showing the subscription gate.
    await asyncio.sleep(0.25)
    confirmed_status = await fetch_status()
    if confirmed_status is True:
        if _SUBSCRIPTION_SUCCESS_TTL_SECONDS > 0:
            _subscription_success_cache[cache_key] = time.monotonic() + _SUBSCRIPTION_SUCCESS_TTL_SECONDS
        return True
    if confirmed_status is None:
        return None

    _subscription_success_cache.pop(cache_key, None)
    return False


def get_partner_shop_markup(partner_bot: dict | None) -> int:
    if partner_bot is None:
        return int(market_markup_percent())

    raw_value = partner_bot.get("shop_markup_percentage")
    if raw_value is None or raw_value == "":
        return int(market_markup_percent())

    return int(raw_value)


def get_partner_category_markup(partner_bot: dict | None, kind: str) -> int:
    if partner_bot is None:
        return 0
    field = {
        "goods": "goods_markup_percentage",
        "proxy": "proxy_markup_percentage",
        "sms": "sms_markup_percentage",
    }.get(kind, "margin_percentage")
    value = partner_bot.get(field)
    if value is None or value == "":
        value = partner_bot.get("margin_percentage")
    return max(int(value or 0), 0)


def get_effective_market_margin(bot: Bot | None) -> int:
    partner_bot = get_current_partner_bot(bot)
    if partner_bot is not None:
        return get_partner_shop_markup(partner_bot) + get_partner_category_markup(partner_bot, "goods")
    return int(market_markup_percent())


def get_proxy_purchase_unit_price(provider_price_rub: float) -> float:
    rate = get_cached_market_rub_per_usdt()
    base_amount = float(provider_price_rub or 0.0) / rate
    return max(round(base_amount, 4), 0.01)


def get_effective_proxy_unit_price(bot: Bot | None, provider_price_rub: float) -> float:
    partner_bot = get_current_partner_bot(bot)
    franchise_markup_percentage = get_partner_category_markup(partner_bot, "proxy")
    return calculate_sale_price_from_base_usdt(
        get_proxy_purchase_unit_price(provider_price_rub),
        proxy_base_markup_percent() + franchise_markup_percentage,
    )


def calculate_sale_price_from_base_usdt(base_price_usdt: float, margin_percentage: int) -> float:
    raw_amount = float(base_price_usdt or 0) * (1 + max(margin_percentage, 0) / 100)
    if raw_amount <= 0:
        return 0.0
    return max(math.ceil(raw_amount * 100) / 100, 0.01)


def calculate_partner_profit_share_amount(
    purchase_unit_price: float,
    quantity: int,
    margin_percentage: int,
) -> float:
    clean_profit_usdt = float(purchase_unit_price or 0) * max(margin_percentage, 0) / 100 * max(quantity, 0)
    return round(clean_profit_usdt, 2)


def calculate_owner_purchase_income(order: dict, partner_bot: dict | None) -> float:
    if partner_bot is not None:
        return round(max(float(order.get("partner_profit_amount") or 0.0), 0.0), 2)

    quantity = max(int(order.get("quantity") or 0), 0)
    supplier_cost_total = float(order.get("purchase_unit_price") or 0.0) * quantity
    return round(max(float(order.get("total_price") or 0.0) - supplier_cost_total, 0.0), 2)


def format_shop_name(bot: Bot | None, partner_bot: dict | None = None) -> str:
    resolved_partner_bot = partner_bot if partner_bot is not None else get_current_partner_bot(bot)
    if resolved_partner_bot is None:
        return SHOP_MAIN_BRAND_NAME

    raw_username = str(resolved_partner_bot.get("bot_username") or "").strip().lstrip("@")
    raw_username = re.sub(r"bot$", "", raw_username, flags=re.IGNORECASE)
    raw_username = re.sub(r"(?<=[a-zа-я])(?=[A-ZА-Я])", " ", raw_username)
    raw_username = re.sub(r"[_\-]+", " ", raw_username)
    raw_username = re.sub(r"\s+", " ", raw_username).strip()
    return raw_username.upper() if raw_username else SHOP_MAIN_BRAND_NAME


def get_order_log_item_label(order: dict) -> str:
    title = str(order.get("product_title") or order.get("protocol") or "Товар").strip()
    quantity = max(int(order.get("quantity") or 0), 0)
    if quantity > 1:
        return f"{title} x{quantity}"
    return title


def build_purchase_owner_notification_text(shop_name: str, item_label: str, buyer_label: str, income_usdt: float) -> str:
    return (
        f"🏪 Шоп: {shop_name}\n"
        "💰 Новая покупка\n"
        f"🛍 Товар: {item_label}\n"
        f"🌸 Купил: {buyer_label}\n"
        f"💸 Ваш доход: {income_usdt:.2f} usdt"
    )


def get_purchase_buyer_label(message: Message | None, order: dict) -> str:
    if message is None:
        return f"ID {order['user_id']}"

    username = (getattr(message.chat, "username", None) or "").strip()
    if username:
        return f"@{username}"

    full_name = (getattr(message.chat, "full_name", None) or "").strip()
    if full_name:
        return full_name

    return f"ID {order['user_id']}"


def get_topup_buyer_label(message: Message, topup: dict) -> str:
    username = (getattr(message.chat, "username", None) or "").strip()
    if username:
        return f"@{username}"

    full_name = (getattr(message.chat, "full_name", None) or "").strip()
    if full_name:
        return full_name

    return f"ID {topup['user_id']}"


def build_topup_notification_text(shop_name: str, buyer_label: str, amount_usdt: float, provider_label: str) -> str:
    return (
        f"🏪 Шоп: {shop_name}\n"
        "💳 Новое пополнение\n"
        f"💵 Сумма: {amount_usdt:.2f} usdt\n"
        f"🌸 Пополнил: {buyer_label}\n"
        f"💱 Способ: {provider_label}"
    )


def wrap_sales_log(text: str) -> str:
    return f"{SALES_LOG_HEADER}\n\n{text}"


def split_sales_log_message(text: str, chunk_size: int = 3500) -> list[str]:
    wrapped_text = wrap_sales_log(text)
    if len(wrapped_text) <= chunk_size:
        return [wrapped_text]
    return [wrapped_text[index:index + chunk_size] for index in range(0, len(wrapped_text), chunk_size)]


def get_message_user_label(message: Message, fallback_user_id: int | None = None) -> str:
    telegram_user = getattr(message, "from_user", None) or getattr(message, "chat", None)
    username = (getattr(telegram_user, "username", None) or "").strip()
    if username:
        return f"@{username}"

    full_name = (getattr(telegram_user, "full_name", None) or "").strip()
    if full_name:
        return full_name

    if fallback_user_id is not None:
        return f"ID {fallback_user_id}"
    return "Unknown user"


async def send_sales_log_message(
    bot: Bot | None,
    text: str,
    *,
    partner_only: bool = False,
    audit_log_id: int | None = None,
) -> None:
    event_id = audit_log_id
    if event_id is None:
        event_id = create_sales_log_event(
            "generic",
            text,
            shop_name=format_shop_name(bot) if bot is not None else None,
        )

    if bot is None:
        mark_sales_log_event_failed(event_id, "Sales log skipped: bot is None")
        return
    if SALES_LOG_CHANNEL_ID == 0:
        mark_sales_log_event_failed(event_id, "Sales log skipped: SALES_LOG_CHANNEL_ID is 0")
        return
    if partner_only and get_current_partner_bot(bot) is None:
        mark_sales_log_event_failed(event_id, "Sales log skipped: current bot is not a partner bot")
        return

    try:
        last_message_id: int | None = None
        for chunk in split_sales_log_message(text):
            sent_message = await bot.send_message(
                SALES_LOG_CHANNEL_ID,
                chunk,
            )
            last_message_id = getattr(sent_message, "message_id", None)
        mark_sales_log_event_sent(event_id, last_message_id)
    except Exception as error:
        mark_sales_log_event_failed(event_id, str(error))
        print(f"[sales-log] failed to send log event #{event_id}: {error}")


async def log_order_debug(
    bot: Bot | None,
    order: dict | None,
    buyer_label: str,
    event_label: str,
    extra_lines: list[str] | None = None,
) -> None:
    if bot is None or order is None:
        return

    lines = [
        f"🏪 Шоп: {format_shop_name(bot)}",
        "🧾 Лог заказа",
        f"📌 Событие: {event_label}",
        f"🆔 Заказ: #{order['id']}",
        f"🌸 Пользователь: {buyer_label}",
        f"🛍 Товар: {get_order_log_item_label(order)}",
        f"📦 Количество: {int(order.get('quantity') or 0)}",
        f"💰 Сумма: {float(order.get('total_price') or 0.0):.2f} $",
        f"📌 Статус: {get_order_status_label(str(order.get('status') or ''))}",
    ]
    if extra_lines:
        lines.extend([line for line in extra_lines if line])
    event_text = "\n".join(lines)
    event_id = create_sales_log_event(
        "order",
        event_text,
        order_id=int(order["id"]),
        user_id=int(order["user_id"]),
        shop_name=format_shop_name(bot),
        event_label=event_label,
    )
    await send_sales_log_message(bot, event_text, audit_log_id=event_id)


async def log_topup_debug(
    bot: Bot | None,
    topup: dict | None,
    buyer_label: str,
    event_label: str,
    extra_lines: list[str] | None = None,
) -> None:
    if bot is None or topup is None:
        return
    if get_current_partner_bot(bot) is not None:
        return

    lines = [
        f"🏪 Шоп: {format_shop_name(bot)}",
        "💳 Лог пополнения",
        f"📌 Событие: {event_label}",
        f"🆔 Пополнение: #{topup['id']}",
        f"🌸 Пользователь: {buyer_label}",
        f"💰 Сумма: {float(topup.get('amount') or 0.0):.2f} $",
        f"💱 Способ: {get_payment_provider_label(str(topup.get('payment_provider') or 'xrocket'))}",
        f"📌 Статус: {str(topup.get('status') or '')}",
    ]
    if extra_lines:
        lines.extend([line for line in extra_lines if line])
    event_text = "\n".join(lines)
    event_id = create_sales_log_event(
        "topup",
        event_text,
        topup_id=int(topup["id"]),
        user_id=int(topup["user_id"]),
        shop_name=format_shop_name(bot),
        event_label=event_label,
    )
    await send_sales_log_message(bot, event_text, audit_log_id=event_id)


async def notify_new_partner_user(bot: Bot | None, message: Message) -> None:
    if bot is None or get_current_partner_bot(bot) is None:
        return
    await send_sales_log_message(
        bot,
        (
            f"🏪 Шоп: {format_shop_name(bot)}\n"
            f"👤 Новый пользователь: {get_message_user_label(message, getattr(message.from_user, 'id', None))}"
        ),
        partner_only=True,
    )


async def notify_purchase_owner(bot: Bot | None, order: dict, buyer_message: Message | None):
    if bot is None:
        return

    partner_bot = get_current_partner_bot(bot)
    owner_id = int(partner_bot.get("owner_id") or 0) if partner_bot is not None else ADMIN_ID
    if owner_id <= 0:
        return

    income_usdt = calculate_owner_purchase_income(order, partner_bot)
    buyer_label = get_purchase_buyer_label(buyer_message, order)
    shop_name = format_shop_name(bot, partner_bot)
    item_label = get_order_log_item_label(order)
    owner_text = build_purchase_owner_notification_text(shop_name, item_label, buyer_label, income_usdt)

    with contextlib.suppress(Exception):
        await bot.send_message(
            owner_id,
            owner_text,
        )

    if SALES_LOG_CHANNEL_ID == 0:
        return

    await send_sales_log_message(
        bot,
        (
            f"🏪 Шоп: {shop_name}\n"
            "💰 Новая покупка\n"
            f"🛍 Товар: {item_label}\n"
            f"🌸 Купил: {buyer_label}\n"
            f"💸 Доход: {income_usdt:.2f} usdt"
        ),
    )


async def notify_topup_event(bot: Bot | None, topup: dict, buyer_message: Message):
    if bot is None:
        return

    partner_bot = get_current_partner_bot(bot)
    # Franchise deposits are intentionally private and do not create an
    # owner/log notification. Franchise earnings come from purchases only.
    if partner_bot is not None:
        return
    owner_id = int(partner_bot.get("owner_id") or 0) if partner_bot is not None else ADMIN_ID
    shop_name = format_shop_name(bot, partner_bot)
    buyer_label = get_topup_buyer_label(buyer_message, topup)
    amount_usdt = round(float(topup.get("amount") or 0.0), 2)
    provider_label = get_payment_provider_label(str(topup.get("payment_provider") or "xrocket"))
    text = build_topup_notification_text(shop_name, buyer_label, amount_usdt, provider_label)

    if owner_id > 0:
        with contextlib.suppress(Exception):
            await bot.send_message(
                owner_id,
                text,
            )

    if SALES_LOG_CHANNEL_ID == 0:
        return

    await send_sales_log_message(bot, text)


def get_market_category_button_config(category: dict, language_code: str | None = None) -> tuple[str, str | None]:
    lang = resolve_language_code(language_code=language_code)
    name = (category.get("name") or "").lower()
    localized_name = strip_supplier_decorations(get_localized_market_category_label(category, lang))
    try:
        category_id = int(category.get("id") or 0)
    except (TypeError, ValueError):
        category_id = 0
    mail_labels = {
        39: ("Gmail + YouTube", MAIL_SERVICE_EMOJI_IDS[39]),
        58: ("Rambler", MAIL_SERVICE_EMOJI_IDS[58]),
        54: ("Yandex", MAIL_SERVICE_EMOJI_IDS[54]),
        50: ("Mail", MAIL_SERVICE_EMOJI_IDS[50]),
        62: ("Другие почты", None),
    }
    if category_id in mail_labels:
        return mail_labels[category_id]
    if category_id == 75:
        return "VPN", None
    mapping = (
        ("вконтакте", ("ВКонтакте", "5323687726615119535")),
        ("vk", ("VK", "5323687726615119535")),
        ("instagram", ("Instagram", "5319160079465857105")),
        ("telegram", ("Telegram", "5330237710655306682")),
        ("tiktok", ("TikTok", "5327982530702359565")),
        ("x.com", ("X.com", "5330337435500951363")),
        ("twitter", ("X.com", "5330337435500951363")),
        ("facebook", ("Facebook", "5323261730283863478")),
        ("gmail", ("Gmail / Google", "5373246052868571826")),
        ("google", ("Gmail / Google", "5373246052868571826")),
        ("гугл", ("Gmail / Google", "5373246052868571826")),
        ("discord", ("Discord", "5325612636467903082")),
        # The application pack has no unambiguous Mail.ru/Rambler/Yandex
        # marks. Do not display an unrelated application icon for them.
        ("mail.ru", ("Mail.ru", None)),
        ("яндекс", ("Яндекс", None)),
        ("rambler", ("Rambler", None)),
        ("почты", ("Другие почты", None)),
        ("почт", ("Другие почты", None)),
        ("steam", ("Steam / Twitch", "5334678011054669335")),
        ("twitch", ("Steam / Twitch", "5334678011054669335")),
        ("reddit", ("Reddit", "5346308584923740680")),
        ("linkedin", ("LinkedIn", "5346024520081751155")),
        ("vpn", ("VPN / Proxy", None)),
        ("ии", ("Нейросети", "5359726582447487916")),
        ("знаком", ("Знакомства", "5328029650788563621")),
        ("dating", ("Dating", "5328029650788563621")),
        ("tinder", ("Tinder", "5328029650788563621")),
        ("odnoklass", ("Одноклассники", "5325865356638569272")),
        ("ok.ru", ("Одноклассники", "5325865356638569272")),
    )
    for keyword, button_config in mapping:
        if keyword in name:
            return truncate_text(localized_name, 28), button_config[1]
    return truncate_text(localized_name or tr(lang, "market.default_category"), 28), None


def _flatten_market_categories(categories: list[dict]) -> dict[int, dict]:
    """Index the provider catalogue by id without exposing provider details."""
    indexed: dict[int, dict] = {}

    def visit(items: list[dict]) -> None:
        for category in items:
            try:
                category_id = int(category.get("id") or 0)
            except (TypeError, ValueError):
                category_id = 0
            if category_id > 0:
                indexed[category_id] = category
            children = category.get("children") or []
            if isinstance(children, list):
                visit(children)

    visit(categories or [])
    return indexed


def _market_group_button(
    category: dict | None,
    category_id: int,
    label: str,
    language_code: str,
) -> InlineKeyboardButton:
    """Build a stable category button even during a temporary catalogue outage."""
    if category is not None:
        localized_label, custom_emoji_id = get_market_category_button_config(category, language_code)
        # Group menus use the provider's localized category name.  The explicit
        # label is used only for stable entries that are not in the response.
        button_label = localized_label or label
    else:
        button_label, custom_emoji_id = label, None
    if category_id == 75:
        custom_emoji_id = VPN_BUTTON_EMOJI_ID
    return InlineKeyboardButton(
        text=truncate_text(button_label, 28),
        icon_custom_emoji_id=custom_emoji_id,
        callback_data=f"market_category:{category_id}",
    )


def build_goods_categories_keyboard(
    categories: list[dict],
    bot: Bot | None = None,
    language_code: str | None = None,
) -> InlineKeyboardMarkup:
    """Top-level product catalogue requested by the storefront UI.

    The same category layout is used by the catalogue screen and the main
    menu. Provider category ids are stable; missing entries are still rendered
    so a short upstream outage does not change the layout.
    """
    lang = resolve_language_code(language_code=language_code)
    indexed = _flatten_market_categories(categories)
    from miniapp import build_email_url, build_sms_url

    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="Избранное", callback_data="market_favorites")],
        [
            InlineKeyboardButton(
                text="Прокси",
                callback_data="proxy_test:home",
                icon_custom_emoji_id=PROXY_MONOCHROME_EMOJI_ID,
                style="success",
            )
        ],
        [
            _market_group_button(indexed.get(75), 75, "VPN", lang),
            InlineKeyboardButton(text="Почта", callback_data="market_mail_menu", icon_custom_emoji_id=MONOCHROME_MAIL_EMOJI_ID),
        ],
        [
            InlineKeyboardButton(
                text="Мессенджеры и соц сети",
                callback_data="market_messaging_social_menu",
                icon_custom_emoji_id=MESSENGERS_BUTTON_EMOJI_ID,
            ),
        ],
        [InlineKeyboardButton(text="Игры и стриминг", callback_data="market_games_menu", icon_custom_emoji_id=GAMES_BUTTON_EMOJI_ID)],
    ]

    partner_bot = get_current_partner_bot(bot)
    bot_username = str(partner_bot.get("bot_username") or "").strip() if partner_bot else None
    email_url = build_email_url(bot_username)
    sms_url = build_sms_url(bot_username)
    temporary_service_row: list[InlineKeyboardButton] = []
    if email_url:
        temporary_service_row.append(
            InlineKeyboardButton(
                text="Одноразовая почта",
                web_app=WebAppInfo(url=email_url),
                icon_custom_emoji_id=MONOCHROME_MAIL_EMOJI_ID,
            )
        )
    if sms_url:
        temporary_service_row.append(
            InlineKeyboardButton(
                text="Одноразовые SMS",
                web_app=WebAppInfo(url=sms_url),
                icon_custom_emoji_id=MONOCHROME_SMS_EMOJI_ID,
            )
        )
    if temporary_service_row:
        rows.append(temporary_service_row)
    rows.append([InlineKeyboardButton(text=tr(lang, "common.back"), callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_market_group_keyboard(
    categories: list[dict],
    category_ids: tuple[int, ...],
    language_code: str | None = None,
) -> InlineKeyboardMarkup:
    lang = resolve_language_code(language_code=language_code)
    indexed = _flatten_market_categories(categories)
    buttons = [
        _market_group_button(indexed.get(category_id), category_id, "Товары", lang)
        for category_id in category_ids
        if is_market_category_available(category_id)
    ]
    rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2) if buttons[index:index + 2]]
    rows.append([InlineKeyboardButton(text=tr(lang, "common.back"), callback_data="magazine")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_market_mail_keyboard(language_code: str | None = None) -> InlineKeyboardMarkup:
    """Mail providers shown after opening the single ``Почта`` button."""
    lang = resolve_language_code(language_code=language_code)
    rows = [
        [
            InlineKeyboardButton(
                text="Gmail + YouTube",
                callback_data="market_category:39",
                icon_custom_emoji_id=MAIL_SERVICE_EMOJI_IDS[39],
            )
        ],
        [
            InlineKeyboardButton(text="Rambler", callback_data="market_category:58", icon_custom_emoji_id=MAIL_SERVICE_EMOJI_IDS[58]),
            InlineKeyboardButton(text="Yandex", callback_data="market_category:54", icon_custom_emoji_id=MAIL_SERVICE_EMOJI_IDS[54]),
            InlineKeyboardButton(text="Mail", callback_data="market_category:50", icon_custom_emoji_id=MAIL_SERVICE_EMOJI_IDS[50]),
        ],
        [
            InlineKeyboardButton(text="GMX.com", callback_data="market_products:65:1", icon_custom_emoji_id=MAIL_SERVICE_EMOJI_IDS[65]),
            InlineKeyboardButton(text="Hotmail / Outlook", callback_data="market_products:63:1", icon_custom_emoji_id=MAIL_SERVICE_EMOJI_IDS[63]),
            InlineKeyboardButton(text="Proton.me", callback_data="market_products:166:1", icon_custom_emoji_id=MAIL_SERVICE_EMOJI_IDS[166]),
        ],
        [
            InlineKeyboardButton(text="Mail.com", callback_data="market_products:66:1", icon_custom_emoji_id=MAIL_SERVICE_EMOJI_IDS[66]),
            InlineKeyboardButton(text="iCloud", callback_data="market_products:149:1", icon_custom_emoji_id=MAIL_SERVICE_EMOJI_IDS[149]),
        ],
        [InlineKeyboardButton(text=tr(lang, "common.back"), callback_data="back_main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_market_categories_keyboard(categories: list[dict], language_code: str | None = None) -> InlineKeyboardMarkup:
    lang = resolve_language_code(language_code=language_code)
    regular_buttons: list[InlineKeyboardButton] = []
    mail_buttons: list[InlineKeyboardButton] = []
    for category in categories:
        try:
            category_id = int(category.get("id") or 0)
        except (TypeError, ValueError):
            category_id = 0
        if not is_market_category_available(category_id):
            continue

        # Show the contents of "Другие почты" immediately in the main menu
        # instead of making the user open one more catalogue level.
        if category_id == 62:
            other_mail_labels = {
                65: "GMX.com",
                63: "Hotmail / Outlook",
                166: "Proton.me",
                66: "Mail.com",
                64: "Yahoo.com",
                149: "iCloud",
                67: "Другие зарубежные",
                84: "Несортированные",
            }
            for child in category.get("children") or []:
                try:
                    child_id = int(child.get("id") or 0)
                except (TypeError, ValueError):
                    child_id = 0
                child_name = other_mail_labels.get(child_id) or localize_market_text(
                    child.get("name"), lang, fallback=tr(lang, "market.default_subcategory")
                )
                child_name = strip_supplier_decorations(child_name)
                mail_buttons.append(
                    InlineKeyboardButton(
                        text=truncate_text(child_name, 22),
                        icon_custom_emoji_id=MAIL_SERVICE_EMOJI_IDS.get(child_id),
                        callback_data=f"market_products:{child['id']}:1",
                    )
                )
            continue

        button_text, custom_emoji_id = get_market_category_button_config(category, lang)
        button = InlineKeyboardButton(
            text=button_text,
            icon_custom_emoji_id=custom_emoji_id,
            callback_data=f"market_category:{category['id']}",
        )
        (mail_buttons if is_market_email_category(category) else regular_buttons).append(button)

    def button_rows(buttons: list[InlineKeyboardButton], columns: int = 2) -> list[list[InlineKeyboardButton]]:
        return [buttons[index:index + columns] for index in range(0, len(buttons), columns)]

    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="♥ Избранное", callback_data="market_favorites")],
        *button_rows(regular_buttons),
    ]
    if mail_buttons:
        rows.append([InlineKeyboardButton(text="Почта", callback_data="market_mail_menu")])
        gmail_button = next(
            (button for button in mail_buttons if button.callback_data == "market_category:39"),
            None,
        )
        remaining_mail_buttons = [button for button in mail_buttons if button is not gmail_button]
        if gmail_button is not None:
            rows.append([gmail_button])
        rows.extend(button_rows(remaining_mail_buttons, 3))
    rows.append([InlineKeyboardButton(text=tr(lang, "common.back"), callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# --- back-target routing for market screens -------------------------------
# Maps a hub callback to the set of top-level category ids that live under it.
# Used by Back buttons on subcategory / product screens so they return to
# the hub the user opened, not the whole shop catalogue.
_MARKET_HUB_ROUTES = (
    ("market_mail_menu",       {39, 50, 54, 58, 62, 63, 64, 65, 66, 67, 84, 149, 166}),
    ("market_social_menu",     {1, 2, 3, 24, 34, 46, 108, 112, 142}),
    ("market_messengers_menu", {28, 44}),
    ("market_games_menu",      {114}),
)

def resolve_market_back_target(category_id: int, parent_id: int) -> str:
    """Return the correct Back callback for a market subcategory / product screen."""
    for hub, ids in _MARKET_HUB_ROUTES:
        if category_id in ids or parent_id in ids:
            return hub
    # VPN (75) shortcut-loops onto itself via show_market_category; keep magazine.
    if category_id == 75 or parent_id == 75:
        return "magazine"
    if parent_id > 0:
        return f"market_category:{parent_id}"
    return "magazine"


def build_market_subcategories_keyboard(category: dict, language_code: str | None = None) -> InlineKeyboardMarkup:
    lang = resolve_language_code(language_code=language_code)
    builder = InlineKeyboardBuilder()
    for child in category.get("children", []):
        if not is_market_category_available(child.get("id")):
            continue
        builder.add(
            InlineKeyboardButton(
                text=truncate_text(
                    strip_supplier_decorations(
                        localize_market_text(child.get("name"), lang, fallback=tr(lang, "market.default_subcategory"))
                    ),
                    40,
                ),
                callback_data=f"market_products:{child['id']}:1",
            )
        )
    builder.adjust(3 if int(category.get("id") or 0) == 62 else 1)
    _cat_id = int(category.get("id") or 0)
    _par_id = int(category.get("parent_id") or 0)
    builder.row(InlineKeyboardButton(text=tr(lang, "common.back"), callback_data=resolve_market_back_target(_cat_id, _par_id)))
    return builder.as_markup()


def build_market_products_keyboard(
    category: dict,
    products: list[dict],
    page: int,
    last_page: int,
    margin_percentage: int,
    language_code: str | None = None,
) -> InlineKeyboardMarkup:
    lang = resolve_language_code(language_code=language_code)
    builder = InlineKeyboardBuilder()
    for product in products:
        product_title = strip_supplier_decorations(
            localize_market_text(product.get("title"), lang, fallback=tr(lang, "order.default_item"))
        )
        builder.row(
            InlineKeyboardButton(
                text=f"{truncate_text(product_title, 34)} • {convert_rub_to_usdt(product.get('price', 0), margin_percentage):.2f} $",
                callback_data=f"market_product:{category['id']}:{product['id']}:{page}",
            )
        )

    nav_row = []
    if page > 1:
        nav_row.append(
            InlineKeyboardButton(
                text="◀️",
                callback_data=f"market_products:{category['id']}:{page - 1}",
            )
        )
    if page < last_page:
        nav_row.append(
            InlineKeyboardButton(
                text="▶️",
                callback_data=f"market_products:{category['id']}:{page + 1}",
            )
        )
    if nav_row:
        builder.row(*nav_row)

    parent_id = int(category.get("parent_id") or 0)
    _cat_id = int(category.get("id") or 0)
    # Route back to the hub the user came from (mail / social / messengers / games)
    # so subcategory-flattened screens return to their landing menu, not to the
    # whole shop catalogue.  VPN (75) still returns to `magazine` because its
    # shortcut would loop back onto the same product list.
    back_callback = resolve_market_back_target(_cat_id, parent_id)
    builder.row(InlineKeyboardButton(text=tr(lang, "common.back"), callback_data=back_callback))
    return builder.as_markup()


def build_market_product_keyboard(
    category_id: int,
    product: dict,
    page: int,
    is_favorite: bool = False,
    language_code: str | None = None,
) -> InlineKeyboardMarkup:
    lang = resolve_language_code(language_code=language_code)
    stock = max(int(product.get("quantity") or 0), 0)
    builder = InlineKeyboardBuilder()
    preset_values = [value for value in (1, 2, 3, 5) if value <= stock]
    if preset_values:
        for value in preset_values:
            builder.add(
                InlineKeyboardButton(
                    text=tr(lang, "order.quantity", value=value).replace("📦 ", ""),
                    callback_data=f"market_qty:{category_id}:{product['id']}:{page}:{value}",
                )
            )
        builder.adjust(2)
    if stock > 0:
        builder.row(
            InlineKeyboardButton(
                text=tr(lang, "proxy.custom_quantity"),
                callback_data=f"market_qty_custom:{category_id}:{product['id']}:{page}",
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="♥ Удалить из избранного" if is_favorite else "♡ Добавить в избранное",
            callback_data=f"market_favorite_toggle:{category_id}:{product['id']}:{page}",
        )
    )
    builder.row(InlineKeyboardButton(text=tr(lang, "common.back"), callback_data=f"market_products:{category_id}:{page}"))
    return builder.as_markup()


def build_market_delivery_pending_keyboard(order_id: int) -> InlineKeyboardMarkup:
    order = get_order(order_id)
    lang = resolve_language_code(user_id=int((order or {}).get("user_id") or 0) or None)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=tr(lang, "profile.orders_button").removeprefix("🧾 "),
                    callback_data="profile_orders:0",
                    icon_custom_emoji_id=PROFILE_ORDERS_BUTTON_EMOJI_ID,
                )
            ],
            [InlineKeyboardButton(text=tr(lang, "common.to_menu"), callback_data="back_main")],
        ]
    )


def build_partner_program_keyboard(
    language_code: str | None = None,
    *,
    bot: Bot | None = None,
) -> InlineKeyboardMarkup:
    lang = resolve_language_code(language_code=language_code)
    # A franchise bot is already a provisioned partner bot.  Its users must
    # not see the owner-only control for creating another partner bot.
    current_partner = get_current_partner_bot(bot)
    is_franchise_bot = current_partner is not None and int(current_partner.get("franchise_hide_create", 0) or 0) == 1
    rows = [
        [InlineKeyboardButton(text=tr(lang, "partner.manage_bots_button"), callback_data="partner_bots_manage")],
    ]
    if not is_franchise_bot:
        rows.append(
            [
                InlineKeyboardButton(
                    text=tr(lang, "partner.add_new_bot_button").removeprefix("➕ "),
                    callback_data="partner_add_bot",
                    icon_custom_emoji_id=PARTNER_BOT_BUTTON_EMOJI_ID,
                )
            ]
        )
    # The referral programme is available from the SousPartners entry bot
    # (and from the main bot), but is not exposed to ordinary franchise users.
    show_referral_program = (
        current_partner is None
        or str(current_partner.get("bot_username") or "").casefold() == "souspartnersbot"
    )
    if show_referral_program:
        rows.append([InlineKeyboardButton(text="🔗 Реферальная программа", callback_data="partner_referral_program")])
    rows.append([InlineKeyboardButton(text=tr(lang, "common.back"), callback_data="back_main")])
    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


def build_partner_add_bot_keyboard(language_code: str | None = None) -> InlineKeyboardMarkup:
    lang = resolve_language_code(language_code=language_code)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=tr(lang, "partner.enter_token_manually_button"), callback_data="partner_add_bot_manual")],
            [InlineKeyboardButton(text=tr(lang, "common.back"), callback_data="partner_program")],
        ]
    )


def build_partner_program_text(
    total_bots: int = 0,
    total_users: int = 0,
    total_earned_rub: float = 0.0,
    total_withdrawn: float = 0.0,
    available_balance: float = 0.0,
    language_code: str | None = None,
) -> str:
    lang = resolve_language_code(language_code=language_code)
    return (
        f"{premium_emoji(PARTNER_BOT_BUTTON_EMOJI_ID, '🤖')} {tr(lang, 'partner.program.title').removeprefix('🤝 ')}\n\n"
        f"{premium_emoji(GOODS_BUTTON_EMOJI_ID, '🏪')} {tr(lang, 'partner.program.subtitle').removeprefix('🛒 ')}\n\n"
        f"{tr(lang, 'partner.program.description')}\n\n"
        f"{premium_emoji(PARTNER_BOT_BUTTON_EMOJI_ID, '🤖')} {tr(lang, 'partner.program.features').removeprefix('🤖 ')}\n\n"
        f"{premium_emoji(PARTNER_PROFIT_EMOJI_ID, '💎')} {tr(lang, 'partner.program.profit').removeprefix('💸 ')}\n\n"
        f"{premium_emoji(PARTNER_AVAILABLE_EMOJI_ID, '⚡️')} {tr(lang, 'partner.program.available', value=available_balance).removeprefix('💰 ')}\n"
        f"{premium_emoji(PARTNER_WITHDRAWN_EMOJI_ID, '👛')} {tr(lang, 'partner.program.withdrawn', value=total_withdrawn).removeprefix('💸 ')}\n\n"
        f"{premium_emoji(PARTNER_STATS_EMOJI_ID, '💡')} {tr(lang, 'partner.program.stats').removeprefix('🔎 ')}\n"
        f"{tr(lang, 'partner.program.total_bots', value=total_bots)}\n"
        f"{tr(lang, 'partner.program.total_users', value=total_users)}\n"
        f"{tr(lang, 'partner.program.total_earned', value=total_earned_rub)}"
    )


def build_partner_bot_invite_text(
    partner_bot: dict,
    language_code: str | None = None,
) -> str:
    """Render the partner-program offer inside an owner's connected bot.

    The partner-program screen in SousPartnersBot keeps its own navigation;
    this is the same offer exposed to users of a connected franchise bot.
    Statistics belong to the owner of that bot, not to the person viewing it.
    """
    owner_id = int(partner_bot.get("owner_id") or 0)
    stats = get_partner_owner_summary(owner_id) if owner_id > 0 else {}
    return build_partner_program_text(
        total_bots=int(stats.get("total_bots") or 0),
        total_users=int(stats.get("total_users") or 0),
        total_earned_rub=float(stats.get("total_earned") or stats.get("total_earned_rub") or 0.0),
        total_withdrawn=float(stats.get("total_withdrawn") or 0.0),
        available_balance=float(stats.get("available_balance") or 0.0),
        language_code=language_code,
    )


def build_partner_bot_invite_keyboard(
    partner_bot: dict,
    language_code: str | None = None,
) -> InlineKeyboardMarkup:
    lang = resolve_language_code(language_code=language_code)
    referral_url = build_partner_referral_link(partner_bot)
    rows: list[list[InlineKeyboardButton]] = []
    if referral_url:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Создать своего бота",
                    url=referral_url,
                    icon_custom_emoji_id=PARTNER_BOT_BUTTON_EMOJI_ID,
                )
            ]
        )
    rows.append([InlineKeyboardButton(text=tr(lang, "common.back"), callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_partner_referral_link(partner_bot: dict | None = None, *, owner_id: int | None = None) -> str | None:
    """Build a SousPartners deep link carrying the partner owner's code."""
    resolved_owner_id = int(owner_id or (partner_bot or {}).get("owner_id") or 0)
    if resolved_owner_id <= 0:
        return None
    profile = get_user_profile(resolved_owner_id)
    referral_code = str((profile or {}).get("referral_code") or resolved_owner_id).strip()
    if not referral_code:
        return None
    username = str(
        os.getenv("PARTNER_MAIN_BOT_USERNAME")
        or os.getenv("MAIN_BOT_USERNAME")
        or "SousPartnersBot"
    ).strip().lstrip("@")
    return f"https://t.me/{username}?start=r_{quote(referral_code)}"


def build_partner_referral_program_text(
    stats: dict | None,
    referral_link: str,
    language_code: str | None = None,
) -> str:
    lang = resolve_language_code(language_code=language_code)
    stats = stats or {}
    if lang == "uk":
        return (
            "🔗 <b>Реферальна програма SousPartners</b>\n\n"
            f"Запрошуйте власників ботів і отримуйте <b>{PARTNER_OWNER_REFERRAL_PERCENT:g}%</b> "
            "від їхнього доходу з націнки.\n\n"
            f"Ваша посилання:\n<code>{html.escape(referral_link)}</code>\n\n"
            f"Запрошено партнерів: <b>{int(stats.get('referred_partners') or 0)}</b>\n"
            f"Зароблено за рефералами: <b>{float(stats.get('partner_referral_earned') or 0.0):.2f}$</b>"
        )
    return (
        "🔗 <b>Реферальная программа SousPartners</b>\n\n"
        f"Приглашайте владельцев ботов и получайте <b>{PARTNER_OWNER_REFERRAL_PERCENT:g}%</b> "
        "от их дохода с наценки.\n\n"
        f"Ваша ссылка:\n<code>{html.escape(referral_link)}</code>\n\n"
        f"Приглашено партнёров: <b>{int(stats.get('referred_partners') or 0)}</b>\n"
        f"Заработано по рефералам: <b>{float(stats.get('partner_referral_earned') or 0.0):.2f}$</b>"
    )


def build_partner_referral_program_keyboard(referral_link: str, language_code: str | None = None) -> InlineKeyboardMarkup:
    lang = resolve_language_code(language_code=language_code)
    share_url = f"https://t.me/share/url?url={quote(referral_link, safe='')}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Копировать ссылку", copy_text=CopyTextButton(text=referral_link))],
            [InlineKeyboardButton(text="Поделиться ссылкой", url=share_url)],
            [InlineKeyboardButton(text="Вывести реферальный доход", callback_data="partner_referral_withdraw")],
            [InlineKeyboardButton(text=tr(lang, "common.back"), callback_data="partner_program")],
        ]
    )


def build_partner_add_bot_text(language_code: str | None = None) -> str:
    lang = resolve_language_code(language_code=language_code)
    return (
        f"{tr(lang, 'partner.add_bot.title')}\n\n"
        f"{tr(lang, 'partner.add_bot.choose_method')}\n\n"
        f"{tr(lang, 'partner.add_bot.instructions')}\n\n"
        f"{tr(lang, 'partner.add_bot.step1')}\n"
        f"{tr(lang, 'partner.add_bot.step2')}\n"
        f"{tr(lang, 'partner.add_bot.step3')}\n"
        f"{tr(lang, 'partner.add_bot.step4')}\n\n"
        f"{tr(lang, 'partner.add_bot.prompt')}"
    )


def build_partner_bots_keyboard(partner_bots: list[dict], language_code: str | None = None) -> InlineKeyboardMarkup:
    lang = resolve_language_code(language_code=language_code)
    rows = []
    for partner_bot in partner_bots:
        rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"@{partner_bot['bot_username']} • "
                        f"+{int(partner_bot.get('margin_percentage') or 0)}%"
                    ),
                    callback_data=f"partner_bot_view:{partner_bot['id']}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text=tr(lang, "common.back"), callback_data="partner_program")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_partner_bots_text(partner_bots: list[dict], language_code: str | None = None) -> str:
    lang = resolve_language_code(language_code=language_code)
    if not partner_bots:
        if lang == "uk":
            return "📋 Керування партнерськими ботами\n\nУ вас поки немає підключених партнерських ботів."
        return (
            "📋 Управление партнёрскими ботами\n\n"
            "У вас пока нет подключённых партнёрских ботов."
        )
    lines = (
        ["📋 Керування партнерськими ботами\n", "Ваші боти:"]
        if lang == "uk"
        else ["📋 Управление партнёрскими ботами\n", "Ваши боты:"]
    )
    for partner_bot in partner_bots[:10]:
        if lang == "uk":
            lines.append(
                f"• @{partner_bot['bot_username']} | "
                f"до виведення: {get_partner_bot_available_balance(partner_bot):.2f}$ | "
                f"дод. націнка: {int(partner_bot.get('margin_percentage') or 0)}%"
            )
        else:
            lines.append(
                f"• @{partner_bot['bot_username']} | "
                f"к выводу: {get_partner_bot_available_balance(partner_bot):.2f}$ | "
                f"доп. наценка: {int(partner_bot.get('margin_percentage') or 0)}%"
            )
    return "\n".join(lines)


def build_partner_bot_manage_text(partner_bot: dict, total_users: int) -> str:
    available_balance = get_partner_bot_available_balance(partner_bot)
    is_active = int(partner_bot.get("is_active") or 0) == 1
    shop_markup_percentage = get_partner_shop_markup(partner_bot)
    lang = resolve_language_code(user_id=int(partner_bot.get("owner_id") or 0) or None)
    if lang == "uk":
        return (
            "🏢 Керування франшизним ботом\n\n"
            f"🤖 Бот: @{partner_bot['bot_username']}\n"
            f"🆔 ID бота: {partner_bot['id']}\n"
            f"🛍 Націнка товарів: {get_partner_category_markup(partner_bot, 'goods')}%\n"
            f"🌐 Націнка проксі: {get_partner_category_markup(partner_bot, 'proxy')}%\n"
            f"📱 Націнка SMS: {get_partner_category_markup(partner_bot, 'sms')}%\n"
            f"👥 Користувачі: {total_users}\n"
            f"💰 Зароблено всього: {float(partner_bot.get('partner_earnings') or 0):.2f}$\n"
            f"💸 Уже виведено: {float(partner_bot.get('partner_withdrawn') or 0):.2f}$\n"
            f"🏦 Доступно до виведення: {available_balance:.2f}$\n"
            f"{'🟢' if is_active else '🔴'} Статус: {'Активний' if is_active else 'Вимкнений'}\n\n"
            "Дохід за франшизою надходить із вашої додаткової націнки.\n\n"
            "📣 Усі інструменти керування ботом доступні в цьому партнерському кабінеті.\n\n"
            "Оберіть дію в меню нижче:"
        )
    return (
        "🏢 Управление франшизным ботом\n\n"
        f"🤖 Бот: @{partner_bot['bot_username']}\n"
        f"🆔 ID бота: {partner_bot['id']}\n"
        f"🛍 Наценка товаров: {get_partner_category_markup(partner_bot, 'goods')}%\n"
        f"🌐 Наценка прокси: {get_partner_category_markup(partner_bot, 'proxy')}%\n"
        f"📱 Наценка SMS: {get_partner_category_markup(partner_bot, 'sms')}%\n"
        f"👥 Пользователи: {total_users}\n"
        f"💰 Заработано всего: {float(partner_bot.get('partner_earnings') or 0):.2f}$\n"
        f"💸 Уже выведено: {float(partner_bot.get('partner_withdrawn') or 0):.2f}$\n"
        f"🏦 Доступно к выводу: {available_balance:.2f}$\n"
        f"{'🟢' if is_active else '🔴'} Статус: {'Активен' if is_active else 'Выключен'}\n\n"
        "Доход по франшизе идёт от вашей дополнительной наценки.\n\n"
        "📣 Все инструменты управления ботом доступны в этом партнёрском кабинете.\n\n"
        "Выберите действие из меню ниже:"
    )


def build_partner_bot_manage_keyboard(partner_bot: dict) -> InlineKeyboardMarkup:
    partner_id = int(partner_bot["id"])
    lang = resolve_language_code(user_id=int(partner_bot.get("owner_id") or 0) or None)
    if lang == "uk":
        labels = (
            "💳 Вивести на XROCKET", "💸 Змінити націнку", "📣 Розсилка", "🔔 Підписка",
            "⚙️ Налаштування реферальної системи", "🔗 UTM-посилання", "🏆 Топ покупців",
            "🔄 Оновити",
        )
    else:
        labels = (
            "💳 Вывести на XROCKET", "💸 Изменить наценку", "📣 Рассылка", "🔔 Подписка",
            "⚙️ Регулировка реферальной системы", "🔗 UTM-ссылки", "🏆 Топ покупателей",
            "🔄 Обновить",
        )
    create_button_label = (
        "🔗 Кнопка приглашения: показывается"
        if int(partner_bot.get("franchise_hide_create", 0) or 0) == 0
        else "🔗 Кнопка приглашения: скрыта"
    )
    rows = [
            [InlineKeyboardButton(text=labels[0], callback_data=f"partner_bot_withdraw:{partner_id}")],
            [InlineKeyboardButton(text=labels[1], callback_data=f"partner_bot_margin:{partner_id}")],
            [InlineKeyboardButton(text=labels[2], callback_data=f"partner_bot_broadcast:{partner_id}")],
            [InlineKeyboardButton(text=labels[3], callback_data=f"partner_bot_subscription:{partner_id}")],
            [InlineKeyboardButton(text=labels[4], callback_data=f"partner_bot_referral:{partner_id}")],
            [InlineKeyboardButton(text=labels[5], callback_data=f"partner_bot_utm:{partner_id}")],
            [InlineKeyboardButton(text=labels[6], callback_data=f"partner_bot_top:{partner_id}")],
            [InlineKeyboardButton(text=create_button_label, callback_data=f"partner_bot_toggle_create:{partner_id}")],
            [InlineKeyboardButton(text=labels[7], callback_data=f"partner_bot_view:{partner_id}")],
            [InlineKeyboardButton(text=tr(lang, "common.back"), callback_data="partner_bots_manage")],
    ]
    if ensure_admin(int(partner_bot.get("owner_id") or 0)):
        rows.insert(0, [InlineKeyboardButton(text="⚙️ Настроить франшизу", callback_data=f"admin_franchise_settings:{partner_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_partner_margin_keyboard(partner_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛍 Товары", callback_data=f"partner_markup_kind:{partner_id}:goods")],
            [InlineKeyboardButton(text="🌐 Прокси", callback_data=f"partner_markup_kind:{partner_id}:proxy")],
            [InlineKeyboardButton(text="📱 SMS", callback_data=f"partner_markup_kind:{partner_id}:sms")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"partner_bot_view:{partner_id}")],
        ]
    )


def build_partner_markup_value_keyboard(partner_id: int, kind: str) -> InlineKeyboardMarkup:
    rows = []
    for left, right in ((25, 50), (100, 150), (200, 300)):
        rows.append([
            InlineKeyboardButton(text=f"{left}%", callback_data=f"partner_markup_set:{partner_id}:{kind}:{left}"),
            InlineKeyboardButton(text=f"{right}%", callback_data=f"partner_markup_set:{partner_id}:{kind}:{right}"),
        ])
    rows.append([InlineKeyboardButton(text="✍️ Своя наценка", callback_data=f"partner_markup_custom:{partner_id}:{kind}")])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"partner_bot_margin:{partner_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_subscription_gate_text(channel_label: str, language_code: str | None = None) -> str:
    lang = resolve_language_code(language_code=language_code)
    return (
        f"{tr(lang, 'generic.subscription_required', channel=channel_label)}\n"
        f"{tr(lang, 'subscription.prompt')}"
    )


def build_subscription_gate_keyboard(
    channel_id: str | None,
    channel_url: str | None,
    check_callback_data: str,
    language_code: str | None = None,
) -> InlineKeyboardMarkup:
    lang = resolve_language_code(language_code=language_code)
    rows = []
    normalized_channel_url = (channel_url or "").strip()
    normalized_channel_id = (channel_id or "").strip()
    if not normalized_channel_url and normalized_channel_id.startswith("@"):
        normalized_channel_url = f"https://t.me/{normalized_channel_id[1:]}"
    if normalized_channel_url:
        rows.append([InlineKeyboardButton(
            text=tr(lang, "common.subscribe"),
            url=normalized_channel_url,
            icon_custom_emoji_id=SUBSCRIPTION_BUTTON_EMOJI_ID,
        )])
    rows.append([InlineKeyboardButton(
        text=tr(lang, "common.check"),
        callback_data=check_callback_data,
        icon_custom_emoji_id=PAYMENT_CHECK_EMOJI_ID,
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_partner_subscription_gate_text(partner_bot: dict, language_code: str | None = None) -> str:
    return build_subscription_gate_text(get_partner_subscription_channel_label(partner_bot), language_code)


def build_partner_subscription_gate_keyboard(partner_bot: dict, language_code: str | None = None) -> InlineKeyboardMarkup:
    return build_subscription_gate_keyboard(
        partner_bot.get("subscription_channel_id"),
        partner_bot.get("subscription_channel_url"),
        "partner_subscription_check",
        language_code,
    )


def build_main_subscription_gate_text(settings: dict, language_code: str | None = None) -> str:
    return build_subscription_gate_text(get_main_subscription_channel_label(settings), language_code)


def build_main_subscription_gate_keyboard(settings: dict, language_code: str | None = None) -> InlineKeyboardMarkup:
    return build_subscription_gate_keyboard(
        settings.get("subscription_channel_id"),
        settings.get("subscription_channel_url"),
        "main_subscription_check",
        language_code,
    )


def build_partner_admin_panel_text(partner_bot: dict, stats: dict) -> str:
    return (
        "🛠 Меню администратора\n"
        "└ Здесь вы можете настраивать своего бота\n\n"
        "📊 Статистика · За все время\n"
        f"├ Пользователей: {stats['total_users']}\n"
        f"├ Кол-во покупок: {stats['delivered_orders']}\n"
        f"├ Оборот: {stats['delivered_revenue']:.2f} $\n"
        f"└ Прибыль: {stats['profit']:.2f} $\n\n"
        f"🤖 Бот: @{partner_bot['bot_username']}\n"
        f"💸 Доп. наценка: {int(partner_bot.get('margin_percentage') or 0)}%"
    )


def build_partner_admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="◀️ В меню", callback_data="back_main")]]
    )


def build_partner_admin_margin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="25%", callback_data="partner_admin_margin_set:25"),
                InlineKeyboardButton(text="50%", callback_data="partner_admin_margin_set:50"),
            ],
            [
                InlineKeyboardButton(text="100%", callback_data="partner_admin_margin_set:100"),
                InlineKeyboardButton(text="150%", callback_data="partner_admin_margin_set:150"),
            ],
            [
                InlineKeyboardButton(text="200%", callback_data="partner_admin_margin_set:200"),
                InlineKeyboardButton(text="✍️ Своя наценка", callback_data="partner_admin_margin_custom"),
            ],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="partner_admin_panel")],
        ]
    )


def build_partner_admin_broadcast_buttons_prompt_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➡️ Пропустить кнопки", callback_data="partner_admin_broadcast_skip_buttons")],
            [InlineKeyboardButton(text="◀️ Отмена", callback_data="partner_admin_panel")],
        ]
    )


def build_partner_admin_broadcast_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить рассылку", callback_data="partner_admin_broadcast_confirm")],
            [InlineKeyboardButton(text="❌ Отменить", callback_data="partner_admin_panel")],
        ]
    )


def build_partner_admin_subscription_text(partner_bot: dict) -> str:
    status = "Включена ✅" if int(partner_bot.get("subscription_enabled") or 0) == 1 else "Выключена ❌"
    channel_url = partner_bot.get("subscription_channel_url") or "Не установлен"
    channel_id = (partner_bot.get("subscription_channel_id") or "").strip()
    channel_label = channel_url if channel_url != "Не установлен" else ("Канал выбран" if channel_id else "Не выбран")
    bot_username = partner_bot.get("bot_username") or "your_bot"
    return (
        "🔔 Настройки подписки:\n\n"
        f"Статус: {status}\n"
        f"Канал: {channel_label}\n"
        f"Ссылка: {channel_url}\n\n"
        "📌 Мини-инструкция:\n"
        f"1. Добавьте в администраторы канала бота @{bot_username}.\n"
        "2. Нажмите «Выбрать канал».\n"
        "3. Перешлите любой пост из канала или отправьте @username канала.\n"
        "4. Подтвердите найденный канал.\n"
        "5. При необходимости укажите URL и включите статус подписки.\n\n"
        "ℹ️ Если канал приватный, обязательно укажите рабочую ссылку-приглашение."
    )


def build_partner_admin_subscription_keyboard(partner_bot: dict) -> InlineKeyboardMarkup:
    enabled = int(partner_bot.get("subscription_enabled") or 0) == 1
    toggle_text = "Статус: ✅" if enabled else "Статус: ❌"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=toggle_text, callback_data="partner_admin_subscription_toggle")],
            [InlineKeyboardButton(text="Выбрать канал", callback_data="partner_admin_subscription_channel_id")],
            [InlineKeyboardButton(text="Изменить URL", callback_data="partner_admin_subscription_channel_url")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="partner_admin_panel")],
        ]
    )


def build_partner_admin_referral_text(partner_bot: dict, whitelist_rows: list[dict]) -> str:
    status = "Включена ✅" if int(partner_bot.get("referral_enabled") or 0) == 1 else "Выключена ❌"
    if whitelist_rows:
        whitelist_text = "\n".join(
            f"• {row['user_id']} -> {float(row['referral_percent'] or 0):.2f}%"
            for row in whitelist_rows[:8]
        )
    else:
        whitelist_text = "Пусто"
    return (
        "👥 Регулировка реферальной системы:\n\n"
        f"Статус: {status}\n"
        f"Текущий % от прибыли: {float(partner_bot.get('referral_percent') or 0):.2f}%\n"
        f"Диапазон настройки: {MIN_PARTNER_REFERRAL_PERCENT:.0f}% - {MAX_PARTNER_REFERRAL_PERCENT:.0f}%\n\n"
        "Вознаграждение рефералу начисляется из вашего дохода с его покупок.\n"
        "Процент считается только от вашего фактического дохода.\n\n"
        "Вайт-лист (особенные условия):\n"
        f"{whitelist_text}"
    )


def build_partner_admin_referral_keyboard(partner_bot: dict) -> InlineKeyboardMarkup:
    enabled = int(partner_bot.get("referral_enabled") or 0) == 1
    toggle_text = "Статус: ✅" if enabled else "Статус: ❌"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=toggle_text, callback_data="partner_admin_referrals_toggle")],
            [InlineKeyboardButton(text="Изменить %", callback_data="partner_admin_referrals_percent")],
            [InlineKeyboardButton(text="Добавить в вайт-лист", callback_data="partner_admin_referrals_whitelist_add")],
            [InlineKeyboardButton(text="Удалить из вайт-листа", callback_data="partner_admin_referrals_whitelist_remove")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="partner_admin_panel")],
        ]
    )


def build_partner_top_buyers_text(partner_bot: dict, top_buyers: list[dict]) -> str:
    lines = [f"🏆 Топ покупателей @{partner_bot['bot_username']}\n"]
    if not top_buyers:
        lines.append("Пока нет выданных заказов по этому боту.")
        return "\n".join(lines)

    for index, row in enumerate(top_buyers, start=1):
        lines.append(
            f"{index}. ID {row['user_id']} | покупок: {row['orders_count']} | сумма: {float(row['total_spent'] or 0):.2f}$"
        )
    return "\n".join(lines)


def build_partner_admin_utm_text(partner_bot: dict, stats: dict, utm_links: list[dict]) -> str:
    source_lines = [
        f"• {row['utm_source']}: {row['total']} чел. · {float(row.get('purchases_total') or 0):.2f}$"
        for row in stats.get("sources", [])[:5]
    ] or ["• пока нет данных"]
    campaign_lines = [
        f"• {row['utm_campaign']}: {row['total']} чел. · {float(row.get('purchases_total') or 0):.2f}$"
        for row in stats.get("campaigns", [])[:5]
    ] or ["• пока нет данных"]
    param_stats = {row["start_param"]: row for row in stats.get("params", [])}
    link_lines = [
        f"• {row['source_word']} -> https://t.me/{partner_bot['bot_username']}?start={row['start_param']}\n"
        f"  {param_stats.get(row['start_param'], {}).get('total', 0)} чел. · "
        f"{float(param_stats.get(row['start_param'], {}).get('purchases_total') or 0):.2f}$"
        for row in utm_links[:6]
    ] or ["• пока нет созданных ссылок"]
    return (
        "🔗 UTM-ссылки и аналитика\n\n"
        f"🤖 Бот: @{partner_bot['bot_username']}\n\n"
        "Создавайте отслеживаемые ссылки для рекламы, постов и партнёрских размещений.\n"
        "Формат создания: одно слово = одна ссылка.\n\n"
        "📎 Созданные ссылки:\n"
        f"{chr(10).join(link_lines)}\n\n"
        "📊 Топ источников:\n"
        f"{chr(10).join(source_lines)}\n\n"
        "📈 Топ кампаний:\n"
        f"{chr(10).join(campaign_lines)}"
    )


def build_partner_admin_utm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Создать UTM-ссылку", callback_data="partner_admin_utm_create")],
            [InlineKeyboardButton(text="🔄 Обновить статистику", callback_data="partner_admin_utm")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="partner_admin_panel")],
        ]
    )


def build_partner_cabinet_broadcast_buttons_prompt_keyboard(partner_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➡️ Пропустить кнопки", callback_data=f"partner_bot_broadcast_skip_buttons:{partner_id}")],
            [InlineKeyboardButton(text="◀️ Отмена", callback_data=f"partner_bot_view:{partner_id}")],
        ]
    )


def build_partner_cabinet_broadcast_confirm_keyboard(partner_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить рассылку", callback_data=f"partner_bot_broadcast_confirm:{partner_id}")],
            [InlineKeyboardButton(text="❌ Отменить", callback_data=f"partner_bot_view:{partner_id}")],
        ]
    )


def build_partner_cabinet_subscription_keyboard(partner_id: int, enabled: bool) -> InlineKeyboardMarkup:
    toggle_text = "Статус: ✅" if enabled else "Статус: ❌"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=toggle_text, callback_data=f"partner_bot_subscription_toggle:{partner_id}")],
            [InlineKeyboardButton(text="Выбрать канал", callback_data=f"partner_bot_subscription_channel_id:{partner_id}")],
            [InlineKeyboardButton(text="Изменить URL", callback_data=f"partner_bot_subscription_channel_url:{partner_id}")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"partner_bot_view:{partner_id}")],
        ]
    )


def build_partner_cabinet_subscription_confirm_keyboard(partner_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, выбрать канал", callback_data=f"partner_bot_subscription_confirm:{partner_id}")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"partner_bot_subscription:{partner_id}")],
        ]
    )


def build_partner_cabinet_referral_keyboard(partner_id: int, enabled: bool) -> InlineKeyboardMarkup:
    toggle_text = "Статус: ✅" if enabled else "Статус: ❌"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=toggle_text, callback_data=f"partner_bot_referral_toggle:{partner_id}")],
            [InlineKeyboardButton(text="Изменить %", callback_data=f"partner_bot_referral_percent:{partner_id}")],
            [InlineKeyboardButton(text="Добавить в вайт-лист", callback_data=f"partner_bot_referral_whitelist_add:{partner_id}")],
            [InlineKeyboardButton(text="Удалить из вайт-листа", callback_data=f"partner_bot_referral_whitelist_remove:{partner_id}")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"partner_bot_view:{partner_id}")],
        ]
    )


def build_partner_cabinet_utm_keyboard(partner_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Создать UTM-ссылку", callback_data=f"partner_bot_utm_create:{partner_id}")],
            [InlineKeyboardButton(text="🔄 Обновить статистику", callback_data=f"partner_bot_utm:{partner_id}")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"partner_bot_view:{partner_id}")],
        ]
    )


def admin_panel() -> InlineKeyboardMarkup:
    inline_keyboard = [
        [InlineKeyboardButton(text="📣 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="🎟 Промокоды", callback_data="admin_promocodes")],
        [InlineKeyboardButton(text="🏪 Все франшизы", callback_data="admin_franchises")],
        [InlineKeyboardButton(text="⚙️ Заявки API", callback_data="admin_api_applications")],
        [InlineKeyboardButton(text="🔔 Подписка главного бота", callback_data="admin_main_subscription")],
        [InlineKeyboardButton(text="⚙️ Цены и наценки", callback_data="admin_pricing")],
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users")],
        [InlineKeyboardButton(text="💳 Добавить баланс пользователю", callback_data="admin_balance_manage")],
        [InlineKeyboardButton(text="🔌 Начислить на API-баланс", callback_data="admin_api_balance_manage")],
        [InlineKeyboardButton(text="📶 Изменить GB прокси пользователя", callback_data="admin_traffic_manage")],
        [InlineKeyboardButton(text="📦 Статистика заказов", callback_data="admin_orders_stats")],
        [InlineKeyboardButton(text="🔗 UTM статистика", callback_data="admin_utm")],
        [InlineKeyboardButton(text="🔗 Ссылки на разделы", callback_data="admin_section_links")],
        [InlineKeyboardButton(text="📊 CRM статистика", callback_data="admin_crm")],
    ]

    try:
        from miniapp import build_crm_url

        crm_url = build_crm_url()
    except Exception:
        crm_url = None

    if crm_url:
        inline_keyboard.append([InlineKeyboardButton(text="🧭 Открыть CRM mini app", web_app=WebAppInfo(url=crm_url))])

    inline_keyboard.append([InlineKeyboardButton(text="◀️ Назад", callback_data="profile")])
    return InlineKeyboardMarkup(inline_keyboard=inline_keyboard)


ADMIN_DEEP_LINK_SECTIONS = (
    ("🛍 Товары", "goods"),
    ("🌐 Прокси", "proxy"),
    ("📨 Почта", "email"),
    ("📲 SMS", "sms"),
    ("👤 Профиль", "profile"),
    ("🤖 Партнёрская программа", "partner"),
)


def build_admin_section_links_text(bot_username: str) -> str:
    return (
        "🔗 <b>Ссылки на разделы</b>\n\n"
        "При переходе бот сразу откроет выбранный раздел. "
        "Нажмите на название, чтобы проверить ссылку, или «Копировать»."
    )


def build_admin_section_links_keyboard(bot_username: str) -> InlineKeyboardMarkup:
    username = str(bot_username or "").lstrip("@")
    rows = []
    for title, section in ADMIN_DEEP_LINK_SECTIONS:
        url = f"https://t.me/{username}?start=section_{section}"
        rows.append(
            [
                InlineKeyboardButton(text=title, url=url),
                InlineKeyboardButton(text="Копировать", copy_text=CopyTextButton(text=url)),
            ]
        )
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def proxy_menu(language_code: str | None = None) -> InlineKeyboardMarkup:
    lang = resolve_language_code(language_code=language_code)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=tr(lang, "proxy.mobile_button"), callback_data="proxy_mobile"),
                InlineKeyboardButton(text=tr(lang, "proxy.static_button"), callback_data="proxy_static"),
            ],
            [InlineKeyboardButton(text=tr(lang, "common.back"), callback_data="back_main")],
        ]
    )


def build_proxy_categories_keyboard(categories: list[dict], language_code: str | None = None) -> InlineKeyboardMarkup:
    lang = resolve_language_code(language_code=language_code)
    builder = InlineKeyboardBuilder()
    for category in categories:
        builder.add(
            InlineKeyboardButton(
                text=get_country_button_label(category),
                callback_data=f"proxy_static:{category['id']}",
            )
        )
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text=tr(lang, "common.back"), callback_data="proxy"))
    return builder.as_markup()


def build_protocol_keyboard(
    category_id: int,
    items: list[dict],
    language_code: str | None = None,
) -> InlineKeyboardMarkup:
    lang = resolve_language_code(language_code=language_code)
    builder = InlineKeyboardBuilder()
    for item in items:
        builder.row(
            InlineKeyboardButton(
                text=item["name"],
                callback_data=f"proxy_item:{category_id}:{item['id']}",
            )
        )
    builder.row(InlineKeyboardButton(text=tr(lang, "common.back"), callback_data="proxy_static"))
    return builder.as_markup()


def build_quantity_keyboard(category_id: int, language_code: str | None = None) -> InlineKeyboardMarkup:
    lang = resolve_language_code(language_code=language_code)
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=tr(lang, "common.back"), callback_data=f"proxy_static:{category_id}")]]
    )


def build_quantity_selection_keyboard(
    category_id: int,
    item_id: int,
    language_code: str | None = None,
) -> InlineKeyboardMarkup:
    lang = resolve_language_code(language_code=language_code)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="5", callback_data=f"proxy_quantity:{category_id}:{item_id}:5"),
                InlineKeyboardButton(text="25", callback_data=f"proxy_quantity:{category_id}:{item_id}:25"),
                InlineKeyboardButton(text="50", callback_data=f"proxy_quantity:{category_id}:{item_id}:50"),
                InlineKeyboardButton(text="100", callback_data=f"proxy_quantity:{category_id}:{item_id}:100"),
            ],
            [
                InlineKeyboardButton(
                    text=tr(lang, "proxy.custom_quantity"),
                    callback_data=f"proxy_quantity_custom:{category_id}:{item_id}",
                )
            ],
            [InlineKeyboardButton(text=tr(lang, "common.back"), callback_data=f"proxy_static:{category_id}")],
        ]
    )


def get_order_source_back_callback(order: dict) -> str:
    if is_account_order(order):
        return f"market_product:{order['category_id']}:{order['item_id']}:1"
    return f"proxy_static:{order['category_id']}"


def build_order_preview_keyboard(order: dict, language_code: str | None = None) -> InlineKeyboardMarkup:
    lang = resolve_language_code(language_code=language_code, user_id=int(order.get("user_id") or 0) or None)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=tr(lang, "common.choose_payment"), callback_data=f"order_methods:{order['id']}")],
            [InlineKeyboardButton(text=tr(lang, "common.back"), callback_data=get_order_source_back_callback(order))],
        ]
    )


def is_heleket_configured() -> bool:
    api_key = os.getenv("HELEKET_PAYMENT_API_KEY", os.getenv("HELEKET_API_KEY", "")).strip()
    merchant_uuid = os.getenv("HELEKET_MERCHANT_UUID", os.getenv("HELEKET_MERCHANT_ID", "")).strip()
    return bool(api_key and merchant_uuid)


def is_crystalpay_configured() -> bool:
    return bool(os.getenv("CRYSTALPAY_AUTH_LOGIN", "").strip() and os.getenv("CRYSTALPAY_AUTH_SECRET", "").strip())


def build_payment_methods_keyboard(order: dict, language_code: str | None = None) -> InlineKeyboardMarkup:
    lang = resolve_language_code(language_code=language_code, user_id=int(order.get("user_id") or 0) or None)
    rows = [
        [InlineKeyboardButton(text=tr(lang, "common.pay_with_balance"), callback_data=f"payment_balance:{order['id']}", icon_custom_emoji_id=PAYMENT_BALANCE_EMOJI_ID)],
        [InlineKeyboardButton(text="XROCKET", callback_data=f"payment_xrocket:{order['id']}", icon_custom_emoji_id=PAYMENT_XROCKET_EMOJI_ID)],
        [InlineKeyboardButton(text="LOLZ", callback_data=f"payment_lolz:{order['id']}", icon_custom_emoji_id=PAYMENT_LOLZ_EMOJI_ID)],
    ]
    if is_heleket_configured():
        rows.append([InlineKeyboardButton(text=tr(lang, "payment.method_heleket"), callback_data=f"payment_heleket:{order['id']}", icon_custom_emoji_id=PAYMENT_HELEKET_EMOJI_ID)])
    if is_crystalpay_configured():
        rows.append([InlineKeyboardButton(text=CRYPTOBOT_PROVIDER_LABEL, callback_data=f"payment_crystalpay:{order['id']}", icon_custom_emoji_id="5361914370068613491")])
    rows.append([InlineKeyboardButton(text=tr(lang, "common.back"), callback_data=get_order_source_back_callback(order))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_xrocket_currency_picker_text(
    title: str,
    amount_usd: float,
    page: int,
    total: int,
    language_code: str | None = None,
) -> str:
    lang = resolve_language_code(language_code=language_code)
    return (
        f"{title}\n\n"
        f"{tr(lang, 'xrocket.order_amount', value=float(amount_usd or 0))}\n"
        f"{tr(lang, 'xrocket.choose_currency')}\n"
        f"{tr(lang, 'xrocket.memecoins_hidden')}\n\n"
        f"{tr(lang, 'xrocket.page', page=page + 1, total=max(total, 1))}"
    )


def build_xrocket_currency_picker_keyboard(
    currencies: list[dict],
    page: int,
    total_pages: int,
    select_callback_builder,
    page_callback_builder,
    back_callback: str,
    language_code: str | None = None,
) -> InlineKeyboardMarkup:
    lang = resolve_language_code(language_code=language_code)
    builder = InlineKeyboardBuilder()
    for row in currencies:
        builder.row(
            InlineKeyboardButton(
                text=f"{row['currency']} ({row['name']})",
                callback_data=select_callback_builder(row["currency"], page),
            )
        )

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="◀️", callback_data=page_callback_builder(page - 1)))
    if page + 1 < total_pages:
        nav_row.append(InlineKeyboardButton(text="▶️", callback_data=page_callback_builder(page + 1)))
    if nav_row:
        builder.row(*nav_row)
    builder.row(InlineKeyboardButton(text=tr(lang, "common.back"), callback_data=back_callback))
    return builder.as_markup()


def build_payment_keyboard(
    order_id: int,
    pay_url: str | None,
    check_callback_data: str | None = None,
    *,
    language_code: str | None = None,
) -> InlineKeyboardMarkup:
    lang = resolve_language_code(language_code=language_code)
    rows = []
    if pay_url:
        rows.append([InlineKeyboardButton(text=payment_button_text(tr(lang, "common.pay")), url=pay_url, icon_custom_emoji_id=PAYMENT_BUTTON_EMOJI_ID)])
    rows.append([InlineKeyboardButton(text=payment_button_text(tr(lang, "common.check_payment")), callback_data=check_callback_data or f"order_check:{order_id}", icon_custom_emoji_id=PAYMENT_CHECK_EMOJI_ID)])
    rows.append([InlineKeyboardButton(text=payment_button_text(tr(lang, "common.back")), callback_data=f"order_methods:{order_id}", icon_custom_emoji_id=BACK_BUTTON_EMOJI_ID)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_payment_provider_label(payment_provider: str) -> str:
    mapping = {
        "xrocket": "XROCKET",
        "lolz": "LOLZ",
        "heleket": "Heleket",
        "crystalpay": CRYPTOBOT_PROVIDER_LABEL,
    }
    return mapping.get(str(payment_provider or "").lower(), str(payment_provider or "").upper() or "PROVIDER")


def is_admin_user(user_id: int) -> bool:
    return user_id == ADMIN_ID


SUPPLIER_BOT_URL = "https://t.me/menupostavshukabot"


def build_profile_keyboard(
    user_id: int,
    bot: Bot | None = None,
    *,
    language_code: str | None = None,
) -> InlineKeyboardMarkup:
    lang = resolve_language_code(language_code=language_code, user_id=user_id)
    rows = [
        [
            InlineKeyboardButton(
                text=tr(lang, "profile.orders_button").removeprefix("🧾 "),
                callback_data="profile_orders:0",
                icon_custom_emoji_id=PROFILE_ORDERS_BUTTON_EMOJI_ID,
            )
        ],
        [
            InlineKeyboardButton(
                text=tr(lang, "profile.topup_button").removeprefix("💳 "),
                callback_data="topup_balance",
                icon_custom_emoji_id=PROFILE_TOPUP_BUTTON_EMOJI_ID,
            )
        ],
        [
            InlineKeyboardButton(
                text=tr(lang, "profile.promocode_button").removeprefix("🎟 "),
                callback_data="activate_promocode",
                icon_custom_emoji_id=PROFILE_PROMOCODE_BUTTON_EMOJI_ID,
            )
        ],
        [
            InlineKeyboardButton(
                text=tr(lang, "profile.referral_button").removeprefix("🤝 "),
                callback_data="referral_menu",
                icon_custom_emoji_id=PROFILE_REFERRAL_BUTTON_EMOJI_ID,
                style="success",
            )
        ],
    ]
    rows.append([InlineKeyboardButton(text="📦 Меню поставщика", url=SUPPLIER_BOT_URL)])
    if is_admin_user(user_id):
        rows.append([InlineKeyboardButton(text="🛠 Админка", callback_data="admin_panel")])
    rows.append([InlineKeyboardButton(text="⚙️ API", callback_data="api_menu")])
    rows.append([InlineKeyboardButton(text=tr(lang, "common.back"), callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_topup_methods_keyboard(
    language_code: str | None = None,
    *,
    user_id: int | None = None,
) -> InlineKeyboardMarkup:
    lang = resolve_language_code(language_code=language_code)
    rows = [
        [InlineKeyboardButton(text="XROCKET", callback_data="topup_xrocket", icon_custom_emoji_id=PAYMENT_XROCKET_EMOJI_ID)],
        [InlineKeyboardButton(text="LOLZ", callback_data="topup_lolz", icon_custom_emoji_id=PAYMENT_LOLZ_EMOJI_ID)],
    ]
    if is_heleket_configured():
        rows.append([InlineKeyboardButton(text=tr(lang, "payment.method_heleket"), callback_data="topup_heleket", icon_custom_emoji_id=PAYMENT_HELEKET_EMOJI_ID)])
    if is_crystalpay_configured():
        rows.append([InlineKeyboardButton(text=CRYPTOBOT_PROVIDER_LABEL, callback_data="topup_crystalpay", icon_custom_emoji_id="5361914370068613491")])
    rows.append([InlineKeyboardButton(text=tr(lang, "common.back"), callback_data="profile")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_topup_amount_keyboard(language_code: str | None = None) -> InlineKeyboardMarkup:
    lang = resolve_language_code(language_code=language_code)
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=tr(lang, "common.back"), callback_data="topup_balance")]]
    )


def build_api_application_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📨 Подать заявку", callback_data="api_apply")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="profile")],
    ])


def build_api_menu_text(account: dict, *, one_time_key: str | None = None) -> str:
    key = str(one_time_key or "Ключ хранится только у вас. Для перевыпуска создайте новый.")
    return (
        "⚙️ <b>API</b>\n\n"
        f"Ваш ключ: <code>{html.escape(key)}</code>\n"
        f"Endpoint: <code>{html.escape(API_BASE_URL)}</code>\n"
        f"Документация: <code>{html.escape(API_DOCS_URL)}</code>\n"
        f"Баланс API: <b>{float(account.get('balance') or 0.0):.2f} USDT</b>\n\n"
        "Заголовок авторизации:\n<code>Authorization: Bearer ВАШ_КЛЮЧ</code>"
    )


def build_api_menu_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="🔑 Сгенерировать API key", callback_data="api_generate_key")],
        [InlineKeyboardButton(text="💳 Пополнить API", callback_data="api_topup")],
    ]
    if API_DOCS_URL:
        rows.append([InlineKeyboardButton(text="📖 Документация", url=API_DOCS_URL)])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="profile")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_api_topup_methods_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="XROCKET", callback_data="api_topup_provider:xrocket")],
        [InlineKeyboardButton(text="LOLZ", callback_data="api_topup_provider:lolz")],
    ]
    if is_heleket_configured():
        rows.append([InlineKeyboardButton(text="Heleket", callback_data="api_topup_provider:heleket")])
    if is_crystalpay_configured():
        rows.append([InlineKeyboardButton(text=CRYPTOBOT_PROVIDER_LABEL, callback_data="api_topup_provider:crystalpay")])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="api_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_admin_api_applications_keyboard(applications: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for row in applications:
        user_id = int(row["user_id"])
        rows.append([
            InlineKeyboardButton(text=f"✅ {user_id}", callback_data=f"admin_api_approve:{user_id}"),
            InlineKeyboardButton(text="❌", callback_data=f"admin_api_reject:{user_id}"),
        ])
    rows.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_api_applications")])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_topup_payment_keyboard(
    topup_id: int,
    pay_url: str | None,
    check_callback_data: str | None = None,
    language_code: str | None = None,
    back_callback_data: str = "topup_balance",
) -> InlineKeyboardMarkup:
    lang = resolve_language_code(language_code=language_code)
    rows = []
    if pay_url:
        rows.append([InlineKeyboardButton(text=payment_button_text(tr(lang, "common.pay")), url=pay_url, icon_custom_emoji_id=PAYMENT_BUTTON_EMOJI_ID)])
    rows.append([InlineKeyboardButton(text=payment_button_text(tr(lang, "common.check_payment")), callback_data=check_callback_data or f"topup_check:{topup_id}", icon_custom_emoji_id=PAYMENT_CHECK_EMOJI_ID)])
    rows.append([InlineKeyboardButton(text=payment_button_text(tr(lang, "common.back")), callback_data=back_callback_data, icon_custom_emoji_id=BACK_BUTTON_EMOJI_ID)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_referral_keyboard(ref_link: str, language_code: str | None = None) -> InlineKeyboardMarkup:
    lang = resolve_language_code(language_code=language_code)
    share_url = f"https://t.me/share/url?url={quote(ref_link, safe='')}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=tr(lang, "referral.share_button"),
                    url=share_url,
                    icon_custom_emoji_id=REFERRAL_SHARE_BUTTON_EMOJI_ID,
                ),
                InlineKeyboardButton(
                    text=tr(lang, "referral.copy_button"),
                    copy_text=CopyTextButton(text=ref_link),
                    icon_custom_emoji_id=REFERRAL_COPY_BUTTON_EMOJI_ID,
                ),
            ],
            [InlineKeyboardButton(text=tr(lang, "common.back"), callback_data="profile")],
        ]
    )


def build_delivery_keyboard(order_id: int) -> InlineKeyboardMarkup:
    order = get_order(order_id)
    lang = resolve_language_code(user_id=int((order or {}).get("user_id") or 0) or None)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=tr(lang, "common.download_txt"), callback_data=f"download_order:{order_id}")],
            [InlineKeyboardButton(text=tr(lang, "common.back"), callback_data="back_main")],
        ]
    )


def build_result_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")]]
    )


def get_order_fulfillment_lock(order_id: int) -> asyncio.Lock:
    lock = ORDER_FULFILLMENT_LOCKS.get(order_id)
    if lock is None:
        lock = asyncio.Lock()
        ORDER_FULFILLMENT_LOCKS[order_id] = lock
    return lock


def resolve_order_bot(default_bot: Bot | None, order: dict | None) -> Bot | None:
    if order is None:
        return default_bot

    partner_id = int(order.get("partner_bot_id") or 0)
    if partner_id <= 0:
        return default_bot

    runtime = get_partner_runtime()
    if runtime is None:
        return default_bot

    return runtime.get_bot_by_partner_id(partner_id) or default_bot


def get_customer_order_number(order: dict) -> str:
    """Use the DJEKXA number for marketplace orders shown to customers."""
    if is_account_order(order):
        return str(order.get("supplier_order_number") or "").strip() or "—"
    return str(order["id"])


def build_market_delivery_pending_text(order_id: int) -> str:
    order = get_order(order_id)
    lang = resolve_language_code(user_id=int((order or {}).get("user_id") or 0) or None)
    supplier_order_number = str((order or {}).get("supplier_order_number") or "").strip()
    order_number_line = (
        f"{tr(lang, 'wait_invoice.order_number', value=supplier_order_number)}\n"
        if supplier_order_number
        else ""
    )

    return (
        f"{tr(lang, 'balance.confirmed')}\n\n"
        f"{order_number_line}"
        f"{tr(lang, 'order.status', value=tr(lang, 'status.delivery_pending'))}\n\n"
        f"{tr(lang, 'delivery.file_when_ready')}"
    )


async def send_order_delivery_document(bot: Bot | None, chat_id: int, order: dict) -> None:
    if bot is None or not order.get("delivery_text"):
        return

    lang = resolve_language_code(user_id=int(order.get("user_id") or 0) or None)
    document = BufferedInputFile(
        str(order["delivery_text"]).encode("utf-8"),
        filename=f"order_{get_customer_order_number(order)}.txt",
    )
    await bot.send_document(
        chat_id,
        document=document,
        caption=(
            tr(lang, "order.delivery_caption_product", value=get_customer_order_number(order))
            if is_account_order(order)
            else tr(lang, "order.delivery_caption_proxy", value=order["id"])
        ),
    )


def schedule_market_order_delivery(
    bot: Bot | None,
    order_id: int,
    chat_id: int,
    buyer_message: Message | None = None,
    status_message: Message | None = None,
) -> bool:
    if bot is None:
        return False

    existing_task = MARKET_ORDER_DELIVERY_TASKS.get(order_id)
    if existing_task is not None and not existing_task.done():
        return False

    task = asyncio.create_task(
        fulfill_market_order(
            status_message,
            order_id,
            notify_bot=bot,
            notify_chat_id=chat_id,
            buyer_message=buyer_message,
            update_status_message=status_message is not None,
        ),
        name=f"market-order-delivery-{order_id}",
    )
    MARKET_ORDER_DELIVERY_TASKS[order_id] = task
    task.add_done_callback(lambda _: MARKET_ORDER_DELIVERY_TASKS.pop(order_id, None))
    return True


async def ensure_order_paid(
    order_id: int,
    *,
    default_bot: Bot | None,
    buyer_message: Message | None = None,
    payment_invoice: dict | None = None,
) -> dict | None:
    order = get_order(order_id)
    if order is None:
        return None

    if order.get("status") in {"paid", "delivery_pending", "delivered", "credited"}:
        return order

    invoice_source = payment_invoice or order
    payment_provider = str(invoice_source.get("payment_provider") or "xrocket").lower()
    if payment_provider == "balance":
        mark_order_paid(order_id)
    elif payment_provider == "lolz":
        invoice_id = invoice_source.get("invoice_id") or invoice_source.get("xrocket_invoice_id")
        payment_id = invoice_source.get("client_invoice_id") or invoice_source.get("xrocket_client_invoice_id")
        if not invoice_id and not payment_id:
            raise LolzError("Для заказа ещё не создан счёт LOLZ")
        invoice = await get_lolz_invoice(invoice_id=invoice_id, payment_id=payment_id)
        if not is_lolz_invoice_paid(invoice):
            fallback_invoice = await get_lolz_paid_invoice_fallback(
                order_id=order_id,
                amount_usd=float(order.get("total_price") or 0.0),
                expected_invoice_id=invoice_id,
                expected_payment_id=payment_id,
            )
            if fallback_invoice is None:
                return order
        if payment_invoice is not None:
            mark_order_paid_from_invoice(order_id, payment_invoice)
        else:
            mark_order_paid(order_id)
    elif payment_provider == "heleket":
        invoice_id = invoice_source.get("invoice_id") or invoice_source.get("xrocket_invoice_id")
        merchant_order_id = invoice_source.get("client_invoice_id") or invoice_source.get("xrocket_client_invoice_id")
        if not invoice_id and not merchant_order_id:
            raise HeleketError("Для заказа ещё не создан счёт Heleket")
        invoice = await get_heleket_payment(invoice_id=invoice_id, order_id=merchant_order_id)
        if not is_heleket_invoice_paid(invoice):
            return order
        if payment_invoice is not None:
            mark_order_paid_from_invoice(order_id, payment_invoice)
        else:
            mark_order_paid(order_id)
    elif payment_provider == "crystalpay":
        invoice_id = invoice_source.get("invoice_id") or invoice_source.get("xrocket_invoice_id")
        if not invoice_id:
            raise CrystalPayError("Для заказа ещё не создан счёт")
        invoice = await get_crystalpay_invoice(str(invoice_id))
        if not is_crystalpay_invoice_paid(invoice):
            return order
        if payment_invoice is not None:
            mark_order_paid_from_invoice(order_id, payment_invoice)
        else:
            mark_order_paid(order_id)
    else:
        invoice_id = (
            invoice_source.get("invoice_id")
            or invoice_source.get("xrocket_invoice_id")
            or invoice_source.get("client_invoice_id")
            or invoice_source.get("xrocket_client_invoice_id")
        )
        if not invoice_id:
            raise XRocketError("Для заказа ещё не создан счёт XROCKET")
        invoice = await get_xrocket_invoice(invoice_id)
        if not is_xrocket_invoice_paid(invoice):
            return order
        if payment_invoice is not None:
            mark_order_paid_from_invoice(order_id, payment_invoice)
        else:
            mark_order_paid(order_id)

    paid_order = get_order(order_id)
    if paid_order is None:
        return None

    await log_order_debug(
        resolve_order_bot(default_bot, paid_order),
        paid_order,
        get_purchase_buyer_label(buyer_message, paid_order),
        f"Оплата через {get_payment_provider_label(payment_provider)} подтверждена",
    )
    return paid_order


async def continue_order_fulfillment(
    order_id: int,
    *,
    default_bot: Bot | None,
    notify_chat_id: int | None = None,
    buyer_message: Message | None = None,
    status_message: Message | None = None,
) -> dict | None:
    order = get_order(order_id)
    if order is None:
        return None

    resolved_bot = resolve_order_bot(default_bot, order)

    if order.get("status") == "delivered" and order.get("delivery_text"):
        if status_message is not None:
            await safe_edit_text(
                status_message,
                build_delivered_text(order, order["delivery_text"]),
                reply_markup=build_delivery_keyboard(order_id),
                parse_mode="HTML",
            )
        return order

    if order.get("status") == "credited":
        if status_message is not None:
            await safe_edit_text(
                status_message,
                build_balance_credit_text(order),
                reply_markup=build_result_keyboard(),
            )
        return order

    if is_account_order(order):
        if order.get("status") != "delivery_pending":
            mark_order_delivery_pending(order_id)
            order = get_order(order_id) or order
        if status_message is not None:
            await safe_edit_text(
                status_message,
                build_market_delivery_pending_text(order_id),
                reply_markup=build_market_delivery_pending_keyboard(order_id),
            )
        schedule_market_order_delivery(
            resolved_bot,
            order_id,
            int(notify_chat_id or order["user_id"]),
            buyer_message=buyer_message,
            status_message=None,
        )
        return get_order(order_id)

    await fulfill_proxy_order(
        status_message,
        order_id,
        notify_bot=resolved_bot,
        notify_chat_id=notify_chat_id,
        buyer_message=buyer_message,
        update_status_message=status_message is not None,
    )
    return get_order(order_id)


async def resume_pending_market_deliveries(bot: Bot | None) -> None:
    for order in list_pending_market_delivery_orders():
        schedule_market_order_delivery(
            resolve_order_bot(bot, order),
            int(order["id"]),
            int(order["user_id"]),
            buyer_message=None,
            status_message=None,
        )
    if bot is None:
        return
    for order in list_pending_proxy_delivery_orders():
        order_id = int(order["id"])
        existing_task = PROXY_ORDER_DELIVERY_TASKS.get(order_id)
        if existing_task is not None and not existing_task.done():
            continue
        task = asyncio.create_task(
            fulfill_proxy_order(
                None,
                order_id,
                notify_bot=resolve_order_bot(bot, order),
                notify_chat_id=int(order["user_id"]),
                update_status_message=False,
                notify_pending=False,
            ),
            name=f"proxy-order-delivery-{order_id}",
        )
        PROXY_ORDER_DELIVERY_TASKS[order_id] = task
        task.add_done_callback(lambda _, value=order_id: PROXY_ORDER_DELIVERY_TASKS.pop(value, None))


def build_profile_back_keyboard(language_code: str | None = None) -> InlineKeyboardMarkup:
    lang = resolve_language_code(language_code=language_code)
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=tr(lang, "common.back"), callback_data="profile")]]
    )


def build_broadcast_buttons_prompt_keyboard(selected: list[str] | None = None) -> InlineKeyboardMarkup:
    selected_set = set(selected or [])
    sections = (
        ("goods", "Товары"),
        ("proxy", "Прокси"),
        ("email", "Почта"),
        ("sms", "SMS"),
        ("profile", "Профиль"),
        ("partner", "Партнёрская программа"),
        ("support", "Поддержка"),
    )
    rows = [
        [
            InlineKeyboardButton(
                text=f"{'✅' if key in selected_set else '▫️'} {label}",
                callback_data=f"admin_broadcast_section:{key}",
            )
        ]
        for key, label in sections
    ]
    rows.extend(
        [
            [InlineKeyboardButton(text="➕ Добавить свои URL-кнопки", callback_data="admin_broadcast_custom_buttons")],
            [InlineKeyboardButton(text="👁 Предпросмотр и подтверждение", callback_data="admin_broadcast_preview")],
            [InlineKeyboardButton(text="Без кнопок", callback_data="admin_broadcast_skip_buttons")],
            [InlineKeyboardButton(text="◀️ Отмена", callback_data="admin_panel")],
        ]
    )
    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


def build_broadcast_sections_markup(bot: Bot, selected: list[str], custom_buttons_text: str = "") -> InlineKeyboardMarkup | None:
    from miniapp import build_email_url, build_sms_url

    rows: list[list[InlineKeyboardButton]] = []
    selected_set = set(selected or [])
    if "goods" in selected_set:
        rows.append([InlineKeyboardButton(text="Товары", callback_data="magazine", icon_custom_emoji_id=GOODS_BUTTON_EMOJI_ID)])
    partner_bot = get_current_partner_bot(bot)
    bot_username = str(partner_bot.get("bot_username") or "").strip() if partner_bot else None
    if "proxy" in selected_set:
        rows.append([
            InlineKeyboardButton(
                text="Прокси",
                callback_data="proxy_test:home",
                icon_custom_emoji_id=PROXY_MINIAPP_BUTTON_EMOJI_ID,
                style="success",
            )
        ])
    email_url = build_email_url(bot_username)
    sms_url = build_sms_url(bot_username)
    if "email" in selected_set and email_url:
        rows.append([InlineKeyboardButton(text="Почта", web_app=WebAppInfo(url=email_url), icon_custom_emoji_id="6030784887093464891")])
    if "sms" in selected_set and sms_url:
        rows.append([InlineKeyboardButton(text="SMS", web_app=WebAppInfo(url=sms_url), icon_custom_emoji_id="5904248647972820334")])
    if "profile" in selected_set:
        rows.append([InlineKeyboardButton(text="Профиль", callback_data="profile", icon_custom_emoji_id=PROFILE_BUTTON_EMOJI_ID)])
    if "partner" in selected_set:
        rows.append([InlineKeyboardButton(text="Партнёрская программа", callback_data="partner_program", icon_custom_emoji_id=PARTNER_BOT_BUTTON_EMOJI_ID)])
    if "support" in selected_set:
        rows.append([InlineKeyboardButton(text="Поддержка", url="https://t.me/UniversallSupportBot?start=market", icon_custom_emoji_id=SUPPORT_BUTTON_EMOJI_ID)])
    custom_markup = parse_broadcast_buttons(custom_buttons_text) if custom_buttons_text else None
    if custom_markup:
        rows.extend(custom_markup.inline_keyboard)
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def build_admin_broadcast_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить рассылку", callback_data="admin_broadcast_confirm")],
            [InlineKeyboardButton(text="↩️ Изменить кнопки", callback_data="admin_broadcast_back_to_sections")],
            [InlineKeyboardButton(text="❌ Отменить", callback_data="admin_panel")],
        ]
    )


def format_money(value: float) -> str:
    return f"{float(value or 0.0):.2f}"


def parse_amount_text(text: str) -> float | None:
    normalized = (text or "").strip().replace(",", ".")
    try:
        amount = float(normalized)
    except ValueError:
        return None
    return round(amount, 2) if amount > 0 else None


def parse_promo_limit_text(text: str | None) -> int | None:
    normalized = (text or "").strip().lower()
    if not normalized or normalized in {"0", "-", "none", "unlimited", "безлимит", "inf", "infinity", "∞"}:
        return None
    if not normalized.isdigit():
        return -1
    value = int(normalized)
    return value if value > 0 else -1


def parse_broadcast_buttons(text: str) -> InlineKeyboardMarkup | None:
    rows = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if " - " in line:
            button_text, button_url = line.split(" - ", 1)
        elif "|" in line:
            button_text, button_url = line.split("|", 1)
        else:
            return None

        button_text = button_text.strip()
        button_url = button_url.strip()
        if not button_text or not button_url:
            return None
        if not (button_url.startswith("http://") or button_url.startswith("https://") or button_url.startswith("tg://")):
            return None
        rows.append([InlineKeyboardButton(text=button_text, url=button_url)])

    if not rows:
        return None
    return InlineKeyboardMarkup(inline_keyboard=rows)


def normalize_utm_value(value: str) -> str:
    normalized = re.sub(r"\s+", "-", (value or "").strip().lower())
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "", normalized)
    return normalized.strip("-_")


def parse_partner_utm_source_word(text: str) -> str | None:
    normalized_value = normalize_utm_value(text)
    if not normalized_value or " " in normalized_value:
        return None
    return normalized_value


def build_partner_utm_payload(source_word: str) -> str:
    return f"utm_{source_word}"


def extract_channel_reference_from_text(text: str) -> str | None:
    value = (text or "").strip()
    if not value:
        return None
    if value.startswith("@") and len(value) > 1:
        return value
    if value.startswith("https://t.me/") or value.startswith("http://t.me/"):
        slug = value.rstrip("/").rsplit("/", 1)[-1].strip()
        return f"@{slug}" if slug else None
    if value.startswith("-100") and value[1:].isdigit():
        return value
    return None


def extract_channel_reference_from_message(message: Message) -> str | None:
    forwarded_chat = getattr(message, "forward_from_chat", None)
    if forwarded_chat is not None:
        if getattr(forwarded_chat, "username", None):
            return f"@{forwarded_chat.username}"
        if getattr(forwarded_chat, "id", None):
            return str(forwarded_chat.id)

    forward_origin = getattr(message, "forward_origin", None)
    origin_chat = getattr(forward_origin, "chat", None) if forward_origin is not None else None
    if origin_chat is not None:
        if getattr(origin_chat, "username", None):
            return f"@{origin_chat.username}"
        if getattr(origin_chat, "id", None):
            return str(origin_chat.id)

    return extract_channel_reference_from_text(message.text or "")


async def resolve_subscription_channel_for_bot(bot_client: Bot, channel_reference: str) -> dict:
    bot_me = await bot_client.get_me()
    chat = await bot_client.get_chat(channel_reference)
    member = await bot_client.get_chat_member(chat.id, bot_me.id)
    if getattr(member, "status", None) not in {"administrator", "creator"}:
        raise ValueError(f"Бот @{bot_me.username or 'userbot'} ещё не является администратором этого канала.")

    username = getattr(chat, "username", None)
    title = getattr(chat, "title", None) or getattr(chat, "full_name", None) or "Канал"
    return {
        "channel_id": str(getattr(chat, "id")),
        "channel_title": title,
        "channel_username": username or "",
        "channel_url": (f"https://t.me/{username}" if username else ""),
    }


async def resolve_partner_subscription_channel(partner_bot: dict, channel_reference: str) -> dict:
    bot_client = Bot(token=partner_bot["bot_token"])
    try:
        return await resolve_subscription_channel_for_bot(bot_client, channel_reference)
    finally:
        with contextlib.suppress(Exception):
            await bot_client.session.close()


def build_profile_text(profile: dict, language_code: str | None = None) -> str:
    lang = resolve_language_code(language_code=language_code, profile=profile)
    active_discount_percent = float(profile.get("active_discount_percent") or 0.0)
    active_discount_code = html.escape(str(profile.get("active_discount_code") or tr(lang, "common.not_specified")))
    title_emoji = premium_emoji(PROFILE_TITLE_EMOJI_ID, "👤")
    first_line_emoji = premium_emoji(PROFILE_FIRST_LINE_EMOJI_ID, "🔹")
    line_emoji = premium_emoji(PROFILE_LINE_EMOJI_ID, "🔹")
    discount_line = (
        f"{line_emoji} {tr(lang, 'profile.active_discount', percent=active_discount_percent, code=active_discount_code)}\n"
        if active_discount_percent > 0
        else ""
    )
    return (
        f"{title_emoji} {tr(lang, 'profile.title')}\n"
        f"{first_line_emoji} {tr(lang, 'profile.id', value=html.escape(str(profile['user_id'])))}\n"
        f"{line_emoji} {tr(lang, 'profile.balance', value=html.escape(str(format_money(profile['balance']))))}\n"
        f"{line_emoji} {tr(lang, 'profile.purchases_count', value=html.escape(str(profile['purchases_count'])))}\n"
        f"{line_emoji} {tr(lang, 'profile.purchases_total', value=html.escape(str(format_money(profile['purchases_total']))))}\n"
        f"{line_emoji} {tr(lang, 'profile.topups_total', value=html.escape(str(format_money(profile['topups_total']))))}\n"
        f"{discount_line}"
        f"{line_emoji} {tr(lang, 'profile.registered_at', value=html.escape(str(profile['registered_at'])))}"
    )


def build_referral_text(
    profile: dict,
    ref_link: str,
    referral_percent: float = REFERRAL_PERCENT,
    referral_enabled: bool = True,
    language_code: str | None = None,
) -> str:
    lang = resolve_language_code(language_code=language_code, profile=profile)
    status_text = (
        tr(lang, "referral.enabled")
        if referral_enabled
        else tr(lang, "referral.disabled")
    )
    intro_emoji = premium_emoji(REFERRAL_INTRO_EMOJI_ID, "🤝")
    invited_emoji = premium_emoji(REFERRAL_INVITED_EMOJI_ID, "👥")
    earned_emoji = premium_emoji(REFERRAL_EARNED_EMOJI_ID, "💎")
    return (
        f"{intro_emoji} {status_text}\n\n"
        f"{tr(lang, 'referral.receive', percent=float(referral_percent))}\n"
        f"{invited_emoji} {tr(lang, 'referral.invited', value=profile['referrals_count'])}\n"
        f"{earned_emoji} {tr(lang, 'referral.earned', value=format_money(profile['referral_earnings']))}\n\n"
        f"{tr(lang, 'referral.link_title')}\n"
        f"{html.escape(ref_link)}"
    )


def build_proxy_details_text(category: dict, unit_price: float, language_code: str | None = None) -> str:
    lang = resolve_language_code(language_code=language_code)
    country_name = resolve_localized_text(category.get("name"), lang, fallback=tr(lang, "common.not_specified"))
    return (
        f"{tr(lang, 'proxy.static_title')}\n"
        f"{tr(lang, 'proxy.country', value=country_name)}\n"
        f"{tr(lang, 'proxy.price_per_item', value=unit_price)}\n\n"
        f"{tr(lang, 'proxy.choose_protocol')}"
    )


def build_quantity_selection_text(category: dict, item: dict, unit_price: float, language_code: str | None = None) -> str:
    lang = resolve_language_code(language_code=language_code)
    country_name = resolve_localized_text(category.get("name"), lang, fallback=tr(lang, "common.not_specified"))
    protocol_name = resolve_localized_text(item.get("name"), lang, fallback=tr(lang, "common.not_specified"))
    return (
        f"{tr(lang, 'proxy.static_title')}\n\n"
        f"{tr(lang, 'proxy.country', value=country_name)}\n"
        f"{tr(lang, 'proxy.protocol', value=protocol_name)}\n"
        f"{tr(lang, 'proxy.price_per_item', value=unit_price)}\n\n"
        f"{tr(lang, 'proxy.choose_quantity')}"
    )


def build_quantity_prompt_text(category: dict, item: dict, unit_price: float, language_code: str | None = None) -> str:
    lang = resolve_language_code(language_code=language_code)
    country_name = resolve_localized_text(category.get("name"), lang, fallback=tr(lang, "common.not_specified"))
    protocol_name = resolve_localized_text(item.get("name"), lang, fallback=tr(lang, "common.not_specified"))
    return (
        f"{tr(lang, 'proxy.static_title')}\n\n"
        f"{tr(lang, 'proxy.country', value=country_name)}\n"
        f"{tr(lang, 'proxy.protocol', value=protocol_name)}\n"
        f"{tr(lang, 'proxy.price_per_item', value=unit_price)}\n\n"
        f"{tr(lang, 'proxy.send_quantity')}"
    )


def build_market_categories_text(language_code: str | None = None) -> str:
    # Telegram does not allow an empty caption/text; this separator is invisible.
    return "\u2063"


def build_market_subcategories_text(category: dict, language_code: str | None = None) -> str:
    lang = resolve_language_code(language_code=language_code)
    category_name = strip_supplier_decorations(get_localized_market_category_label(category, lang))
    return (
        f"🛒 {category_name}\n\n"
        f"{tr(lang, 'market.choose_subcategory')}"
    )


def build_market_products_text(
    category: dict,
    products: list[dict],
    page: int,
    last_page: int,
    margin_percentage: int,
    language_code: str | None = None,
) -> str:
    lang = resolve_language_code(language_code=language_code)
    category_name = strip_supplier_decorations(get_localized_market_category_label(category, lang))
    if not products:
        return f"🛒 {category_name}\n\n{tr(lang, 'market.empty_category')}"

    lines = [f"🛒 {category_name}\n"]
    for product in products:
        # Use exactly the same normalized supplier title in both the message
        # list and its inline button, otherwise emoji/arrows make them look
        # like two different products.
        product_title = strip_supplier_decorations(
            localize_market_text(product.get("title"), lang, fallback=tr(lang, "order.default_item"))
        )
        lines.append(
            f"• {truncate_text(product_title, 70)}\n"
            f"  {convert_rub_to_usdt(product.get('price', 0), margin_percentage):.2f} $ | "
            f"{tr(lang, 'market.stock', value=int(product.get('quantity') or 0)).replace('📦 ', '')}"
        )
    lines.append("")
    lines.append(tr(lang, "xrocket.page", page=page, total=last_page))
    return "\n".join(lines)


def build_market_product_text(
    category: dict,
    product: dict,
    margin_percentage: int,
    language_code: str | None = None,
) -> str:
    lang = resolve_language_code(language_code=language_code)
    category_name = strip_supplier_decorations(get_localized_market_category_label(category, lang))
    product_title = strip_supplier_decorations(
        localize_market_text(product.get("title"), lang, fallback=tr(lang, "order.default_item"))
    )
    attributes = [strip_supplier_decorations(localize_market_text(row.get("value"), lang)) for row in product.get("attributes", []) if row.get("value")]
    attributes_line = ", ".join(attributes[:6]) if attributes else tr(lang, "market.no_attributes")
    description = truncate_multiline_text(
        strip_supplier_decorations(localize_market_text(clean_html_text(product.get("description") or ""), lang)),
        1200,
    )
    if not description:
        description = tr(lang, "market.description_missing")
    category_emoji = premium_emoji(MARKET_CATEGORY_EMOJI_ID, "📂")
    product_emoji = premium_emoji(MARKET_PRODUCT_EMOJI_ID, "🛍")
    price_emoji = premium_emoji(MARKET_PRICE_EMOJI_ID, "💰")
    stock_emoji = premium_emoji(MARKET_STOCK_EMOJI_ID, "📦")
    attributes_emoji = premium_emoji(MARKET_ATTRIBUTES_EMOJI_ID, "🏷")
    description_emoji = premium_emoji(MARKET_DESCRIPTION_EMOJI_ID, "📝")
    quantity_emoji = premium_emoji(MARKET_QUANTITY_EMOJI_ID, "🛒")
    return (
        f"{product_emoji} <b>{html.escape(product_title)}</b>\n\n"
        f"{category_emoji} <b>{tr(lang, 'market.category', value=html.escape(category_name)).removeprefix('📂 ')}</b>\n"
        f"{price_emoji} <b>{tr(lang, 'market.price', value=format_rub_and_usdt(product.get('price', 0), margin_percentage)).removeprefix('💰 ')}</b>\n"
        f"{stock_emoji} {tr(lang, 'market.stock', value=int(product.get('quantity') or 0)).removeprefix('📦 ')}\n"
        f"{attributes_emoji} {tr(lang, 'market.attributes', value=html.escape(attributes_line)).removeprefix('🏷 ')}\n\n"
        f"{description_emoji} <b>{tr(lang, 'market.description_title').removeprefix('📝 ')}</b>\n\n"
        f"{html.escape(description)}\n\n"
        f"{quantity_emoji} <b>{tr(lang, 'market.choose_quantity')}</b>"
    )


def get_market_direct_child_id(category: dict) -> int | None:
    """Skip redundant one-item category screens such as VPN -> VPN services."""
    children = category.get("children") or []
    if int(category.get("id") or 0) == 75 and len(children) == 1:
        return int(children[0].get("id") or 0) or None
    return None


def build_market_quantity_prompt_text(
    product: dict,
    margin_percentage: int,
    language_code: str | None = None,
) -> str:
    lang = resolve_language_code(language_code=language_code)
    product_title = localize_market_text(product.get("title"), lang, fallback=tr(lang, "order.default_item"))
    return (
        f"{tr(lang, 'market.quantity_title')}\n\n"
        f"{tr(lang, 'market.product', value=product_title)}\n"
        f"{tr(lang, 'market.price', value=format_rub_and_usdt(product.get('price', 0), margin_percentage))}\n"
        f"{tr(lang, 'market.stock', value=int(product.get('quantity') or 0))}\n\n"
        f"{tr(lang, 'market.send_quantity')}"
    )


def build_order_primary_line(order: dict, language_code: str | None = None) -> tuple[str, str]:
    lang = resolve_language_code(language_code=language_code, user_id=int(order.get("user_id") or 0) or None)
    if is_account_order(order):
        return tr(lang, "order.primary.category"), order["category_name"]
    return tr(lang, "order.primary.country"), order["category_name"]


def build_order_secondary_line(order: dict, language_code: str | None = None) -> tuple[str, str]:
    lang = resolve_language_code(language_code=language_code, user_id=int(order.get("user_id") or 0) or None)
    if is_account_order(order):
        return tr(lang, "order.secondary.product"), order.get("product_title") or tr(lang, "order.default_item")
    return tr(lang, "order.secondary.protocol"), order["protocol"]


def build_order_preview_text(order: dict, language_code: str | None = None) -> str:
    lang = resolve_language_code(language_code=language_code, user_id=int(order.get("user_id") or 0) or None)
    primary_label, primary_value = build_order_primary_line(order, lang)
    secondary_label, secondary_value = build_order_secondary_line(order, lang)
    discount_percent = float(order.get("promo_discount_percent") or 0.0)
    original_unit_price = order.get("original_unit_price")
    original_total_price = order.get("original_total_price")
    discount_lines = ""
    if discount_percent > 0 and original_total_price:
        discount_lines = (
            f"{tr(lang, 'order.promo', value=order.get('promo_code_text') or 'discount')}\n"
            f"{tr(lang, 'order.discount', value=discount_percent)}\n"
            f"{tr(lang, 'order.price_no_discount', value=float(original_unit_price or 0))}\n"
            f"{tr(lang, 'order.total_no_discount', value=float(original_total_price or 0))}\n"
        )
    return (
        f"{tr(lang, 'order.confirmation_title')}\n\n"
        f"{primary_label}: {primary_value}\n"
        f"{secondary_label}: {secondary_value}\n"
        f"{tr(lang, 'order.quantity', value=order['quantity'])}\n"
        f"{discount_lines}"
        f"{tr(lang, 'order.unit_price', value=order['unit_price'])}\n"
        f"{tr(lang, 'order.total', value=order['total_price'])}\n"
        f"{tr(lang, 'order.next_choose_payment')}"
    )


def build_payment_methods_text(order: dict, language_code: str | None = None) -> str:
    lang = resolve_language_code(language_code=language_code, user_id=int(order.get("user_id") or 0) or None)
    primary_label, primary_value = build_order_primary_line(order, lang)
    secondary_label, secondary_value = build_order_secondary_line(order, lang)
    discount_percent = float(order.get("promo_discount_percent") or 0.0)
    original_total_price = order.get("original_total_price")
    discount_lines = ""
    if discount_percent > 0 and original_total_price:
        discount_lines = (
            f"{tr(lang, 'order.promo', value=order.get('promo_code_text') or 'discount')}\n"
            f"{tr(lang, 'order.discount', value=discount_percent)}\n"
            f"{tr(lang, 'order.total_no_discount', value=float(original_total_price or 0))}\n"
        )
    return (
        f"{tr(lang, 'payment.choose_method')}\n\n"
        f"{primary_label}: {primary_value}\n"
        f"{secondary_label}: {secondary_value}\n"
        f"{tr(lang, 'order.quantity', value=order['quantity'])}\n"
        f"{discount_lines}"
        f"{tr(lang, 'order.total', value=order['total_price'])}"
    )


def build_topup_methods_text(language_code: str | None = None) -> str:
    lang = resolve_language_code(language_code=language_code)
    return tr(lang, "topup.choose_method")


def build_topup_amount_text(provider_label: str = "XROCKET", language_code: str | None = None) -> str:
    lang = resolve_language_code(language_code=language_code)
    return f"{tr(lang, 'topup.title')} {provider_label}\n\n{tr(lang, 'topup.amount_prompt')}"


def build_topup_wait_payment_text(
    topup: dict,
    pay_url: str | None,
    invoice_currency: str | None = None,
    invoice_amount: float | None = None,
    provider_label: str = "XROCKET",
    language_code: str | None = None,
) -> str:
    lang = resolve_language_code(language_code=language_code, user_id=int(topup.get("user_id") or 0) or None)
    pay_line = pay_url if pay_url else tr(lang, "topup.pay_link_missing")
    invoice_line = (
        f"{tr(lang, 'topup.amount_provider', provider=provider_label, amount=float(invoice_amount or 0), currency=invoice_currency)}\n"
        if invoice_currency and invoice_amount is not None
        else ""
    )
    return (
        f"{tr(lang, 'topup.invoice_created')}\n\n"
        f"{tr(lang, 'topup.number', value=topup['id'])}\n"
        f"{tr(lang, 'topup.amount', value=topup['amount'])}\n"
        f"{invoice_line}"
        f"{tr(lang, 'topup.method', value=provider_label)}\n\n"
        f"{tr(lang, 'topup.pay_link_title')}\n{pay_line}\n\n"
        f"{tr(lang, 'topup.after_payment')}"
    )


def build_topup_success_text(topup: dict, language_code: str | None = None) -> str:
    lang = resolve_language_code(language_code=language_code, user_id=int(topup.get("user_id") or 0) or None)
    return (
        f"{tr(lang, 'topup.success')}\n\n"
        f"{tr(lang, 'topup.number', value=topup['id'])}\n"
        f"{tr(lang, 'topup.credited', value=topup['amount'])}"
    )


def get_order_status_label(status: str, language_code: str | None = None) -> str:
    key = f"status.{status}"
    value = tr(resolve_language_code(language_code=language_code), key)
    return status if value == key else value


def build_orders_list_text(orders: list[dict], page: int, total: int, language_code: str | None = None) -> str:
    lang = resolve_language_code(
        language_code=language_code,
        user_id=int(orders[0].get("user_id") or 0) if orders else None,
    )
    if not orders:
        return f"{tr(lang, 'orders.title')}\n\n{tr(lang, 'orders.empty')}"

    lines = [f"{tr(lang, 'orders.title')}\n"]
    for order in orders:
        item_label = order.get("product_title") if is_account_order(order) else order["protocol"]
        lines.append(
            f"#{get_customer_order_number(order)} | {truncate_text(order['category_name'], 24)} | {truncate_text(item_label or '', 28)} | "
            f"{order['total_price']:.2f} $ | {get_order_status_label(order['status'], lang)}"
        )
    lines.append("")
    lines.append(tr(lang, "orders.page_total", page=page + 1, total=total))
    return "\n".join(lines)


def build_orders_list_keyboard(orders: list[dict], page: int, total: int, language_code: str | None = None) -> InlineKeyboardMarkup:
    lang = resolve_language_code(
        language_code=language_code,
        user_id=int(orders[0].get("user_id") or 0) if orders else None,
    )
    builder = InlineKeyboardBuilder()
    for order in orders:
        item_label = order.get("product_title") if is_account_order(order) else order["protocol"]
        builder.row(
            InlineKeyboardButton(
                text=(
                    f"#{get_customer_order_number(order)} • {truncate_text(item_label or order['category_name'], 18)} • "
                    f"{get_order_status_label(order['status'], lang)}"
                ),
                callback_data=f"profile_order:{order['id']}:{page}",
            )
        )
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="◀️", callback_data=f"profile_orders:{page - 1}"))
    if (page + 1) * ORDERS_PAGE_SIZE < total:
        nav_row.append(InlineKeyboardButton(text="▶️", callback_data=f"profile_orders:{page + 1}"))
    if nav_row:
        builder.row(*nav_row)
    builder.row(InlineKeyboardButton(text=tr(lang, "common.back"), callback_data="profile"))
    return builder.as_markup()


def build_order_detail_text(order: dict, language_code: str | None = None) -> str:
    lang = resolve_language_code(language_code=language_code, user_id=int(order.get("user_id") or 0) or None)
    primary_label, primary_value = build_order_primary_line(order, lang)
    secondary_label, secondary_value = build_order_secondary_line(order, lang)
    discount_percent = float(order.get("promo_discount_percent") or 0.0)
    original_total_price = order.get("original_total_price")
    discount_lines = ""
    if discount_percent > 0 and original_total_price:
        discount_lines = (
            f"{tr(lang, 'order.promo', value=order.get('promo_code_text') or 'discount')}\n"
            f"{tr(lang, 'order.discount', value=discount_percent)}\n"
            f"{tr(lang, 'order.total_no_discount', value=float(original_total_price or 0))}\n"
        )
    return (
        f"{tr(lang, 'wait_invoice.order_number', value=get_customer_order_number(order))}\n\n"
        f"{primary_label}: {primary_value}\n"
        f"{secondary_label}: {secondary_value}\n"
        f"{tr(lang, 'order.quantity', value=order['quantity'])}\n"
        f"{discount_lines}"
        f"{tr(lang, 'order.amount_short', value=order['total_price'])}\n"
        f"{tr(lang, 'order.status', value=get_order_status_label(order['status'], lang))}\n"
        f"{tr(lang, 'order.created', value=order['created_at'])}"
    )


def build_order_detail_keyboard(order: dict, page: int, language_code: str | None = None) -> InlineKeyboardMarkup:
    lang = resolve_language_code(language_code=language_code, user_id=int(order.get("user_id") or 0) or None)
    rows = []
    if order.get("status") == "delivered" and order.get("delivery_text"):
        rows.append([InlineKeyboardButton(text=tr(lang, "common.download_txt"), callback_data=f"download_order:{order['id']}")])
    rows.append([InlineKeyboardButton(text=tr(lang, "common.back"), callback_data=f"profile_orders:{page}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_wait_payment_text(
    order: dict,
    pay_url: str | None,
    invoice_currency: str | None = None,
    invoice_amount: float | None = None,
    provider_label: str = "XROCKET",
    language_code: str | None = None,
) -> str:
    lang = resolve_language_code(language_code=language_code, user_id=int(order.get("user_id") or 0) or None)
    pay_line = pay_url if pay_url else tr(lang, "topup.pay_link_missing")
    invoice_line = (
        f"{tr(lang, 'topup.amount_provider', provider=provider_label, amount=float(invoice_amount or 0), currency=invoice_currency)}\n"
        if invoice_currency and invoice_amount is not None
        else ""
    )
    return (
        f"{tr(lang, 'wait_invoice.created')}\n\n"
        f"{tr(lang, 'wait_invoice.order_number', value=order['id'])}\n"
        f"{tr(lang, 'wait_invoice.to_pay', value=order['total_price'])}\n"
        f"{invoice_line}"
        f"{tr(lang, 'wait_invoice.method', value=provider_label)}\n\n"
        f"{tr(lang, 'topup.pay_link_title')}\n{pay_line}\n\n"
        f"{tr(lang, 'wait_invoice.after_payment')}"
    )


def build_delivered_text(order: dict, delivery_text: str, language_code: str | None = None) -> str:
    lang = resolve_language_code(language_code=language_code, user_id=int(order.get("user_id") or 0) or None)
    if is_account_order(order):
        return (
            f"{tr(lang, 'delivery.accounts')}\n\n"
            f"{tr(lang, 'wait_invoice.order_number', value=get_customer_order_number(order))}\n"
            f"{tr(lang, 'order.primary.category')}: {order['category_name']}\n"
            f"{tr(lang, 'order.secondary.product')}: {order.get('product_title') or tr(lang, 'order.default_item')}\n"
            f"{tr(lang, 'order.quantity', value=order['quantity'])}\n\n"
            f"{tr(lang, 'delivery.saved_txt')}"
        )

    preview = html.escape(delivery_text[:1400])
    return (
        f"{tr(lang, 'delivery.proxies')}\n\n"
        f"{tr(lang, 'wait_invoice.order_number', value=order['id'])}\n"
        f"{tr(lang, 'order.primary.country')}: {order['category_name']}\n"
        f"{tr(lang, 'order.secondary.protocol')}: {order['protocol']}\n"
        f"{tr(lang, 'order.quantity', value=order['quantity'])}\n\n"
        f"{tr(lang, 'delivery.your_proxies')}\n"
        f"<code>{preview}</code>"
    )


def build_balance_credit_text(order: dict, language_code: str | None = None) -> str:
    lang = resolve_language_code(language_code=language_code, user_id=int(order.get("user_id") or 0) or None)
    return (
        f"{tr(lang, 'balance.confirmed')}\n\n"
        f"{tr(lang, 'wait_invoice.order_number', value=order['id'])}\n"
        f"{tr(lang, 'order.amount_short', value=order['total_price'])}\n\n"
        f"{tr(lang, 'balance.support_unavailable')}"
    )


def build_balance_payment_success_text(order: dict, language_code: str | None = None) -> str:
    lang = resolve_language_code(language_code=language_code, user_id=int(order.get("user_id") or 0) or None)
    return (
        f"{tr(lang, 'balance.payment_confirmed')}\n\n"
        f"{tr(lang, 'wait_invoice.order_number', value=order['id'])}\n"
        f"{tr(lang, 'balance.debited', value=order['total_price'])}\n\n"
        f"{tr(lang, 'balance.waiting_delivery')}"
    )


def build_admin_panel_text() -> str:
    return "🛠 Админка\n\nВыберите действие:"


def get_promo_status_label(promo_code: dict) -> str:
    max_activations = promo_code.get("max_activations")
    if max_activations is not None and int(promo_code.get("activations_count") or 0) >= int(max_activations):
        return "лимит исчерпан"
    if int(promo_code.get("is_active") or 0) != 1:
        return "выключен"
    return "активен"


def format_promo_limit_line(promo_code: dict) -> str:
    used = int(promo_code.get("activations_count") or 0)
    max_activations = promo_code.get("max_activations")
    if max_activations is None:
        return f"{used}/∞"
    return f"{used}/{int(max_activations)}"


def build_admin_promocodes_text(promo_codes: list[dict]) -> str:
    if promo_codes:
        recent_lines = []
        for row in promo_codes[:8]:
            reward_label = (
                f"+{float(row['reward_value'] or 0):.2f}$"
                if row["reward_type"] == "balance"
                else f"{float(row['reward_value'] or 0):.2f}% скидка"
            )
            recent_lines.append(
                f"• {row['code']} -> {reward_label} | активации: {format_promo_limit_line(row)} | {get_promo_status_label(row)}"
            )
        recent_text = "\n".join(recent_lines)
    else:
        recent_text = "Пока нет созданных промокодов."

    return (
        "🎟 Промокоды\n\n"
        "Здесь можно создавать промокоды двух типов:\n"
        "1. Начисление суммы в $ на баланс.\n"
        "2. Процент скидки на следующий созданный заказ.\n\n"
        "Лимит активаций можно указывать при создании третьим параметром.\n"
        "Если лимит не указан, промокод будет безлимитным.\n\n"
        "Последние промокоды:\n"
        f"{recent_text}"
    )


def build_admin_promocodes_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💵 Промокод на баланс", callback_data="admin_promo_create_balance")],
            [InlineKeyboardButton(text="🏷 Промокод на скидку", callback_data="admin_promo_create_discount")],
            [InlineKeyboardButton(text="🗑 Удалить промокод", callback_data="admin_promo_delete")],
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_promocodes")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")],
        ]
    )


def build_admin_franchises_text(stats: dict) -> str:
    summary = (
        "🏪 Все франшизы\n\n"
        f"Активных ботов: {stats['total_bots']}\n"
        f"Пользователей во франшизах: {stats['total_users']}\n"
        f"Выданных заказов: {stats['delivered_orders']}\n"
        f"Оборот: {stats['delivered_revenue']:.2f} $\n"
        f"Прибыль партнёров: {stats['profit']:.2f} $"
    )

    bots = stats.get("bots") or []
    if not bots:
        return summary + "\n\nСписок ботов пуст."

    bot_lines = [
        f"{bot['bot_username']} - {bot['users_count']} пользователей - {bot['partner_earnings']:.2f} $"
        for bot in bots
    ]
    return summary + "\n\nСписок ботов:\n" + "\n".join(bot_lines)


def build_admin_franchises_keyboard() -> InlineKeyboardMarkup:
    stats = get_all_franchises_stats()
    rows = [
        [InlineKeyboardButton(text="⚙️ Настроить франшизу", callback_data="admin_franchise_settings")],
        [InlineKeyboardButton(text="💸 Списать с баланса", callback_data="admin_franchise_payouts")],
        [InlineKeyboardButton(text="📣 Рассылка по франшизам", callback_data="admin_franchises_broadcast")],
        [InlineKeyboardButton(text="🔄 Обновить статистику", callback_data="admin_franchises")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_admin_franchise_settings_keyboard(stats: dict) -> InlineKeyboardMarkup:
    rows = []
    for bot in stats.get("bots") or []:
        rows.append([InlineKeyboardButton(text=f"@{str(bot['bot_username']).lstrip('@')}", callback_data=f"admin_franchise_settings:{bot['id']}")])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_franchises")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_admin_franchise_edit_keyboard(partner_bot: dict) -> InlineKeyboardMarkup:
    partner_id = int(partner_bot["id"])
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📰 Изменить новостной канал", callback_data=f"admin_franchise_edit:{partner_id}:franchise_news_url")],
            [InlineKeyboardButton(text="🛠 Изменить поддержку", callback_data=f"admin_franchise_edit:{partner_id}:franchise_support_url")],
            [InlineKeyboardButton(text="📜 Изменить пользовательское соглашение", callback_data=f"admin_franchise_edit:{partner_id}:franchise_usage_url")],
            [InlineKeyboardButton(text="🔒 Изменить политику конфиденциальности", callback_data=f"admin_franchise_edit:{partner_id}:franchise_privacy_url")],
            [InlineKeyboardButton(text="✏️ Изменить название в информации", callback_data=f"admin_franchise_edit:{partner_id}:franchise_info_brand")],
            [InlineKeyboardButton(text=("✅ Скрыть создание партнёрского бота" if int(partner_bot.get("franchise_hide_create", 1) or 0) == 0 else "❌ Показать создание партнёрского бота"), callback_data=f"admin_franchise_toggle_create:{partner_id}")],
            [InlineKeyboardButton(text="◀️ К списку франшиз", callback_data="admin_franchise_settings")],
        ]
    )


def build_admin_franchises_broadcast_buttons_prompt_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➡️ Пропустить кнопки", callback_data="admin_franchises_broadcast_skip_buttons")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_franchises")],
        ]
    )


def build_admin_franchises_broadcast_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить рассылку", callback_data="admin_franchises_broadcast_confirm")],
            [InlineKeyboardButton(text="❌ Отменить", callback_data="admin_franchises")],
        ]
    )


def build_admin_main_subscription_text(settings: dict, bot_username: str) -> str:
    status = "Включена ✅" if int(settings.get("subscription_enabled") or 0) == 1 else "Выключена ❌"
    channel_url = settings.get("subscription_channel_url") or "Не установлен"
    channel_id = (settings.get("subscription_channel_id") or "").strip()
    channel_label = channel_url if channel_url != "Не установлен" else ("Канал выбран" if channel_id else "Не выбран")
    return (
        "🔔 Подписка в главном боте\n\n"
        f"Статус: {status}\n"
        f"Канал: {channel_label}\n"
        f"Ссылка: {channel_url}\n\n"
        "📌 Мини-инструкция:\n"
        f"1. Добавьте бота @{bot_username} в администраторы канала.\n"
        "2. Нажмите «Выбрать канал».\n"
        "3. Перешлите любой пост из канала или отправьте @username канала.\n"
        "4. Подтвердите найденный канал.\n"
        "5. При необходимости укажите URL и включите статус подписки.\n\n"
        "ℹ️ Для приватного канала укажите рабочую ссылку-приглашение."
    )


def build_admin_main_subscription_keyboard(settings: dict) -> InlineKeyboardMarkup:
    enabled = int(settings.get("subscription_enabled") or 0) == 1
    toggle_text = "Статус: ✅" if enabled else "Статус: ❌"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=toggle_text, callback_data="admin_main_subscription_toggle")],
            [InlineKeyboardButton(text="Выбрать канал", callback_data="admin_main_subscription_channel_id")],
            [InlineKeyboardButton(text="Изменить URL", callback_data="admin_main_subscription_channel_url")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")],
        ]
    )


def build_admin_main_subscription_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, выбрать канал", callback_data="admin_main_subscription_confirm")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_main_subscription")],
        ]
    )


def build_admin_users_text(total_users: int) -> str:
    return f"👥 Пользователи\n\nВсего пользователей: {total_users}"


def build_admin_balance_manage_text() -> str:
    return (
        "💳 Добавление баланса пользователю\n\n"
        "Отправьте данные в формате:\n"
        "USER_ID СУММА\n\n"
        "Если нужно списать баланс, укажите сумму с минусом.\n\n"
        "Примеры:\n"
        "1907513941 5\n"
        "1907513941 -2.5"
    )


def build_admin_api_balance_manage_text() -> str:
    return (
        "🔌 Начисление на API-баланс пользователя\n\n"
        "Укажите Telegram ID пользователя (не ID API-аккаунта) и сумму в $:\n"
        "USER_ID СУММА\n\n"
        "Для списания укажите сумму с минусом.\n"
        "У пользователя должен быть одобренный доступ к API.\n\n"
        "Примеры:\n"
        "1907513941 5\n"
        "1907513941 -2.5"
    )


# Admin-editable pricing knobs. Each entry: DB key -> (button label, screen
# label, unit, hint shown when prompting for a new value). Defaults live in
# database.data (MAIN_BOT_PRICING_KEYS) and in miniapp.py / keyboard.py.
ADMIN_PRICING_FIELDS: tuple[tuple[str, str, str, str, str], ...] = (
    ("market_markup_percent", "🛍 Товары · наценка в SOUS MARKET", "Товары · наценка в SOUS MARKET", "%", "35"),
    ("api_markup_percent", "🧩 Наценка в API (товары/прокси/SMS/почта)", "Наценка в API", "%", "35"),
    ("proxy_markup_percent", "🌐 Прокси · наценка в SOUS MARKET", "Прокси · наценка в SOUS MARKET", "%", "15"),
    ("sms_markup_percent", "📲 SMS · наценка в SOUS MARKET", "SMS · наценка в SOUS MARKET", "%", "50"),
    ("email_markup_percent", "📨 Почта · наценка в SOUS MARKET", "Почта · наценка в SOUS MARKET", "%", "50"),
    ("residential_base_price_per_gb", "🌐 Резид. трафик · базовая цена $/ГБ", "Резид. трафик · базовая цена (цена поставщика)", " $/ГБ", "0.3"),
    ("proxyma_markup_percent", "🖥 Proxyma (ISP/мобильные) · наценка", "Proxyma · наценка в SOUS MARKET", "%", "100"),
)
_ADMIN_PRICING_DEFAULTS = {
    "market_markup_percent": MARKET_MARKUP_PERCENT_DEFAULT,
    "api_markup_percent": 35.0,
    "proxy_markup_percent": float(PROXY_BASE_MARKUP_PERCENT_DEFAULT),
    "sms_markup_percent": 50.0,
    "email_markup_percent": 50.0,
    "residential_base_price_per_gb": 0.30,
    "proxyma_markup_percent": 100.0,
}


def _format_pricing_value(key: str, unit: str) -> str:
    value = get_main_bot_pricing_value(key, _ADMIN_PRICING_DEFAULTS[key])
    if unit.strip() == "%":
        shown = f"{value:g}%"
    else:
        shown = f"${value:g}/ГБ"
    return shown


def build_admin_pricing_text() -> str:
    lines = [
        "⚙️ Цены и наценки",
        "",
        "Наценка = процент поверх цены поставщика. Применяется вживую (кэш ~15 сек).",
        "«Наценка в API» общая для товаров, прокси, SMS и почты в /api/v1.",
        "Резид. трафик: цена в боте = базовая × (1 + «Прокси · наценка в SOUS MARKET»),",
        "в API = базовая × (1 + «Наценка в API»).",
        "Proxyma (статика ISP / мобильные): цена поставщика × (1 + наценка Proxyma).",
        "",
    ]
    for key, _button, label, unit, _hint in ADMIN_PRICING_FIELDS:
        lines.append(f"• {label}: {_format_pricing_value(key, unit)}")
    return "\n".join(lines)


def build_admin_pricing_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=button, callback_data=f"admin_pricing_set:{key}")]
        for key, button, _label, _unit, _hint in ADMIN_PRICING_FIELDS
    ]
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_admin_orders_stats_text(stats: dict) -> str:
    return (
        "📦 Статистика заказов\n\n"
        f"Всего заказов: {stats['total_orders']}\n"
        f"Выдано: {stats['delivered_orders']}\n"
        f"Ожидают оплату: {stats['waiting_orders']}\n"
        f"Зачислены в баланс: {stats['credited_orders']}\n"
        f"Оборот всех заказов: {stats['gross_revenue']:.2f} $\n"
        f"Оборот выданных: {stats['delivered_revenue']:.2f} $"
    )


CRM_CHANNEL_ICONS = {"bot": "🤖", "franchise": "🏪", "api": "🔌"}


def build_admin_crm_channels_block(channel_stats: dict | None) -> str:
    """Gross revenue of every sales channel: the main bot, the franchise network
    and the public API. Revenue covers all streams (goods, residential proxy,
    SMS, e-mail), profit only the goods/proxy orders that store a supplier
    cost."""
    if not channel_stats:
        return ""

    rows = channel_stats.get("channels") or []
    totals = channel_stats.get("total") or {}
    lines = ["", "💰 Валовый доход по каналам"]
    for row in rows:
        icon = CRM_CHANNEL_ICONS.get(row.get("key", ""), "•")
        lines.append(
            f"{icon} {row.get('title', '')}: {float(row.get('gross_revenue') or 0):.2f} $"
            f" · {float(row.get('revenue_share') or 0):.1f}%"
            f" · продаж {int(row.get('sales') or 0)}"
        )
        lines.append(
            f"   товары {float(row.get('goods_revenue') or 0):.2f} $"
            f" · прокси {float(row.get('proxy_revenue') or 0):.2f} $"
            f" · SMS {float(row.get('sms_revenue') or 0):.2f} $"
            f" · почта {float(row.get('email_revenue') or 0):.2f} $"
        )
        lines.append(
            f"   прибыль площадки {float(row.get('platform_profit') or 0):.2f} $"
            f" · франчайзи {float(row.get('partner_profit') or 0):.2f} $"
            f" · возвраты {float(row.get('refund_amount') or 0):.2f} $"
        )
    lines.append(
        f"Итого: {float(totals.get('gross_revenue') or 0):.2f} $"
        f" · сегодня {float(totals.get('today_revenue') or 0):.2f} $"
        f" · 7 дней {float(totals.get('week_revenue') or 0):.2f} $"
    )
    if not channel_stats.get("vproxy_available", True):
        lines.append("⚠️ База резидентных прокси недоступна — трафик не учтён")
    return "\n".join(lines)


def build_admin_crm_text(stats: dict) -> str:
    return (
        "📊 CRM статистика\n\n"
        f"Всего пользователей: {stats['total_users']}\n"
        f"Покупателей: {stats['buyers']}\n"
        f"Повторных покупателей: {stats['repeat_buyers']}\n"
        f"Пришли по рефералке: {stats['referred_users']}\n"
        f"Сумма пополнений: {stats['topups_total']:.2f} $\n"
        f"Сумма покупок: {stats['purchases_total']:.2f} $\n"
        f"Баланс пользователей: {stats['balances_total']:.2f} $\n"
        f"Оплаченных пополнений: {stats['paid_topups']}\n"
        + build_admin_crm_channels_block(stats.get("channels"))
    )


def build_admin_utm_text(bot_username: str, stats: dict) -> str:
    source_lines = [
        f"{row['utm_source']}: {row['total']} чел. · покупок на {float(row.get('purchases_total') or 0):.2f}$"
        for row in stats["sources"]
    ] or ["пока нет данных"]
    campaign_lines = [
        f"{row['utm_campaign']}: {row['total']} чел. · покупок на {float(row.get('purchases_total') or 0):.2f}$"
        for row in stats["campaigns"]
    ] or ["пока нет данных"]
    param_lines = [
        f"{row['start_param']}: {row['total']} чел. · покупок на {float(row.get('purchases_total') or 0):.2f}$"
        for row in stats.get("params", [])
    ] or ["пока нет данных"]
    return (
        "🔗 UTM статистика\n\n"
        "Пример ссылки:\n"
        f"https://t.me/{bot_username}?start=utm_instagram\n\n"
        "Источники:\n"
        + "\n".join(source_lines)
        + "\n\nКампании:\n"
        + "\n".join(campaign_lines)
        + "\n\nStart-параметры:\n"
        + "\n".join(param_lines)
    )


def build_admin_utm_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👥 Реферальные ссылки", callback_data="admin_utm_category:referrals")],
            [InlineKeyboardButton(text="📣 Рекламные (?start=)", callback_data="admin_utm_category:ads")],
            [InlineKeyboardButton(text="🏪 Франшизы", callback_data="admin_utm_category:franchises")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")],
        ]
    )


ADMIN_UTM_PAGE_SIZE = 10


def _paginate_utm_rows(rows: list[dict], page: int) -> tuple[list[dict], int, int, int]:
    total = len(rows)
    pages = max((total + ADMIN_UTM_PAGE_SIZE - 1) // ADMIN_UTM_PAGE_SIZE, 1)
    current = max(0, min(int(page), pages - 1))
    start = current * ADMIN_UTM_PAGE_SIZE
    return rows[start:start + ADMIN_UTM_PAGE_SIZE], current, pages, total


def build_admin_utm_category_text(
    category: str,
    stats: dict,
    franchises: dict | None = None,
    page: int = 0,
) -> str:
    category = str(category or "").lower()
    if category == "franchises":
        rows = (franchises or {}).get("bots", [])
        visible_rows, current, pages, total = _paginate_utm_rows(rows, page)
        lines = [
            f"@{str(row.get('bot_username') or '').lstrip('@')}: "
            f"{int(row.get('utm_users') or 0)} переходов · "
            f"покупок на {float(row.get('utm_purchases_total') or 0):.2f}$"
            for row in visible_rows
        ] or ["пока нет активных франшиз"]
        return f"🏪 Франшизы · {total} всего · страница {current + 1}/{pages}\n\n" + "\n".join(lines)
    params = stats.get("params", [])
    if category == "referrals":
        rows = [row for row in params if str(row.get("start_param") or "").startswith("r_")]
        title = "👥 Реферальные ссылки"
    else:
        rows = [row for row in params if not str(row.get("start_param") or "").startswith("r_")]
        title = "📣 Рекламные ссылки (?start=)"
    visible_rows, current, pages, total = _paginate_utm_rows(rows, page)
    lines = [
        f"{row.get('start_param')}: {int(row.get('total') or 0)} чел. · покупок на {float(row.get('purchases_total') or 0):.2f}$"
        for row in visible_rows
    ] or ["пока нет данных"]
    return f"{title} · {total} всего · страница {current + 1}/{pages}\n\n" + "\n".join(lines)


def build_admin_utm_category_keyboard(category: str, page: int, total: int) -> InlineKeyboardMarkup:
    pages = max((max(int(total), 0) + ADMIN_UTM_PAGE_SIZE - 1) // ADMIN_UTM_PAGE_SIZE, 1)
    current = max(0, min(int(page), pages - 1))
    navigation = []
    if current > 0:
        navigation.append(InlineKeyboardButton(
            text="⬅️ Предыдущая", callback_data=f"admin_utm_category:{category}:{current - 1}"
        ))
    if current + 1 < pages:
        navigation.append(InlineKeyboardButton(
            text="Следующая ➡️", callback_data=f"admin_utm_category:{category}:{current + 1}"
        ))
    rows = []
    if navigation:
        rows.append(navigation)
    rows.extend([
        [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"admin_utm_category:{category}:{current}")],
        [InlineKeyboardButton(text="◀️ К категориям", callback_data="admin_utm")],
        [InlineKeyboardButton(text="◀️ В админку", callback_data="admin_panel")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_admin_traffic_manage_text() -> str:
    return (
        "📶 Изменение proxy-трафика пользователя\n\n"
        "Отправьте данные в формате:\n"
        "USER_ID GB\n\n"
        "Положительное значение начисляет GB, отрицательное — списывает.\n\n"
        "Примеры:\n"
        "1907513941 5\n"
        "1907513941 -2.5"
    )


async def show_order_preview(target_message, order_id: int, user_id: int | None = None):
    order = get_order(order_id)
    lang = resolve_language_code(user_id=user_id)
    if order is None:
        await render_screen(
            target_message,
            tr(lang, "generic.order_not_found"),
            reply_markup=main_menu(user_id=user_id, language_code=lang),
        )
        return

    await render_screen(
        target_message,
        build_order_preview_text(order, lang),
        reply_markup=build_order_preview_keyboard(order, lang),
    )


async def show_payment_methods(target_message, order_id: int, user_id: int | None = None):
    order = get_order(order_id)
    lang = resolve_language_code(user_id=user_id)
    if order is None:
        await render_screen(
            target_message,
            tr(lang, "generic.order_not_found"),
            reply_markup=main_menu(user_id=user_id, language_code=lang),
        )
        return

    await render_screen(
        target_message,
        build_payment_methods_text(order, lang),
        reply_markup=build_payment_methods_keyboard(order, lang),
    )


async def show_profile_orders(target_message, user_id: int, page: int):
    lang = resolve_language_code(user_id=user_id)
    # This is the customer's completed-order archive. Payment and fulfillment
    # states are intentionally excluded until a delivery is actually present.
    total = count_user_orders(user_id)
    safe_page = min(max(page, 0), max((total - 1) // ORDERS_PAGE_SIZE, 0))
    offset = safe_page * ORDERS_PAGE_SIZE
    orders = list_user_orders(user_id, limit=ORDERS_PAGE_SIZE, offset=offset)
    await render_screen(
        target_message,
        build_orders_list_text(orders, safe_page, total, lang),
        reply_markup=build_orders_list_keyboard(orders, safe_page, total, lang),
    )


async def show_order_xrocket_currencies(target_message: Message, order: dict, page: int = 0):
    lang = resolve_language_code(user_id=int(order.get("user_id") or 0) or None)
    currencies = await get_xrocket_available_payment_currencies()
    if not currencies:
        await target_message.edit_text(
            tr(lang, "xrocket.no_currencies"),
            reply_markup=build_payment_methods_keyboard(order, lang),
        )
        return

    total_pages = max(math.ceil(len(currencies) / XROCKET_CURRENCIES_PAGE_SIZE), 1)
    safe_page = min(max(page, 0), total_pages - 1)
    start = safe_page * XROCKET_CURRENCIES_PAGE_SIZE
    page_rows = currencies[start : start + XROCKET_CURRENCIES_PAGE_SIZE]
    await target_message.edit_text(
        build_xrocket_currency_picker_text(
            tr(lang, "xrocket.payment_title"),
            float(order["total_price"] or 0),
            safe_page,
            total_pages,
            lang,
        ),
        reply_markup=build_xrocket_currency_picker_keyboard(
            page_rows,
            safe_page,
            total_pages,
            select_callback_builder=lambda currency, current_page: (
                f"payment_xrocket_currency:{order['id']}:{current_page}:{currency}"
            ),
            page_callback_builder=lambda next_page: f"payment_xrocket_page:{order['id']}:{next_page}",
            back_callback=f"order_methods:{order['id']}",
            language_code=lang,
        ),
    )


async def show_topup_xrocket_currencies(
    target_message: Message,
    amount_usd: float,
    page: int = 0,
    *,
    user_id: int | None = None,
):
    lang = resolve_language_code(user_id=user_id)
    currencies = await get_xrocket_available_payment_currencies()
    if not currencies:
        await target_message.edit_text(
            tr(lang, "xrocket.no_currencies"),
            reply_markup=build_topup_amount_keyboard(lang),
        )
        return

    total_pages = max(math.ceil(len(currencies) / XROCKET_CURRENCIES_PAGE_SIZE), 1)
    safe_page = min(max(page, 0), total_pages - 1)
    start = safe_page * XROCKET_CURRENCIES_PAGE_SIZE
    page_rows = currencies[start : start + XROCKET_CURRENCIES_PAGE_SIZE]
    await target_message.edit_text(
        build_xrocket_currency_picker_text(
            tr(lang, "xrocket.topup_title"),
            amount_usd,
            safe_page,
            total_pages,
            lang,
        ),
        reply_markup=build_xrocket_currency_picker_keyboard(
            page_rows,
            safe_page,
            total_pages,
            select_callback_builder=lambda currency, current_page: (
                f"topup_xrocket_currency:{current_page}:{currency}"
            ),
            page_callback_builder=lambda next_page: f"topup_xrocket_page:{next_page}",
            back_callback="topup_xrocket",
            language_code=lang,
        ),
    )


async def show_admin_main_subscription_screen(target_message: Message, bot: Bot):
    bot_info = await bot.get_me()
    settings = get_main_bot_settings()
    await target_message.edit_text(
        build_admin_main_subscription_text(settings, bot_info.username or "userbot"),
        reply_markup=build_admin_main_subscription_keyboard(settings),
        disable_web_page_preview=True,
    )


async def render_market_goods_unavailable(
    target_message,
    *,
    user_id: int | None = None,
    back_data: str = "back_main",
) -> None:
    """Show the support notice on every goods screen while the catalogue is off."""
    lang = resolve_language_code(user_id=user_id)
    await render_screen(
        target_message,
        market_goods_notice(),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=tr(lang, "common.back"), callback_data=back_data)]]
        ),
        banner="market",
    )


async def show_market_categories(
    target_message,
    user_id: int | None = None,
    bot: Bot | None = None,
):
    if not market_goods_enabled():
        await render_market_goods_unavailable(target_message, user_id=user_id)
        return
    categories = await get_market_categories()
    lang = resolve_language_code(user_id=user_id)
    await render_screen(
        target_message,
        build_market_categories_text(lang),
        reply_markup=build_goods_categories_keyboard(categories, bot or getattr(target_message, "bot", None), lang),
        banner="market",
    )


async def show_market_group(
    target_message,
    category_ids: tuple[int, ...],
    user_id: int | None = None,
):
    if not market_goods_enabled():
        await render_market_goods_unavailable(target_message, user_id=user_id)
        return
    categories = await get_market_categories()
    lang = resolve_language_code(user_id=user_id)
    await render_screen(
        target_message,
        build_market_categories_text(lang),
        reply_markup=build_market_group_keyboard(categories, category_ids, lang),
        banner="market",
    )


async def show_market_category(target_message, category_id: int, user_id: int | None = None):
    if not market_goods_enabled():
        await render_market_goods_unavailable(target_message, user_id=user_id)
        return
    lang = resolve_language_code(user_id=user_id)
    category = await get_market_category_by_id(category_id)
    if category is None:
        await render_screen(target_message, tr(lang, "generic.category_not_found"), reply_markup=main_menu(user_id=user_id, language_code=lang))
        return

    children = category.get("children", [])
    direct_child_id = get_market_direct_child_id(category)
    if direct_child_id is not None:
        await show_market_products(target_message, direct_child_id, 1, user_id=user_id)
        return
    if children:
        await render_screen(
            target_message,
            build_market_subcategories_text(category, lang),
            reply_markup=build_market_subcategories_keyboard(category, lang),
            banner="market",
        )
        return

    await show_market_products(target_message, category_id, 1, user_id=user_id)


async def show_market_products(target_message, category_id: int, page: int, user_id: int | None = None):
    if not market_goods_enabled():
        await render_market_goods_unavailable(target_message, user_id=user_id)
        return
    lang = resolve_language_code(user_id=user_id)
    category = await get_market_category_by_id(category_id)
    if category is None:
        await render_screen(target_message, tr(lang, "generic.category_not_found"), reply_markup=main_menu(user_id=user_id, language_code=lang))
        return

    payload = await get_market_products(category_id, page=page, per_page=MARKET_PRODUCTS_PAGE_SIZE)
    meta = payload.get("meta", {})
    current_page = int(meta.get("current_page") or page or 1)
    last_page = int(meta.get("last_page") or 1)
    products = payload.get("items", [])
    margin_percentage = get_effective_market_margin(getattr(target_message, "bot", None))

    await render_screen(
        target_message,
        build_market_products_text(category, products, current_page, last_page, margin_percentage, lang),
        reply_markup=build_market_products_keyboard(
            category,
            products,
            current_page,
            last_page,
            margin_percentage,
            lang,
        ),
        banner="market",
    )


async def show_market_product(target_message, category_id: int, product_id: int, page: int, user_id: int | None = None):
    if not market_goods_enabled():
        await render_market_goods_unavailable(target_message, user_id=user_id)
        return
    lang = resolve_language_code(user_id=user_id)
    category = await get_market_category_by_id(category_id)
    if category is None:
        await render_screen(target_message, tr(lang, "generic.category_not_found"), reply_markup=main_menu(user_id=user_id, language_code=lang))
        return

    product = await get_market_product(product_id)
    is_favorite = bool(user_id and product_id in list_favorite_product_ids(user_id))
    margin_percentage = get_effective_market_margin(getattr(target_message, "bot", None))
    await render_screen(
        target_message,
        build_market_product_text(category, product, margin_percentage, lang),
        reply_markup=build_market_product_keyboard(category_id, product, page, is_favorite, lang),
        parse_mode="HTML",
    )


def create_market_order_from_selection(
    user_id: int,
    category_name: str,
    product: dict,
    quantity: int,
    bot: Bot | None,
) -> int:
    effective_margin_percentage = get_effective_market_margin(bot)
    purchase_unit_price = round(float(product.get("price", 0) or 0) / get_cached_market_rub_per_usdt(), 4)
    unit_price = convert_rub_to_usdt(product.get("price", 0), effective_margin_percentage)
    partner_bot = get_current_partner_bot(bot)
    partner_margin_percentage = get_partner_category_markup(partner_bot, "goods")
    return create_order(
        user_id=user_id,
        proxy_kind="account",
        category_id=int(product["category_id"]),
        category_name=category_name,
        item_id=int(product["id"]),
        protocol="Аккаунты",
        quantity=quantity,
        unit_price=unit_price,
        total_price=round(unit_price * quantity, 2),
        product_title=product.get("title"),
        partner_bot_id=(int(partner_bot["id"]) if partner_bot else None),
        purchase_unit_price=purchase_unit_price,
        partner_margin_percentage=partner_margin_percentage,
        partner_profit_amount=(
            calculate_partner_profit_share_amount(purchase_unit_price, quantity, partner_margin_percentage)
            if partner_bot
            else 0.0
        ),
    )


def ensure_admin(user_id: int) -> bool:
    return is_admin_user(user_id)


async def fulfill_proxy_order(
    target_message,
    order_id: int,
    *,
    notify_bot: Bot | None = None,
    notify_chat_id: int | None = None,
    buyer_message: Message | None = None,
    update_status_message: bool = True,
    notify_pending: bool = True,
):
    async with get_order_fulfillment_lock(order_id):
        order = get_order(order_id)
        status_target = target_message if update_status_message else None
        bot = notify_bot or getattr(target_message, "bot", None)
        chat_id = notify_chat_id
        buyer_context = buyer_message or target_message
        if order is None:
            await safe_edit_text(status_target, "Заказ не найден.", reply_markup=main_menu())
            return
        if order.get("status") == "delivered" and order.get("delivery_text"):
            await safe_edit_text(
                status_target,
                build_delivered_text(order, order["delivery_text"]),
                reply_markup=build_delivery_keyboard(order_id),
                parse_mode="HTML",
            )
            return
        if order.get("status") == "credited":
            await safe_edit_text(status_target, build_balance_credit_text(order), reply_markup=build_result_keyboard())
            return

        provider_order_id = int(order.get("provider_order_id") or 0)
        try:
            provider_order = {}
            if provider_order_id <= 0:
                created_order: dict[str, Any] | None = None
                last_create_error: ProxyProviderError | None = None
                for attempt in range(PROXY_PROVIDER_CREATE_ATTEMPTS):
                    try:
                        created_order = await create_proxy_provider_order(
                            category_id=order["category_id"],
                            item_id=order["item_id"],
                            count=order["quantity"],
                        )
                        break
                    except ProxyProviderError as error:
                        last_create_error = error
                        if attempt + 1 < PROXY_PROVIDER_CREATE_ATTEMPTS:
                            await asyncio.sleep(PROXY_PROVIDER_CREATE_RETRY_DELAY_SECONDS)
                if created_order is None:
                    raise last_create_error or ProxyProviderError(PROXY_GENERIC_ERROR_CODE)
                provider_order = created_order.get("order", {})
                provider_order_id = int(provider_order.get("id") or 0)
                await log_order_debug(
                    bot,
                    order,
                    get_purchase_buyer_label(buyer_context, order),
                    "Заказ отправлен поставщику прокси",
                    [f"📡 ID поставщика: {provider_order_id or '-'}"],
                )
                if provider_order_id <= 0:
                    raise ValueError("Proxy provider order id is missing")
                set_proxy_order_provider_reference(order_id, provider_order_id, status="delivery_pending")
                order = get_order(order_id) or order
                if provider_order.get("details"):
                    provider_order["details"] = await filter_working_proxy_details(
                        list(provider_order.get("details") or []),
                        str(order.get("protocol") or "HTTP"),
                    )
                if provider_order.get("details"):
                    delivery_text = format_proxy_delivery(provider_order, order["protocol"])
                    complete_order(order_id, provider_order_id, delivery_text)
                    order = get_order(order_id)
                    await notify_purchase_owner(bot, order, buyer_context)
                    await log_order_debug(
                        bot,
                        order,
                        get_purchase_buyer_label(buyer_context, order),
                        "Прокси выданы",
                    )
                    if chat_id is not None and bot is not None:
                        await bot.send_message(
                            chat_id,
                            build_delivered_text(order, delivery_text),
                            reply_markup=build_delivery_keyboard(order_id),
                            parse_mode="HTML",
                        )
                    else:
                        await safe_edit_text(
                            status_target,
                            build_delivered_text(order, delivery_text),
                            reply_markup=build_delivery_keyboard(order_id),
                            parse_mode="HTML",
                        )
                    return

            provider_order_data = None
            for attempt in range(PROXY_PROVIDER_DETAILS_POLL_ATTEMPTS):
                provider_order_data = await get_proxy_provider_order(provider_order_id)
                if provider_order_data.get("details"):
                    provider_order_data["details"] = await filter_working_proxy_details(
                        list(provider_order_data.get("details") or []),
                        str(order.get("protocol") or "HTTP"),
                    )
                    if provider_order_data.get("details"):
                        break
                if attempt + 1 < PROXY_PROVIDER_DETAILS_POLL_ATTEMPTS:
                    await asyncio.sleep(PROXY_PROVIDER_DETAILS_POLL_INTERVAL_SECONDS)
            if not provider_order_data or not provider_order_data.get("details"):
                raise ProxyProviderError(PROXY_GENERIC_ERROR_CODE)
            delivery_text = format_proxy_delivery(provider_order_data, order["protocol"])
            complete_order(order_id, provider_order_id, delivery_text)
            order = get_order(order_id)
            await notify_purchase_owner(bot, order, buyer_context)
            await log_order_debug(
                bot,
                order,
                get_purchase_buyer_label(buyer_context, order),
                "Прокси выданы",
            )
            if chat_id is not None and bot is not None:
                await bot.send_message(
                    chat_id,
                    build_delivered_text(order, delivery_text),
                    reply_markup=build_delivery_keyboard(order_id),
                    parse_mode="HTML",
                )
            else:
                await safe_edit_text(
                    status_target,
                    build_delivered_text(order, delivery_text),
                    reply_markup=build_delivery_keyboard(order_id),
                    parse_mode="HTML",
                )
        except (ProxyProviderError, ValueError):
            if provider_order_id > 0:
                pending_order = get_order(order_id)
                if pending_order is not None and str(pending_order.get("status") or "") not in {"delivered", "credited"}:
                    mark_order_delivery_pending(order_id)
                    pending_order = get_order(order_id) or pending_order
                if notify_pending:
                    await log_order_debug(
                        bot,
                        pending_order or order,
                        get_purchase_buyer_label(buyer_context, pending_order or order),
                        "Прокси заказан, ожидаем проверку доступности",
                        [f"📡 ID поставщика: {provider_order_id}"],
                    )
                if notify_pending and chat_id is not None and bot is not None:
                    await bot.send_message(
                        chat_id,
                        build_market_delivery_pending_text(order_id),
                        reply_markup=build_market_delivery_pending_keyboard(order_id),
                    )
                elif notify_pending:
                    await safe_edit_text(
                        status_target,
                        build_market_delivery_pending_text(order_id),
                        reply_markup=build_market_delivery_pending_keyboard(order_id),
                    )
                return
            order = credit_order_to_user_balance(order_id)
            await log_order_debug(
                bot,
                order,
                get_purchase_buyer_label(buyer_context, order),
                "Ошибка выдачи у поставщика прокси",
                ["💸 Сумма возвращена на баланс"],
            )
            if chat_id is not None and bot is not None:
                await bot.send_message(
                    chat_id,
                    build_balance_credit_text(order),
                    reply_markup=build_result_keyboard(),
                )
            else:
                await safe_edit_text(status_target, build_balance_credit_text(order), reply_markup=build_result_keyboard())


async def fulfill_market_order(
    target_message,
    order_id: int,
    *,
    notify_bot: Bot | None = None,
    notify_chat_id: int | None = None,
    buyer_message: Message | None = None,
    update_status_message: bool = True,
):
    async with get_order_fulfillment_lock(order_id):
        order = get_order(order_id)
        status_target = target_message if update_status_message else None
        bot = notify_bot or getattr(target_message, "bot", None)
        chat_id = notify_chat_id or getattr(getattr(target_message, "chat", None), "id", None)
        # Public-API buyers live under a negative pseudo user id and have no
        # Telegram chat.  Sending to it raises "chat not found" and kills the
        # delivery task midway, so such orders are settled in the database only
        # and read back by the API client.
        if chat_id is not None and int(chat_id) < 0:
            chat_id = None
        buyer_context = buyer_message or target_message

        if order is None:
            if status_target is not None:
                await safe_edit_text(status_target, "Заказ не найден.", reply_markup=main_menu())
            return
        if order.get("status") == "delivered" and order.get("delivery_text"):
            if status_target is not None:
                await safe_edit_text(
                    status_target,
                    build_delivered_text(order, order["delivery_text"]),
                    reply_markup=build_delivery_keyboard(order_id),
                )
            return
        if order.get("status") == "credited":
            if status_target is not None:
                await safe_edit_text(
                    status_target,
                    build_balance_credit_text(order),
                    reply_markup=build_result_keyboard(),
                )
            return

        mark_order_delivery_pending(order_id)
        order = get_order(order_id)
        if status_target is not None:
            await safe_edit_text(
                status_target,
                build_market_delivery_pending_text(order_id),
                reply_markup=build_market_delivery_pending_keyboard(order_id),
            )

        try:
            if not order.get("supplier_order_uuid"):
                current_product = await get_market_product(int(order["item_id"]))
                if int(current_product.get("quantity") or 0) < int(order["quantity"]):
                    order = credit_order_to_user_balance(order_id)
                    await log_order_debug(
                        bot,
                        order,
                        get_purchase_buyer_label(buyer_context, order),
                        "Товар закончился до создания заказа у поставщика",
                        ["💸 Сумма автоматически возвращена на баланс"],
                    )
                    if chat_id is not None and bot is not None:
                        await bot.send_message(
                            chat_id,
                            build_balance_credit_text(order),
                            reply_markup=build_result_keyboard(),
                        )
                    elif status_target is not None:
                        await safe_edit_text(
                            status_target,
                            build_balance_credit_text(order),
                            reply_markup=build_result_keyboard(),
                        )
                    return
                supplier_order = await create_market_order(
                    order["item_id"],
                    order["quantity"],
                    product_prevalidated=True,
                )
                update_order_supplier_data(
                    order_id,
                    supplier_order_uuid=supplier_order["uuid"],
                    supplier_order_number=supplier_order.get("order_number"),
                )
                order = get_order(order_id)
                await log_order_debug(
                    bot,
                    order,
                    get_purchase_buyer_label(buyer_context, order),
                    "Заказ отправлен поставщику",
                    [
                        f"📡 UUID поставщика: {supplier_order.get('uuid')}",
                        f"🧾 Номер поставщика: {supplier_order.get('order_number') or '-'}",
                    ],
                )

            last_supplier_order = None
            for attempt in range(MARKET_ORDER_POLL_ATTEMPTS):
                supplier_order = await get_market_order(order["supplier_order_uuid"])
                last_supplier_order = supplier_order

                if is_market_order_ready(supplier_order):
                    file_url = get_market_order_file_url(supplier_order)
                    if not file_url:
                        raise MarketProviderError("Поставщик не вернул ссылку на файл")
                    delivery_text = await download_text_file(file_url)
                    # Marketplace accounts are supplier-delivered digital goods.
                    # Instagram reachability validation belongs only to proxy
                    # products and must not block or refund account orders.
                    delivered_lines = len(
                        [line for line in str(delivery_text or "").splitlines() if line.strip()]
                    )
                    ordered_quantity = int(order.get("quantity") or 0)
                    if delivered_lines < ordered_quantity:
                        # The supplier file has fewer rows than the customer paid
                        # for. Still deliver what arrived, but make the shortfall
                        # loud so it can be topped up or refunded manually.
                        await log_order_debug(
                            bot,
                            order,
                            get_purchase_buyer_label(buyer_context, order),
                            "⚠️ Поставщик выдал меньше, чем заказано",
                            [
                                f"🧾 Номер поставщика: {supplier_order.get('order_number') or '-'}",
                                f"📦 Заказано: {ordered_quantity}",
                                f"📥 В файле строк: {delivered_lines}",
                            ],
                        )
                    complete_market_order(
                        order_id,
                        supplier_order_uuid=supplier_order["uuid"],
                        supplier_order_number=supplier_order.get("order_number"),
                        delivery_text=delivery_text,
                    )
                    order = get_order(order_id)
                    await notify_purchase_owner(bot, order, buyer_context)
                    await log_order_debug(
                        bot,
                        order,
                        get_purchase_buyer_label(buyer_context, order),
                        "Аккаунты выданы",
                        [f"🧾 Номер поставщика: {supplier_order.get('order_number') or '-'}"],
                    )
                    if chat_id is not None and bot is not None:
                        await bot.send_message(
                            chat_id,
                            build_delivered_text(order, delivery_text),
                            reply_markup=build_delivery_keyboard(order_id),
                        )
                        await send_order_delivery_document(bot, chat_id, order)
                    elif status_target is not None:
                        await safe_edit_text(
                            status_target,
                            build_delivered_text(order, delivery_text),
                            reply_markup=build_delivery_keyboard(order_id),
                        )
                    return

                if str(supplier_order.get("status", "")).lower() in {"uncompleted", "cancelled"}:
                    order = credit_order_to_user_balance(order_id)
                    await log_order_debug(
                        bot,
                        order,
                        get_purchase_buyer_label(buyer_context, order),
                        "Поставщик отменил заказ",
                        ["💸 Сумма возвращена на баланс"],
                    )
                    if chat_id is not None and bot is not None:
                        await bot.send_message(
                            chat_id,
                            build_balance_credit_text(order),
                            reply_markup=build_result_keyboard(),
                        )
                    elif status_target is not None:
                        await safe_edit_text(
                            status_target,
                            build_balance_credit_text(order),
                            reply_markup=build_result_keyboard(),
                        )
                    return

                if attempt + 1 < MARKET_ORDER_POLL_ATTEMPTS:
                    await asyncio.sleep(MARKET_ORDER_POLL_INTERVAL_SECONDS)

            update_order_supplier_data(
                order_id,
                supplier_order_uuid=order["supplier_order_uuid"],
                supplier_order_number=(last_supplier_order or {}).get("order_number"),
            )
            # Background recovery runs this function repeatedly until the supplier
            # reaches a terminal state.  Only update an existing interactive status
            # message here; sending a new message on every exhausted polling window
            # turns a long-running supplier order into Telegram spam.
            if status_target is not None:
                await safe_edit_text(
                    status_target,
                    build_market_delivery_pending_text(order_id),
                    reply_markup=build_market_delivery_pending_keyboard(order_id),
                )
        except MarketProviderError as error:
            updated_order = get_order(order_id)
            if not (updated_order or {}).get("supplier_order_uuid") and is_market_order_creation_rejected(error):
                credited_order = credit_order_to_user_balance(order_id)
                await log_order_debug(
                    bot,
                    credited_order,
                    get_purchase_buyer_label(buyer_context, credited_order or order),
                    "Поставщик отклонил создание заказа",
                    [f"❗ Причина: {error}", "💸 Сумма автоматически возвращена на баланс"],
                )
                if chat_id is not None and bot is not None:
                    await bot.send_message(
                        chat_id,
                        build_balance_credit_text(credited_order),
                        reply_markup=build_result_keyboard(),
                    )
                elif status_target is not None:
                    await safe_edit_text(
                        status_target,
                        build_balance_credit_text(credited_order),
                        reply_markup=build_result_keyboard(),
                    )
                return
            await log_order_debug(
                bot,
                updated_order,
                get_purchase_buyer_label(buyer_context, updated_order or order),
                "Временная ошибка проверки выдачи у поставщика",
                [f"❗ Причина: {error}", "🔄 Заказ оставлен на автопроверке; автовозврат не выполнялся"],
            )
            # A failed status/download request does not mean that the supplier
            # rejected the purchase.  Refunding here can give the buyer both the
            # money and an already purchased item.  Recovery will safely retry;
            # only an explicit cancelled/uncompleted supplier status is refunded.
            if status_target is not None:
                await safe_edit_text(
                    status_target,
                    build_market_delivery_pending_text(order_id),
                    reply_markup=build_market_delivery_pending_keyboard(order_id),
                )


async def show_quantity_selection(target_message, category_id: int, item_id: int):
    category = await get_proxy_category_by_id(category_id, "static")
    if category is None:
        await target_message.edit_text(
            "Позиция не найдена.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="proxy_static")]]
            ),
        )
        return

    item = next((row for row in category.get("items", []) if row["id"] == item_id), None)
    if item is None:
        await target_message.edit_text(
            "Протокол не найден.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data=f"proxy_static:{category_id}")]]
            ),
        )
        return

    unit_price = get_effective_proxy_unit_price(getattr(target_message, "bot", None), float(category.get("price") or 0.0))
    await target_message.edit_text(
        build_quantity_selection_text(category, item, unit_price),
        reply_markup=build_quantity_selection_keyboard(category_id, item_id),
    )


async def show_partner_admin_panel_screen(target_message: Message, bot: Bot, owner_id: int):
    partner_bot = get_owned_current_partner_bot(bot, owner_id)
    if partner_bot is None:
        await target_message.edit_text("Это меню доступно только владельцу партнёрского бота.")
        return

    stats = get_partner_bot_stats(int(partner_bot["id"]))
    await target_message.edit_text(
        build_partner_admin_panel_text(partner_bot, stats),
        reply_markup=build_partner_admin_panel_keyboard(),
    )


async def show_partner_admin_subscription_screen(target_message: Message, bot: Bot, owner_id: int):
    partner_bot = get_owned_current_partner_bot(bot, owner_id)
    if partner_bot is None:
        await target_message.edit_text("Настройки подписки недоступны.")
        return

    await target_message.edit_text(
        build_partner_admin_subscription_text(partner_bot),
        reply_markup=build_partner_admin_subscription_keyboard(partner_bot),
        disable_web_page_preview=True,
    )


async def show_partner_admin_referral_screen(target_message: Message, bot: Bot, owner_id: int):
    partner_bot = get_owned_current_partner_bot(bot, owner_id)
    if partner_bot is None:
        await target_message.edit_text("Настройки рефералки недоступны.")
        return

    whitelist_rows = list_partner_referral_whitelist(int(partner_bot["id"]))
    await target_message.edit_text(
        build_partner_admin_referral_text(partner_bot, whitelist_rows),
        reply_markup=build_partner_admin_referral_keyboard(partner_bot),
    )


async def show_partner_admin_utm_screen(target_message: Message, bot: Bot, owner_id: int):
    partner_bot = get_owned_current_partner_bot(bot, owner_id)
    if partner_bot is None:
        await target_message.edit_text("UTM-аналитика недоступна.")
        return

    stats = get_partner_bot_utm_stats(int(partner_bot["id"]))
    utm_links = list_partner_bot_utm_links(int(partner_bot["id"]))
    await target_message.edit_text(
        build_partner_admin_utm_text(partner_bot, stats, utm_links),
        reply_markup=build_partner_admin_utm_keyboard(),
        disable_web_page_preview=True,
    )


async def show_partner_cabinet_subscription_screen(target_message: Message, partner_bot: dict):
    await target_message.edit_text(
        build_partner_admin_subscription_text(partner_bot),
        reply_markup=build_partner_cabinet_subscription_keyboard(
            int(partner_bot["id"]),
            int(partner_bot.get("subscription_enabled") or 0) == 1,
        ),
        disable_web_page_preview=True,
    )


async def show_partner_cabinet_referral_screen(target_message: Message, partner_bot: dict):
    whitelist_rows = list_partner_referral_whitelist(int(partner_bot["id"]))
    await target_message.edit_text(
        build_partner_admin_referral_text(partner_bot, whitelist_rows),
        reply_markup=build_partner_cabinet_referral_keyboard(
            int(partner_bot["id"]),
            int(partner_bot.get("referral_enabled") or 0) == 1,
        ),
    )


async def show_partner_cabinet_utm_screen(target_message: Message, partner_bot: dict):
    stats = get_partner_bot_utm_stats(int(partner_bot["id"]))
    utm_links = list_partner_bot_utm_links(int(partner_bot["id"]))
    await target_message.edit_text(
        build_partner_admin_utm_text(partner_bot, stats, utm_links),
        reply_markup=build_partner_cabinet_utm_keyboard(int(partner_bot["id"])),
        disable_web_page_preview=True,
    )


def extract_partner_broadcast_payload(message: Message) -> dict | None:
    if message.photo:
        photo = message.photo[-1]
        return {
            "type": "photo",
            "file_id": photo.file_id,
            "caption": message.caption or "",
        }
    if message.video:
        return {
            "type": "video",
            "file_id": message.video.file_id,
            "caption": message.caption or "",
        }
    if message.animation:
        return {
            "type": "animation",
            "file_id": message.animation.file_id,
            "caption": message.caption or "",
        }
    if message.document:
        return {
            "type": "document",
            "file_id": message.document.file_id,
            "caption": message.caption or "",
        }
    if message.text or message.caption:
        return {
            "type": "text",
            "text": message.text or message.caption or "",
        }
    return None


async def send_partner_payload_to_user(
    partner_bot: dict,
    user_id: int,
    payload: dict,
    reply_markup: InlineKeyboardMarkup | None,
):
    bot_client = Bot(token=partner_bot["bot_token"])
    try:
        payload_type = payload.get("type")
        if payload_type == "text":
            await bot_client.send_message(
                chat_id=user_id,
                text=payload.get("text") or "",
                reply_markup=reply_markup,
            )
        elif payload_type == "photo":
            await bot_client.send_photo(
                chat_id=user_id,
                photo=payload.get("file_id"),
                caption=payload.get("caption") or None,
                reply_markup=reply_markup,
            )
        elif payload_type == "video":
            await bot_client.send_video(
                chat_id=user_id,
                video=payload.get("file_id"),
                caption=payload.get("caption") or None,
                reply_markup=reply_markup,
            )
        elif payload_type == "animation":
            await bot_client.send_animation(
                chat_id=user_id,
                animation=payload.get("file_id"),
                caption=payload.get("caption") or None,
                reply_markup=reply_markup,
            )
        elif payload_type == "document":
            await bot_client.send_document(
                chat_id=user_id,
                document=payload.get("file_id"),
                caption=payload.get("caption") or None,
                reply_markup=reply_markup,
            )
        else:
            raise ValueError("unsupported_payload")
    finally:
        with contextlib.suppress(Exception):
            await bot_client.session.close()


async def send_partner_broadcast(
    target_message: Message,
    state: FSMContext,
    reply_markup: InlineKeyboardMarkup | None,
):
    data = await state.get_data()
    source_chat_id = data.get("partner_broadcast_chat_id")
    source_message_id = data.get("partner_broadcast_message_id")
    partner_bot_id = int(data.get("partner_broadcast_bot_id") or 0)
    if not source_chat_id or not source_message_id or partner_bot_id <= 0:
        await state.clear()
        await target_message.answer("Не удалось найти сообщение для рассылки.")
        return

    user_ids = list_partner_bot_user_ids(partner_bot_id)
    success_count = 0
    fail_count = 0
    blocked_count = 0
    for user_id in user_ids:
        for attempt in range(3):
            try:
                await target_message.bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=source_chat_id,
                    message_id=source_message_id,
                    reply_markup=reply_markup,
                )
                success_count += 1
                break
            except TelegramRetryAfter as error:
                await asyncio.sleep(float(getattr(error, "retry_after", 1)) + 1)
                continue
            except TelegramForbiddenError:
                blocked_count += 1
                break
            except Exception:
                fail_count += 1
                break
        await asyncio.sleep(0.05)

    await state.clear()
    await target_message.answer(
        "📣 Рассылка завершена\n\n"
        f"Успешно: {success_count}\n"
        f"Заблокировали бота: {blocked_count}\n"
        f"Ошибок: {fail_count}",
        reply_markup=main_menu(),
    )


async def send_partner_cabinet_broadcast(
    target_message: Message,
    partner_bot: dict,
    payload: dict,
    reply_markup: InlineKeyboardMarkup | None,
):
    user_ids = list_partner_bot_user_ids(int(partner_bot["id"]))
    success_count = 0
    fail_count = 0
    blocked_count = 0
    for user_id in user_ids:
        for attempt in range(3):
            try:
                await send_partner_payload_to_user(partner_bot, user_id, payload, reply_markup)
                success_count += 1
                break
            except TelegramRetryAfter as error:
                await asyncio.sleep(float(getattr(error, "retry_after", 1)) + 1)
                continue
            except TelegramForbiddenError:
                blocked_count += 1
                break
            except Exception:
                fail_count += 1
                break
        await asyncio.sleep(0.05)

    await target_message.answer(
        "📣 Рассылка завершена\n\n"
        f"Бот: @{partner_bot['bot_username']}\n"
        f"Успешно: {success_count}\n"
        f"Заблокировали бота: {blocked_count}\n"
        f"Ошибок: {fail_count}",
        reply_markup=build_partner_bot_manage_keyboard(partner_bot),
    )


def create_proxy_order_from_selection(user_id: int, data: dict, quantity: int, bot: Bot | None) -> int:
    partner_bot = get_current_partner_bot(bot)
    margin_percentage = get_partner_category_markup(partner_bot, "proxy")
    sale_unit_price = (
        calculate_sale_price_from_base_usdt(
            data["unit_price"],
            proxy_base_markup_percent() + margin_percentage,
        )
        if partner_bot
        else calculate_sale_price_from_base_usdt(data["unit_price"], proxy_base_markup_percent())
    )
    return create_order(
        user_id=user_id,
        proxy_kind="static",
        category_id=data["category_id"],
        category_name=data["category_name"],
        item_id=data["item_id"],
        protocol=data["protocol"],
        quantity=quantity,
        unit_price=sale_unit_price,
        total_price=round(sale_unit_price * quantity, 2),
        partner_bot_id=(int(partner_bot["id"]) if partner_bot else None),
        purchase_unit_price=data["unit_price"],
        partner_margin_percentage=margin_percentage,
        partner_profit_amount=(
            calculate_partner_profit_share_amount(data["unit_price"], quantity, margin_percentage)
            if partner_bot
            else 0.0
        ),
    )


@user.message(F.text.in_(tuple(REPLY_MENU_ACTIONS)))
async def reply_menu_action(message: Message, state: FSMContext):
    await state.clear()
    action = REPLY_MENU_ACTIONS.get(str(message.text or ""))
    lang = get_event_language_code(message)

    if action == "category":
        await render_main_menu_screen(
            message,
            message.from_user.id,
            message.bot,
            language_code=lang,
        )
        return

    if action == "all_products":
        await message.answer(
            partner_text_for(get_current_partner_bot(message.bot), "text_all_products"),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=ALL_PRODUCTS_REPLY_TEXT, web_app=WebAppInfo(url=ALL_PRODUCTS_SITE_URL), icon_custom_emoji_id=GOODS_BUTTON_EMOJI_ID)]]
            ),
        )
        return

    if action == "profile":
        add_user(message.from_user.id)
        profile = get_user_profile(message.from_user.id)
        await render_screen(
            message,
            build_profile_text(profile, lang),
            reply_markup=build_profile_keyboard(message.from_user.id, message.bot, language_code=lang),
            banner="profile",
            parse_mode="HTML",
        )
        return

    if action == "discounts":
        info = get_loyalty_discount_info(message.from_user.id)
        await render_screen(
            message,
            "💸 <b>Скидки</b>\n\n"
            f"Сумма пополнений: <b>{info['topups_total']:.2f}$</b>\n\n"
            "<blockquote><b>Пополнения ≥ 250$</b>\nСкидка на все: <b>3%</b></blockquote>\n"
            "<blockquote><b>Пополнения ≥ 500$</b>\nСкидка на все: <b>5%</b></blockquote>\n"
            "<blockquote><b>Пополнения ≥ 1000$</b>\nСкидка на все: <b>7%</b></blockquote>\n"
            f"Ваша скидка: <b>{info['discount_percent']:g}%</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=tr(lang, "common.back"), callback_data="back_main")]]),
            parse_mode="HTML",
        )
        return

    if action == "language":
        await render_screen(
            message,
            build_language_menu_text(lang),
            reply_markup=build_language_keyboard(lang),
        )
        return

    if action == "partner":
        current_partner = get_current_partner_bot(message.bot)
        if (
            current_partner is not None
            and str(current_partner.get("bot_username") or "").casefold() != "souspartnersbot"
            and int(current_partner.get("franchise_hide_create", 0) or 0) == 0
        ):
            if build_partner_referral_link(current_partner):
                await render_screen(
                    message,
                    build_partner_bot_invite_text(current_partner, lang),
                    reply_markup=build_partner_bot_invite_keyboard(current_partner, lang),
                    parse_mode="HTML",
                )
                return
        stats = get_partner_owner_summary(message.from_user.id)
        await render_screen(
            message,
            build_partner_program_text(
                total_bots=stats["total_bots"],
                total_users=stats["total_users"],
                total_earned_rub=stats["total_earned"],
                total_withdrawn=stats["total_withdrawn"],
                available_balance=stats["available_balance"],
                language_code=lang,
            ),
            reply_markup=build_partner_program_keyboard(lang, bot=message.bot),
            parse_mode="HTML",
        )
        return

    if action == "information":
        current_partner_bot = get_current_partner_bot(message.bot)
        if current_partner_bot is not None:
            owner_links = FRANCHISE_OWNER_LINKS.get(str(current_partner_bot.get("owner_id") or ""), {})
            # News/support links: only the partner's own value, or an explicit
            # per-franchise-owner override. No SOUS fallback — a fresh franchise
            # ships without a channel/support button; the partner adds their own
            # in the cabinet. Empty -> the button is not shown.
            news_url = (current_partner_bot.get("franchise_news_url") or owner_links.get("news") or "").strip()
            support_url = (current_partner_bot.get("franchise_support_url") or owner_links.get("support") or "").strip()
            usage_url = current_partner_bot.get("franchise_usage_url") or "https://telegra.ph/Polzovatelskoe-soglashenie-08-22-52"
            privacy_url = current_partner_bot.get("franchise_privacy_url") or "https://telegra.ph/Politika-konfidencialnosti-08-22-78"
            info_brand = current_partner_bot.get("franchise_info_brand") or "MARKET"
            info_rows = [
                [
                    InlineKeyboardButton(
                        text="Политика использования",
                        url=usage_url,
                        icon_custom_emoji_id=PROFILE_ORDERS_BUTTON_EMOJI_ID,
                    ),
                    InlineKeyboardButton(
                        text="Конфиденциальность",
                        url=privacy_url,
                        icon_custom_emoji_id=PROFILE_ORDERS_BUTTON_EMOJI_ID,
                    ),
                ],
            ]
            if news_url:
                info_rows.append([
                    InlineKeyboardButton(
                        text="Новостной канал",
                        url=news_url,
                        icon_custom_emoji_id=SUBSCRIPTION_BUTTON_EMOJI_ID,
                    )
                ])
            if support_url:
                info_rows.append([
                    InlineKeyboardButton(
                        text=tr(lang, "menu.support"),
                        url=support_url,
                        icon_custom_emoji_id=SUPPORT_BUTTON_EMOJI_ID,
                    )
                ])
            info_rows.append([InlineKeyboardButton(text=tr(lang, "common.back"), callback_data="back_main")])
            await render_screen(
                message,
                f"{premium_emoji(PROFILE_ORDERS_BUTTON_EMOJI_ID, 'ℹ️')} <b>{html.escape(str(info_brand))}</b>",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=info_rows),
                parse_mode="HTML",
            )
            return
        await render_screen(
            message,
            (
                f"{premium_emoji(PROFILE_ORDERS_BUTTON_EMOJI_ID, 'ℹ️')} <b>SOUS MARKET</b>\n\n"
                "Цифровые товары, SMS, Email и прокси в одном магазине. "
                "Перед покупкой ознакомьтесь с документацией и правилами сервиса."
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Политика использования",
                            url="https://sousmarketfranchize.shop/docs/#usage",
                            icon_custom_emoji_id=PROFILE_ORDERS_BUTTON_EMOJI_ID,
                        ),
                        InlineKeyboardButton(
                            text="Конфиденциальность",
                            url="https://sousmarketfranchize.shop/docs/#privacy",
                            icon_custom_emoji_id=PROFILE_ORDERS_BUTTON_EMOJI_ID,
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            text="Новостной канал",
                            url="https://t.me/+gLZDgbYbyGViMjZi",
                            icon_custom_emoji_id=SUBSCRIPTION_BUTTON_EMOJI_ID,
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text=tr(lang, "menu.support"),
                            url="https://t.me/UniversallSupportBot?start=bot",
                            icon_custom_emoji_id=SUPPORT_BUTTON_EMOJI_ID,
                        )
                    ],
                    [InlineKeyboardButton(text="Документация", url="https://sousmarketfranchize.shop/docs/", icon_custom_emoji_id=GOODS_BUTTON_EMOJI_ID)],
                    [InlineKeyboardButton(text=tr(lang, "common.back"), callback_data="back_main")],
                ]
            ),
            parse_mode="HTML",
        )


@user.callback_query(F.data == "magazine")
async def market_menu(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    lang = get_event_language_code(callback)
    try:
        await show_market_categories(callback.message, callback.from_user.id, callback.bot)
    except MarketProviderError as error:
        await render_screen(
            callback.message,
            f"{tr(lang, 'market.load_products_failed')}\n\n"
            f"{tr(lang, 'generic.reason', error=error)}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=tr(lang, "buttons.back"), callback_data="back_main")]]
            ),
        )


@user.callback_query(F.data == "market_mail_menu")
async def market_mail_menu(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    if not market_goods_enabled():
        await render_market_goods_unavailable(callback.message, user_id=callback.from_user.id)
        return
    await render_screen(
        callback.message,
        build_market_categories_text(get_event_language_code(callback)),
        reply_markup=build_market_mail_keyboard(get_event_language_code(callback)),
        banner="market",
    )


@user.callback_query(F.data == "market_social_menu")
async def market_social_menu(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    try:
        await show_market_group(
            callback.message,
            (1, 2, 3, 24, 34, 46, 108, 112, 142),
            callback.from_user.id,
        )
    except MarketProviderError as error:
        lang = get_event_language_code(callback)
        await render_screen(
            callback.message,
            f"{tr(lang, 'market.load_subcategories_failed')}\n\n"
            f"{tr(lang, 'generic.reason', error=error)}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=tr(lang, "buttons.back"), callback_data="magazine")]]
            ),
        )


@user.callback_query(F.data == "market_messengers_menu")
async def market_messengers_menu(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    try:
        await show_market_group(callback.message, (28, 44), callback.from_user.id)
    except MarketProviderError as error:
        lang = get_event_language_code(callback)
        await render_screen(
            callback.message,
            f"{tr(lang, 'market.load_subcategories_failed')}\n\n"
            f"{tr(lang, 'generic.reason', error=error)}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=tr(lang, "buttons.back"), callback_data="back_main")]]
            ),
        )


@user.callback_query(F.data == "market_messaging_social_menu")
async def market_messaging_social_menu(callback: CallbackQuery, state: FSMContext):
    """Show the combined messengers and social networks catalogue."""
    await callback.answer()
    await state.clear()
    try:
        await show_market_group(
            callback.message,
            (28, 44, 1, 2, 3, 24, 34, 46, 108, 112, 142),
            callback.from_user.id,
        )
    except MarketProviderError as error:
        lang = get_event_language_code(callback)
        await render_screen(
            callback.message,
            f"{tr(lang, 'market.load_subcategories_failed')}\n\n"
            f"{tr(lang, 'generic.reason', error=error)}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=tr(lang, "buttons.back"), callback_data="magazine")]]
            ),
        )


@user.callback_query(F.data == "market_games_menu")
async def market_games_menu(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    try:
        await show_market_group(callback.message, (114,), callback.from_user.id)
    except MarketProviderError as error:
        lang = get_event_language_code(callback)
        await render_screen(
            callback.message,
            f"{tr(lang, 'market.load_subcategories_failed')}\n\n"
            f"{tr(lang, 'generic.reason', error=error)}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=tr(lang, "buttons.back"), callback_data="magazine")]]
            ),
        )


@user.callback_query(F.data.startswith("market_favorites"))
async def market_favorites(callback: CallbackQuery):
    await callback.answer()
    if not market_goods_enabled():
        await render_market_goods_unavailable(callback.message, user_id=callback.from_user.id)
        return
    parts = str(callback.data or "").split(":", 1)
    page = max(int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1, 1)
    per_page = 15
    favorite_ids = sorted(list_favorite_product_ids(callback.from_user.id), reverse=True)
    products = await asyncio.gather(
        *(get_market_product(product_id) for product_id in favorite_ids),
        return_exceptions=True,
    )
    available_products = [
        product for product in products
        if isinstance(product, dict) and int(product.get("id") or 0) > 0
    ]
    last_page = max(math.ceil(len(available_products) / per_page), 1)
    page = min(page, last_page)
    page_products = available_products[(page - 1) * per_page:page * per_page]
    margin = get_effective_market_margin(callback.bot)
    rows = [
        [
            InlineKeyboardButton(
                text=f"{truncate_text(localize_market_text(product.get('title'), get_event_language_code(callback), fallback='Товар'), 34)} • "
                     f"{convert_rub_to_usdt(product.get('price', 0), margin):.2f} $",
                callback_data=f"market_product:{int(product.get('category_id') or 0)}:{product['id']}:1",
            )
        ]
        for product in page_products
        if int(product.get("category_id") or 0) > 0
    ]
    navigation = []
    if page > 1:
        navigation.append(InlineKeyboardButton(text="◀️", callback_data=f"market_favorites:{page - 1}"))
    if page < last_page:
        navigation.append(InlineKeyboardButton(text="▶️", callback_data=f"market_favorites:{page + 1}"))
    if navigation:
        rows.append(navigation)
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="magazine")])
    text = "♥ <b>Избранное</b>"
    if available_products:
        text += f"\n\nСохранённых товаров: <b>{len(available_products)}</b>"
        if last_page > 1:
            text += f"\nСтраница: <b>{page}/{last_page}</b>"
    else:
        text += "\n\nЗдесь пока нет добавленных товаров. Откройте карточку товара и нажмите «Добавить в избранное»."
    await render_screen(
        callback.message,
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )


@user.callback_query(F.data == "partner_bot_invite")
async def partner_bot_invite(callback: CallbackQuery, state: FSMContext):
    """Show the partner-program offer from inside a connected partner bot."""
    await state.clear()
    current_partner = get_current_partner_bot(callback.bot)
    if (
        current_partner is None
        or str(current_partner.get("bot_username") or "").casefold() == "souspartnersbot"
        or int(current_partner.get("franchise_hide_create", 0) or 0) != 0
    ):
        await callback.answer("Раздел недоступен в этом боте.", show_alert=True)
        return
    await callback.answer()
    lang = get_event_language_code(callback)
    await render_screen(
        callback.message,
        build_partner_bot_invite_text(current_partner, lang),
        reply_markup=build_partner_bot_invite_keyboard(current_partner, lang),
        parse_mode="HTML",
    )


@user.callback_query(F.data == "partner_program")
async def partner_program(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    stats = get_partner_owner_summary(callback.from_user.id)
    lang = get_event_language_code(callback)
    await render_screen(
        callback.message,
        build_partner_program_text(
            total_bots=stats["total_bots"],
            total_users=stats["total_users"],
            total_earned_rub=stats["total_earned"],
            total_withdrawn=stats["total_withdrawn"],
            available_balance=stats["available_balance"],
            language_code=lang,
        ),
        reply_markup=build_partner_program_keyboard(lang, bot=callback.bot),
        parse_mode="HTML",
    )


@user.callback_query(F.data == "partner_referral_program")
async def partner_referral_program(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    current_partner = get_current_partner_bot(callback.bot)
    if current_partner is not None and str(current_partner.get("bot_username") or "").casefold() != "souspartnersbot":
        await callback.answer("Откройте реферальную программу в SousPartnersBot.", show_alert=True)
        return
    add_user(callback.from_user.id)
    stats = get_partner_owner_summary(callback.from_user.id)
    referral_link = build_partner_referral_link(owner_id=callback.from_user.id)
    if not referral_link:
        await callback.answer("Не удалось создать ссылку.", show_alert=True)
        return
    lang = get_event_language_code(callback)
    await render_screen(
        callback.message,
        build_partner_referral_program_text(stats, referral_link, lang),
        reply_markup=build_partner_referral_program_keyboard(referral_link, lang),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@user.callback_query(F.data == "partner_referral_withdraw")
async def partner_referral_withdraw(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    current_partner = get_current_partner_bot(callback.bot)
    if current_partner is not None and str(current_partner.get("bot_username") or "").casefold() != "souspartnersbot":
        await callback.answer("Откройте реферальную программу в SousPartnersBot.", show_alert=True)
        return
    stats = get_partner_owner_summary(callback.from_user.id)
    referral_available = round(
        max(
            float(stats.get("partner_referral_earned") or 0.0)
            - float(stats.get("partner_referral_withdrawn") or 0.0),
            0.0,
        ),
        2,
    )
    if referral_available < PARTNER_MIN_WITHDRAW_AMOUNT:
        await callback.answer(f"❌ Вывод доступен от {PARTNER_MIN_WITHDRAW_AMOUNT:g}$", show_alert=True)
        return
    await callback.answer("Обрабатываю вывод…")
    try:
        transfer = await create_xrocket_transfer(
            tg_user_id=callback.from_user.id,
            amount=referral_available,
            currency=XROCKET_PAYMENT_ASSET,
            description="SousPartners referral payout",
        )
    except XRocketError as error:
        await callback.answer("Не удалось выполнить вывод.", show_alert=True)
        await callback.message.answer(f"Причина: {error}")
        return
    saved = record_partner_referral_withdrawal(
        owner_id=callback.from_user.id,
        amount=referral_available,
        asset=XROCKET_PAYMENT_ASSET,
        xrocket_transfer_id=transfer["transfer_id"],
    )
    if not saved:
        await callback.message.answer(
            "Перевод отправлен, но запись операции не сохранилась. Не повторяйте вывод и сообщите администратору."
        )
        return
    referral_link = build_partner_referral_link(owner_id=callback.from_user.id)
    updated_stats = get_partner_owner_summary(callback.from_user.id)
    lang = get_event_language_code(callback)
    await callback.message.edit_text(
        build_partner_referral_program_text(updated_stats, referral_link or "", lang),
        reply_markup=build_partner_referral_program_keyboard(referral_link or "", lang),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@user.callback_query(F.data == "partner_bots_manage")
async def partner_bots_manage(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    partner_bots = list_partner_bots_by_owner(callback.from_user.id)
    lang = get_event_language_code(callback)
    await render_screen(
        callback.message,
        build_partner_bots_text(partner_bots, lang),
        reply_markup=build_partner_bots_keyboard(partner_bots, lang),
    )


@user.callback_query(F.data.startswith("partner_bot_view:"))
async def partner_bot_view(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    partner_id = int(callback.data.split(":")[1])
    partner_bot = get_partner_bot_by_id(partner_id)
    if partner_bot is None or int(partner_bot["owner_id"]) != callback.from_user.id:
        await callback.answer("Бот не найден.", show_alert=True)
        return

    total_users = count_partner_bot_users(partner_id)
    await callback.message.edit_text(
        build_partner_bot_manage_text(partner_bot, total_users),
        reply_markup=build_partner_bot_manage_keyboard(partner_bot),
    )


@user.callback_query(F.data.startswith("partner_bot_toggle_create:"))
async def partner_bot_toggle_create(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    partner_id = int(callback.data.split(":", 1)[1])
    partner_bot = get_partner_bot_by_id(partner_id)
    if partner_bot is None or int(partner_bot.get("owner_id") or 0) != callback.from_user.id:
        await callback.answer("Бот не найден.", show_alert=True)
        return
    current = int(partner_bot.get("franchise_hide_create", 0) or 0)
    update_partner_franchise_setting(partner_id, "franchise_hide_create", 0 if current else 1)
    partner_bot = get_partner_bot_by_id(partner_id)
    await callback.message.edit_text(
        build_partner_bot_manage_text(partner_bot, count_partner_bot_users(partner_id)),
        reply_markup=build_partner_bot_manage_keyboard(partner_bot),
    )


@user.callback_query(F.data.startswith("partner_bot_margin:"))
async def partner_bot_margin(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    partner_id = int(callback.data.split(":")[1])
    partner_bot = get_partner_bot_by_id(partner_id)
    if partner_bot is None or int(partner_bot["owner_id"]) != callback.from_user.id:
        await callback.answer("Бот не найден.", show_alert=True)
        return

    await callback.message.edit_text(
        "💸 Изменение доп. наценки\n\n"
        f"Бот: @{partner_bot['bot_username']}\n"
        f"Базовая наценка магазина: {get_partner_shop_markup(partner_bot)}%\n\n"
        "Выберите направление, для которого нужно изменить наценку.",
        reply_markup=build_partner_margin_keyboard(partner_id),
    )


@user.callback_query(F.data.startswith("partner_markup_kind:"))
async def partner_markup_kind(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    _, partner_id_text, kind = callback.data.split(":")
    partner_id = int(partner_id_text)
    partner_bot = get_owned_partner_bot_by_id(partner_id, callback.from_user.id)
    if partner_bot is None or kind not in {"goods", "proxy", "sms"}:
        await callback.answer("Бот не найден.", show_alert=True)
        return
    labels = {"goods": "товары", "proxy": "прокси", "sms": "SMS"}
    await callback.message.edit_text(
        f"💸 Наценка: {labels[kind]}\n\n"
        f"Текущее значение: {get_partner_category_markup(partner_bot, kind)}%\n\n"
        "Выберите значение или введите своё.",
        reply_markup=build_partner_markup_value_keyboard(partner_id, kind),
    )


@user.callback_query(F.data.startswith("partner_markup_set:"))
async def partner_markup_set(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    _, partner_id, kind, margin = callback.data.split(":")
    if not update_partner_bot_markup(int(partner_id), callback.from_user.id, kind, int(margin)):
        await callback.answer("Не удалось обновить наценку.", show_alert=True)
        return
    partner_bot = get_partner_bot_by_id(int(partner_id))
    await callback.message.edit_text(
        build_partner_bot_manage_text(partner_bot, count_partner_bot_users(int(partner_id))),
        reply_markup=build_partner_bot_manage_keyboard(partner_bot),
    )


@user.callback_query(F.data.startswith("partner_markup_custom:"))
async def partner_markup_custom(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    _, partner_id_text, kind = callback.data.split(":")
    partner_id = int(partner_id_text)
    if get_owned_partner_bot_by_id(partner_id, callback.from_user.id) is None or kind not in {"goods", "proxy", "sms"}:
        await callback.answer("Бот не найден.", show_alert=True)
        return
    await state.set_state(PartnerBotState.waiting_markup_value)
    await state.update_data(partner_bot_id=partner_id, partner_markup_kind=kind)
    await callback.message.edit_text(
        "✍️ Введите наценку числом от 0 до 500%.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="◀️ Назад", callback_data=f"partner_markup_kind:{partner_id}:{kind}")
        ]]),
    )


@user.callback_query(F.data.startswith("partner_bot_withdraw:"))
async def partner_bot_withdraw(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    partner_id = int(callback.data.split(":")[1])
    partner_bot = get_partner_bot_by_id(partner_id)
    if partner_bot is None or int(partner_bot["owner_id"]) != callback.from_user.id:
        await callback.answer("Бот не найден.", show_alert=True)
        return

    available_balance = get_partner_bot_available_balance(partner_bot)
    if available_balance <= 0:
        await callback.answer(
            "❌ Вывод доступен от 5$",
            show_alert=True,
        )
        return
    if available_balance < PARTNER_MIN_WITHDRAW_AMOUNT:
        await callback.answer(
            "❌ Вывод доступен от 5$",
            show_alert=True,
        )
        return

    await callback.answer("Обрабатываю вывод...")

    try:
        transfer = await create_xrocket_transfer(
            tg_user_id=callback.from_user.id,
            amount=available_balance,
            currency=XROCKET_PAYMENT_ASSET,
            description=f"Partner payout for @{partner_bot['bot_username']}",
        )
    except XRocketError as error:
        await callback.message.edit_text(
            "Не удалось выполнить вывод на XROCKET.\n\n"
            f"Причина: {error}\n\n"
            "Проверьте, хватает ли баланса приложения в XROCKET, и попробуйте ещё раз.",
            reply_markup=build_partner_bot_manage_keyboard(partner_bot),
        )
        return

    saved = record_partner_withdrawal(
        partner_bot_id=partner_id,
        owner_id=callback.from_user.id,
        amount=available_balance,
        asset=XROCKET_PAYMENT_ASSET,
        xrocket_transfer_id=transfer["transfer_id"],
    )
    if not saved:
        await callback.message.edit_text(
            "Перевод в XROCKET был отправлен, но не удалось сохранить операцию в базе.\n\n"
            "Не повторяйте вывод автоматически. Лучше проверьте кошелёк и сообщите администратору.",
            reply_markup=build_partner_bot_manage_keyboard(partner_bot),
        )
        return

    partner_bot = get_partner_bot_by_id(partner_id)
    await callback.message.edit_text(
        "✅ Вывод на XROCKET выполнен.\n\n"
        f"Бот: @{partner_bot['bot_username']}\n"
        f"Сумма: {available_balance:.2f} {XROCKET_PAYMENT_ASSET}\n"
        f"Получатель: Telegram ID {callback.from_user.id}",
        reply_markup=build_partner_bot_manage_keyboard(partner_bot),
    )


@user.callback_query(F.data.startswith("partner_margin_set:"))
async def partner_margin_set(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    _, partner_id, margin = callback.data.split(":")
    updated = update_partner_bot_margin(int(partner_id), callback.from_user.id, int(margin))
    if not updated:
        await callback.answer("Не удалось обновить доп. наценку.", show_alert=True)
        return
    await callback.answer("Доп. наценка обновлена.")
    partner_bot = get_partner_bot_by_id(int(partner_id))
    await callback.message.edit_text(
        build_partner_bot_manage_text(partner_bot, count_partner_bot_users(int(partner_id))),
        reply_markup=build_partner_bot_manage_keyboard(partner_bot),
    )


@user.callback_query(F.data.startswith("partner_margin_custom:"))
async def partner_margin_custom(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    partner_id = int(callback.data.split(":")[1])
    partner_bot = get_partner_bot_by_id(partner_id)
    if partner_bot is None or int(partner_bot["owner_id"]) != callback.from_user.id:
        await callback.answer("Бот не найден.", show_alert=True)
        return
    await state.set_state(PartnerBotState.waiting_margin_value)
    await state.update_data(partner_bot_id=partner_id)
    await callback.message.edit_text(
        "✍️ Введите новую доп. наценку числом.\n\n"
        "Пример: 75\n"
        "Диапазон: от 0 до 500%",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data=f"partner_bot_margin:{partner_id}")]]
        ),
    )


@user.callback_query(F.data == "partner_add_bot")
async def partner_add_bot(callback: CallbackQuery, state: FSMContext):
    current_partner = get_current_partner_bot(callback.bot)
    is_franchise_entry = (
        current_partner is not None
        and str(current_partner.get("bot_username") or "").casefold() == "souspartnersbot"
    )
    if current_partner is None:
        await callback.answer()
        await state.clear()
        await callback.message.edit_text(
            "Подключение к партнёрской программе выполняется в отдельном боте франшизы.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Открыть @SousPartnersBot", url="https://t.me/SousPartnersBot")]
                ]
            ),
        )
        return
    if current_partner is not None and not is_franchise_entry:
        await callback.answer()
        await state.clear()
        await callback.message.edit_text(
            "Подключение к партнёрской программе выполняется в отдельном боте франшизы.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Открыть @SousPartnersBot", url="https://t.me/SousPartnersBot")]
                ]
            ),
        )
        return
    lang = get_event_language_code(callback)
    if int(get_partner_owner_summary(callback.from_user.id).get("total_bots") or 0) >= 2:
        await callback.answer("Можно создать не больше двух ботов.", show_alert=True)
        return
    await callback.answer()
    await state.clear()
    await state.set_state(PartnerBotState.waiting_bot_token)
    await callback.message.edit_text(build_partner_add_bot_text(lang))


@user.callback_query(F.data == "partner_add_bot_manual")
async def partner_add_bot_manual(callback: CallbackQuery, state: FSMContext):
    lang = get_event_language_code(callback)
    if int(get_partner_owner_summary(callback.from_user.id).get("total_bots") or 0) >= 2:
        await callback.answer("Можно создать не больше двух ботов.", show_alert=True)
        return
    await callback.answer(tr(lang, "partner.add_bot.prompt"))
    await state.set_state(PartnerBotState.waiting_bot_token)
    await callback.message.edit_text(
        build_partner_add_bot_text(lang),
        reply_markup=build_partner_add_bot_keyboard(lang),
    )


@user.message(PartnerBotState.waiting_bot_token)
async def receive_partner_bot_token(message: Message, state: FSMContext):
    lang = get_event_language_code(message)
    token = (message.text or "").strip()
    if not re.fullmatch(r"\d{8,12}:[A-Za-z0-9_-]{30,}", token):
        await message.answer(
            (
                "Токен виглядає некоректно.\n\n"
                "Перевірте, що ви повністю скопіювали його з @BotFather, і надішліть ще раз."
                if lang == "uk"
                else "Токен выглядит некорректно.\n\nПроверьте, что вы скопировали его полностью из @BotFather, и отправьте ещё раз."
            ),
        )
        return

    existing_partner = get_partner_bot_by_token(token)
    owner_summary = get_partner_owner_summary(message.from_user.id)
    is_current_active_bot = bool(
        existing_partner
        and int(existing_partner.get("owner_id") or 0) == message.from_user.id
        and int(existing_partner.get("is_active") or 0) == 1
    )
    if (
        int(owner_summary.get("total_bots") or 0) >= 2
        and not is_current_active_bot
    ):
        await state.clear()
        await message.answer(
            "⚠️ Можно создать не больше двух ботов во франшизе.",
            reply_markup=build_partner_program_keyboard(lang),
        )
        return

    runtime = get_partner_runtime()
    if runtime is None:
        await message.answer(
            "Партнёрский рантайм сейчас не инициализирован.\n\n"
            "Нужно перезапустить основной бот с подключённым менеджером партнёрских ботов.",
            reply_markup=build_partner_program_keyboard(lang),
        )
        return

    try:
        partner_bot = await runtime.onboard_partner_bot(
            owner_id=message.from_user.id,
            bot_token=token,
            margin_percentage=PARTNER_DEFAULT_MARGIN_PERCENT,
        )
    except ValueError as error:
        await message.answer(
            (
                f"Не вдалося підключити бота.\n\nПричина: {error}"
                if lang == "uk"
                else f"Не удалось подключить бота.\n\nПричина: {error}"
            ),
            reply_markup=build_partner_add_bot_keyboard(lang),
        )
        return
    except Exception:
        await message.answer(
            "Не удалось запустить партнёрского бота прямо сейчас.\n\n"
            "Проверьте токен и повторите попытку чуть позже.",
            reply_markup=build_partner_add_bot_keyboard(lang),
        )
        return

    await state.clear()
    await message.answer(
        (
            "✅ Бота підключено.\n\n"
            f"Ваш бот: @{partner_bot['bot_username']}\n"
            f"Посилання: https://t.me/{partner_bot['bot_username']}"
            if lang == "uk"
            else "✅ Бот подключён.\n\n"
            f"Ваш бот: @{partner_bot['bot_username']}\n"
            f"Ссылка: https://t.me/{partner_bot['bot_username']}"
        ),
        reply_markup=build_partner_program_keyboard(lang),
    )


@user.message(PartnerBotState.waiting_margin_value)
async def receive_partner_margin_value(message: Message, state: FSMContext):
    margin_text = (message.text or "").strip()
    if not margin_text.isdigit():
        await message.answer("Отправьте доп. наценку числом, например: 75")
        return

    margin_percentage = int(margin_text)
    if not 0 <= margin_percentage <= 500:
        await message.answer("Допустимая доп. наценка: от 0 до 500%.")
        return

    data = await state.get_data()
    partner_id = int(data.get("partner_bot_id", 0))
    if partner_id <= 0:
        await state.clear()
        await message.answer("Не удалось определить бота для обновления.")
        return

    updated = update_partner_bot_margin(partner_id, message.from_user.id, margin_percentage)
    if not updated:
        await state.clear()
        await message.answer("Не удалось обновить доп. наценку для этого бота.")
        return

    partner_bot = get_partner_bot_by_id(partner_id)
    await state.clear()
    await message.answer(
        build_partner_bot_manage_text(partner_bot, count_partner_bot_users(partner_id)),
        reply_markup=build_partner_bot_manage_keyboard(partner_bot),
    )


@user.message(PartnerBotState.waiting_markup_value)
async def receive_partner_markup_value(message: Message, state: FSMContext):
    margin_text = (message.text or "").strip()
    if not margin_text.isdigit() or not 0 <= int(margin_text) <= 500:
        await message.answer("Отправьте число от 0 до 500.")
        return
    data = await state.get_data()
    partner_id = int(data.get("partner_bot_id") or 0)
    kind = str(data.get("partner_markup_kind") or "")
    if not update_partner_bot_markup(partner_id, message.from_user.id, kind, int(margin_text)):
        await state.clear()
        await message.answer("Не удалось обновить наценку.")
        return
    partner_bot = get_partner_bot_by_id(partner_id)
    await state.clear()
    await message.answer(
        build_partner_bot_manage_text(partner_bot, count_partner_bot_users(partner_id)),
        reply_markup=build_partner_bot_manage_keyboard(partner_bot),
    )


@user.callback_query(F.data.startswith("partner_bot_top:"))
async def partner_bot_top_buyers(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    partner_id = int(callback.data.split(":")[1])
    partner_bot = get_partner_bot_by_id(partner_id)
    if partner_bot is None or int(partner_bot["owner_id"]) != callback.from_user.id:
        await callback.answer("Бот не найден.", show_alert=True)
        return

    await callback.message.edit_text(
        build_partner_top_buyers_text(partner_bot, get_partner_top_buyers(partner_id)),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data=f"partner_bot_view:{partner_id}")]]
        ),
    )


@user.callback_query(F.data.startswith("partner_bot_broadcast:"))
async def partner_bot_broadcast(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    partner_id = int(callback.data.split(":")[1])
    partner_bot = get_owned_partner_bot_by_id(partner_id, callback.from_user.id)
    if partner_bot is None:
        await callback.answer("Бот не найден.", show_alert=True)
        return

    await state.set_state(PartnerCabinetState.waiting_broadcast_message)
    await state.update_data(partner_bot_id=partner_id)
    await callback.message.edit_text(
        "📣 Рассылка\n\n"
        "Отправьте следующим сообщением текст, фото, видео, GIF или документ для рассылки от имени этого бота.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data=f"partner_bot_view:{partner_id}")]]
        ),
    )


@user.message(PartnerCabinetState.waiting_broadcast_message)
async def receive_partner_cabinet_broadcast_message(message: Message, state: FSMContext):
    data = await state.get_data()
    partner_id = int(data.get("partner_bot_id") or 0)
    partner_bot = get_owned_partner_bot_by_id(partner_id, message.from_user.id)
    if partner_bot is None:
        await state.clear()
        await message.answer("Доступ запрещён.")
        return

    payload = extract_partner_broadcast_payload(message)
    if payload is None:
        await message.answer("Поддерживаются текст, фото, видео, GIF и документы.")
        return

    await state.update_data(partner_broadcast_payload=payload)
    await state.set_state(PartnerCabinetState.waiting_broadcast_buttons)
    await message.answer(
        "Сообщение сохранено.\n\n"
        "Если нужны кнопки, отправьте их следующим сообщением в формате:\n"
        "Текст кнопки - https://example.com\n\n"
        "Если кнопки не нужны, нажмите «Пропустить кнопки».",
        reply_markup=build_partner_cabinet_broadcast_buttons_prompt_keyboard(partner_id),
    )


async def prompt_partner_cabinet_broadcast_confirmation(
    target_message: Message,
    state: FSMContext,
    partner_bot: dict,
    buttons_text: str,
):
    audience_count = count_partner_bot_users(int(partner_bot["id"]))
    await state.update_data(partner_broadcast_buttons_text=buttons_text)
    await target_message.answer(
        "⚠️ Подтверждение рассылки\n\n"
        f"Бот: @{partner_bot['bot_username']}\n"
        f"Вы собираетесь отправить сообщение аудитории: {audience_count} пользователей.\n\n"
        "Подтвердите действие.",
        reply_markup=build_partner_cabinet_broadcast_confirm_keyboard(int(partner_bot["id"])),
    )


@user.callback_query(F.data.startswith("partner_bot_broadcast_skip_buttons:"))
async def partner_bot_broadcast_skip_buttons(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    partner_id = int(callback.data.split(":")[1])
    partner_bot = get_owned_partner_bot_by_id(partner_id, callback.from_user.id)
    if partner_bot is None:
        await state.clear()
        await callback.answer("Бот не найден.", show_alert=True)
        return
    await prompt_partner_cabinet_broadcast_confirmation(callback.message, state, partner_bot, "")


@user.message(PartnerCabinetState.waiting_broadcast_buttons)
async def receive_partner_cabinet_broadcast_buttons(message: Message, state: FSMContext):
    data = await state.get_data()
    partner_id = int(data.get("partner_bot_id") or 0)
    partner_bot = get_owned_partner_bot_by_id(partner_id, message.from_user.id)
    if partner_bot is None:
        await state.clear()
        await message.answer("Доступ запрещён.")
        return

    reply_markup = parse_broadcast_buttons(message.text or "")
    if reply_markup is None:
        await message.answer(
            "Не удалось распознать кнопки.\n\n"
            "Используйте формат:\n"
            "Кнопка 1 - https://example.com\n"
            "Кнопка 2 - https://example.com",
            reply_markup=build_partner_cabinet_broadcast_buttons_prompt_keyboard(partner_id),
        )
        return

    await prompt_partner_cabinet_broadcast_confirmation(message, state, partner_bot, message.text or "")


@user.callback_query(F.data.startswith("partner_bot_broadcast_confirm:"))
async def partner_bot_broadcast_confirm(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Запускаю рассылку...")
    partner_id = int(callback.data.split(":")[1])
    partner_bot = get_owned_partner_bot_by_id(partner_id, callback.from_user.id)
    if partner_bot is None:
        await state.clear()
        await callback.answer("Бот не найден.", show_alert=True)
        return

    data = await state.get_data()
    payload = data.get("partner_broadcast_payload")
    if not isinstance(payload, dict):
        await state.clear()
        await callback.message.answer("Не удалось найти сохранённое сообщение для рассылки.")
        return
    buttons_text = data.get("partner_broadcast_buttons_text") or ""
    reply_markup = parse_broadcast_buttons(buttons_text) if buttons_text else None
    await state.clear()
    await send_partner_cabinet_broadcast(callback.message, partner_bot, payload, reply_markup)


@user.callback_query(F.data.startswith("partner_bot_subscription:"))
async def partner_bot_subscription(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    partner_id = int(callback.data.split(":")[1])
    partner_bot = get_owned_partner_bot_by_id(partner_id, callback.from_user.id)
    if partner_bot is None:
        await callback.answer("Бот не найден.", show_alert=True)
        return
    await show_partner_cabinet_subscription_screen(callback.message, partner_bot)


@user.callback_query(F.data.startswith("partner_bot_subscription_toggle:"))
async def partner_bot_subscription_toggle(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    partner_id = int(callback.data.split(":")[1])
    partner_bot = get_owned_partner_bot_by_id(partner_id, callback.from_user.id)
    if partner_bot is None:
        await callback.answer("Бот не найден.", show_alert=True)
        return
    enabled = int(partner_bot.get("subscription_enabled") or 0) == 1
    channel_id = partner_bot.get("subscription_channel_id")
    channel_url = partner_bot.get("subscription_channel_url")
    if not enabled and not channel_id:
        await callback.answer("Сначала выберите канал.", show_alert=True)
        return

    update_partner_subscription_settings(
        partner_id,
        callback.from_user.id,
        enabled=not enabled,
        channel_id=channel_id,
        channel_url=channel_url,
    )
    updated_bot = get_partner_bot_by_id(partner_id)
    await show_partner_cabinet_subscription_screen(callback.message, updated_bot)


@user.callback_query(F.data.startswith("partner_bot_subscription_channel_id:"))
async def partner_bot_subscription_channel_id(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    partner_id = int(callback.data.split(":")[1])
    partner_bot = get_owned_partner_bot_by_id(partner_id, callback.from_user.id)
    if partner_bot is None:
        await callback.answer("Бот не найден.", show_alert=True)
        return
    await state.set_state(PartnerCabinetState.waiting_subscription_channel_id)
    await state.update_data(partner_bot_id=partner_id)
    await callback.message.edit_text(
        "🔔 Выбор канала для обязательной подписки\n\n"
        f"1. Добавьте @{partner_bot['bot_username']} в администраторы канала.\n"
        "2. После этого перешлите сюда любой пост из канала\n"
        "или отправьте @username канала.\n\n"
        "После проверки я предложу подтвердить найденный канал.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data=f"partner_bot_subscription:{partner_id}")]]
        ),
    )


@user.message(PartnerCabinetState.waiting_subscription_channel_id)
async def receive_partner_cabinet_subscription_channel_id(message: Message, state: FSMContext):
    data = await state.get_data()
    partner_id = int(data.get("partner_bot_id") or 0)
    partner_bot = get_owned_partner_bot_by_id(partner_id, message.from_user.id)
    if partner_bot is None:
        await state.clear()
        await message.answer("Доступ запрещён.")
        return

    channel_reference = extract_channel_reference_from_message(message)
    if not channel_reference:
        await message.answer(
            "Не удалось определить канал.\n\n"
            "Перешлите пост из канала или отправьте @username канала."
        )
        return

    try:
        resolved_channel = await resolve_partner_subscription_channel(partner_bot, channel_reference)
    except ValueError as error:
        await message.answer(str(error))
        return
    except Exception:
        await message.answer(
            "Не удалось проверить канал.\n\n"
            "Убедитесь, что бот уже добавлен в администраторы, и отправьте канал ещё раз."
        )
        return

    display_channel = (
        f"@{resolved_channel['channel_username']}"
        if resolved_channel.get("channel_username")
        else resolved_channel["channel_title"]
    )
    await state.update_data(
        partner_subscription_candidate_id=resolved_channel["channel_id"],
        partner_subscription_candidate_url=resolved_channel.get("channel_url") or "",
    )
    await state.set_state(PartnerCabinetState.waiting_subscription_channel_confirm)
    await message.answer(
        "✅ Канал найден\n\n"
        f"Этот канал: {display_channel}\n"
        "Подтвердить выбор?",
        reply_markup=build_partner_cabinet_subscription_confirm_keyboard(partner_id),
    )


@user.callback_query(F.data.startswith("partner_bot_subscription_confirm:"))
async def partner_bot_subscription_confirm(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    partner_id = int(callback.data.split(":")[1])
    partner_bot = get_owned_partner_bot_by_id(partner_id, callback.from_user.id)
    if partner_bot is None:
        await state.clear()
        await callback.answer("Бот не найден.", show_alert=True)
        return

    data = await state.get_data()
    channel_id = (data.get("partner_subscription_candidate_id") or "").strip()
    candidate_url = (data.get("partner_subscription_candidate_url") or "").strip()
    if not channel_id:
        await state.clear()
        await callback.answer("Не удалось найти выбранный канал.", show_alert=True)
        return

    final_url = candidate_url or (partner_bot.get("subscription_channel_url") or None)
    update_partner_subscription_settings(
        partner_id,
        callback.from_user.id,
        enabled=bool(int(partner_bot.get("subscription_enabled") or 0)),
        channel_id=channel_id,
        channel_url=final_url,
    )
    updated_bot = get_partner_bot_by_id(partner_id)
    await state.clear()
    await callback.message.edit_text(
        "✅ Канал для обязательной подписки сохранён.\n\n"
        f"Канал выбран успешно.\n"
        f"Ссылка: {final_url or 'не установлена'}",
        reply_markup=build_partner_bot_manage_keyboard(updated_bot),
    )


@user.callback_query(F.data.startswith("partner_bot_subscription_channel_url:"))
async def partner_bot_subscription_channel_url(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    partner_id = int(callback.data.split(":")[1])
    partner_bot = get_owned_partner_bot_by_id(partner_id, callback.from_user.id)
    if partner_bot is None:
        await callback.answer("Бот не найден.", show_alert=True)
        return
    await state.set_state(PartnerCabinetState.waiting_subscription_channel_url)
    await state.update_data(partner_bot_id=partner_id)
    await callback.message.edit_text(
        "Введите ссылку на канал.\n\nПример:\nhttps://t.me/my_channel",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data=f"partner_bot_subscription:{partner_id}")]]
        ),
    )


@user.message(PartnerCabinetState.waiting_subscription_channel_url)
async def receive_partner_cabinet_subscription_channel_url(message: Message, state: FSMContext):
    data = await state.get_data()
    partner_id = int(data.get("partner_bot_id") or 0)
    partner_bot = get_owned_partner_bot_by_id(partner_id, message.from_user.id)
    if partner_bot is None:
        await state.clear()
        await message.answer("Доступ запрещён.")
        return

    channel_url = (message.text or "").strip()
    if channel_url and not (channel_url.startswith("https://") or channel_url.startswith("http://") or channel_url.startswith("tg://")):
        await message.answer("Отправьте корректную ссылку на канал.")
        return

    update_partner_subscription_settings(
        partner_id,
        message.from_user.id,
        enabled=bool(int(partner_bot.get("subscription_enabled") or 0)),
        channel_id=partner_bot.get("subscription_channel_id"),
        channel_url=channel_url or None,
    )
    updated_bot = get_partner_bot_by_id(partner_id)
    await state.clear()
    await message.answer(
        "✅ Ссылка на канал обновлена.",
        reply_markup=build_partner_bot_manage_keyboard(updated_bot),
    )


@user.callback_query(F.data.startswith("partner_bot_referral:"))
async def partner_bot_referral(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    partner_id = int(callback.data.split(":")[1])
    partner_bot = get_owned_partner_bot_by_id(partner_id, callback.from_user.id)
    if partner_bot is None:
        await callback.answer("Бот не найден.", show_alert=True)
        return
    await show_partner_cabinet_referral_screen(callback.message, partner_bot)


@user.callback_query(F.data.startswith("partner_bot_referral_toggle:"))
async def partner_bot_referral_toggle(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    partner_id = int(callback.data.split(":")[1])
    partner_bot = get_owned_partner_bot_by_id(partner_id, callback.from_user.id)
    if partner_bot is None:
        await callback.answer("Бот не найден.", show_alert=True)
        return
    update_partner_referral_settings(
        partner_id,
        callback.from_user.id,
        enabled=not bool(int(partner_bot.get("referral_enabled") or 0)),
        referral_percent=float(partner_bot.get("referral_percent") or 0),
    )
    updated_bot = get_partner_bot_by_id(partner_id)
    await show_partner_cabinet_referral_screen(callback.message, updated_bot)


@user.callback_query(F.data.startswith("partner_bot_referral_percent:"))
async def partner_bot_referral_percent(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    partner_id = int(callback.data.split(":")[1])
    partner_bot = get_owned_partner_bot_by_id(partner_id, callback.from_user.id)
    if partner_bot is None:
        await callback.answer("Бот не найден.", show_alert=True)
        return
    await state.set_state(PartnerCabinetState.waiting_referral_percent)
    await state.update_data(partner_bot_id=partner_id)
    await callback.message.edit_text(
        "Введите новый процент реферальных отчислений от прибыли.\n\n"
        "Диапазон: 10-100\n"
        "Пример: 50",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data=f"partner_bot_referral:{partner_id}")]]
        ),
    )


@user.message(PartnerCabinetState.waiting_referral_percent)
async def receive_partner_cabinet_referral_percent(message: Message, state: FSMContext):
    percent_text = (message.text or "").strip().replace(",", ".")
    try:
        percent_value = float(percent_text)
    except ValueError:
        await message.answer("Отправьте процент числом, например: 25")
        return

    if not MIN_PARTNER_REFERRAL_PERCENT <= percent_value <= MAX_PARTNER_REFERRAL_PERCENT:
        await message.answer("Допустимый диапазон: от 10 до 100%.")
        return

    data = await state.get_data()
    partner_id = int(data.get("partner_bot_id") or 0)
    partner_bot = get_owned_partner_bot_by_id(partner_id, message.from_user.id)
    if partner_bot is None:
        await state.clear()
        await message.answer("Доступ запрещён.")
        return

    update_partner_referral_settings(
        partner_id,
        message.from_user.id,
        enabled=bool(int(partner_bot.get("referral_enabled") or 0)),
        referral_percent=percent_value,
    )
    updated_bot = get_partner_bot_by_id(partner_id)
    await state.clear()
    await message.answer(
        f"✅ Реферальный процент от прибыли обновлён: {percent_value:.2f}%",
        reply_markup=build_partner_bot_manage_keyboard(updated_bot),
    )


@user.callback_query(F.data.startswith("partner_bot_referral_whitelist_add:"))
async def partner_bot_referral_whitelist_add(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    partner_id = int(callback.data.split(":")[1])
    partner_bot = get_owned_partner_bot_by_id(partner_id, callback.from_user.id)
    if partner_bot is None:
        await callback.answer("Бот не найден.", show_alert=True)
        return
    await state.set_state(PartnerCabinetState.waiting_referral_whitelist_add)
    await state.update_data(partner_bot_id=partner_id)
    await callback.message.edit_text(
        "Введите данные в формате:\n"
        "user_id процент\n\n"
        "Пример:\n1907513941 60",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data=f"partner_bot_referral:{partner_id}")]]
        ),
    )


@user.message(PartnerCabinetState.waiting_referral_whitelist_add)
async def receive_partner_cabinet_referral_whitelist_add(message: Message, state: FSMContext):
    parts = re.split(r"\s+", (message.text or "").strip())
    if len(parts) not in {1, 2} or not parts[0].isdigit():
        await message.answer("Формат: user_id процент\nДиапазон процента: 10-100\nПример: 1907513941 60")
        return

    data = await state.get_data()
    partner_id = int(data.get("partner_bot_id") or 0)
    partner_bot = get_owned_partner_bot_by_id(partner_id, message.from_user.id)
    if partner_bot is None:
        await state.clear()
        await message.answer("Доступ запрещён.")
        return

    user_id = int(parts[0])
    percent_value = None
    if len(parts) == 2:
        try:
            percent_value = float(parts[1].replace(",", "."))
        except ValueError:
            await message.answer("Процент должен быть числом.")
            return

    whitelist_percent = percent_value if percent_value is not None else float(partner_bot.get("referral_percent") or 0)
    if not MIN_PARTNER_REFERRAL_PERCENT <= whitelist_percent <= MAX_PARTNER_REFERRAL_PERCENT:
        await message.answer("Допустимый диапазон процента: от 10 до 100.")
        return

    upsert_partner_referral_whitelist(partner_id, user_id, whitelist_percent)
    updated_bot = get_partner_bot_by_id(partner_id)
    await state.clear()
    await message.answer(
        f"✅ Пользователь {user_id} добавлен в вайт-лист с {whitelist_percent:.2f}%.",
        reply_markup=build_partner_bot_manage_keyboard(updated_bot),
    )


@user.callback_query(F.data.startswith("partner_bot_referral_whitelist_remove:"))
async def partner_bot_referral_whitelist_remove(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    partner_id = int(callback.data.split(":")[1])
    partner_bot = get_owned_partner_bot_by_id(partner_id, callback.from_user.id)
    if partner_bot is None:
        await callback.answer("Бот не найден.", show_alert=True)
        return
    await state.set_state(PartnerCabinetState.waiting_referral_whitelist_remove)
    await state.update_data(partner_bot_id=partner_id)
    await callback.message.edit_text(
        "Введите user_id пользователя, которого нужно удалить из вайт-листа.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data=f"partner_bot_referral:{partner_id}")]]
        ),
    )


@user.message(PartnerCabinetState.waiting_referral_whitelist_remove)
async def receive_partner_cabinet_referral_whitelist_remove(message: Message, state: FSMContext):
    user_id_text = (message.text or "").strip()
    if not user_id_text.isdigit():
        await message.answer("Отправьте только числовой user_id.")
        return

    data = await state.get_data()
    partner_id = int(data.get("partner_bot_id") or 0)
    partner_bot = get_owned_partner_bot_by_id(partner_id, message.from_user.id)
    if partner_bot is None:
        await state.clear()
        await message.answer("Доступ запрещён.")
        return

    deleted = remove_partner_referral_whitelist(partner_id, int(user_id_text))
    updated_bot = get_partner_bot_by_id(partner_id)
    await state.clear()
    await message.answer(
        "✅ Пользователь удалён из вайт-листа."
        if deleted
        else "Такого пользователя не было в вайт-листе.",
        reply_markup=build_partner_bot_manage_keyboard(updated_bot),
    )


@user.callback_query(F.data.startswith("partner_bot_utm:"))
async def partner_bot_utm(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    partner_id = int(callback.data.split(":")[1])
    partner_bot = get_owned_partner_bot_by_id(partner_id, callback.from_user.id)
    if partner_bot is None:
        await callback.answer("Бот не найден.", show_alert=True)
        return
    await show_partner_cabinet_utm_screen(callback.message, partner_bot)


@user.callback_query(F.data.startswith("partner_bot_utm_create:"))
async def partner_bot_utm_create(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    partner_id = int(callback.data.split(":")[1])
    partner_bot = get_owned_partner_bot_by_id(partner_id, callback.from_user.id)
    if partner_bot is None:
        await callback.answer("Бот не найден.", show_alert=True)
        return
    await state.set_state(PartnerCabinetState.waiting_utm_link_data)
    await state.update_data(partner_bot_id=partner_id)
    await callback.message.edit_text(
        "🔗 Создание UTM-ссылки\n\n"
        "Отправьте одно слово для ссылки.\n\n"
        "Примеры:\n"
        "instagram\n"
        "tiktok\n"
        "summerads\n\n"
        "Это слово будет использовано как источник UTM.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data=f"partner_bot_utm:{partner_id}")]]
        ),
    )


@user.message(PartnerCabinetState.waiting_utm_link_data)
async def receive_partner_cabinet_utm_link_data(message: Message, state: FSMContext):
    data = await state.get_data()
    partner_id = int(data.get("partner_bot_id") or 0)
    partner_bot = get_owned_partner_bot_by_id(partner_id, message.from_user.id)
    if partner_bot is None:
        await state.clear()
        await message.answer("Доступ запрещён.")
        return

    source_word = parse_partner_utm_source_word(message.text or "")
    if source_word is None:
        await message.answer(
            "Отправьте одно слово без пробелов.\n\n"
            "Примеры:\ninstagram\nsummerads\ntiktok"
        )
        return

    payload = build_partner_utm_payload(source_word)
    upsert_partner_bot_utm_link(partner_id, source_word, payload)
    utm_link = f"https://t.me/{partner_bot['bot_username']}?start={payload}"
    await state.clear()
    await message.answer(
        "✅ UTM-ссылка создана\n\n"
        f"🤖 Бот: @{partner_bot['bot_username']}\n"
        f"🏷 Метка: {source_word}\n"
        f"🔗 Ссылка:\n{utm_link}\n\n"
        "Переходы по этой ссылке будут учитываться в аналитике этого бота.",
        reply_markup=build_partner_cabinet_utm_keyboard(partner_id),
        disable_web_page_preview=True,
    )


@user.callback_query(F.data == "partner_subscription_check")
async def partner_subscription_check(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    lang = get_event_language_code(callback)
    partner_bot = get_current_partner_bot(callback.bot)
    if partner_bot is None:
        await render_main_menu_screen(callback.message, callback.from_user.id, callback.bot, language_code=lang, refresh_reply_keyboard=True)
        return

    if int(partner_bot.get("owner_id") or 0) == callback.from_user.id:
        await render_main_menu_screen(callback.message, callback.from_user.id, callback.bot, language_code=lang, refresh_reply_keyboard=True)
        return

    channel_id = (partner_bot.get("subscription_channel_id") or "").strip()
    if not channel_id:
        await render_main_menu_screen(callback.message, callback.from_user.id, callback.bot, language_code=lang, refresh_reply_keyboard=True)
        return

    subscription_status = await is_user_subscribed_to_channel(callback.bot, channel_id, callback.from_user.id)
    if subscription_status is False:
        await callback.answer(
            tr(lang, "generic.subscription_required", channel=get_partner_subscription_channel_label(partner_bot)),
            show_alert=True,
        )
        await render_screen(
            callback.message,
            build_partner_subscription_gate_text(partner_bot, lang),
            reply_markup=build_partner_subscription_gate_keyboard(partner_bot, lang),
            disable_web_page_preview=True,
        )
        return

    await render_main_menu_screen(callback.message, callback.from_user.id, callback.bot, language_code=lang, refresh_reply_keyboard=True)


@user.callback_query(F.data == "main_subscription_check")
async def main_subscription_check(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    lang = get_event_language_code(callback)
    if get_current_partner_bot(callback.bot) is not None:
        await render_main_menu_screen(callback.message, callback.from_user.id, callback.bot, language_code=lang, refresh_reply_keyboard=True)
        return

    if is_admin_user(callback.from_user.id):
        await render_main_menu_screen(callback.message, callback.from_user.id, callback.bot, language_code=lang, refresh_reply_keyboard=True)
        return

    settings = get_main_bot_settings()
    channel_id = (settings.get("subscription_channel_id") or "").strip()
    if not channel_id:
        await render_main_menu_screen(callback.message, callback.from_user.id, callback.bot, language_code=lang, refresh_reply_keyboard=True)
        return

    subscription_status = await is_user_subscribed_to_channel(callback.bot, channel_id, callback.from_user.id)
    if subscription_status is False:
        await callback.answer(
            tr(lang, "generic.subscription_required", channel=get_main_subscription_channel_label(settings)),
            show_alert=True,
        )
        await render_screen(
            callback.message,
            build_main_subscription_gate_text(settings, lang),
            reply_markup=build_main_subscription_gate_keyboard(settings, lang),
            disable_web_page_preview=True,
        )
        return

    await render_main_menu_screen(callback.message, callback.from_user.id, callback.bot, language_code=lang, refresh_reply_keyboard=True)


@user.callback_query(F.data == "partner_admin_panel")
async def partner_admin_panel(callback: CallbackQuery, state: FSMContext):
    await callback.answer(
        "Управление перенесено в раздел «Партнёрские боты» основного бота.",
        show_alert=True,
    )


@user.callback_query(F.data == "partner_admin_margin")
async def partner_admin_margin(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    partner_bot = get_owned_current_partner_bot(callback.bot, callback.from_user.id)
    if partner_bot is None:
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await callback.message.edit_text(
        "💸 Изменение доп. наценки\n\n"
        f"Бот: @{partner_bot['bot_username']}\n"
        f"Текущая доп. наценка: {int(partner_bot.get('margin_percentage') or 0)}%\n"
        f"Базовая наценка магазина: {get_partner_shop_markup(partner_bot)}%\n\n"
        "Выберите новую доп. наценку или введите свою.",
        reply_markup=build_partner_admin_margin_keyboard(),
    )


@user.callback_query(F.data.startswith("partner_admin_margin_set:"))
async def partner_admin_margin_set(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    partner_bot = get_owned_current_partner_bot(callback.bot, callback.from_user.id)
    if partner_bot is None:
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    margin = int(callback.data.split(":")[1])
    update_partner_bot_margin(int(partner_bot["id"]), callback.from_user.id, margin)
    updated_bot = get_partner_bot_by_id(int(partner_bot["id"]))
    await callback.message.edit_text(
        "✅ Доп. наценка обновлена.\n\n"
        f"Текущая доп. наценка: {int(updated_bot.get('margin_percentage') or 0)}%",
        reply_markup=build_partner_admin_panel_keyboard(),
    )


@user.callback_query(F.data == "partner_admin_margin_custom")
async def partner_admin_margin_custom(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    partner_bot = get_owned_current_partner_bot(callback.bot, callback.from_user.id)
    if partner_bot is None:
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await state.set_state(PartnerAdminState.waiting_margin_value)
    await callback.message.edit_text(
        "✍️ Введите новую доп. наценку числом.\n\n"
        "Пример: 75\n"
        "Диапазон: от 0 до 500%",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="partner_admin_margin")]]
        ),
    )


@user.message(PartnerAdminState.waiting_margin_value)
async def receive_partner_admin_margin_value(message: Message, state: FSMContext):
    margin_text = (message.text or "").strip()
    if not margin_text.isdigit():
        await message.answer("Отправьте доп. наценку числом, например: 75")
        return

    margin_percentage = int(margin_text)
    if not 0 <= margin_percentage <= 500:
        await message.answer("Допустимая доп. наценка: от 0 до 500%.")
        return

    partner_bot = get_owned_current_partner_bot(message.bot, message.from_user.id)
    if partner_bot is None:
        await state.clear()
        await message.answer("Доступ запрещён.")
        return

    update_partner_bot_margin(int(partner_bot["id"]), message.from_user.id, margin_percentage)
    await state.clear()
    await message.answer(
        f"✅ Доп. наценка обновлена до {margin_percentage}%.",
        reply_markup=build_partner_admin_panel_keyboard(),
    )


@user.callback_query(F.data == "partner_admin_broadcast")
async def partner_admin_broadcast(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    partner_bot = get_owned_current_partner_bot(callback.bot, callback.from_user.id)
    if partner_bot is None:
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await state.set_state(PartnerAdminState.waiting_broadcast_message)
    await state.update_data(partner_broadcast_bot_id=int(partner_bot["id"]))
    await callback.message.edit_text(
        "📣 Рассылка\n\nОтправьте следующим сообщением текст, фото или любой пост, который нужно разослать.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Отмена", callback_data="partner_admin_panel")]]
        ),
    )


@user.message(PartnerAdminState.waiting_broadcast_message)
async def partner_admin_broadcast_message(message: Message, state: FSMContext):
    partner_bot = get_owned_current_partner_bot(message.bot, message.from_user.id)
    if partner_bot is None:
        await state.clear()
        await message.answer("Доступ запрещён.")
        return

    await state.update_data(
        partner_broadcast_chat_id=message.chat.id,
        partner_broadcast_message_id=message.message_id,
    )
    await state.set_state(PartnerAdminState.waiting_broadcast_buttons)
    await message.answer(
        "Сообщение для рассылки сохранено.\n\n"
        "Если нужны кнопки, отправьте их следующим сообщением в формате:\n"
        "Текст кнопки - https://example.com\n\n"
        "Каждая кнопка с новой строки.\n"
        "Если кнопки не нужны, нажмите «Пропустить кнопки».",
        reply_markup=build_partner_admin_broadcast_buttons_prompt_keyboard(),
    )


async def prompt_partner_broadcast_confirmation(
    target_message: Message,
    state: FSMContext,
    buttons_text: str,
):
    data = await state.get_data()
    partner_bot_id = int(data.get("partner_broadcast_bot_id") or 0)
    audience_count = count_partner_bot_users(partner_bot_id) if partner_bot_id > 0 else 0
    await state.update_data(partner_broadcast_buttons_text=buttons_text)
    await target_message.answer(
        "⚠️ Подтверждение рассылки\n\n"
        f"Вы собираетесь отправить сообщение аудитории: {audience_count} пользователей.\n\n"
        "Подтвердите действие.",
        reply_markup=build_partner_admin_broadcast_confirm_keyboard(),
    )


@user.callback_query(F.data == "partner_admin_broadcast_skip_buttons")
async def partner_admin_broadcast_skip_buttons(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    partner_bot = get_owned_current_partner_bot(callback.bot, callback.from_user.id)
    if partner_bot is None:
        await state.clear()
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await prompt_partner_broadcast_confirmation(callback.message, state, "")


@user.message(PartnerAdminState.waiting_broadcast_buttons)
async def partner_admin_broadcast_buttons(message: Message, state: FSMContext):
    partner_bot = get_owned_current_partner_bot(message.bot, message.from_user.id)
    if partner_bot is None:
        await state.clear()
        await message.answer("Доступ запрещён.")
        return

    reply_markup = parse_broadcast_buttons(message.text or "")
    if reply_markup is None:
        await message.answer(
            "Не удалось распознать кнопки.\n\n"
            "Используйте формат:\n"
            "Кнопка 1 - https://example.com\n"
            "Кнопка 2 - https://example.com",
            reply_markup=build_partner_admin_broadcast_buttons_prompt_keyboard(),
        )
        return

    await prompt_partner_broadcast_confirmation(message, state, message.text or "")


@user.callback_query(F.data == "partner_admin_broadcast_confirm")
async def partner_admin_broadcast_confirm(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    partner_bot = get_owned_current_partner_bot(callback.bot, callback.from_user.id)
    if partner_bot is None:
        await state.clear()
        await callback.answer("Доступ запрещён.", show_alert=True)
        return

    data = await state.get_data()
    buttons_text = data.get("partner_broadcast_buttons_text") or ""
    reply_markup = parse_broadcast_buttons(buttons_text) if buttons_text else None
    await send_partner_broadcast(callback.message, state, reply_markup)


@user.callback_query(F.data == "partner_admin_subscription")
async def partner_admin_subscription(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    if get_owned_current_partner_bot(callback.bot, callback.from_user.id) is None:
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await show_partner_admin_subscription_screen(callback.message, callback.bot, callback.from_user.id)


@user.callback_query(F.data == "partner_admin_subscription_toggle")
async def partner_admin_subscription_toggle(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    partner_bot = get_owned_current_partner_bot(callback.bot, callback.from_user.id)
    if partner_bot is None:
        await callback.answer("Доступ запрещён.", show_alert=True)
        return

    enabled = int(partner_bot.get("subscription_enabled") or 0) == 1
    channel_id = partner_bot.get("subscription_channel_id")
    channel_url = partner_bot.get("subscription_channel_url")
    if not enabled and not channel_id:
        await callback.answer("Сначала выберите канал.", show_alert=True)
        return

    update_partner_subscription_settings(
        int(partner_bot["id"]),
        callback.from_user.id,
        enabled=not enabled,
        channel_id=channel_id,
        channel_url=channel_url,
    )
    await show_partner_admin_subscription_screen(callback.message, callback.bot, callback.from_user.id)


@user.callback_query(F.data == "partner_admin_subscription_channel_id")
async def partner_admin_subscription_channel_id(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    partner_bot = get_owned_current_partner_bot(callback.bot, callback.from_user.id)
    if partner_bot is None:
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await state.set_state(PartnerAdminState.waiting_subscription_channel_id)
    await callback.message.edit_text(
        "Введите ID или @username канала для обязательной подписки.\n\n"
        "Примеры:\n@my_channel\n-1001234567890",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="partner_admin_subscription")]]
        ),
    )


@user.message(PartnerAdminState.waiting_subscription_channel_id)
async def receive_partner_subscription_channel_id(message: Message, state: FSMContext):
    partner_bot = get_owned_current_partner_bot(message.bot, message.from_user.id)
    if partner_bot is None:
        await state.clear()
        await message.answer("Доступ запрещён.")
        return

    channel_id = (message.text or "").strip()
    if not channel_id:
        await message.answer("Отправьте ID или @username канала.")
        return

    update_partner_subscription_settings(
        int(partner_bot["id"]),
        message.from_user.id,
        enabled=bool(int(partner_bot.get("subscription_enabled") or 0)),
        channel_id=channel_id,
        channel_url=partner_bot.get("subscription_channel_url"),
    )
    await state.clear()
    await message.answer(
        "✅ Канал обновлён.",
        reply_markup=build_partner_admin_panel_keyboard(),
    )


@user.callback_query(F.data == "partner_admin_subscription_channel_url")
async def partner_admin_subscription_channel_url(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    partner_bot = get_owned_current_partner_bot(callback.bot, callback.from_user.id)
    if partner_bot is None:
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await state.set_state(PartnerAdminState.waiting_subscription_channel_url)
    await callback.message.edit_text(
        "Введите ссылку на канал.\n\nПример:\nhttps://t.me/my_channel",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="partner_admin_subscription")]]
        ),
    )


@user.message(PartnerAdminState.waiting_subscription_channel_url)
async def receive_partner_subscription_channel_url(message: Message, state: FSMContext):
    partner_bot = get_owned_current_partner_bot(message.bot, message.from_user.id)
    if partner_bot is None:
        await state.clear()
        await message.answer("Доступ запрещён.")
        return

    channel_url = (message.text or "").strip()
    if channel_url and not (channel_url.startswith("https://") or channel_url.startswith("http://") or channel_url.startswith("tg://")):
        await message.answer("Отправьте корректную ссылку на канал.")
        return

    update_partner_subscription_settings(
        int(partner_bot["id"]),
        message.from_user.id,
        enabled=bool(int(partner_bot.get("subscription_enabled") or 0)),
        channel_id=partner_bot.get("subscription_channel_id"),
        channel_url=channel_url or None,
    )
    await state.clear()
    await message.answer(
        "✅ Ссылка на канал обновлена.",
        reply_markup=build_partner_admin_panel_keyboard(),
    )


@user.callback_query(F.data == "partner_admin_referrals")
async def partner_admin_referrals(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    if get_owned_current_partner_bot(callback.bot, callback.from_user.id) is None:
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await show_partner_admin_referral_screen(callback.message, callback.bot, callback.from_user.id)


@user.callback_query(F.data == "partner_admin_utm")
async def partner_admin_utm(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    if get_owned_current_partner_bot(callback.bot, callback.from_user.id) is None:
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await show_partner_admin_utm_screen(callback.message, callback.bot, callback.from_user.id)


@user.callback_query(F.data == "partner_admin_utm_create")
async def partner_admin_utm_create(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    partner_bot = get_owned_current_partner_bot(callback.bot, callback.from_user.id)
    if partner_bot is None:
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await state.set_state(PartnerAdminState.waiting_utm_link_data)
    await callback.message.edit_text(
        "🔗 Создание UTM-ссылки\n\n"
        "Отправьте одно слово для ссылки.\n\n"
        "Примеры:\n"
        "instagram\n"
        "tiktok\n"
        "summerads\n\n"
        "Это слово будет использовано как источник UTM.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="partner_admin_utm")]]
        ),
    )


@user.message(PartnerAdminState.waiting_utm_link_data)
async def receive_partner_utm_link_data(message: Message, state: FSMContext):
    partner_bot = get_owned_current_partner_bot(message.bot, message.from_user.id)
    if partner_bot is None:
        await state.clear()
        await message.answer("Доступ запрещён.")
        return

    source_word = parse_partner_utm_source_word(message.text or "")
    if source_word is None:
        await message.answer(
            "Отправьте одно слово без пробелов.\n\n"
            "Примеры:\ninstagram\nsummerads\ntiktok"
        )
        return

    payload = build_partner_utm_payload(source_word)
    upsert_partner_bot_utm_link(int(partner_bot["id"]), source_word, payload)
    utm_link = f"https://t.me/{partner_bot['bot_username']}?start={payload}"
    await state.clear()
    await message.answer(
        "✅ UTM-ссылка создана\n\n"
        f"🤖 Бот: @{partner_bot['bot_username']}\n"
        f"🏷 Метка: {source_word}\n"
        f"🔗 Ссылка:\n{utm_link}\n\n"
        "Переходы по этой ссылке будут учитываться в UTM-аналитике этого бота.",
        reply_markup=build_partner_admin_utm_keyboard(),
        disable_web_page_preview=True,
    )


@user.callback_query(F.data == "partner_admin_referrals_toggle")
async def partner_admin_referrals_toggle(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    partner_bot = get_owned_current_partner_bot(callback.bot, callback.from_user.id)
    if partner_bot is None:
        await callback.answer("Доступ запрещён.", show_alert=True)
        return

    update_partner_referral_settings(
        int(partner_bot["id"]),
        callback.from_user.id,
        enabled=not bool(int(partner_bot.get("referral_enabled") or 0)),
        referral_percent=float(partner_bot.get("referral_percent") or 0),
    )
    await show_partner_admin_referral_screen(callback.message, callback.bot, callback.from_user.id)


@user.callback_query(F.data == "partner_admin_referrals_percent")
async def partner_admin_referrals_percent(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    partner_bot = get_owned_current_partner_bot(callback.bot, callback.from_user.id)
    if partner_bot is None:
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await state.set_state(PartnerAdminState.waiting_referral_percent)
    await callback.message.edit_text(
        "Введите новый процент реферальных отчислений от прибыли.\n\n"
        "Диапазон: 10-100\n"
        "Пример: 50",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="partner_admin_referrals")]]
        ),
    )


@user.message(PartnerAdminState.waiting_referral_percent)
async def receive_partner_referral_percent(message: Message, state: FSMContext):
    percent_text = (message.text or "").strip().replace(",", ".")
    try:
        percent_value = float(percent_text)
    except ValueError:
        await message.answer("Отправьте процент числом, например: 25")
        return

    if not MIN_PARTNER_REFERRAL_PERCENT <= percent_value <= MAX_PARTNER_REFERRAL_PERCENT:
        await message.answer("Допустимый диапазон: от 10 до 100%.")
        return

    partner_bot = get_owned_current_partner_bot(message.bot, message.from_user.id)
    if partner_bot is None:
        await state.clear()
        await message.answer("Доступ запрещён.")
        return

    update_partner_referral_settings(
        int(partner_bot["id"]),
        message.from_user.id,
        enabled=bool(int(partner_bot.get("referral_enabled") or 0)),
        referral_percent=percent_value,
    )
    await state.clear()
    await message.answer(
        f"✅ Реферальный процент от прибыли обновлён: {percent_value:.2f}%",
        reply_markup=build_partner_admin_panel_keyboard(),
    )


@user.callback_query(F.data == "partner_admin_referrals_whitelist_add")
async def partner_admin_referrals_whitelist_add(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    partner_bot = get_owned_current_partner_bot(callback.bot, callback.from_user.id)
    if partner_bot is None:
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await state.set_state(PartnerAdminState.waiting_referral_whitelist_add)
    await callback.message.edit_text(
        "Введите данные в формате:\n"
        "user_id процент\n\n"
        "Пример:\n1907513941 60",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="partner_admin_referrals")]]
        ),
    )


@user.message(PartnerAdminState.waiting_referral_whitelist_add)
async def receive_partner_referral_whitelist_add(message: Message, state: FSMContext):
    parts = re.split(r"\s+", (message.text or "").strip())
    if len(parts) not in {1, 2} or not parts[0].isdigit():
        await message.answer("Формат: user_id процент\nДиапазон процента: 10-100\nПример: 1907513941 60")
        return

    user_id = int(parts[0])
    percent_value = None
    if len(parts) == 2:
        try:
            percent_value = float(parts[1].replace(",", "."))
        except ValueError:
            await message.answer("Процент должен быть числом.")
            return

    partner_bot = get_owned_current_partner_bot(message.bot, message.from_user.id)
    if partner_bot is None:
        await state.clear()
        await message.answer("Доступ запрещён.")
        return

    whitelist_percent = (
        percent_value
        if percent_value is not None
        else float(partner_bot.get("referral_percent") or 0)
    )
    if not MIN_PARTNER_REFERRAL_PERCENT <= whitelist_percent <= MAX_PARTNER_REFERRAL_PERCENT:
        await message.answer("Допустимый диапазон процента: от 10 до 100.")
        return

    upsert_partner_referral_whitelist(int(partner_bot["id"]), user_id, whitelist_percent)
    await state.clear()
    await message.answer(
        f"✅ Пользователь {user_id} добавлен в вайт-лист с {whitelist_percent:.2f}%.",
        reply_markup=build_partner_admin_panel_keyboard(),
    )


@user.callback_query(F.data == "partner_admin_referrals_whitelist_remove")
async def partner_admin_referrals_whitelist_remove(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    partner_bot = get_owned_current_partner_bot(callback.bot, callback.from_user.id)
    if partner_bot is None:
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await state.set_state(PartnerAdminState.waiting_referral_whitelist_remove)
    await callback.message.edit_text(
        "Введите user_id пользователя, которого нужно удалить из вайт-листа.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="partner_admin_referrals")]]
        ),
    )


@user.message(PartnerAdminState.waiting_referral_whitelist_remove)
async def receive_partner_referral_whitelist_remove(message: Message, state: FSMContext):
    user_id_text = (message.text or "").strip()
    if not user_id_text.isdigit():
        await message.answer("Отправьте только числовой user_id.")
        return

    partner_bot = get_owned_current_partner_bot(message.bot, message.from_user.id)
    if partner_bot is None:
        await state.clear()
        await message.answer("Доступ запрещён.")
        return

    deleted = remove_partner_referral_whitelist(int(partner_bot["id"]), int(user_id_text))
    await state.clear()
    await message.answer(
        "✅ Пользователь удалён из вайт-листа."
        if deleted
        else "Такого пользователя не было в вайт-листе.",
        reply_markup=build_partner_admin_panel_keyboard(),
    )


@user.callback_query(F.data == "partner_admin_top_buyers")
async def partner_admin_top_buyers(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    partner_bot = get_owned_current_partner_bot(callback.bot, callback.from_user.id)
    if partner_bot is None:
        await callback.answer("Доступ запрещён.", show_alert=True)
        return

    await callback.message.edit_text(
        build_partner_top_buyers_text(partner_bot, get_partner_top_buyers(int(partner_bot["id"]))),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="partner_admin_panel")]]
        ),
    )


@user.callback_query(F.data.startswith("market_category:"))
async def market_category(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    lang = get_event_language_code(callback)
    category_id = int(callback.data.split(":")[1])
    try:
        await show_market_category(callback.message, category_id, callback.from_user.id)
    except MarketProviderError as error:
        await render_screen(
            callback.message,
            f"{tr(lang, 'market.load_subcategories_failed')}\n\n"
            f"{tr(lang, 'generic.reason', error=error)}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=tr(lang, "buttons.back"), callback_data="magazine")]]
            ),
        )


@user.callback_query(F.data == "market_mail_separator")
async def market_mail_separator(callback: CallbackQuery):
    """Acknowledge the decorative divider without changing the screen."""
    await callback.answer()


@user.callback_query(F.data.startswith("market_products:"))
async def market_products(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    lang = get_event_language_code(callback)
    _, category_id, page = callback.data.split(":")
    try:
        await show_market_products(callback.message, int(category_id), int(page), callback.from_user.id)
    except MarketProviderError as error:
        await render_screen(
            callback.message,
            f"{tr(lang, 'market.load_products_failed')}\n\n"
            f"{tr(lang, 'generic.reason', error=error)}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=tr(lang, "buttons.back"), callback_data="magazine")]]
            ),
        )


@user.callback_query(F.data.startswith("market_product:"))
async def market_product(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    lang = get_event_language_code(callback)
    _, category_id, product_id, page = callback.data.split(":")
    try:
        await show_market_product(callback.message, int(category_id), int(product_id), int(page), callback.from_user.id)
    except MarketProviderError as error:
        await render_screen(
            callback.message,
            f"{tr(lang, 'market.load_product_failed')}\n\n"
            f"{tr(lang, 'generic.reason', error=error)}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=tr(lang, "buttons.back"), callback_data=f"market_products:{category_id}:{page}")]]
            ),
        )


@user.callback_query(F.data.startswith("market_favorite_toggle:"))
async def market_favorite_toggle(callback: CallbackQuery):
    _, category_id, product_id, page = callback.data.split(":")
    product_id_int = int(product_id)
    favorite_ids = list_favorite_product_ids(callback.from_user.id)
    saved = set_product_favorite(
        callback.from_user.id,
        product_id_int,
        product_id_int not in favorite_ids,
    )
    await callback.answer("Добавлено в избранное" if saved else "Удалено из избранного")
    try:
        await show_market_product(
            callback.message,
            int(category_id),
            product_id_int,
            int(page),
            callback.from_user.id,
        )
    except MarketProviderError as error:
        await callback.answer(str(error), show_alert=True)


@user.callback_query(F.data.startswith("market_qty:"))
async def market_quantity_selected(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    lang = get_event_language_code(callback)
    _, category_id, product_id, page, quantity = callback.data.split(":")

    try:
        category = await get_market_category_by_id(int(category_id))
        product = await get_market_product(int(product_id))
    except MarketProviderError as error:
        await render_screen(
            callback.message,
            f"{tr(lang, 'market.prepare_order_failed')}\n\n"
            f"{tr(lang, 'generic.reason', error=error)}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=tr(lang, "buttons.back"), callback_data=f"market_products:{category_id}:{page}")]]
            ),
        )
        return

    if category is None:
        await render_screen(callback.message, tr(lang, "generic.category_not_found"), reply_markup=main_menu(user_id=callback.from_user.id, language_code=lang))
        return

    stock = int(product.get("quantity") or 0)
    selected_quantity = int(quantity)
    if selected_quantity <= 0 or selected_quantity > stock:
        await callback.answer(tr(lang, "generic.invalid_quantity"), show_alert=True)
        return

    order_id = create_market_order_from_selection(
        callback.from_user.id,
        category["name"],
        product,
        selected_quantity,
        callback.bot,
    )
    order = get_order(order_id)
    await log_order_debug(
        callback.bot,
        order,
        get_purchase_buyer_label(callback.message, order),
        "Created order",
    )
    await state.clear()
    await render_screen(
        callback.message,
        build_payment_methods_text(order, lang),
        reply_markup=build_payment_methods_keyboard(order, lang),
    )


@user.callback_query(F.data.startswith("market_qty_custom:"))
async def market_quantity_custom(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    lang = get_event_language_code(callback)
    _, category_id, product_id, page = callback.data.split(":")

    try:
        category = await get_market_category_by_id(int(category_id))
        product = await get_market_product(int(product_id))
    except MarketProviderError as error:
        await render_screen(
            callback.message,
            f"{tr(lang, 'market.load_product_failed')}\n\n"
            f"{tr(lang, 'generic.reason', error=error)}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=tr(lang, "buttons.back"), callback_data=f"market_products:{category_id}:{page}")]]
            ),
        )
        return

    if category is None:
        await render_screen(callback.message, tr(lang, "generic.category_not_found"), reply_markup=main_menu(user_id=callback.from_user.id, language_code=lang))
        return

    await state.set_state(ProxyPurchaseState.waiting_account_quantity)
    await state.update_data(
        market_category_id=int(category_id),
        market_category_name=category["name"],
        market_product_id=int(product_id),
        market_product_page=int(page),
    )
    await render_screen(
        callback.message,
        build_market_quantity_prompt_text(product, get_effective_market_margin(callback.bot), lang),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=tr(lang, "buttons.back"), callback_data=f"market_product:{category_id}:{product_id}:{page}")]]
        ),
    )


@user.callback_query(F.data.startswith("market_order_check:"))
async def market_order_check(callback: CallbackQuery):
    await callback.answer(tr(get_event_language_code(callback), "generic.delivery_auto_check"), show_alert=True)
    order_id = int(callback.data.split(":")[1])
    order = get_order(order_id)
    if order is None or order["user_id"] != callback.from_user.id:
        return
    if order.get("status") == "delivered":
        return
    if not is_account_order(order):
        return


@user.callback_query(F.data == "proxy")
async def proxy_root(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    lang = get_event_language_code(callback)
    await render_screen(
        callback.message,
        tr(lang, "proxy.root"),
        reply_markup=proxy_menu(lang),
        banner="proxy",
    )


@user.callback_query(F.data == "proxy_mobile")
async def proxy_mobile(callback: CallbackQuery):
    await callback.answer()
    lang = get_event_language_code(callback)
    await render_screen(
        callback.message,
        tr(lang, "proxy.mobile.empty"),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=tr(lang, "buttons.back"), callback_data="proxy")]]
        ),
    )


@user.callback_query(F.data == "proxy_static")
async def proxy_static(callback: CallbackQuery):
    await callback.answer()
    lang = get_event_language_code(callback)
    try:
        categories = await get_static_proxy_categories()
    except ProxyProviderError:
        await render_screen(
            callback.message,
            f"{tr(lang, 'proxy.load_list_failed')}\n\n{PROXY_GENERIC_ERROR_CODE}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=tr(lang, "buttons.back"), callback_data="proxy")]]
            )
        )
        return

    await render_screen(
        callback.message,
        tr(lang, "proxy.static.title"),
        reply_markup=build_proxy_categories_keyboard(categories, lang),
    )


@user.callback_query(F.data.startswith("proxy_static:"))
async def proxy_static_country(callback: CallbackQuery):
    await callback.answer()
    lang = get_event_language_code(callback)
    category_id = int(callback.data.split(":")[1])

    try:
        category = await get_proxy_category_by_id(category_id, "static")
    except ProxyProviderError:
        await render_screen(
            callback.message,
            f"{tr(lang, 'proxy.load_category_failed')}\n\n{PROXY_GENERIC_ERROR_CODE}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=tr(lang, "buttons.back"), callback_data="proxy_static")]]
            )
        )
        return

    if category is None:
        await render_screen(
            callback.message,
            tr(lang, "generic.category_not_found"),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=tr(lang, "buttons.back"), callback_data="proxy_static")]]
            )
        )
        return

    await render_screen(
        callback.message,
        build_proxy_details_text(category, get_effective_proxy_unit_price(callback.bot, float(category.get("price") or 0.0)), lang),
        reply_markup=build_protocol_keyboard(category_id, category.get("items", []), lang),
    )


@user.callback_query(F.data.startswith("proxy_item:"))
async def proxy_item_selected(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    _, category_id, item_id = callback.data.split(":")
    await state.clear()
    await show_quantity_selection(callback.message, int(category_id), int(item_id), callback.from_user.id)


@user.callback_query(F.data.startswith("proxy_quantity_custom:"))
async def proxy_quantity_custom(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    lang = get_event_language_code(callback)
    _, category_id, item_id = callback.data.split(":")

    category = await get_proxy_category_by_id(int(category_id), "static")
    if category is None:
        await callback.message.edit_text(
            tr(lang, "generic.item_not_found"),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=tr(lang, "buttons.back"), callback_data="proxy_static")]]
            ),
        )
        return

    item = next((row for row in category.get("items", []) if row["id"] == int(item_id)), None)
    if item is None:
        await callback.message.edit_text(
            tr(lang, "generic.protocol_not_found"),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=tr(lang, "buttons.back"), callback_data=f"proxy_static:{category_id}")]]
            ),
        )
        return

    await state.set_state(ProxyPurchaseState.waiting_quantity)
    unit_price = get_effective_proxy_unit_price(callback.bot, float(category.get("price") or 0.0))
    await state.update_data(
        category_id=int(category_id),
        item_id=int(item_id),
        category_name=category["name"]["ru"],
        protocol=item["name"],
        unit_price=get_proxy_purchase_unit_price(float(category.get("price") or 0.0)),
    )
    await callback.message.edit_text(
        build_quantity_prompt_text(category, item, unit_price, lang),
        reply_markup=build_quantity_keyboard(int(category_id), lang),
    )


@user.callback_query(F.data.startswith("proxy_quantity:"))
async def proxy_quantity_selected(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    lang = get_event_language_code(callback)
    _, category_id, item_id, quantity = callback.data.split(":")

    category = await get_proxy_category_by_id(int(category_id), "static")
    if category is None:
        await callback.message.edit_text(
            tr(lang, "generic.item_not_found"),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=tr(lang, "buttons.back"), callback_data="proxy_static")]]
            ),
        )
        return

    item = next((row for row in category.get("items", []) if row["id"] == int(item_id)), None)
    if item is None:
        await callback.message.edit_text(
            tr(lang, "generic.protocol_not_found"),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=tr(lang, "buttons.back"), callback_data=f"proxy_static:{category_id}")]]
            ),
        )
        return

    data = {
        "category_id": int(category_id),
        "item_id": int(item_id),
        "category_name": category["name"]["ru"],
        "protocol": item["name"],
        "unit_price": get_proxy_purchase_unit_price(float(category.get("price") or 0.0)),
    }
    order_id = create_proxy_order_from_selection(callback.from_user.id, data, int(quantity), callback.bot)
    order = get_order(order_id)
    await log_order_debug(
        callback.bot,
        order,
        get_purchase_buyer_label(callback.message, order),
        "Created order",
    )
    await state.clear()
    await callback.message.edit_text(
        build_payment_methods_text(order, lang),
        reply_markup=build_payment_methods_keyboard(order, lang),
    )


@user.message(ProxyPurchaseState.waiting_quantity)
async def receive_proxy_quantity(message: Message, state: FSMContext):
    lang = get_event_language_code(message)
    if not message.text or not message.text.isdigit():
        await message.answer(tr(lang, "generic.send_digit_quantity"))
        return

    quantity = int(message.text)
    if quantity <= 0:
        await message.answer(tr(lang, "generic.quantity_more_than_zero"))
        return

    data = await state.get_data()
    order_id = create_proxy_order_from_selection(message.from_user.id, data, quantity, message.bot)
    await state.clear()
    order = get_order(order_id)
    await log_order_debug(
        message.bot,
        order,
        get_purchase_buyer_label(message, order),
        "Created order",
    )
    await message.answer(
        build_payment_methods_text(order, lang),
        reply_markup=build_payment_methods_keyboard(order, lang),
    )


@user.message(ProxyPurchaseState.waiting_account_quantity)
async def receive_account_quantity(message: Message, state: FSMContext):
    lang = get_event_language_code(message)
    if not message.text or not message.text.isdigit():
        await message.answer(tr(lang, "generic.send_digit_quantity"))
        return

    quantity = int(message.text)
    if quantity <= 0:
        await message.answer(tr(lang, "generic.quantity_more_than_zero"))
        return

    data = await state.get_data()
    category_id = int(data["market_category_id"])
    product_id = int(data["market_product_id"])
    page = int(data["market_product_page"])

    try:
        product = await get_market_product(product_id)
    except MarketProviderError as error:
        await state.clear()
        await message.answer(
            tr(lang, "market.stock_check_failed", reason=error),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=tr(lang, "common.back"),
                            callback_data=f"market_product:{category_id}:{product_id}:{page}",
                        )
                    ]
                ]
            ),
        )
        return

    stock = int(product.get("quantity") or 0)
    if quantity > stock:
        await message.answer(tr(lang, "generic.max_available", value=stock))
        return

    order_id = create_market_order_from_selection(
        message.from_user.id,
        data["market_category_name"],
        product,
        quantity,
        message.bot,
    )
    order = get_order(order_id)
    await log_order_debug(
        message.bot,
        order,
        get_purchase_buyer_label(message, order),
        "Created order",
    )
    await state.clear()
    await message.answer(
        build_payment_methods_text(order, lang),
        reply_markup=build_payment_methods_keyboard(order, lang),
    )


@user.message(ProxyPurchaseState.waiting_topup_amount)
async def receive_topup_amount(message: Message, state: FSMContext):
    lang = get_event_language_code(message)
    amount = parse_amount_text(message.text or "")
    if amount is None:
        await message.answer(tr(lang, "topup.send_amount_number"))
        return
    if amount < MIN_TOPUP_USD:
        await message.answer(f"Минимальная сумма пополнения — {MIN_TOPUP_USD:g}$.")
        return

    add_user(message.from_user.id)
    data = await state.get_data()
    provider = str(data.get("topup_provider") or "xrocket").lower()
    await state.update_data(topup_amount=amount)

    if provider == "heleket":
        partner_bot = get_current_partner_bot(message.bot)
        topup_id = create_topup(message.from_user.id, amount, int(partner_bot["id"]) if partner_bot else None)
        bot_info = await message.bot.get_me()
        success_url = f"https://t.me/{bot_info.username}" if bot_info.username else "https://t.me"
        merchant_order_id = f"topup-{topup_id}-{message.from_user.id}-{int(time.time())}"
        description = f"Balance topup #{topup_id} | user {message.from_user.id}"
        try:
            invoice = await create_heleket_invoice(
                order_id=merchant_order_id,
                amount_usd=amount,
                description=description,
                success_url=success_url,
                additional_data=str(topup_id),
            )
        except HeleketError as error:
            await state.clear()
            await message.answer(
                tr(lang, "topup.invoice_heleket_create_failed", reason=tr(lang, "generic.reason", error=error)),
                reply_markup=build_topup_methods_keyboard(lang),
            )
            return

        update_topup_invoice(
            topup_id=topup_id,
            invoice_id=invoice["invoice_id"],
            pay_url=invoice["pay_url"],
            payment_asset=invoice["currency"] or "USD",
            payment_provider="heleket",
        )
        await state.clear()
        topup = get_topup(topup_id)
        await message.answer(
            build_topup_wait_payment_text(
                topup,
                invoice["pay_url"],
                invoice["currency"],
                invoice["amount"],
                provider_label="Heleket",
                language_code=lang,
            ),
            reply_markup=build_topup_payment_keyboard(topup_id, invoice["pay_url"], language_code=lang),
            disable_web_page_preview=True,
        )
    elif provider == "crystalpay":
        partner_bot = get_current_partner_bot(message.bot)
        topup_id = create_topup(message.from_user.id, amount, int(partner_bot["id"]) if partner_bot else None)
        bot_info = await message.bot.get_me()
        redirect_url = f"https://t.me/{bot_info.username}" if bot_info.username else "https://t.me"
        try:
            invoice = await create_crystalpay_invoice(
                amount_usd=amount,
                description=f"SOUS MARKET balance top-up #{topup_id}",
                extra=f"topup:{topup_id}:user:{message.from_user.id}",
                redirect_url=redirect_url,
            )
        except CrystalPayError as error:
            await state.clear()
            await message.answer(
                f"Не удалось создать счёт CrystalPAY.\n\nПричина: {html.escape(str(error))}",
                reply_markup=build_topup_methods_keyboard(lang),
            )
            return
        update_topup_invoice(
            topup_id=topup_id,
            invoice_id=invoice["invoice_id"],
            pay_url=invoice["pay_url"],
            payment_asset=invoice["currency"],
            payment_provider="crystalpay",
        )
        await state.clear()
        topup = get_topup(topup_id)
        await message.answer(
            build_topup_wait_payment_text(topup, invoice["pay_url"], invoice["currency"], invoice["amount"], provider_label=CRYPTOBOT_PROVIDER_LABEL, language_code=lang),
            reply_markup=build_topup_payment_keyboard(topup_id, invoice["pay_url"], language_code=lang),
            disable_web_page_preview=True,
        )
        return

    if provider == "lolz":
        partner_bot = get_current_partner_bot(message.bot)
        topup_id = create_topup(message.from_user.id, amount, int(partner_bot["id"]) if partner_bot else None)
        bot_info = await message.bot.get_me()
        success_url = f"https://t.me/{bot_info.username}" if bot_info.username else "https://t.me"
        payment_id = f"topup-{topup_id}-{message.from_user.id}-{int(time.time())}"
        description = f"Balance topup #{topup_id} | user {message.from_user.id}"
        try:
            invoice = await create_lolz_invoice(
                amount_usd=amount,
                payment_id=payment_id,
                comment=description,
                success_url=success_url,
                additional_data=str(topup_id),
            )
        except LolzError as error:
            await state.clear()
            await message.answer(
                tr(lang, "topup.invoice_lolz_create_failed", reason=tr(lang, "generic.reason", error=error)),
                reply_markup=build_topup_methods_keyboard(lang),
            )
            return

        update_topup_invoice(
            topup_id=topup_id,
            invoice_id=invoice["invoice_id"],
            pay_url=invoice["pay_url"],
            payment_asset=invoice["currency"],
            payment_provider="lolz",
        )
        await state.clear()
        topup = get_topup(topup_id)
        await message.answer(
            build_topup_wait_payment_text(
                topup,
                invoice["pay_url"],
                invoice["currency"],
                invoice["amount"],
                provider_label="LOLZ",
                language_code=lang,
            ),
            reply_markup=build_topup_payment_keyboard(topup_id, invoice["pay_url"], language_code=lang),
            disable_web_page_preview=True,
        )
        return

    currencies = await get_xrocket_available_payment_currencies()
    if not currencies:
        await state.clear()
        await message.answer(
            tr(lang, "xrocket.no_currencies"),
            reply_markup=build_topup_methods_keyboard(lang),
        )
        return

    total_pages = max(math.ceil(len(currencies) / XROCKET_CURRENCIES_PAGE_SIZE), 1)
    page_rows = currencies[:XROCKET_CURRENCIES_PAGE_SIZE]
    lang = get_event_language_code(message)
    await message.answer(
        build_xrocket_currency_picker_text(tr(lang, "xrocket.topup_title"), amount, 0, total_pages, lang),
        reply_markup=build_xrocket_currency_picker_keyboard(
            page_rows,
            0,
            total_pages,
            select_callback_builder=lambda currency, current_page: (
                f"topup_xrocket_currency:{current_page}:{currency}"
            ),
            page_callback_builder=lambda next_page: f"topup_xrocket_page:{next_page}",
            back_callback="topup_xrocket",
            language_code=lang,
        ),
    )


@user.callback_query(F.data.startswith("order_preview:"))
async def order_preview(callback: CallbackQuery):
    await callback.answer()
    lang = get_event_language_code(callback)
    order_id = int(callback.data.split(":")[1])
    order = get_order(order_id)
    if not is_entity_owned_by_user(order, callback.from_user.id):
        await callback.answer(tr(lang, "generic.order_not_found"), show_alert=True)
        return
    await show_order_preview(callback.message, order_id, callback.from_user.id)


@user.callback_query(F.data.startswith("order_methods:"))
async def order_methods(callback: CallbackQuery):
    await callback.answer()
    lang = get_event_language_code(callback)
    order_id = int(callback.data.split(":")[1])
    order = get_order(order_id)
    if not is_entity_owned_by_user(order, callback.from_user.id):
        await callback.answer(tr(lang, "generic.order_not_found"), show_alert=True)
        return
    await show_payment_methods(callback.message, order_id, callback.from_user.id)


@user.callback_query(F.data.startswith("payment_xrocket:"))
async def payment_xrocket(callback: CallbackQuery):
    await callback.answer()
    lang = get_event_language_code(callback)
    order_id = int(callback.data.split(":")[1])
    order = get_order(order_id)
    if not is_entity_owned_by_user(order, callback.from_user.id):
        await callback.message.edit_text(tr(lang, "generic.order_not_found"), reply_markup=main_menu(user_id=callback.from_user.id, language_code=lang))
        return

    await show_order_xrocket_currencies(callback.message, order, page=0)


@user.callback_query(F.data.startswith("payment_xrocket_page:"))
async def payment_xrocket_page(callback: CallbackQuery):
    await callback.answer()
    lang = get_event_language_code(callback)
    _, order_id, page = callback.data.split(":")
    order = get_order(int(order_id))
    if not is_entity_owned_by_user(order, callback.from_user.id):
        await callback.message.edit_text(tr(lang, "generic.order_not_found"), reply_markup=main_menu(user_id=callback.from_user.id, language_code=lang))
        return
    await show_order_xrocket_currencies(callback.message, order, page=int(page))


@user.callback_query(F.data.startswith("payment_xrocket_currency:"))
async def payment_xrocket_currency(callback: CallbackQuery):
    await callback.answer()
    lang = get_event_language_code(callback)
    _, order_id, _page, currency = callback.data.split(":")
    order = get_order(int(order_id))
    if not is_entity_owned_by_user(order, callback.from_user.id):
        await callback.message.edit_text(tr(lang, "generic.order_not_found"), reply_markup=main_menu(user_id=callback.from_user.id, language_code=lang))
        return

    secondary_label = order.get("product_title") if is_account_order(order) else order["protocol"]
    order_kind = "Account" if is_account_order(order) else "Proxy"
    description = (
        f"{order_kind} order #{order_id} | {order['category_name']} | "
        f"{secondary_label} | {order['quantity']} pcs"
    )

    try:
        invoice = await create_xrocket_invoice(order["id"], order["total_price"], description, currency=currency)
    except XRocketError:
        await callback.message.edit_text(
            tr(lang, "payment.invoice_xrocket_create_failed"),
            reply_markup=build_payment_methods_keyboard(order, lang),
        )
        return

    update_order_invoice(
        order_id=order["id"],
        client_invoice_id=invoice["client_invoice_id"],
        invoice_id=invoice["invoice_id"],
        pay_url=invoice["pay_url"],
        payment_asset=invoice["currency"],
        payment_provider="xrocket",
    )
    order = get_order(order_id)
    await log_order_debug(
        callback.bot,
        order,
        get_purchase_buyer_label(callback.message, order),
        "Created XROCKET invoice",
        [
            f"💱 Currency: {invoice['currency']}",
            f"🧾 Invoice ID: {invoice['invoice_id']}",
            f"💸 Amount due: {float(invoice['amount'] or 0.0):.8f} {invoice['currency']}",
        ],
    )
    await callback.message.edit_text(
        build_wait_payment_text(order, invoice["pay_url"], invoice["currency"], invoice["amount"], language_code=lang),
        reply_markup=build_payment_keyboard(order_id, invoice["pay_url"], language_code=lang),
        disable_web_page_preview=True,
    )


@user.callback_query(F.data.startswith("payment_lolz:"))
async def payment_lolz(callback: CallbackQuery):
    await callback.answer()
    lang = get_event_language_code(callback)
    order_id = int(callback.data.split(":")[1])
    order = get_order(order_id)
    if not is_entity_owned_by_user(order, callback.from_user.id):
        await callback.message.edit_text(tr(lang, "generic.order_not_found"), reply_markup=main_menu(user_id=callback.from_user.id, language_code=lang))
        return

    secondary_label = order.get("product_title") if is_account_order(order) else order["protocol"]
    order_kind = "Account" if is_account_order(order) else "Proxy"
    description = (
        f"{order_kind} order #{order_id} | {order['category_name']} | "
        f"{secondary_label} | {order['quantity']} pcs"
    )
    bot_info = await callback.bot.get_me()
    success_url = f"https://t.me/{bot_info.username}" if bot_info.username else "https://t.me"
    payment_id = f"order-{order_id}-{callback.from_user.id}-{int(time.time())}"

    try:
        invoice = await create_lolz_invoice(
            amount_usd=float(order["total_price"] or 0.0),
            payment_id=payment_id,
            comment=description,
            success_url=success_url,
            additional_data=str(order_id),
        )
    except LolzError as error:
        with contextlib.suppress(TelegramBadRequest):
            await callback.message.edit_text(
                tr(lang, "payment.invoice_lolz_create_failed", reason=tr(lang, "generic.reason", error=error)),
                reply_markup=build_payment_methods_keyboard(order, lang),
            )
        return

    update_order_invoice(
        order_id=order_id,
        client_invoice_id=invoice["client_invoice_id"],
        invoice_id=invoice["invoice_id"],
        pay_url=invoice["pay_url"],
        payment_asset=invoice["currency"],
        payment_provider="lolz",
    )
    order = get_order(order_id)
    await log_order_debug(
        callback.bot,
        order,
        get_purchase_buyer_label(callback.message, order),
        "Created LOLZ invoice",
        [
            f"🧾 Invoice ID: {invoice['invoice_id']}",
            f"🆔 Payment ID: {invoice['client_invoice_id']}",
            f"💸 Amount due: {float(invoice['amount'] or 0.0):.2f} {invoice['currency']}",
        ],
    )
    await callback.message.edit_text(
        build_wait_payment_text(order, invoice["pay_url"], invoice["currency"], invoice["amount"], provider_label="LOLZ", language_code=lang),
        reply_markup=build_payment_keyboard(order_id, invoice["pay_url"], language_code=lang),
        disable_web_page_preview=True,
    )


@user.callback_query(F.data.startswith("payment_heleket:"))
async def payment_heleket(callback: CallbackQuery):
    await callback.answer()
    lang = get_event_language_code(callback)
    order_id = int(callback.data.split(":")[1])
    order = get_order(order_id)
    if not is_entity_owned_by_user(order, callback.from_user.id):
        await callback.message.edit_text(tr(lang, "generic.order_not_found"), reply_markup=main_menu(user_id=callback.from_user.id, language_code=lang))
        return

    secondary_label = order.get("product_title") if is_account_order(order) else order["protocol"]
    order_kind = "Account" if is_account_order(order) else "Proxy"
    description = (
        f"{order_kind} order #{order_id} | {order['category_name']} | "
        f"{secondary_label} | {order['quantity']} pcs"
    )
    bot_info = await callback.bot.get_me()
    success_url = f"https://t.me/{bot_info.username}" if bot_info.username else "https://t.me"
    merchant_order_id = f"order-{order_id}-{callback.from_user.id}-{int(time.time())}"

    try:
        invoice = await create_heleket_invoice(
            order_id=merchant_order_id,
            amount_usd=float(order["total_price"] or 0.0),
            description=description,
            success_url=success_url,
            additional_data=str(order_id),
        )
    except HeleketError as error:
        await callback.message.edit_text(
            tr(lang, "payment.invoice_heleket_create_failed", reason=tr(lang, "generic.reason", error=error)),
            reply_markup=build_payment_methods_keyboard(order, lang),
        )
        return

    update_order_invoice(
        order_id=order_id,
        client_invoice_id=invoice["client_invoice_id"],
        invoice_id=invoice["invoice_id"],
        pay_url=invoice["pay_url"],
        payment_asset=invoice["currency"] or "USD",
        payment_provider="heleket",
    )
    order = get_order(order_id)
    await log_order_debug(
        callback.bot,
        order,
        get_purchase_buyer_label(callback.message, order),
        "Created Heleket invoice",
        [
            f"🧾 Invoice ID: {invoice['invoice_id']}",
            f"🆔 Order ID: {invoice['client_invoice_id']}",
            (
                f"💸 Amount due: {float(invoice['amount'] or 0.0):.8f} {invoice['currency']}"
                if invoice.get("currency")
                else "💸 Amount due: pending provider confirmation"
            ),
        ],
    )
    await callback.message.edit_text(
        build_wait_payment_text(
            order,
            invoice["pay_url"],
            invoice["currency"],
            invoice["amount"],
            provider_label="Heleket",
            language_code=lang,
        ),
        reply_markup=build_payment_keyboard(order_id, invoice["pay_url"], language_code=lang),
        disable_web_page_preview=True,
    )


@user.callback_query(F.data.startswith("payment_crystalpay:"))
async def payment_crystalpay(callback: CallbackQuery):
    await callback.answer()
    lang = get_event_language_code(callback)
    order_id = int(callback.data.split(":")[1])
    order = get_order(order_id)
    if not is_entity_owned_by_user(order, callback.from_user.id):
        await callback.message.edit_text(tr(lang, "generic.order_not_found"), reply_markup=main_menu(user_id=callback.from_user.id, language_code=lang))
        return
    description = f"SOUS MARKET order #{order_id}"
    bot_info = await callback.bot.get_me()
    redirect_url = f"https://t.me/{bot_info.username}" if bot_info.username else "https://t.me"
    try:
        invoice = await create_crystalpay_invoice(
            amount_usd=float(order["total_price"] or 0),
            description=description,
            extra=f"order:{order_id}:user:{callback.from_user.id}",
            redirect_url=redirect_url,
        )
    except CrystalPayError as error:
        with contextlib.suppress(TelegramBadRequest):
            await callback.message.edit_text(
                f"Не удалось создать счёт CrystalPAY.\n\nПричина: {html.escape(str(error))}",
                reply_markup=build_payment_methods_keyboard(order, lang),
            )
        return
    update_order_invoice(
        order_id=order_id,
        client_invoice_id=invoice["client_invoice_id"],
        invoice_id=invoice["invoice_id"],
        pay_url=invoice["pay_url"],
        payment_asset=invoice["currency"],
        payment_provider="crystalpay",
    )
    order = get_order(order_id)
    await callback.message.edit_text(
        build_wait_payment_text(order, invoice["pay_url"], invoice["currency"], invoice["amount"], provider_label=CRYPTOBOT_PROVIDER_LABEL, language_code=lang),
        reply_markup=build_payment_keyboard(order_id, invoice["pay_url"], language_code=lang),
        disable_web_page_preview=True,
    )


@user.callback_query(F.data.startswith("payment_balance:"))
async def payment_balance(callback: CallbackQuery):
    await callback.answer()
    lang = get_event_language_code(callback)
    order_id = int(callback.data.split(":")[1])
    add_user(callback.from_user.id)
    order = get_order(order_id)
    if not is_entity_owned_by_user(order, callback.from_user.id):
        await callback.message.edit_text(tr(lang, "generic.order_not_found"), reply_markup=main_menu(user_id=callback.from_user.id, language_code=lang))
        return

    charged_order, error_code = charge_user_balance_for_order(order_id)
    if error_code == "insufficient_balance":
        await callback.answer(tr(lang, "generic.insufficient_balance"), show_alert=True)
        return
    if error_code in {"order_not_found", "user_not_found"} or charged_order is None:
        await callback.message.edit_text(
            tr(lang, "generic.balance_payment_failed"),
            reply_markup=main_menu(user_id=callback.from_user.id, language_code=lang),
        )
        return

    await callback.message.edit_text(build_balance_payment_success_text(charged_order, lang))
    updated_profile = get_user_profile(callback.from_user.id)
    await log_order_debug(
        callback.bot,
        charged_order,
        get_purchase_buyer_label(callback.message, charged_order),
        "Balance payment confirmed",
        [f"💳 Balance remaining: {float((updated_profile or {}).get('balance') or 0.0):.2f} $"],
    )
    if is_account_order(charged_order):
        await fulfill_market_order(callback.message, order_id)
    else:
        await fulfill_proxy_order(callback.message, order_id)


@user.callback_query(F.data.startswith("order_check:"))
async def order_check(callback: CallbackQuery):
    await callback.answer()
    lang = get_event_language_code(callback)
    order_id = int(callback.data.split(":")[1])
    order = get_order(order_id)
    if not is_entity_owned_by_user(order, callback.from_user.id):
        await callback.message.edit_text(tr(lang, "generic.order_not_found"), reply_markup=main_menu(user_id=callback.from_user.id, language_code=lang))
        return

    if order["status"] == "delivered":
        await callback.answer(tr(lang, "generic.order_already_delivered"), show_alert=True)
        return

    if order["status"] == "credited":
        await callback.message.edit_text(
            build_balance_credit_text(order, lang),
            reply_markup=build_result_keyboard(),
        )
        return

    try:
        paid_order = await ensure_order_paid(
            order_id,
            default_bot=callback.bot,
            buyer_message=callback.message,
        )
    except (XRocketError, LolzError, HeleketError, CrystalPayError) as error:
        provider_label = get_payment_provider_label(str(order.get("payment_provider") or "xrocket").lower())
        await callback.message.edit_text(
            tr(
                lang,
                "generic.payment_check_failed",
                provider=provider_label,
                reason=tr(lang, "generic.reason", error=error),
            ),
            reply_markup=build_payment_keyboard(order_id, order["xrocket_pay_url"], language_code=lang),
        )
        return

    if paid_order is None:
        await callback.message.edit_text(tr(lang, "generic.order_not_found"), reply_markup=main_menu(user_id=callback.from_user.id, language_code=lang))
        return

    if paid_order.get("status") == "waiting_payment":
        await callback.answer(tr(lang, "generic.payment_not_confirmed"), show_alert=True)
        return

    await continue_order_fulfillment(
        order_id,
        default_bot=callback.bot,
        notify_chat_id=callback.from_user.id,
        buyer_message=callback.message,
        status_message=callback.message,
    )


@user.callback_query(F.data.startswith("download_order:"))
async def download_order(callback: CallbackQuery):
    order_id = int(callback.data.split(":")[1])
    order = get_order(order_id)
    if (
        order is None
        or int(order.get("user_id") or 0) != callback.from_user.id
        or order.get("status") != "delivered"
        or not order.get("delivery_text")
    ):
        await callback.answer(tr(get_event_language_code(callback), "generic.file_unavailable"), show_alert=True)
        return

    await callback.answer(tr(get_event_language_code(callback), "generic.sending_file"))
    await send_order_delivery_document(callback.bot, callback.from_user.id, order)


@user.message(Command("money"))
async def money_referral_contest(message: Message):
    add_user(message.from_user.id, language_code=message.from_user.language_code)
    stats = get_money_referral_stats(message.from_user.id)
    bot_info = await message.bot.get_me()
    bot_username = bot_info.username or "MarketPlaceABot"
    referral_link = f"https://t.me/{bot_username}?start=r_{stats['referral_code']}"
    leaders = stats.get("leaders") or []
    top_lines = []
    for position in range(1, 6):
        count = int(leaders[position - 1]["count"]) if position <= len(leaders) else 0
        top_lines.append(f"{position}. Anon#{position} — {count}")
    text = (
        "💸 <b>Реферальный конкурс</b>\n"
        "22.08.2026 17:00 — 25.08.2026 17:00 (МСК)\n\n"
        f"<blockquote>{html.escape(chr(10).join(top_lines))}</blockquote>\n"
        f"<blockquote>Ваша реферальная ссылка: {html.escape(referral_link)}</blockquote>\n"
        f"<blockquote>Приглашено: {int(stats.get('personal_count') or 0)}</blockquote>"
    )
    await message.answer(
        text,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Копировать", copy_text=CopyTextButton(text=referral_link))],
                [InlineKeyboardButton(text="Закрыть", callback_data="money_close")],
            ]
        ),
    )
    if ADMIN_ID > 0 and message.from_user.id == ADMIN_ID:
        await send_money_referral_owner_report(message.bot)


async def get_money_contest_user_label(bot: Bot, user_id: int) -> str:
    try:
        chat = await bot.get_chat(int(user_id))
    except Exception:
        logger.warning("Unable to resolve /money contest user %s", user_id, exc_info=True)
        return f"ID: {int(user_id)}"

    username = str(getattr(chat, "username", "") or "").strip()
    if username:
        return f"@{username} (ID: {int(user_id)})"
    full_name = " ".join(
        part
        for part in (
            str(getattr(chat, "first_name", "") or "").strip(),
            str(getattr(chat, "last_name", "") or "").strip(),
        )
        if part
    )
    return f"{full_name} (ID: {int(user_id)})" if full_name else f"ID: {int(user_id)}"


def split_money_owner_report(text: str, limit: int = 3800) -> list[str]:
    chunks: list[str] = []
    current_lines: list[str] = []
    current_length = 0
    pending_lines: list[str] = []
    for raw_line in text.splitlines():
        while len(raw_line) > limit:
            pending_lines.append(raw_line[:limit])
            raw_line = raw_line[limit:]
        pending_lines.append(raw_line)
    for line in pending_lines:
        added_length = len(line) + (1 if current_lines else 0)
        if current_lines and current_length + added_length > limit:
            chunks.append("\n".join(current_lines))
            current_lines = []
            current_length = 0
            added_length = len(line)
        current_lines.append(line)
        current_length += added_length
    if current_lines:
        chunks.append("\n".join(current_lines))
    return chunks or [text]


async def send_money_referral_owner_report(bot: Bot) -> None:
    leaders = get_money_referral_admin_report()
    lines = [
        "🔐 /money — подробный отчёт владельца",
        "22.08.2026 17:00 — 25.08.2026 17:00 (МСК)",
    ]
    if not leaders:
        lines.extend(["", "За период приглашённых пользователей нет."])
    for leader in leaders:
        referrer_id = int(leader["user_id"])
        identity = await get_money_contest_user_label(bot, referrer_id)
        lines.extend(
            [
                "",
                f"Anon#{int(leader['position'])}: {identity}",
                f"Язык: {get_language_button_label(leader['language_code'])} ({leader['language_code']})",
                f"Приглашено: {int(leader['count'])}",
                "Приглашённые пользователи:",
            ]
        )
        for index, invited_user in enumerate(leader.get("invited_users") or [], start=1):
            lines.append(
                f"{index}. ID: {int(invited_user['user_id'])} — язык: "
                f"{get_language_button_label(invited_user['language_code'])} "
                f"({invited_user['language_code']}) — "
                f"{invited_user['registered_at_msk']} МСК"
            )

    try:
        for chunk in split_money_owner_report("\n".join(lines)):
            await bot.send_message(ADMIN_ID, chunk)
    except Exception:
        logger.exception("Unable to send private /money report to owner %s", ADMIN_ID)


@user.callback_query(F.data == "money_close")
async def money_referral_contest_close(callback: CallbackQuery):
    await callback.answer()
    if callback.message:
        with contextlib.suppress(Exception):
            await callback.message.delete()


@user.message(Command("admin"))
async def admin_command(message: Message, state: FSMContext):
    await state.clear()
    if ensure_admin(message.from_user.id):
        await message.answer(
            build_admin_panel_text(),
            reply_markup=admin_panel(),
        )
        return

    if get_owned_current_partner_bot(message.bot, message.from_user.id) is not None:
        await message.answer(
            "Управление партнёрским ботом перенесено в основной бот.\n\n"
            "Откройте раздел «Партнёрские боты» в основном кабинете и управляйте ботом там.",
        )
        return

    await message.answer("Доступ запрещён.")


@user.callback_query(F.data == "profile")
async def profile_menu(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    add_user(callback.from_user.id)
    profile = get_user_profile(callback.from_user.id)
    lang = get_event_language_code(callback, profile)
    await render_screen(
        callback.message,
        build_profile_text(profile, lang),
        reply_markup=build_profile_keyboard(callback.from_user.id, callback.bot, language_code=lang),
        banner="profile",
        parse_mode="HTML",
    )


@user.callback_query(F.data == "language_menu")
async def language_menu(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    lang = get_event_language_code(callback)
    await render_screen(
        callback.message,
        build_language_menu_text(lang),
        reply_markup=build_language_keyboard(lang),
    )


@user.callback_query(F.data.startswith("language_set:"))
async def language_set(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    language_code = normalize_language_code(callback.data.split(":", 1)[1])
    set_user_language(callback.from_user.id, language_code)
    await callback.answer(tr(language_code, "language.updated"), show_alert=False)
    await render_main_menu_screen(
        callback.message,
        callback.from_user.id,
        callback.bot,
        language_code=language_code,
        refresh_reply_keyboard=True,
    )


@user.callback_query(F.data == "activate_promocode")
async def activate_promocode(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    add_user(callback.from_user.id)
    lang = get_event_language_code(callback)
    await state.set_state(ProfileState.waiting_promo_code)
    await render_screen(
        callback.message,
        f"{premium_emoji(PROMOCODE_PROMPT_EMOJI_ID, '🎟')} {tr(lang, 'promocode.enter')}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=tr(lang, "common.back"), callback_data="profile")]]
        ),
        parse_mode="HTML",
    )


@user.message(ProfileState.waiting_promo_code)
async def receive_promocode(message: Message, state: FSMContext):
    lang = get_event_language_code(message)
    activation_result, error_code = activate_promo_code(message.from_user.id, message.text or "")
    if error_code == "invalid_code":
        await message.answer(tr(lang, "promocode.invalid"))
        return
    if error_code == "promo_not_found":
        await message.answer(tr(lang, "promocode.not_found"))
        return
    if error_code == "already_used":
        await message.answer(tr(lang, "promocode.already_used"))
        return
    if error_code == "limit_reached":
        await message.answer(tr(lang, "promocode.limit_reached"))
        return
    if error_code == "discount_already_active":
        await message.answer(tr(lang, "promocode.discount_already_active"))
        return
    if error_code is not None or activation_result is None:
        await state.clear()
        await message.answer(
            tr(lang, "promocode.user_not_found"),
            reply_markup=build_profile_keyboard(message.from_user.id, message.bot, language_code=lang),
        )
        return

    promo_code = activation_result["promo_code"]
    profile = get_user_profile(message.from_user.id)
    await state.clear()
    if promo_code["reward_type"] == "balance":
        await message.answer(
            f"{tr(lang, 'promocode.balance_applied', value=float(promo_code['reward_value'] or 0))}\n"
            f"Код: {promo_code['code']}\n"
            f"{tr(lang, 'profile.balance', value=float(profile['balance'] or 0))}",
            reply_markup=build_profile_keyboard(message.from_user.id, message.bot, language_code=lang),
        )
        return

    await message.answer(
        f"{tr(lang, 'promocode.discount_applied', value=float(promo_code['reward_value'] or 0))}\n"
        f"Код: {promo_code['code']}",
        reply_markup=build_profile_keyboard(message.from_user.id, message.bot, language_code=lang),
    )


@user.callback_query(F.data == "admin_panel")
async def admin_panel_menu(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not ensure_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await state.clear()
    await render_screen(
        callback.message,
        build_admin_panel_text(),
        reply_markup=admin_panel(),
    )


@user.callback_query(F.data == "admin_section_links")
async def admin_section_links(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not ensure_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await state.clear()
    bot_info = await callback.bot.get_me()
    await render_screen(
        callback.message,
        build_admin_section_links_text(bot_info.username or "userbot"),
        reply_markup=build_admin_section_links_keyboard(bot_info.username or "userbot"),
        parse_mode="HTML",
    )


@user.callback_query(F.data == "admin_promocodes")
async def admin_promocodes(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not ensure_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        build_admin_promocodes_text(list_recent_promo_codes()),
        reply_markup=build_admin_promocodes_keyboard(),
    )


@user.callback_query(F.data == "admin_promo_create_balance")
async def admin_promo_create_balance(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not ensure_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await state.set_state(AdminState.waiting_promo_balance_data)
    await callback.message.edit_text(
        "💵 Промокод на баланс\n\n"
        "Отправьте данные в формате:\n"
        "КОД СУММА [ЛИМИТ]\n\n"
        "Примеры:\nWELCOME5 5\nWELCOME5 5 100",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="admin_promocodes")]]
        ),
    )


@user.message(AdminState.waiting_promo_balance_data)
async def receive_admin_promo_balance_data(message: Message, state: FSMContext):
    if not ensure_admin(message.from_user.id):
        await state.clear()
        await message.answer("Доступ запрещён.")
        return

    parts = re.split(r"\s+", (message.text or "").strip())
    if len(parts) not in {2, 3}:
        await message.answer("Формат: КОД СУММА [ЛИМИТ]\nПример: WELCOME5 5 100")
        return

    code, amount_text = parts[:2]
    amount = parse_amount_text(amount_text)
    if amount is None:
        await message.answer("Сумма должна быть положительным числом.")
        return
    max_activations = parse_promo_limit_text(parts[2] if len(parts) == 3 else None)
    if max_activations == -1:
        await message.answer("Лимит должен быть положительным целым числом. Для безлимита просто не указывайте его.")
        return

    promo_code, error_code = create_promo_code(
        code,
        "balance",
        amount,
        message.from_user.id,
        max_activations=max_activations,
    )
    if error_code == "already_exists":
        await message.answer("Промокод с таким названием уже существует.")
        return
    if error_code == "invalid_limit":
        await message.answer("Лимит должен быть больше нуля.")
        return
    if error_code is not None or promo_code is None:
        await message.answer("Не удалось создать промокод.")
        return

    await state.clear()
    limit_line = (
        f"\nЛимит активаций: {int(promo_code['max_activations'])}"
        if promo_code.get("max_activations") is not None
        else "\nЛимит активаций: безлимит"
    )
    await message.answer(
        "✅ Промокод создан\n\n"
        f"Код: {promo_code['code']}\n"
        f"Начисление: {float(promo_code['reward_value'] or 0):.2f} $"
        f"{limit_line}",
        reply_markup=build_admin_promocodes_keyboard(),
    )


@user.callback_query(F.data == "admin_promo_create_discount")
async def admin_promo_create_discount(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not ensure_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await state.set_state(AdminState.waiting_promo_discount_data)
    await callback.message.edit_text(
        "🏷 Промокод на скидку\n\n"
        "Отправьте данные в формате:\n"
        "КОД ПРОЦЕНТ [ЛИМИТ]\n\n"
        "Примеры:\nSALE10 10\nSALE10 10 50",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="admin_promocodes")]]
        ),
    )


@user.callback_query(F.data == "admin_promo_delete")
async def admin_promo_delete(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not ensure_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await state.set_state(AdminState.waiting_promo_delete_code)
    await callback.message.edit_text(
        "🗑 Удаление промокода\n\n"
        "Отправьте код промокода следующим сообщением.\n\n"
        "Промокод будет выключен, а неиспользованные скидки по нему будут отменены.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="admin_promocodes")]]
        ),
    )


@user.message(AdminState.waiting_promo_discount_data)
async def receive_admin_promo_discount_data(message: Message, state: FSMContext):
    if not ensure_admin(message.from_user.id):
        await state.clear()
        await message.answer("Доступ запрещён.")
        return

    parts = re.split(r"\s+", (message.text or "").strip())
    if len(parts) not in {2, 3}:
        await message.answer("Формат: КОД ПРОЦЕНТ [ЛИМИТ]\nПример: SALE10 10 50")
        return

    code, percent_text = parts[:2]
    try:
        percent_value = float(percent_text.replace(",", "."))
    except ValueError:
        await message.answer("Процент должен быть числом.")
        return

    if not 0 < percent_value <= 100:
        await message.answer("Допустимый диапазон скидки: от 0 до 100.")
        return
    max_activations = parse_promo_limit_text(parts[2] if len(parts) == 3 else None)
    if max_activations == -1:
        await message.answer("Лимит должен быть положительным целым числом. Для безлимита просто не указывайте его.")
        return

    promo_code, error_code = create_promo_code(
        code,
        "discount",
        percent_value,
        message.from_user.id,
        max_activations=max_activations,
    )
    if error_code == "already_exists":
        await message.answer("Промокод с таким названием уже существует.")
        return
    if error_code == "invalid_limit":
        await message.answer("Лимит должен быть больше нуля.")
        return
    if error_code is not None or promo_code is None:
        await message.answer("Не удалось создать промокод.")
        return

    await state.clear()
    limit_line = (
        f"\nЛимит активаций: {int(promo_code['max_activations'])}"
        if promo_code.get("max_activations") is not None
        else "\nЛимит активаций: безлимит"
    )
    await message.answer(
        "✅ Промокод создан\n\n"
        f"Код: {promo_code['code']}\n"
        f"Скидка: {float(promo_code['reward_value'] or 0):.2f}%"
        f"{limit_line}",
        reply_markup=build_admin_promocodes_keyboard(),
    )


@user.message(AdminState.waiting_promo_delete_code)
async def receive_admin_promo_delete_code(message: Message, state: FSMContext):
    if not ensure_admin(message.from_user.id):
        await state.clear()
        await message.answer("Доступ запрещён.")
        return

    promo_code, error_code = delete_promo_code(message.text or "")
    if error_code == "invalid_code":
        await message.answer("Отправьте непустой код промокода.")
        return
    if error_code == "promo_not_found":
        await message.answer("Промокод не найден.")
        return
    if error_code == "already_inactive":
        await state.clear()
        await message.answer(
            f"Промокод {promo_code['code']} уже выключен.",
            reply_markup=build_admin_promocodes_keyboard(),
        )
        return
    if error_code is not None or promo_code is None:
        await message.answer("Не удалось удалить промокод.")
        return

    await state.clear()
    await message.answer(
        "🗑 Промокод выключен\n\n"
        f"Код: {promo_code['code']}\n"
        "Новые активации запрещены.",
        reply_markup=build_admin_promocodes_keyboard(),
    )


@user.callback_query(F.data == "admin_franchises")
async def admin_franchises(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not ensure_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        build_admin_franchises_text(get_all_franchises_stats()),
        reply_markup=build_admin_franchises_keyboard(),
    )


@user.callback_query(F.data == "admin_franchises_broadcast")
async def admin_franchises_broadcast(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not ensure_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await state.set_state(AdminState.waiting_franchises_broadcast_message)
    await callback.message.edit_text(
        "📣 Рассылка по франшизам\n\n"
        "Отправьте следующим сообщением текст, фото, видео, GIF или документ.\n"
        "Сообщение уйдёт от имени каждого партнёрского бота его аудитории.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="admin_franchises")]]
        ),
    )


@user.message(AdminState.waiting_franchises_broadcast_message)
async def admin_franchises_broadcast_message(message: Message, state: FSMContext):
    if not ensure_admin(message.from_user.id):
        await state.clear()
        await message.answer("Доступ запрещён.")
        return

    payload = extract_partner_broadcast_payload(message)
    if payload is None:
        await message.answer("Поддерживаются текст, фото, видео, GIF и документы.")
        return

    await state.update_data(franchises_broadcast_payload=payload)
    await state.set_state(AdminState.waiting_franchises_broadcast_buttons)
    await message.answer(
        "Сообщение сохранено.\n\n"
        "Если нужны кнопки, отправьте их следующим сообщением в формате:\n"
        "Текст кнопки - https://example.com\n\n"
        "Если кнопки не нужны, нажмите «Пропустить кнопки».",
        reply_markup=build_admin_franchises_broadcast_buttons_prompt_keyboard(),
    )


@user.callback_query(F.data == "admin_franchises_broadcast_skip_buttons")
async def admin_franchises_broadcast_skip_buttons(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not ensure_admin(callback.from_user.id):
        await state.clear()
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await prompt_admin_franchises_broadcast_confirmation(callback.message, state, "")


@user.message(AdminState.waiting_franchises_broadcast_buttons)
async def admin_franchises_broadcast_buttons(message: Message, state: FSMContext):
    if not ensure_admin(message.from_user.id):
        await state.clear()
        await message.answer("Доступ запрещён.")
        return

    reply_markup = parse_broadcast_buttons(message.text or "")
    if reply_markup is None:
        await message.answer(
            "Не удалось распознать кнопки.\n\n"
            "Используйте формат:\n"
            "Кнопка 1 - https://example.com\n"
            "Кнопка 2 - https://example.com",
            reply_markup=build_admin_franchises_broadcast_buttons_prompt_keyboard(),
        )
        return

    await prompt_admin_franchises_broadcast_confirmation(message, state, message.text or "")


@user.callback_query(F.data == "admin_franchises_broadcast_confirm")
async def admin_franchises_broadcast_confirm(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not ensure_admin(callback.from_user.id):
        await state.clear()
        await callback.answer("Доступ запрещён.", show_alert=True)
        return

    data = await state.get_data()
    buttons_text = data.get("franchises_broadcast_buttons_text") or ""
    reply_markup = parse_broadcast_buttons(buttons_text) if buttons_text else None
    await send_franchises_broadcast(callback.message, state, reply_markup)


@user.callback_query(F.data == "admin_main_subscription")
async def admin_main_subscription(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not ensure_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await state.clear()
    await show_admin_main_subscription_screen(callback.message, callback.bot)


@user.callback_query(F.data == "admin_main_subscription_toggle")
async def admin_main_subscription_toggle(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not ensure_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await state.clear()
    settings = get_main_bot_settings()
    enabled = int(settings.get("subscription_enabled") or 0) == 1
    channel_id = settings.get("subscription_channel_id")
    channel_url = settings.get("subscription_channel_url")
    if not enabled and not channel_id:
        await callback.answer("Сначала выберите канал.", show_alert=True)
        return

    update_main_bot_subscription_settings(
        enabled=not enabled,
        channel_id=channel_id,
        channel_url=channel_url,
    )
    await show_admin_main_subscription_screen(callback.message, callback.bot)


@user.callback_query(F.data == "admin_main_subscription_channel_id")
async def admin_main_subscription_channel_id(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not ensure_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await state.set_state(AdminState.waiting_main_subscription_channel_id)
    await callback.message.edit_text(
        "Введите ID или @username канала для обязательной подписки.\n\n"
        "Можно также переслать любой пост из нужного канала.\n\n"
        "Примеры:\n@my_channel\n-1001234567890",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="admin_main_subscription")]]
        ),
    )


@user.message(AdminState.waiting_main_subscription_channel_id)
async def receive_admin_main_subscription_channel_id(message: Message, state: FSMContext):
    if not ensure_admin(message.from_user.id):
        await state.clear()
        await message.answer("Доступ запрещён.")
        return

    channel_reference = extract_channel_reference_from_message(message)
    if channel_reference is None:
        await message.answer("Отправьте ID, @username или перешлите пост из канала.")
        return

    try:
        resolved_channel = await resolve_subscription_channel_for_bot(message.bot, channel_reference)
    except ValueError as error:
        await message.answer(str(error))
        return
    except Exception:
        await message.answer(
            "Не удалось найти канал.\n\n"
            "Убедитесь, что бот уже добавлен в администраторы, и отправьте канал ещё раз."
        )
        return

    await state.update_data(main_subscription_channel=resolved_channel)
    await state.set_state(AdminState.waiting_main_subscription_channel_confirm)
    title = resolved_channel["channel_title"]
    username = resolved_channel["channel_username"]
    url = resolved_channel["channel_url"] or "не найдена автоматически"
    details = [f"Название: {title}"]
    if username:
        details.append(f"Username: @{username}")
    details.append(f"ID: {resolved_channel['channel_id']}")
    details.append(f"URL: {url}")
    await message.answer(
        "🔔 Канал найден\n\n"
        + "\n".join(details)
        + "\n\nВыбрать его для обязательной подписки в главном боте?",
        reply_markup=build_admin_main_subscription_confirm_keyboard(),
        disable_web_page_preview=True,
    )


@user.callback_query(F.data == "admin_main_subscription_confirm")
async def admin_main_subscription_confirm(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not ensure_admin(callback.from_user.id):
        await state.clear()
        await callback.answer("Доступ запрещён.", show_alert=True)
        return

    data = await state.get_data()
    resolved_channel = data.get("main_subscription_channel")
    if not resolved_channel:
        await state.clear()
        await callback.message.edit_text(
            "Не удалось найти выбранный канал.",
            reply_markup=build_admin_main_subscription_keyboard(get_main_bot_settings()),
        )
        return

    settings = get_main_bot_settings()
    candidate_url = (resolved_channel.get("channel_url") or "").strip()
    final_url = candidate_url or (settings.get("subscription_channel_url") or None)
    update_main_bot_subscription_settings(
        enabled=bool(int(settings.get("subscription_enabled") or 0)),
        channel_id=resolved_channel["channel_id"],
        channel_url=final_url,
    )
    await state.clear()
    await callback.message.edit_text(
        "✅ Канал для обязательной подписки сохранён.\n\n"
        f"Название: {resolved_channel['channel_title']}\n"
        f"ID: {resolved_channel['channel_id']}\n"
        f"Ссылка: {final_url or 'не указана'}",
        reply_markup=build_admin_main_subscription_keyboard(get_main_bot_settings()),
        disable_web_page_preview=True,
    )


@user.callback_query(F.data == "admin_main_subscription_channel_url")
async def admin_main_subscription_channel_url(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not ensure_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await state.set_state(AdminState.waiting_main_subscription_channel_url)
    await callback.message.edit_text(
        "Введите ссылку на канал.\n\nПример:\nhttps://t.me/my_channel",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="admin_main_subscription")]]
        ),
    )


@user.message(AdminState.waiting_main_subscription_channel_url)
async def receive_admin_main_subscription_channel_url(message: Message, state: FSMContext):
    if not ensure_admin(message.from_user.id):
        await state.clear()
        await message.answer("Доступ запрещён.")
        return

    channel_url = (message.text or "").strip()
    if channel_url and not (
        channel_url.startswith("https://")
        or channel_url.startswith("http://")
        or channel_url.startswith("tg://")
    ):
        await message.answer("Отправьте корректную ссылку на канал.")
        return

    settings = get_main_bot_settings()
    update_main_bot_subscription_settings(
        enabled=bool(int(settings.get("subscription_enabled") or 0)),
        channel_id=settings.get("subscription_channel_id"),
        channel_url=channel_url or None,
    )
    await state.clear()
    await message.answer(
        "✅ Ссылка на канал обновлена.",
        reply_markup=build_admin_main_subscription_keyboard(get_main_bot_settings()),
    )


@user.callback_query(F.data.startswith("profile_orders:"))
async def profile_orders(callback: CallbackQuery):
    await callback.answer()
    page = int(callback.data.split(":")[1])
    await show_profile_orders(callback.message, callback.from_user.id, page)


@user.callback_query(F.data.startswith("profile_order:"))
async def profile_order_detail(callback: CallbackQuery):
    await callback.answer()
    lang = get_event_language_code(callback)
    _, order_id, page = callback.data.split(":")
    order = get_order(int(order_id))
    if order is None or order["user_id"] != callback.from_user.id or order.get("status") != "delivered":
        await callback.answer(tr(lang, "generic.order_not_found"), show_alert=True)
        return

    await render_screen(
        callback.message,
        build_order_detail_text(order, lang),
        reply_markup=build_order_detail_keyboard(order, int(page), lang),
    )


@user.callback_query(F.data == "referral_menu")
async def referral_menu(callback: CallbackQuery):
    await callback.answer()
    profile = get_user_profile(callback.from_user.id)
    lang = get_event_language_code(callback, profile)
    bot_info = await callback.bot.get_me()
    bot_username = bot_info.username or "userbot"
    partner_bot = get_current_partner_bot(callback.bot)
    referral_link = f"https://t.me/{bot_username}?start=r_{profile['referral_code']}"
    await render_screen(
        callback.message,
        build_referral_text(
            profile,
            referral_link,
            referral_percent=(
                float(partner_bot.get("referral_percent") or REFERRAL_PERCENT)
                if partner_bot is not None
                else REFERRAL_PERCENT
            ),
            referral_enabled=(
                int(partner_bot.get("referral_enabled") or 0) == 1
                if partner_bot is not None
                else True
            ),
            language_code=lang,
        ),
        reply_markup=build_referral_keyboard(referral_link, lang),
        banner="referral",
        parse_mode="HTML",
    )


@user.callback_query(F.data == "admin_users")
async def admin_users(callback: CallbackQuery):
    await callback.answer()
    if not ensure_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await callback.message.edit_text(
        build_admin_users_text(get_count()),
        reply_markup=admin_panel(),
    )


@user.callback_query(F.data == "admin_balance_manage")
async def admin_balance_manage(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not ensure_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await state.set_state(AdminState.waiting_balance_adjustment)
    await callback.message.edit_text(
        build_admin_balance_manage_text(),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")]]
        ),
    )


@user.message(AdminState.waiting_balance_adjustment)
async def receive_admin_balance_adjustment(message: Message, state: FSMContext):
    if not ensure_admin(message.from_user.id):
        await state.clear()
        await message.answer("Доступ запрещён.")
        return

    parts = re.split(r"\s+", (message.text or "").strip(), maxsplit=1)
    if len(parts) != 2 or not parts[0].isdigit():
        await message.answer("Формат: USER_ID СУММА\nПример: 1907513941 -2.5")
        return

    amount = parse_amount_text(parts[1].lstrip("+")) if not parts[1].startswith("-") else parse_amount_text(parts[1][1:])
    if amount is None:
        await message.answer("Сумма должна быть числом, например: 5 или -2.5")
        return

    signed_amount = -amount if parts[1].startswith("-") else amount
    updated_user, error_code = adjust_user_balance(int(parts[0]), signed_amount)
    if error_code == "insufficient_balance":
        await message.answer("Нельзя списать больше, чем есть на балансе.")
        return
    if error_code == "zero_amount" or updated_user is None:
        await message.answer("Сумма не должна быть равна нулю.")
        return

    await state.clear()
    await send_sales_log_message(
        message.bot,
        (
            f"💳 Ручная корректировка баланса\n"
            f"👮 Админ: {get_message_user_label(message, message.from_user.id)}\n"
            f"🆔 Пользователь: {parts[0]}\n"
            f"📈 Изменение: {signed_amount:+.2f} $\n"
            f"🏦 Новый баланс: {float(updated_user.get('balance') or 0.0):.2f} $"
        ),
    )
    if signed_amount > 0:
        with contextlib.suppress(Exception):
            await message.bot.send_message(
                int(parts[0]),
                f"Пополнено на {signed_amount:.2f} $",
            )
    await message.answer(
        "Баланс обновлён\n\n"
        f"Пользователь: {parts[0]}\n"
        f"Изменение: {signed_amount:+.2f} $\n"
        f"Новый баланс: {float(updated_user.get('balance') or 0.0):.2f} $",
        reply_markup=admin_panel(),
    )


@user.callback_query(F.data == "admin_api_balance_manage")
async def admin_api_balance_manage(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not ensure_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await state.set_state(AdminState.waiting_api_balance_adjustment)
    await callback.message.edit_text(
        build_admin_api_balance_manage_text(),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")]]
        ),
    )


@user.message(AdminState.waiting_api_balance_adjustment)
async def receive_admin_api_balance_adjustment(message: Message, state: FSMContext):
    if not ensure_admin(message.from_user.id):
        await state.clear()
        await message.answer("Доступ запрещён.")
        return

    parts = re.split(r"\s+", (message.text or "").strip(), maxsplit=1)
    if len(parts) != 2 or not parts[0].isdigit():
        await message.answer("Формат: USER_ID СУММА\nПример: 1907513941 5 или 1907513941 -2.5")
        return

    amount = parse_amount_text(parts[1][1:]) if parts[1].startswith("-") else parse_amount_text(parts[1].lstrip("+"))
    if amount is None:
        await message.answer("Сумма должна быть числом, например: 5 или -2.5")
        return
    signed_amount = -amount if parts[1].startswith("-") else amount

    api_account = get_api_account(int(parts[0]))
    if api_account is None or str(api_account.get("status")) != "approved" or not api_account.get("api_user_id"):
        await message.answer("У пользователя нет одобренного доступа к API.")
        return

    updated_user, error_code = adjust_user_balance(int(api_account["api_user_id"]), signed_amount)
    if error_code == "insufficient_balance":
        await message.answer("Нельзя списать больше, чем есть на API-балансе.")
        return
    if error_code == "zero_amount" or updated_user is None:
        await message.answer("Сумма не должна быть равна нулю.")
        return

    await state.clear()
    new_balance = float(updated_user.get("balance") or 0.0)
    await send_sales_log_message(
        message.bot,
        (
            f"🔌 Ручная корректировка API-баланса\n"
            f"👮 Админ: {get_message_user_label(message, message.from_user.id)}\n"
            f"🆔 Пользователь: {parts[0]}\n"
            f"📈 Изменение: {signed_amount:+.2f} $\n"
            f"🏦 Новый API-баланс: {new_balance:.2f} $"
        ),
    )
    if signed_amount > 0:
        with contextlib.suppress(Exception):
            await message.bot.send_message(
                int(parts[0]),
                f"🔌 Ваш API-баланс пополнен на {signed_amount:.2f} $.\nТекущий API-баланс: {new_balance:.2f} $",
            )
    await message.answer(
        "API-баланс обновлён\n\n"
        f"Пользователь: {parts[0]}\n"
        f"Изменение: {signed_amount:+.2f} $\n"
        f"Новый API-баланс: {new_balance:.2f} $",
        reply_markup=admin_panel(),
    )


@user.callback_query(F.data == "admin_pricing")
async def admin_pricing(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not ensure_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        build_admin_pricing_text(),
        reply_markup=build_admin_pricing_keyboard(),
    )


@user.callback_query(F.data.startswith("admin_pricing_set:"))
async def admin_pricing_set(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not ensure_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    key = callback.data.split(":", 1)[1]
    field = next((item for item in ADMIN_PRICING_FIELDS if item[0] == key), None)
    if field is None:
        await callback.answer("Неизвестный параметр.", show_alert=True)
        return
    _key, _button, label, unit, hint = field
    current = get_main_bot_pricing_value(key, _ADMIN_PRICING_DEFAULTS[key])
    await state.set_state(AdminState.waiting_pricing_value)
    await state.update_data(pricing_key=key)
    await callback.message.edit_text(
        f"✏️ {label}\n\n"
        f"Текущее значение: {current:g}{unit}\n\n"
        f"Отправьте новое число (например: {hint}).",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="admin_pricing")]]
        ),
    )


@user.message(AdminState.waiting_pricing_value)
async def receive_admin_pricing_value(message: Message, state: FSMContext):
    if not ensure_admin(message.from_user.id):
        await state.clear()
        await message.answer("Доступ запрещён.")
        return
    data = await state.get_data()
    key = str(data.get("pricing_key") or "")
    field = next((item for item in ADMIN_PRICING_FIELDS if item[0] == key), None)
    if field is None:
        await state.clear()
        await message.answer("Сессия редактирования истекла.", reply_markup=admin_panel())
        return
    _key, _button, label, unit, _hint = field
    raw = (message.text or "").strip().replace(",", ".").replace("%", "").replace("$", "").replace("/ГБ", "").strip()
    try:
        value = float(raw)
    except ValueError:
        await message.answer("Нужно число, например: 35 или 0.2")
        return
    if value < 0 or value > 100000:
        await message.answer("Число вне допустимого диапазона.")
        return
    try:
        update_main_bot_pricing_value(key, value)
    except Exception:
        await message.answer("Не удалось сохранить значение.")
        return
    await state.clear()
    with contextlib.suppress(Exception):
        await send_sales_log_message(
            message.bot,
            (
                "⚙️ Изменена наценка/цена главного бота\n"
                f"👮 Админ: {get_message_user_label(message, message.from_user.id)}\n"
                f"🔧 {label}: {value:g}{unit}"
            ),
        )
    await message.answer(
        f"✅ Сохранено. {label}: {value:g}{unit}\n\n" + build_admin_pricing_text(),
        reply_markup=build_admin_pricing_keyboard(),
    )


@user.callback_query(F.data == "admin_traffic_manage")
async def admin_traffic_manage(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not ensure_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return

    await state.set_state(AdminState.waiting_traffic_adjustment)
    await callback.message.edit_text(
        build_admin_traffic_manage_text(),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")]]),
    )


@user.message(AdminState.waiting_traffic_adjustment)
async def receive_admin_traffic_adjustment(message: Message, state: FSMContext):
    if not ensure_admin(message.from_user.id):
        await state.clear()
        await message.answer("Доступ запрещён.")
        return
    parts = re.split(r"\s+", (message.text or "").strip())
    if len(parts) != 2 or not parts[0].isdigit():
        await message.answer("Формат: USER_ID GB\nПримеры: 1907513941 5 или 1907513941 -2.5")
        return
    raw_amount = parts[1]
    amount = parse_amount_text(raw_amount[1:] if raw_amount.startswith("-") else raw_amount.lstrip("+"))
    if amount is None or amount <= 0 or amount > 10000:
        await message.answer("Количество GB должно быть от 0.01 до 10000; для списания добавьте минус.")
        return
    signed_amount = round(-amount if raw_amount.startswith("-") else amount, 2)
    try:
        remaining = await adjust_maskify_personal_user_traffic(int(parts[0]), signed_amount)
    except Exception as exc:
        await message.answer(f"Не удалось изменить трафик: {exc}")
        return
    await state.clear()
    with contextlib.suppress(Exception):
        action_text = "начислено" if signed_amount > 0 else "списано"
        await message.bot.send_message(int(parts[0]), f"📶 Вам {action_text} <b>{abs(signed_amount):g} GB</b> прокси-трафика.", parse_mode="HTML")
    await message.answer(
        f"Трафик обновлён\n\nПользователь: {parts[0]}\nИзменение: {signed_amount:+g} GB\nОстаток: {remaining:g} GB",
        reply_markup=admin_panel(),
    )


@user.callback_query(F.data == "admin_orders_stats")
async def admin_orders_stats(callback: CallbackQuery):
    await callback.answer()
    if not ensure_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await callback.message.edit_text(
        build_admin_orders_stats_text(get_orders_stats()),
        reply_markup=admin_panel(),
    )


@user.callback_query(F.data == "admin_crm")
async def admin_crm(callback: CallbackQuery):
    await callback.answer()
    if not ensure_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await callback.message.edit_text(
        build_admin_crm_text(get_crm_stats()),
        reply_markup=admin_panel(),
    )


@user.callback_query(F.data == "admin_utm")
async def admin_utm(callback: CallbackQuery):
    await callback.answer()
    if not ensure_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await callback.message.edit_text(
        "🔗 UTM статистика\n\nВыберите категорию:",
        reply_markup=build_admin_utm_menu_keyboard(),
    )


@user.callback_query(F.data == "admin_franchise_settings")
async def admin_franchise_settings(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not ensure_admin(callback.from_user.id):
        return
    await state.clear()
    stats = get_all_franchises_stats()
    await callback.message.edit_text(
        "⚙️ <b>Настройка франшизы</b>\n\nВыберите франшизный бот:",
        reply_markup=build_admin_franchise_settings_keyboard(stats),
        parse_mode="HTML",
    )


@user.callback_query(F.data.startswith("admin_franchise_settings:"))
async def admin_franchise_settings_view(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not ensure_admin(callback.from_user.id):
        return
    await state.clear()
    partner_bot = get_partner_bot_by_id(int(callback.data.split(":", 1)[1]))
    if partner_bot is None:
        await callback.answer("Франшиза не найдена.", show_alert=True)
        return
    text = (
        f"⚙️ <b>Настройки @{html.escape(str(partner_bot.get('bot_username') or 'bot'))}</b>\n\n"
        f"Информация: <b>{html.escape(str(partner_bot.get('franchise_info_brand') or 'MARKET'))}</b>\n"
        f"Новости: {html.escape(str(partner_bot.get('franchise_news_url') or 'не задано'))}\n"
        f"Поддержка: {html.escape(str(partner_bot.get('franchise_support_url') or 'не задано'))}\n"
        f"Соглашение: {html.escape(str(partner_bot.get('franchise_usage_url') or 'не задано'))}\n"
        f"Конфиденциальность: {html.escape(str(partner_bot.get('franchise_privacy_url') or 'не задано'))}\n"
        f"Кнопка создания: {'скрыта' if int(partner_bot.get('franchise_hide_create', 1) or 0) else 'показана'}"
    )
    await callback.message.edit_text(text, reply_markup=build_admin_franchise_edit_keyboard(partner_bot), parse_mode="HTML")


@user.callback_query(F.data.startswith("admin_franchise_toggle_create:"))
async def admin_franchise_toggle_create(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not ensure_admin(callback.from_user.id):
        return
    partner_id = int(callback.data.split(":", 1)[1])
    partner_bot = get_partner_bot_by_id(partner_id)
    if partner_bot is None:
        return
    current = int(partner_bot.get("franchise_hide_create", 1) or 0)
    update_partner_franchise_setting(partner_id, "franchise_hide_create", 0 if current else 1)
    partner_bot = get_partner_bot_by_id(partner_id)
    await callback.message.edit_text(
        f"⚙️ <b>Настройки @{html.escape(str(partner_bot.get('bot_username') or 'bot'))}</b>\n\nНастройка кнопки обновлена.",
        reply_markup=build_admin_franchise_edit_keyboard(partner_bot),
        parse_mode="HTML",
    )


@user.callback_query(F.data.startswith("admin_franchise_edit:"))
async def admin_franchise_edit(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not ensure_admin(callback.from_user.id):
        return
    _, partner_id, field = callback.data.split(":", 2)
    labels = {
        "franchise_news_url": "ссылку на новостной канал",
        "franchise_support_url": "ссылку на поддержку",
        "franchise_usage_url": "ссылку на пользовательское соглашение",
        "franchise_privacy_url": "ссылку на политику конфиденциальности",
        "franchise_info_brand": "название в разделе «Информация»",
    }
    if field not in labels:
        return
    await state.set_state(AdminState.waiting_franchise_setting_value)
    await state.update_data(franchise_setting_partner_id=int(partner_id), franchise_setting_field=field)
    await callback.message.edit_text(
        f"✏️ Отправьте {labels[field]} одним сообщением.\n\nДля отмены нажмите /cancel.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data=f"admin_franchise_settings:{partner_id}")]]),
    )


@user.message(AdminState.waiting_franchise_setting_value)
async def admin_franchise_setting_value(message: Message, state: FSMContext):
    if not ensure_admin(message.from_user.id):
        await state.clear()
        return
    if (message.text or "").strip().lower() == "/cancel":
        await state.clear()
        await message.answer("Изменение отменено.", reply_markup=build_admin_franchises_keyboard())
        return
    data = await state.get_data()
    partner_id = int(data.get("franchise_setting_partner_id") or 0)
    field = str(data.get("franchise_setting_field") or "")
    value = (message.text or "").strip()
    if field != "franchise_info_brand" and not re.match(r"^https?://", value):
        await message.answer("Нужна ссылка, начинающаяся с http:// или https://")
        return
    if field == "franchise_info_brand" and (not value or len(value) > 40):
        await message.answer("Название должно быть от 1 до 40 символов.")
        return
    update_partner_franchise_setting(partner_id, field, value)
    await state.clear()
    partner_bot = get_partner_bot_by_id(partner_id)
    await message.answer("✅ Настройка сохранена.", reply_markup=build_admin_franchise_edit_keyboard(partner_bot) if partner_bot else build_admin_franchises_keyboard())


@user.callback_query(F.data.startswith("admin_utm_category:"))
async def admin_utm_category(callback: CallbackQuery):
    await callback.answer()
    if not ensure_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    parts = callback.data.split(":")
    category = parts[1] if len(parts) > 1 else "ads"
    try:
        requested_page = max(int(parts[2]), 0) if len(parts) > 2 else 0
    except (TypeError, ValueError):
        requested_page = 0
    franchises = get_all_franchises_stats()
    if category == "franchises":
        for row in franchises.get("bots", []):
            partner_stats = get_partner_bot_utm_stats(int(row["id"]))
            row["utm_users"] = sum(int(item.get("total") or 0) for item in partner_stats.get("params", []))
            row["utm_purchases_total"] = sum(
                float(item.get("purchases_total") or 0.0) for item in partner_stats.get("params", [])
            )
    stats = get_utm_stats()
    if category == "franchises":
        category_rows = franchises.get("bots", [])
    elif category == "referrals":
        category_rows = [
            row for row in stats.get("params", [])
            if str(row.get("start_param") or "").startswith("r_")
        ]
    else:
        category_rows = [
            row for row in stats.get("params", [])
            if not str(row.get("start_param") or "").startswith("r_")
        ]
    pages = max((len(category_rows) + ADMIN_UTM_PAGE_SIZE - 1) // ADMIN_UTM_PAGE_SIZE, 1)
    page = min(requested_page, pages - 1)
    await callback.message.edit_text(
        build_admin_utm_category_text(category, stats, franchises, page),
        reply_markup=build_admin_utm_category_keyboard(category, page, len(category_rows)),
        disable_web_page_preview=True,
    )


@user.callback_query(F.data == "admin_broadcast")
async def admin_broadcast(callback: CallbackQuery, state: FSMContext):
    # Answer only after the admin check: Telegram accepts one answer per query,
    # so an early empty answer would swallow the "access denied" alert.
    if not ensure_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await callback.answer()
    await state.set_state(AdminState.waiting_broadcast_message)
    await callback.message.edit_text(
        "📣 Рассылка\n\nОтправьте следующим сообщением текст, фото или любой пост, который нужно разослать.",
        reply_markup=build_result_keyboard(),
    )


@user.message(AdminState.waiting_broadcast_message)
async def admin_broadcast_send(message: Message, state: FSMContext):
    if not ensure_admin(message.from_user.id):
        await state.clear()
        await message.answer("Доступ запрещён.")
        return

    await message.answer(
        "Сообщение для рассылки сохранено.\n\n"
        "Выберите разделы, кнопки которых нужно добавить к сообщению.\n\n"
        "Можно выбрать несколько разделов или добавить свои URL-кнопки.",
        reply_markup=build_broadcast_buttons_prompt_keyboard(),
    )
    await state.update_data(
        broadcast_chat_id=message.chat.id,
        broadcast_message_id=message.message_id,
        broadcast_sections=[],
        broadcast_custom_buttons_text="",
    )
    await state.set_state(AdminState.waiting_broadcast_buttons)


async def _run_broadcast_job(
    bot: Bot,
    admin_chat_id: int,
    source_chat_id: int,
    source_message_id: int,
    reply_markup: InlineKeyboardMarkup | None,
) -> None:
    """Deliver the broadcast in the background so the admin handler never blocks."""
    # Telegram user ids are always positive; the users table also holds synthetic
    # negative rows which would only ever produce "chat not found".
    recipients = [int(uid) for uid in give_all() if int(uid) > 0]
    total = len(recipients)
    logger.info("[broadcast] started: %s recipients", total)

    progress_message = None
    try:
        progress_message = await bot.send_message(
            admin_chat_id, f"📣 Рассылка запущена\n\nПолучателей: {total}\nОтправлено: 0"
        )
    except Exception:
        logger.exception("[broadcast] cannot send progress message")

    success_count = 0
    fail_count = 0
    blocked_count = 0
    error_samples: list[str] = []

    for index, user_id in enumerate(recipients, start=1):
        for _ in range(3):
            try:
                await bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=source_chat_id,
                    message_id=source_message_id,
                    reply_markup=reply_markup,
                )
                success_count += 1
                break
            except TelegramRetryAfter as error:
                # Respect the pause Telegram asks for, then retry the same user.
                delay = float(getattr(error, "retry_after", 1)) + 1
                logger.warning("[broadcast] flood control, sleeping %.1fs", delay)
                await asyncio.sleep(delay)
                continue
            except TelegramForbiddenError:
                blocked_count += 1
                break
            except Exception as error:
                fail_count += 1
                if len(error_samples) < 5:
                    error_samples.append(f"{type(error).__name__}: {error}")
                break
        # ~20 messages/second keeps us under Telegram's broadcast limit.
        await asyncio.sleep(0.05)

        if progress_message is not None and index % 200 == 0:
            with contextlib.suppress(Exception):
                await progress_message.edit_text(
                    "📣 Рассылка идёт\n\n"
                    f"Получателей: {total}\n"
                    f"Обработано: {index}\n"
                    f"Успешно: {success_count}"
                )

    logger.info(
        "[broadcast] finished: ok=%s blocked=%s failed=%s samples=%s",
        success_count,
        blocked_count,
        fail_count,
        error_samples,
    )

    report = (
        "📣 Рассылка завершена\n\n"
        f"Получателей: {total}\n"
        f"Успешно: {success_count}\n"
        f"Заблокировали бота: {blocked_count}\n"
        f"Ошибок: {fail_count}"
    )
    if error_samples:
        report += "\n\nПримеры ошибок:\n" + "\n".join(error_samples)
    with contextlib.suppress(Exception):
        await bot.send_message(admin_chat_id, report, reply_markup=admin_panel())


async def send_broadcast(message: Message, state: FSMContext, reply_markup: InlineKeyboardMarkup | None):
    data = await state.get_data()
    source_chat_id = data.get("broadcast_chat_id")
    source_message_id = data.get("broadcast_message_id")
    if not source_chat_id or not source_message_id:
        await state.clear()
        await message.answer("Не удалось найти сообщение для рассылки.", reply_markup=admin_panel())
        return

    await state.clear()
    # Run detached: a large audience takes minutes and must not block the handler.
    asyncio.create_task(
        _run_broadcast_job(
            message.bot,
            message.chat.id,
            int(source_chat_id),
            int(source_message_id),
            reply_markup,
        ),
        name="admin-broadcast",
    )


async def prompt_admin_broadcast_confirmation(target_message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    source_chat_id = data.get("broadcast_chat_id")
    source_message_id = data.get("broadcast_message_id")
    if not source_chat_id or not source_message_id:
        await state.clear()
        await target_message.answer("Не удалось найти сообщение для рассылки.", reply_markup=admin_panel())
        return
    selected = list(data.get("broadcast_sections") or [])
    custom_text = str(data.get("broadcast_custom_buttons_text") or "")
    reply_markup = build_broadcast_sections_markup(target_message.bot, selected, custom_text)
    await state.update_data(broadcast_ready=True)
    await target_message.answer("👁 <b>Предпросмотр рассылки:</b>", parse_mode="HTML")
    try:
        await target_message.bot.copy_message(
            chat_id=target_message.chat.id,
            from_chat_id=source_chat_id,
            message_id=source_message_id,
            reply_markup=reply_markup,
        )
    except Exception:
        await target_message.answer("Не удалось создать предпросмотр сообщения.")
        return
    recipients = len(give_all())
    button_count = len(reply_markup.inline_keyboard) if reply_markup else 0
    await target_message.answer(
        "⚠️ <b>Подтверждение рассылки</b>\n\n"
        f"Получателей: <b>{recipients}</b>\n"
        f"Кнопок: <b>{button_count}</b>\n\n"
        "Рассылка начнётся только после подтверждения.",
        reply_markup=build_admin_broadcast_confirm_keyboard(),
        parse_mode="HTML",
    )


async def prompt_admin_franchises_broadcast_confirmation(
    target_message: Message,
    state: FSMContext,
    buttons_text: str,
):
    stats = get_all_franchises_stats()
    await state.update_data(franchises_broadcast_buttons_text=buttons_text)
    await target_message.answer(
        "⚠️ Подтверждение рассылки\n\n"
        f"Активных франшиз: {stats['total_bots']}\n"
        f"Получателей суммарно: {stats['total_users']}\n\n"
        "Сообщение будет отправлено от имени каждого партнёрского бота его аудитории.",
        reply_markup=build_admin_franchises_broadcast_confirm_keyboard(),
    )


async def send_franchises_broadcast(
    target_message: Message,
    state: FSMContext,
    reply_markup: InlineKeyboardMarkup | None,
):
    data = await state.get_data()
    payload = data.get("franchises_broadcast_payload")
    if payload is None:
        await state.clear()
        await target_message.answer("Не удалось найти сообщение для рассылки.", reply_markup=build_admin_franchises_keyboard())
        return

    bots = list_active_partner_bots()
    success_count = 0
    fail_count = 0
    blocked_count = 0
    for partner_bot in bots:
        user_ids = list_partner_bot_user_ids(int(partner_bot["id"]))
        for user_id in user_ids:
            for attempt in range(3):
                try:
                    await send_partner_payload_to_user(partner_bot, user_id, payload, reply_markup)
                    success_count += 1
                    break
                except TelegramRetryAfter as error:
                    await asyncio.sleep(float(getattr(error, "retry_after", 1)) + 1)
                    continue
                except TelegramForbiddenError:
                    blocked_count += 1
                    break
                except Exception:
                    fail_count += 1
                    break
            await asyncio.sleep(0.05)

    await state.clear()
    await target_message.answer(
        "📣 Рассылка по франшизам завершена\n\n"
        f"Франшиз обработано: {len(bots)}\n"
        f"Успешно: {success_count}\n"
        f"Заблокировали бота: {blocked_count}\n"
        f"Ошибок: {fail_count}",
        reply_markup=build_admin_franchises_keyboard(),
    )


@user.callback_query(F.data == "admin_broadcast_skip_buttons")
async def admin_broadcast_skip_buttons(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not ensure_admin(callback.from_user.id):
        await state.clear()
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await state.update_data(broadcast_sections=[], broadcast_custom_buttons_text="")
    await prompt_admin_broadcast_confirmation(callback.message, state)


@user.callback_query(F.data.startswith("admin_broadcast_section:"))
async def admin_broadcast_toggle_section(callback: CallbackQuery, state: FSMContext):
    if not ensure_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    section = (callback.data or "").split(":", 1)[1]
    allowed = {"goods", "proxy", "email", "sms", "profile", "partner", "support"}
    if section not in allowed:
        await callback.answer("Неизвестный раздел.", show_alert=True)
        return
    data = await state.get_data()
    selected = list(data.get("broadcast_sections") or [])
    if section in selected:
        selected.remove(section)
    else:
        selected.append(section)
    await state.update_data(broadcast_sections=selected, broadcast_ready=False)
    await callback.message.edit_reply_markup(reply_markup=build_broadcast_buttons_prompt_keyboard(selected))
    await callback.answer("Выбор обновлён")


@user.callback_query(F.data == "admin_broadcast_custom_buttons")
async def admin_broadcast_custom_buttons_prompt(callback: CallbackQuery, state: FSMContext):
    if not ensure_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await state.set_state(AdminState.waiting_broadcast_buttons)
    await callback.message.answer(
        "Отправьте свои URL-кнопки, каждую с новой строки:\n\n"
        "Текст кнопки - https://example.com",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ К выбору разделов", callback_data="admin_broadcast_back_to_sections")]]
        ),
    )
    await callback.answer()


@user.callback_query(F.data == "admin_broadcast_back_to_sections")
async def admin_broadcast_back_to_sections(callback: CallbackQuery, state: FSMContext):
    if not ensure_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    data = await state.get_data()
    selected = list(data.get("broadcast_sections") or [])
    await state.set_state(AdminState.waiting_broadcast_buttons)
    await callback.message.answer(
        "Выберите кнопки разделов для рассылки:",
        reply_markup=build_broadcast_buttons_prompt_keyboard(selected),
    )
    await callback.answer()


@user.callback_query(F.data == "admin_broadcast_preview")
async def admin_broadcast_preview(callback: CallbackQuery, state: FSMContext):
    if not ensure_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await prompt_admin_broadcast_confirmation(callback.message, state)
    await callback.answer()


@user.callback_query(F.data == "admin_broadcast_confirm")
async def admin_broadcast_confirm(callback: CallbackQuery, state: FSMContext):
    if not ensure_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    data = await state.get_data()
    if not data.get("broadcast_ready"):
        await callback.answer("Сначала откройте предпросмотр.", show_alert=True)
        return
    await state.update_data(broadcast_ready=False)
    selected = list(data.get("broadcast_sections") or [])
    custom_text = str(data.get("broadcast_custom_buttons_text") or "")
    reply_markup = build_broadcast_sections_markup(callback.bot, selected, custom_text)
    await callback.answer("Рассылка запущена")
    await send_broadcast(callback.message, state, reply_markup)


@user.message(AdminState.waiting_broadcast_buttons)
async def admin_broadcast_buttons(message: Message, state: FSMContext):
    if not ensure_admin(message.from_user.id):
        await state.clear()
        await message.answer("Доступ запрещён.")
        return

    reply_markup = parse_broadcast_buttons(message.text or "")
    if reply_markup is None:
        await message.answer(
            "Не удалось распознать кнопки.\n\n"
            "Используйте формат:\n"
            "Кнопка 1 - https://example.com\n"
            "Кнопка 2 - https://example.com",
            reply_markup=build_broadcast_buttons_prompt_keyboard(),
        )
        return

    await state.update_data(broadcast_custom_buttons_text=message.text or "", broadcast_ready=False)
    await prompt_admin_broadcast_confirmation(message, state)


@user.callback_query(F.data == "topup_balance")
async def topup_balance(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    lang = get_event_language_code(callback)
    await render_screen(
        callback.message,
        build_topup_methods_text(lang),
        reply_markup=build_topup_methods_keyboard(lang, user_id=callback.from_user.id),
    )


@user.message(ProxyPurchaseState.waiting_api_topup_amount)
async def receive_api_topup_amount(message: Message, state: FSMContext):
    amount = parse_amount_text(message.text or "")
    if amount is None:
        await message.answer("Введите положительную сумму числом.")
        return
    if amount < MIN_TOPUP_USD:
        await message.answer(f"Минимальная сумма пополнения — {MIN_TOPUP_USD:g}$.")
        return
    account = get_api_account(message.from_user.id)
    if account is None or str(account.get("status")) != "approved":
        await state.clear()
        await message.answer("Доступ к API не одобрен.")
        return
    data = await state.get_data()
    provider = str(data.get("api_topup_provider") or "xrocket")
    topup_id = create_topup(message.from_user.id, amount, balance_kind="api")
    bot_info = await message.bot.get_me()
    success_url = f"https://t.me/{bot_info.username}" if bot_info.username else "https://t.me"
    description = f"API balance topup #{topup_id} | user {message.from_user.id}"
    try:
        if provider == "heleket":
            invoice = await create_heleket_invoice(
                order_id=f"api-topup-{topup_id}-{int(time.time())}", amount_usd=amount,
                description=description, success_url=success_url, additional_data=str(topup_id),
            )
        elif provider == "crystalpay":
            invoice = await create_crystalpay_invoice(
                amount_usd=amount, description=description,
                extra=f"api_topup:{topup_id}:user:{message.from_user.id}", redirect_url=success_url,
            )
        elif provider == "lolz":
            invoice = await create_lolz_invoice(
                amount_usd=amount, payment_id=f"api-topup-{topup_id}-{int(time.time())}",
                comment=description, success_url=success_url, additional_data=str(topup_id),
            )
        else:
            invoice = await create_xrocket_invoice(
                topup_id, amount, description,
                payload_data={"topup_id": topup_id, "user_id": message.from_user.id, "balance_kind": "api"},
            )
    except (XRocketError, LolzError, HeleketError, CrystalPayError) as error:
        await state.clear()
        await message.answer(f"Не удалось создать счёт: {html.escape(str(error))}", reply_markup=build_api_topup_methods_keyboard())
        return
    update_topup_invoice(
        topup_id=topup_id, invoice_id=invoice["invoice_id"], pay_url=invoice["pay_url"],
        payment_asset=invoice.get("currency") or "USD", payment_provider=provider,
    )
    await state.clear()
    await message.answer(
        f"✅ Счёт на пополнение API создан\n\nСумма: {amount:.2f} USDT\nСпособ: {get_payment_provider_label(provider)}",
        reply_markup=build_topup_payment_keyboard(
            topup_id, invoice["pay_url"], check_callback_data=f"api_topup_check:{topup_id}", back_callback_data="api_menu"
        ),
    )


@user.callback_query(F.data == "api_menu")
async def api_menu(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    account = get_api_account(callback.from_user.id)
    if account is None or str(account.get("status") or "") == "rejected":
        await render_screen(
            callback.message,
            "⚙️ <b>API</b>\n\nПодать заявку на API?",
            reply_markup=build_api_application_keyboard(),
            parse_mode="HTML",
        )
        return
    if str(account.get("status")) == "pending":
        await render_screen(
            callback.message,
            "⏳ <b>Заявка на API отправлена</b>\n\nОжидайте решения администратора.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="◀️ Назад", callback_data="profile")
            ]]),
            parse_mode="HTML",
        )
        return
    await render_screen(
        callback.message,
        build_api_menu_text(account),
        reply_markup=build_api_menu_keyboard(),
        parse_mode="HTML",
    )


@user.callback_query(F.data == "api_apply")
async def api_apply(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Заявка отправлена")
    await state.clear()
    account = submit_api_application(callback.from_user.id)
    if account and str(account.get("status")) == "approved":
        await api_menu(callback, state)
        return
    user_label = f"@{callback.from_user.username}" if callback.from_user.username else str(callback.from_user.id)
    with contextlib.suppress(Exception):
        await callback.bot.send_message(
            ADMIN_ID,
            f"⚙️ Новая заявка на API\n\nПользователь: {html.escape(user_label)}\nID: <code>{callback.from_user.id}</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Одобрить", callback_data=f"admin_api_approve:{callback.from_user.id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"admin_api_reject:{callback.from_user.id}"),
            ]]),
        )
    await callback.message.edit_text(
        "⏳ Заявка отправлена администратору. Ожидайте решения.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="◀️ Назад", callback_data="profile")
        ]]),
    )


@user.callback_query(F.data == "api_generate_key")
async def api_generate_key_handler(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    raw_key = generate_api_key(callback.from_user.id)
    if raw_key is None:
        await callback.answer("Доступ к API не одобрен.", show_alert=True)
        return
    account = get_api_account(callback.from_user.id)
    await callback.message.edit_text(
        "✅ Новый API key создан. Сохраните его сейчас: повторно он не показывается.\n\n"
        + build_api_menu_text(account, one_time_key=raw_key),
        reply_markup=build_api_menu_keyboard(),
        parse_mode="HTML",
    )


@user.callback_query(F.data == "api_topup")
async def api_topup(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    account = get_api_account(callback.from_user.id)
    if account is None or str(account.get("status")) != "approved":
        await callback.answer("Доступ к API не одобрен.", show_alert=True)
        return
    await callback.message.edit_text(
        "💳 Выберите способ пополнения API-баланса:",
        reply_markup=build_api_topup_methods_keyboard(),
    )


@user.callback_query(F.data.startswith("api_topup_provider:"))
async def api_topup_provider(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    provider = callback.data.split(":", 1)[1]
    if provider not in {"xrocket", "lolz", "heleket", "crystalpay"}:
        return
    await state.set_state(ProxyPurchaseState.waiting_api_topup_amount)
    await state.update_data(api_topup_provider=provider)
    await callback.message.edit_text(
        "💳 Введите сумму пополнения API-баланса в USDT:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="◀️ Назад", callback_data="api_topup")
        ]]),
    )


@user.callback_query(F.data == "admin_api_applications")
async def admin_api_applications(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not ensure_admin(callback.from_user.id):
        return
    await state.clear()
    applications = list_pending_api_applications()
    text = "⚙️ <b>Заявки API</b>\n\n" + ("\n".join(f"• <code>{row['user_id']}</code>" for row in applications) or "Новых заявок нет.")
    await callback.message.edit_text(text, reply_markup=build_admin_api_applications_keyboard(applications), parse_mode="HTML")


@user.callback_query(F.data.startswith(("admin_api_approve:", "admin_api_reject:")))
async def admin_api_review(callback: CallbackQuery, state: FSMContext):
    if not ensure_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    action, user_id_text = callback.data.split(":", 1)
    user_id = int(user_id_text)
    approved = action.endswith("approve")
    if not review_api_application(user_id, approved, callback.from_user.id):
        await callback.answer("Заявка уже обработана.", show_alert=True)
        return
    await callback.answer("Одобрено" if approved else "Отклонено")
    with contextlib.suppress(Exception):
        await callback.bot.send_message(
            user_id,
            "✅ Ваша заявка на API одобрена. Откройте Профиль → API." if approved else "❌ Ваша заявка на API отклонена.",
        )
    applications = list_pending_api_applications()
    text = "⚙️ <b>Заявки API</b>\n\n" + ("\n".join(f"• <code>{row['user_id']}</code>" for row in applications) or "Новых заявок нет.")
    await callback.message.edit_text(text, reply_markup=build_admin_api_applications_keyboard(applications), parse_mode="HTML")


@user.callback_query(F.data == "topup_xrocket")
async def topup_xrocket(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    lang = get_event_language_code(callback)
    await state.set_state(ProxyPurchaseState.waiting_topup_amount)
    await state.update_data(topup_amount=None, topup_provider="xrocket")
    await callback.message.edit_text(
        build_topup_amount_text(language_code=lang),
        reply_markup=build_topup_amount_keyboard(lang),
    )


@user.callback_query(F.data == "topup_lolz")
async def topup_lolz(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    lang = get_event_language_code(callback)
    await state.set_state(ProxyPurchaseState.waiting_topup_amount)
    await state.update_data(topup_amount=None, topup_provider="lolz")
    await callback.message.edit_text(
        build_topup_amount_text("LOLZ", lang),
        reply_markup=build_topup_amount_keyboard(lang),
    )


@user.callback_query(F.data == "topup_heleket")
async def topup_heleket(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    lang = get_event_language_code(callback)
    await state.set_state(ProxyPurchaseState.waiting_topup_amount)
    await state.update_data(topup_amount=None, topup_provider="heleket")
    await callback.message.edit_text(
        build_topup_amount_text("Heleket", lang),
        reply_markup=build_topup_amount_keyboard(lang),
    )


@user.callback_query(F.data == "topup_crystalpay")
async def topup_crystalpay(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    lang = get_event_language_code(callback)
    await state.set_state(ProxyPurchaseState.waiting_topup_amount)
    await state.update_data(topup_amount=None, topup_provider="crystalpay")
    await callback.message.edit_text(
        build_topup_amount_text(CRYPTOBOT_PROVIDER_LABEL, lang),
        reply_markup=build_topup_amount_keyboard(lang),
    )


@user.callback_query(F.data.startswith("topup_xrocket_page:"))
async def topup_xrocket_page(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    lang = get_event_language_code(callback)
    data = await state.get_data()
    amount = parse_amount_text(str(data.get("topup_amount") or ""))
    if amount is None:
        await state.set_state(ProxyPurchaseState.waiting_topup_amount)
        await callback.message.edit_text(
            build_topup_amount_text(language_code=lang),
            reply_markup=build_topup_amount_keyboard(lang),
        )
        return
    page = int(callback.data.split(":")[1])
    await show_topup_xrocket_currencies(callback.message, amount, page=page, user_id=callback.from_user.id)


@user.callback_query(F.data.startswith("topup_xrocket_currency:"))
async def topup_xrocket_currency(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    lang = get_event_language_code(callback)
    _, _page, currency = callback.data.split(":")
    data = await state.get_data()
    amount = parse_amount_text(str(data.get("topup_amount") or ""))
    if amount is None:
        await state.set_state(ProxyPurchaseState.waiting_topup_amount)
        await callback.message.edit_text(
            build_topup_amount_text(language_code=lang),
            reply_markup=build_topup_amount_keyboard(lang),
        )
        return

    add_user(callback.from_user.id)
    partner_bot = get_current_partner_bot(callback.bot)
    topup_id = create_topup(callback.from_user.id, amount, int(partner_bot["id"]) if partner_bot else None)
    description = f"Balance topup #{topup_id} | user {callback.from_user.id}"

    try:
        invoice = await create_xrocket_invoice(
            topup_id,
            amount,
            description,
            currency=currency,
            payload_data={"topup_id": topup_id, "user_id": callback.from_user.id},
        )
    except XRocketError:
        await state.clear()
        await callback.message.edit_text(
            tr(lang, "topup.invoice_xrocket_create_failed"),
            reply_markup=build_topup_methods_keyboard(lang),
        )
        return

    update_topup_invoice(
        topup_id=topup_id,
        invoice_id=invoice["invoice_id"],
        pay_url=invoice["pay_url"],
        payment_asset=invoice["currency"],
        payment_provider="xrocket",
    )
    await state.clear()
    topup = get_topup(topup_id)
    await callback.message.edit_text(
        build_topup_wait_payment_text(topup, invoice["pay_url"], invoice["currency"], invoice["amount"], language_code=lang),
        reply_markup=build_topup_payment_keyboard(topup_id, invoice["pay_url"], language_code=lang),
        disable_web_page_preview=True,
    )


@user.callback_query(F.data.startswith("topup_check:"))
async def topup_check(callback: CallbackQuery):
    await callback.answer()
    lang = get_event_language_code(callback)
    topup_id = int(callback.data.split(":")[1])
    topup = get_topup(topup_id)
    if not is_entity_owned_by_user(topup, callback.from_user.id):
        await callback.message.edit_text(tr(lang, "generic.topup_not_found"), reply_markup=build_profile_back_keyboard(lang))
        return

    if topup["status"] == "paid":
        await callback.message.edit_text(
            build_topup_success_text(topup, lang),
            reply_markup=build_profile_back_keyboard(lang),
        )
        return

    payment_provider = str(topup.get("payment_provider") or "xrocket").lower()
    try:
        if payment_provider == "lolz":
            invoice = await get_lolz_invoice(
                invoice_id=topup.get("xrocket_invoice_id"),
                payment_id=None,
            )
            if not is_lolz_invoice_paid(invoice):
                await callback.answer(tr(lang, "generic.payment_not_confirmed"), show_alert=True)
                return
        elif payment_provider == "heleket":
            invoice = await get_heleket_payment(invoice_id=topup.get("xrocket_invoice_id"))
            if not is_heleket_invoice_paid(invoice):
                await callback.answer(tr(lang, "generic.payment_not_confirmed"), show_alert=True)
                return
        elif payment_provider == "crystalpay":
            invoice = await get_crystalpay_invoice(str(topup.get("xrocket_invoice_id") or ""))
            if not is_crystalpay_invoice_paid(invoice):
                await callback.answer(tr(lang, "generic.payment_not_confirmed"), show_alert=True)
                return
        else:
            invoice = await get_xrocket_invoice(topup["xrocket_invoice_id"])
            if not is_xrocket_invoice_paid(invoice):
                await callback.answer(tr(lang, "generic.payment_not_confirmed"), show_alert=True)
                return
    except (XRocketError, LolzError, HeleketError, CrystalPayError) as error:
        provider_label = get_payment_provider_label(payment_provider)
        await callback.message.edit_text(
            tr(
                lang,
                "generic.topup_check_failed",
                provider=provider_label,
                reason=tr(lang, "generic.reason", error=error),
            ),
            reply_markup=build_topup_payment_keyboard(topup_id, topup["xrocket_pay_url"], language_code=lang),
        )
        return

    topup = complete_topup_payment(topup_id)
    await log_topup_debug(
        callback.bot,
        topup,
        get_topup_buyer_label(callback.message, topup),
        "Top-up confirmed",
    )
    await notify_topup_event(callback.bot, topup, callback.message)
    await callback.message.edit_text(
        build_topup_success_text(topup, lang),
        reply_markup=build_profile_back_keyboard(lang),
    )


@user.callback_query(F.data.startswith("api_topup_check:"))
async def api_topup_check(callback: CallbackQuery):
    await callback.answer()
    topup_id = int(callback.data.split(":", 1)[1])
    topup = get_topup(topup_id)
    if not is_entity_owned_by_user(topup, callback.from_user.id) or str((topup or {}).get("balance_kind")) != "api":
        await callback.answer("Счёт не найден.", show_alert=True)
        return
    if str(topup.get("status")) != "paid":
        provider = str(topup.get("payment_provider") or "xrocket").lower()
        try:
            if provider == "lolz":
                paid = is_lolz_invoice_paid(await get_lolz_invoice(invoice_id=topup.get("xrocket_invoice_id")))
            elif provider == "heleket":
                paid = is_heleket_invoice_paid(await get_heleket_payment(invoice_id=topup.get("xrocket_invoice_id")))
            elif provider == "crystalpay":
                paid = is_crystalpay_invoice_paid(await get_crystalpay_invoice(str(topup.get("xrocket_invoice_id") or "")))
            else:
                paid = is_xrocket_invoice_paid(await get_xrocket_invoice(topup["xrocket_invoice_id"]))
        except (XRocketError, LolzError, HeleketError, CrystalPayError) as error:
            await callback.answer(str(error), show_alert=True)
            return
        if not paid:
            await callback.answer("Оплата ещё не подтверждена.", show_alert=True)
            return
        complete_topup_payment(topup_id)
    account = get_api_account(callback.from_user.id)
    await callback.message.edit_text(
        "✅ API-баланс пополнен.\n\n" + build_api_menu_text(account),
        reply_markup=build_api_menu_keyboard(),
        parse_mode="HTML",
    )


@user.callback_query(F.data == "back_main")
async def back_main(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    lang = get_event_language_code(callback)
    await render_main_menu_screen(
        callback.message,
        callback.from_user.id,
        callback.bot,
        language_code=lang,
    )
