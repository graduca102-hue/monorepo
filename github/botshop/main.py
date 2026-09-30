import asyncio
import contextlib
import logging
import os
import re
import time
import unicodedata
import uuid
from datetime import datetime

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram import F
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, ReplyKeyboardRemove, WebAppInfo
from dotenv import load_dotenv
from tracking import parse_start_tracking

from database.data import (
    add_user,
    accrue_partner_earning_once,
    adjust_user_balance,
    complete_topup_payment,
    create_db,
    get_user_by_referral_code,
    get_main_bot_settings,
    get_user_profile,
    get_loyalty_discount_percent,
    get_partner_owner_summary,
    list_partner_bots_by_owner,
    list_pending_invoice_reminders,
    mark_invoice_reminder_sent,
    parse_datetime,
    save_partner_bot_start_data,
    save_user_start_data,
)
from keyboard import (
    PartnerBotState,
    build_partner_add_bot_text,
    build_main_subscription_gate_keyboard,
    build_main_subscription_gate_text,
    build_subscription_reply_menu,
    build_partner_program_keyboard,
    build_partner_program_text,
    build_profile_keyboard,
    build_profile_text,
    continue_order_fulfillment,
    ensure_order_paid,
    notify_new_partner_user,
    build_partner_subscription_gate_keyboard,
    build_partner_subscription_gate_text,
    render_main_menu_screen,
    get_current_partner_bot,
    get_event_language_code,
    get_message_user_label,
    is_user_subscribed_to_channel,
    is_admin_user,
    format_shop_name,
    render_screen,
    resume_pending_market_deliveries,
    show_market_categories,
    user,
)
from i18n import tr
from miniapp import (
    MINIAPP_HOST,
    MINIAPP_PORT,
    MiniAppServer,
    build_email_url,
    build_miniapp_url,
    build_partner_url,
    build_proxy_url,
    build_sms_url,
    configure_bot_menu_button,
    ensure_miniapp_commands,
)
from partner_runtime import PartnerBotsRuntime, set_partner_runtime
from services import (
    HeleketError,
    CrystalPayError,
    LolzError,
    XRocketError,
    get_heleket_payment,
    get_crystalpay_invoice,
    get_lolz_invoice,
    get_xrocket_invoice,
    is_heleket_invoice_paid,
    is_crystalpay_invoice_paid,
    is_lolz_invoice_paid,
    is_xrocket_invoice_paid,
    create_crystalpay_invoice,
    create_xrocket_invoice,
    create_lolz_invoice,
    create_heleket_invoice,
    PartnerProxyApiError,
    MarketProviderError,
    check_partner_proxy_order,
    create_partner_proxy_order,
    get_partner_proxy_account_info,
    get_market_balance_rub,
    refresh_market_rub_per_usdt,
)
from sms_service import GreedySmsError, get_provider_balance_usd
from maskify_service import (
    MaskifyError,
    add_maskify_personal_user_traffic,
    claim_maskify_proxy_purchase,
    create_maskify_proxy_purchase,
    create_partner_proxy_purchase,
    finish_maskify_proxy_purchase,
    generate_maskify_proxies,
    get_maskify_available_gb,
    get_maskify_reseller_account,
    get_maskify_user_remaining_gb,
    get_maskify_sellable_gb,
    get_maskify_proxy_purchase,
    get_proxy_settings,
    get_maskify_countries,
    save_proxy_settings,
    set_maskify_proxy_purchase_invoice,
    set_partner_proxy_delivery,
    mark_partner_proxy_delivery_pending,
)


load_dotenv()

logger = logging.getLogger(__name__)

TOKEN = os.getenv("TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
PAYMENT_REMINDER_DELAY_SECONDS = 10 * 60
PAYMENT_REMINDER_CHECK_INTERVAL_SECONDS = 3
PAYMENT_REMINDER_REMAINING_MINUTES = 20
MARKET_DELIVERY_RECOVERY_INTERVAL_SECONDS = 5
PROXY_MINIAPP_BUTTON_EMOJI_ID = "5776233299424843260"
GOODS_BUTTON_EMOJI_ID = "5920332557466997677"
PROXY_TEST_PREMIUM_EMOJI_ID = "602817127493979725"
PROXY_BACK_BUTTON_TEXT = "\u041d\u0430\u0437\u0430\u0434"


def apply_loyalty_discount(user_id: int, amount: float) -> float:
    percent = get_loyalty_discount_percent(user_id)
    return round(float(amount) * (1 - percent / 100), 2)

PROXY_TEST_POOL_BUTTONS = (
    ("🫆 Датацентр", "datacenter"),
    ("💻 Резидентские", "residential"),
    ("💻 Резидентские премиум", "residential_premium"),
    ("📱 Мобильные", "mobile"),
)
MASKIFY_POOLS = {"residential": "Резидентские Maskify"}

PROXY_TEST_GB_PRICES = {
    "datacenter": 1.0,
    "residential": 0.4,
    "mobile": 4.0,
    "residential_premium": 5.5,
}
PROXY_TRAFFIC_RETRY_INTERVAL_SECONDS = 60
SUPPLIER_BALANCE_CHECK_INTERVAL_SECONDS = int(os.getenv("SUPPLIER_BALANCE_CHECK_INTERVAL_SECONDS", "300") or 300)
SUPPLIER_BALANCE_ALERT_REPEAT_SECONDS = int(os.getenv("SUPPLIER_BALANCE_ALERT_REPEAT_SECONDS", "21600") or 21600)
MARKET_BALANCE_ALERT_USD = float(os.getenv("DJEKXA_BALANCE_ALERT_USD", "30") or 30)
MASKIFY_BALANCE_ALERT_GB = float(os.getenv("MASKIFY_BALANCE_ALERT_GB", "20") or 20)
SMS_BALANCE_ALERT_USD = float(os.getenv("SMS_BALANCE_ALERT_USD", "3") or 3)
_supplier_low_balance_alerts: dict[str, float] = {}
PROXY_PURCHASE_LOCK = asyncio.Lock()
PARTNER_PROXY_SERVICES = {
    "piaproxy",
    "piaproxy_gb",
    "piaproxy_long_acting",
    "922proxy",
    "922proxy_gb",
    "abcproxy",
    "abcproxy_gb",
    "9proxy",
    "9proxy_gb",
    "proxy001_gb",
    "711proxy",
    "cliproxy",
    "lokiproxy",
}
PARTNER_PROXY_GUIDES = {
    "9proxy": "https://telegra.ph/Kak-aktivirovat-9Proxy-promokod-12-18",
    "9proxy_gb": "https://telegra.ph/Kak-aktivirovat-9Proxy-promokod-12-18",
    "cliproxy": "https://telegra.ph/Kak-aktivirovat-CLiProxy-promokod-02-06",
    "711proxy": "https://telegra.ph/Kak-aktivirovat-711-Proxy-promokod-07-12",
    "proxy001_gb": "https://telegra.ph/Kak-aktivirovat-Proxy-001-promokod-07-13",
}
PARTNER_PROXY_LABELS = {
    "piaproxy": "PIA Proxy",
    "piaproxy_gb": "PIA Proxy",
    "piaproxy_long_acting": "PIA Proxy Long Acting ISP",
    "922proxy": "922 Proxy",
    "922proxy_gb": "922 Proxy",
    "abcproxy": "ABC Proxy",
    "abcproxy_gb": "ABC Proxy",
    "9proxy": "9Proxy",
    "9proxy_gb": "9Proxy",
    "proxy001_gb": "Proxy 001",
    "711proxy": "711 Proxy",
    "cliproxy": "CliProxy",
    "lokiproxy": "LokiProxy",
}
PARTNER_PROXY_UNITS = {
    "piaproxy": "IPs",
    "piaproxy_gb": "GB",
    "piaproxy_long_acting": "IPs",
    "922proxy": "IPs",
    "922proxy_gb": "GB",
    "abcproxy": "IPs",
    "abcproxy_gb": "GB",
    "9proxy": "IPs",
    "9proxy_gb": "GB",
    "proxy001_gb": "GB",
    "711proxy": "IPs",
    "cliproxy": "IPs",
    "lokiproxy": "IPs",
}
PARTNER_PROXY_TARIFFS = {
    "piaproxy": ((10, 2), (25, 3), (50, 5), (100, 7), (200, 12), (400, 23), (800, 45), (1930, 110), (3600, 205), (5000, 260)),
    "piaproxy_gb": ((1, 1), (2, 2), (5, 5), (10, 10), (20, 20), (50, 50), (100, 100), (200, 200), (500, 500)),
    "piaproxy_long_acting": ((10, 3), (25, 7), (50, 13), (100, 26), (200, 52), (400, 103), (800, 205), (1930, 495), (3600, 920), (5000, 1280)),
    "922proxy": ((10, 2), (25, 3), (50, 5), (100, 7), (200, 14), (400, 27), (800, 53), (1930, 128), (3600, 238), (5000, 320)),
    "922proxy_gb": ((1, 1), (2, 2), (5, 5), (10, 10), (20, 20), (50, 50), (100, 100), (200, 200), (500, 500)),
    "abcproxy": ((10, 2), (25, 3), (50, 5), (100, 7), (200, 12), (400, 23), (800, 45), (1930, 110), (3600, 205), (5000, 260)),
    "abcproxy_gb": ((1, 1), (2, 2), (5, 5), (10, 10), (20, 20), (50, 50), (100, 100), (200, 200), (500, 500)),
    "proxy001_gb": ((1, 1), (2, 2), (5, 5), (10, 10), (20, 16), (50, 38), (100, 70), (200, 130), (500, 310)),
    "711proxy": ((10, 2), (25, 3), (50, 5), (100, 7), (200, 11), (400, 20), (800, 38), (1930, 89), (3600, 162), (5000, 200)),
    "cliproxy": ((10, 2), (25, 3), (50, 5), (100, 7), (200, 11), (400, 20), (800, 38), (1930, 89), (3600, 162), (5000, 225)),
    "9proxy": ((10, 1), (50, 3), (100, 5), (200, 10), (400, 19), (800, 36), (1930, 83), (3600, 152), (5000, 200)),
    "9proxy_gb": ((1, 1), (2, 2), (5, 5), (10, 10), (20, 20), (50, 50), (100, 100), (200, 200), (500, 500)),
    "lokiproxy": ((10, 1), (25, 2), (50, 3), (100, 5), (200, 9), (400, 18), (800, 34), (1930, 80), (3600, 147), (5000, 199)),
}
PARTNER_PROXY_HOME_SERVICES = (
    ("9Proxy | IPs / GB", "9proxy_menu"),
    ("PIA Proxy | IPs / GB", "piaproxy_menu"),
    ("922 Proxy | IPs / GB", "922proxy_menu"),
    ("ABC Proxy | IPs / GB", "abcproxy_menu"),
    ("LokiProxy | IPs", "lokiproxy"),
    ("Proxy 001 | GB", "proxy001_gb"),
    ("711 Proxy | IPs", "711proxy"),
    ("CliProxy | IPs", "cliproxy"),
)
PARTNER_PROXY_SUBMENUS = {
    "9proxy_menu": ("9Proxy", (("9Proxy | IPs", "9proxy"), ("9Proxy | GB", "9proxy_gb"))),
    "piaproxy_menu": (
        "PIA Proxy",
        (
            ("PIA Proxy | IPs", "piaproxy"),
            ("PIA Proxy | GB", "piaproxy_gb"),
            ("PIA Proxy | Long Acting ISP", "piaproxy_long_acting"),
        ),
    ),
    "922proxy_menu": ("922 Proxy", (("922 Proxy | IPs", "922proxy"), ("922 Proxy | GB", "922proxy_gb"))),
    "abcproxy_menu": ("ABC Proxy", (("ABC Proxy | IPs", "abcproxy"), ("ABC Proxy | GB", "abcproxy_gb"))),
}
PAYMENT_XROCKET_EMOJI_ID = "5341788140434637169"
PAYMENT_LOLZ_EMOJI_ID = "5388960547930678866"
PAYMENT_HELEKET_EMOJI_ID = "5328161038133133296"
PAYMENT_BALANCE_EMOJI_ID = "5388898833545597646"
PAYMENT_CRYPTOBOT_EMOJI_ID = "5361914370068613491"
PAYMENT_BUTTON_EMOJI_ID = "5769126056262898415"
PAYMENT_CHECK_EMOJI_ID = "5879814368572478751"
PAYMENT_BACK_EMOJI_ID = "6039539366177541657"
SALES_LOG_CHANNEL_ID = int(os.getenv("SALES_LOG_CHANNEL_ID", "0") or 0)
SALES_LOG_HEADER = os.getenv("SALES_LOG_HEADER", "🏷 [LOG]").strip() or "🏷 [LOG]"


class ProxyTestState(StatesGroup):
    waiting_topup_gb = State()
    waiting_country_search = State()
    waiting_custom_quantity = State()


COUNTRY_SEARCH_ALIASES = {
    "RU": ("ru", "rus", "russia", "russian federation", "россия", "рф", "рус", "российская федерация"),
    "UA": ("ua", "ukr", "ukraine", "украина", "україна"),
    "BY": ("by", "blr", "belarus", "беларусь", "белоруссия"),
    "KZ": ("kz", "kaz", "kazakhstan", "казахстан"),
    "US": ("us", "usa", "united states", "united states of america", "america", "сша", "америка"),
    "GB": ("gb", "uk", "gbr", "united kingdom", "great britain", "britain", "великобритания", "англия"),
    "DE": ("de", "deu", "germany", "deutschland", "германия"),
    "FR": ("fr", "fra", "france", "франция"),
    "NL": ("nl", "nld", "netherlands", "holland", "нидерланды", "голландия"),
    "PL": ("pl", "pol", "poland", "польша"),
    "ES": ("es", "esp", "spain", "испания"),
    "IT": ("it", "ita", "italy", "италия"),
    "TR": ("tr", "tur", "turkey", "turkiye", "турция"),
    "CN": ("cn", "chn", "china", "китай"),
    "JP": ("jp", "jpn", "japan", "\u044f\u043f\u043e\u043d\u0438\u044f"),
    "IN": ("in", "ind", "india", "индия"),
    "CA": ("ca", "can", "canada", "канада"),
    "AU": ("au", "aus", "australia", "австралия"),
    "BR": ("br", "bra", "brazil", "бразилия"),
    "MX": ("mx", "mex", "mexico", "мексика"),
    "AE": ("ae", "are", "uae", "united arab emirates", "оаэ", "эмираты"),
}


def normalize_country_query(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").casefold().replace("ё", "е")
    return " ".join(re.sub(r"[^a-zа-яіїєґ0-9]+", " ", value).split())


def search_countries(countries: list[dict], query: str) -> list[dict]:
    needle = normalize_country_query(query)
    if not needle:
        return []
    exact_codes = {
        code for code, aliases in COUNTRY_SEARCH_ALIASES.items()
        if needle in {normalize_country_query(alias) for alias in aliases}
    }
    ranked = []
    for row in countries:
        code = str(row.get("code") or "").upper()
        name = normalize_country_query(str(row.get("name") or ""))
        aliases = tuple(normalize_country_query(x) for x in COUNTRY_SEARCH_ALIASES.get(code, ()))
        if code in exact_codes or needle == code.casefold() or needle == name or needle in aliases:
            rank = 0
        elif name.startswith(needle) or any(alias.startswith(needle) for alias in aliases):
            rank = 1
        elif needle in name or any(needle in alias for alias in aliases):
            rank = 2
        else:
            continue
        ranked.append((rank, name, row))
    return [row for _, _, row in sorted(ranked, key=lambda x: (x[0], x[1]))]


async def send_proxy_traffic_log(
    target_bot: Bot,
    *,
    event: str,
    user_id: int,
    pool: str,
    gb: float,
    price: float,
    extra: str = "",
) -> None:
    if not SALES_LOG_CHANNEL_ID:
        return
    text = (
        f"{SALES_LOG_HEADER}\n\n"
        f"🌐 <b>Прокси</b>\n"
        f"📌 Событие: {event}\n"
        f"👤 Пользователь: <code>{user_id}</code>\n"
        f"📦 Тип: {MASKIFY_POOLS.get(pool, pool)}\n"
        f"📊 Трафик: {gb:g} GB\n"
        f"💰 Сумма: ${price:.2f}"
    )
    if extra:
        text += f"\n{extra}"
    with contextlib.suppress(Exception):
        await target_bot.send_message(SALES_LOG_CHANNEL_ID, text, parse_mode="HTML")


async def notify_proxy_traffic_purchase_owner(
    target_bot: Bot,
    buyer_message: Message | None,
    *,
    user_id: int,
    gb: float,
    price: float,
    user_traffic_remaining: float,
) -> None:
    partner_bot = get_current_partner_bot(target_bot)
    owner_id = int(partner_bot.get("owner_id") or 0) if partner_bot is not None else ADMIN_ID
    if owner_id <= 0:
        return

    buyer_label = get_message_user_label(buyer_message, user_id) if buyer_message is not None else f"ID {user_id}"
    text = (
        f"🏪 Шоп: {format_shop_name(target_bot, partner_bot)}\n"
        "🌐 Новая покупка трафика\n"
        f"📦 Трафик: {gb:g} GB\n"
        f"🌸 Купил: {buyer_label}\n"
        f"💰 Сумма: ${price:.2f}\n"
        f"📊 Трафик пользователя: {user_traffic_remaining:g} GB"
    )
    with contextlib.suppress(Exception):
        await target_bot.send_message(owner_id, text, parse_mode="HTML")


async def notify_partner_proxy_purchase_owner(
    target_bot: Bot,
    buyer_message: Message | None,
    purchase: dict,
) -> None:
    """Send the shop owner a concise private purchase receipt."""
    partner_bot = get_current_partner_bot(target_bot)
    owner_id = int(partner_bot.get("owner_id") or 0) if partner_bot else ADMIN_ID
    if owner_id <= 0:
        return
    user_id = int(purchase.get("telegram_user_id") or 0)
    service = str(purchase.get("partner_service") or "proxy")
    quantity = int(purchase.get("partner_quantity") or 0)
    label = PARTNER_PROXY_LABELS.get(service, service)
    buyer = get_message_user_label(buyer_message, user_id) if buyer_message else f"ID {user_id}"
    text = (
        "🛒 <b>Покупка проксей</b>\n\n"
        f"👤 Покупатель: {buyer}\n"
        f"🌐 Сервис: <b>{label}</b>\n"
        f"📦 Количество: <b>{quantity}</b>\n"
        f"💰 Сумма: <b>${float(purchase.get('price_usd') or 0):.2f}</b>\n"
        f"🧾 Заказ: <code>#{int(purchase.get('id') or 0)}</code>"
    )
    with contextlib.suppress(Exception):
        await target_bot.send_message(owner_id, text, parse_mode="HTML")

dp = Dispatcher()
bot = Bot(token=TOKEN)
dp.include_router(user)


def extract_referrer_id(command: CommandObject | None, current_user_id: int) -> int | None:
    if command is None or not command.args:
        return None

    payload = command.args.strip()
    if payload.startswith("create_bot_r_"):
        referral_code = payload[len("create_bot_r_"):].strip()
    elif payload.startswith("r_"):
        referral_code = payload[2:].strip()
    else:
        return None
    if not referral_code:
        return None

    referrer = get_user_by_referral_code(referral_code)
    if referrer is None or referrer["user_id"] == current_user_id:
        return None

    return referrer["user_id"]


def extract_utm_data(command: CommandObject | None) -> tuple[str | None, dict]:
    if command is None or not command.args:
        return None, {}
    return parse_start_tracking(command.args)


def get_start_section(start_param: str | None) -> str:
    """Resolve deep-link aliases to the storefront section to open."""
    payload = (start_param or "").strip().casefold()
    if payload == "tgads1":
        return "proxy"
    if payload.startswith("section_"):
        return payload.removeprefix("section_")
    return ""


@dp.message(CommandStart())
async def start_project(
    message: Message,
    state: FSMContext,
    command: CommandObject | None = None,
):
    referrer_id = extract_referrer_id(command, message.from_user.id)
    partner_bot = get_current_partner_bot(message.bot)
    created_user = add_user(
        message.from_user.id,
        referred_by=referrer_id,
        partner_bot_id=int(partner_bot["id"]) if partner_bot is not None else None,
        language_code=message.from_user.language_code,
    )
    lang = get_event_language_code(message)
    start_param, utm_data = extract_utm_data(command)
    save_user_start_data(message.from_user.id, start_param, utm_data)
    if created_user and partner_bot is not None:
        await notify_new_partner_user(message.bot, message)
    if partner_bot is not None:
        save_partner_bot_start_data(
            int(partner_bot["id"]),
            message.from_user.id,
            start_param,
            utm_data,
        )
    if (
        partner_bot is not None
        and int(partner_bot.get("subscription_enabled") or 0) == 1
        and int(partner_bot.get("owner_id") or 0) != message.from_user.id
        and (partner_bot.get("subscription_channel_id") or "").strip()
    ):
        subscription_status = await is_user_subscribed_to_channel(
            message.bot,
            partner_bot.get("subscription_channel_id"),
            message.from_user.id,
        )
        if subscription_status is False:
            await message.answer("\u2063", reply_markup=build_subscription_reply_menu())
            await message.answer(
                build_partner_subscription_gate_text(partner_bot, lang),
                reply_markup=build_partner_subscription_gate_keyboard(partner_bot, lang),
                disable_web_page_preview=True,
            )
            return
    # SousPartnersBot is the franchise entry point, not a storefront. Keep its
    # /start screen focused on the partner cabinet and support contact for all
    # users (including people arriving through a referral/deep link).
    if (
        partner_bot is not None
        and str(partner_bot.get("bot_username") or "").casefold() == "souspartnersbot"
    ):
        if str(start_param or "").strip().casefold().startswith("create_bot"):
            if int(get_partner_owner_summary(message.from_user.id).get("total_bots") or 0) >= 2:
                await message.answer("Можно создать не больше двух ботов.")
                return
            await state.clear()
            await state.set_state(PartnerBotState.waiting_bot_token)
            await message.answer("\u2063", reply_markup=ReplyKeyboardRemove())
            await message.answer(build_partner_add_bot_text(lang))
            return
        owned_bots = list_partner_bots_by_owner(message.from_user.id)
        cabinet_bot = owned_bots[0] if owned_bots else None
        # The Mini App is launched from SousPartnersBot, so Telegram signs
        # initData with that bot's token. Pass the owned bot as a cabinet
        # target instead of opening the owned bot's URL directly.
        # The cabinet is available to every Telegram user. Owners get their
        # selected bot prefilled; users without a bot see the empty state and
        # can start the creation flow from the Mini App.
        url = build_partner_url(partner_bot.get("bot_username"))
        if url and cabinet_bot:
            url += f"?cabinet_bot={cabinet_bot.get('bot_username')}"
        # Paused bots still belong to the owner and must remain accessible in
        # the cabinet. Only their polling is stopped.
        keyboard_rows = []
        if url:
            keyboard_rows.append([
                InlineKeyboardButton(text="Кабинет", web_app=WebAppInfo(url=url))
            ])
        keyboard_rows.append([
            InlineKeyboardButton(
                text="Тех поддержка",
                url="https://t.me/UniversallSupportBot",
            )
        ])
        # Remove the old storefront reply keyboard (e.g. «Мой профиль»,
        # «Магазин», «Пополнить баланс») that may still be visible for users
        # who opened this bot before it became the franchise entry point.
        await message.answer("\u2063", reply_markup=ReplyKeyboardRemove())
        await message.answer(
            "Добро пожаловать в франшизу!\n\n"
            "ℹ Подключите своего Telegram-бота к партнёрской программе.\n"
            "Подключение и настройка выполняются в разделе «Кабинет».",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_rows),
        )
        return
    if partner_bot is None and not is_admin_user(message.from_user.id):
        main_settings = get_main_bot_settings()
        if (
            int(main_settings.get("subscription_enabled") or 0) == 1
            and (main_settings.get("subscription_channel_id") or "").strip()
        ):
            subscription_status = await is_user_subscribed_to_channel(
                message.bot,
                main_settings.get("subscription_channel_id"),
                message.from_user.id,
            )
            if subscription_status is False:
                await message.answer("\u2063", reply_markup=build_subscription_reply_menu())
                await message.answer(
                    build_main_subscription_gate_text(main_settings, lang),
                    reply_markup=build_main_subscription_gate_keyboard(main_settings, lang),
                    disable_web_page_preview=True,
                )
                return
    section = get_start_section(start_param)
    if section == "goods":
        await show_market_categories(message, message.from_user.id, message.bot)
        return
    if section == "proxy":
        await show_proxy_test_home(message, message.from_user.id)
        return
    if section == "email":
        await send_email_miniapp_link(message)
        return
    if section == "sms":
        await send_sms_miniapp_link(message)
        return
    if section == "profile":
        profile = get_user_profile(message.from_user.id)
        await render_screen(
            message,
            build_profile_text(profile, lang),
            reply_markup=build_profile_keyboard(message.from_user.id, message.bot, language_code=lang),
            banner="profile",
            parse_mode="HTML",
        )
        return
    if section == "partner":
        stats = get_partner_owner_summary(message.from_user.id)
        await render_screen(
            message,
            build_partner_program_text(
                total_bots=stats["total_bots"], total_users=stats["total_users"],
                total_earned_rub=stats["total_earned"], total_withdrawn=stats["total_withdrawn"],
                available_balance=stats["available_balance"], language_code=lang,
            ),
            reply_markup=build_partner_program_keyboard(lang),
            parse_mode="HTML",
        )
        return
    await render_main_menu_screen(
        message,
        message.from_user.id,
        message.bot,
        language_code=lang,
        refresh_reply_keyboard=True,
    )


async def send_miniapp_link(message: Message) -> None:
    lang = get_event_language_code(message)
    bot_info = await message.bot.get_me()
    miniapp_url = build_miniapp_url(bot_info.username)
    if not miniapp_url:
        await message.answer(tr(lang, "miniapp.store_url_missing"))
        return

    await message.answer(
        "👇",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=tr(lang, "miniapp.open_store"),
                        web_app=WebAppInfo(url=miniapp_url),
                        icon_custom_emoji_id=GOODS_BUTTON_EMOJI_ID,
                    )
                ]
            ]
        ),
    )


async def send_proxy_miniapp_link(message: Message) -> None:
    lang = get_event_language_code(message)
    bot_info = await message.bot.get_me()
    proxy_url = build_proxy_url(bot_info.username)
    if not proxy_url:
        await message.answer(tr(lang, "miniapp.proxy_url_missing"))
        return

    await message.answer(
        "👇",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=tr(lang, "miniapp.open_proxy_buy"),
                        web_app=WebAppInfo(url=proxy_url),
                        icon_custom_emoji_id=PROXY_MINIAPP_BUTTON_EMOJI_ID,
                    )
                ]
            ]
        ),
    )


def _traffic_text(value: float | None) -> str:
    return "недоступно" if value is None else f"{value:g} GB"


def build_proxy_test_menu_keyboard(bot: Bot | None = None) -> InlineKeyboardMarkup:
    residential_price = get_proxy_traffic_sale_price("residential", 1, bot)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Резидентские | {residential_price:g}$ / GB",
                    callback_data="proxy_test:residential",
                    icon_custom_emoji_id=PROXY_MINIAPP_BUTTON_EMOJI_ID,
                    style="success",
                )
            ],
            *[
                [InlineKeyboardButton(text=label, callback_data=f"proxy_test:service:{service}")]
                for label, service in PARTNER_PROXY_HOME_SERVICES
            ],
            [InlineKeyboardButton(text=PROXY_BACK_BUTTON_TEXT, callback_data="back_main", icon_custom_emoji_id=PAYMENT_BACK_EMOJI_ID)],
        ]
    )


def build_proxy_pool_keyboard(pool: str, bot: Bot | None = None) -> InlineKeyboardMarkup:
    unit_price = get_proxy_traffic_sale_price(pool, 1, bot)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"☁️ Пополнить трафик · ${unit_price:g}/GB", callback_data=f"proxy_test:topup:{pool}")],
            [
                InlineKeyboardButton(text="Страна", callback_data=f"proxy_test:country:{pool}:0"),
                InlineKeyboardButton(text="Сессия", callback_data=f"proxy_test:session:{pool}"),
                InlineKeyboardButton(text="Ротация", callback_data=f"proxy_test:rotation:{pool}"),
            ],
            [
                InlineKeyboardButton(text="Формат", callback_data=f"proxy_test:format:{pool}"),
                InlineKeyboardButton(text="Протокол", callback_data=f"proxy_test:protocol:{pool}"),
                InlineKeyboardButton(text="Количество", callback_data=f"proxy_test:quantity:{pool}"),
            ],
            [InlineKeyboardButton(text="🗑 Сбросить", callback_data=f"proxy_test:reset:{pool}")],
            [InlineKeyboardButton(text="📄 Получить прокси", callback_data=f"proxy_test:get:{pool}")],
            [InlineKeyboardButton(text=PROXY_BACK_BUTTON_TEXT, callback_data="proxy_test:home", icon_custom_emoji_id=PAYMENT_BACK_EMOJI_ID)],
        ]
    )


def build_choices_keyboard(pool: str, action: str, choices: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=label, callback_data=f"proxy_test:set:{pool}:{action}:{value}")] for label, value in choices]
    rows.append([InlineKeyboardButton(text=PROXY_BACK_BUTTON_TEXT, callback_data=f"proxy_test:{pool}", icon_custom_emoji_id=PAYMENT_BACK_EMOJI_ID)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def build_proxy_test_menu_text(user_id: int) -> str:
    balance = await get_maskify_user_remaining_gb(user_id)
    return (
        "⭐ <b>Прокси</b>\n\n"
        "<b>Количество трафика:</b>\n"
        f"💻 Резидентские: <b>{_traffic_text(balance)}</b>"
    )


def get_current_proxy_markup(bot: Bot | None) -> int:
    """Return the configured franchise markup for proxy-only legacy flows."""
    partner_bot = get_current_partner_bot(bot)
    if partner_bot is None:
        return 0
    # Import lazily to avoid a module import cycle during bot startup.
    from keyboard import get_partner_category_markup

    return get_partner_category_markup(partner_bot, "proxy")


def get_proxy_traffic_sale_price(pool: str, gb: float, bot: Bot | None) -> float:
    base_price = float(PROXY_TEST_GB_PRICES[pool]) * float(gb)
    return round(base_price * (1 + get_current_proxy_markup(bot) / 100.0), 2)


async def get_partner_proxy_quote(
    service: str,
    quantity: int,
    bot: Bot | None = None,
    *,
    require_available_balance: bool = True,
) -> tuple[float, float]:
    if service not in PARTNER_PROXY_SERVICES or not 1 <= int(quantity) <= 10000:
        raise PartnerProxyApiError("Некорректный сервис или количество")
    # Always preflight the upstream before any balance is debited. This prevents
    # the former "API прокси не настроен" -> refund flow after checkout.
    info = await get_partner_proxy_account_info()
    prices = info.get("prices") if isinstance(info, dict) else None
    try:
        supplier_unit_price = float((prices or {})[service])
    except (KeyError, TypeError, ValueError) as error:
        raise PartnerProxyApiError("Цена сервиса недоступна") from error
    supplier_total = supplier_unit_price * int(quantity)
    try:
        supplier_balance = float(info.get("balance_usd") or 0.0)
    except (TypeError, ValueError):
        supplier_balance = 0.0
    if require_available_balance and supplier_balance + 1e-9 < supplier_total:
        raise PartnerProxyApiError("Прокси временно недоступны. Попробуйте позже.")
    requested = int(quantity)
    tariff_price = next(
        (float(price) for amount, price in PARTNER_PROXY_TARIFFS[service] if int(amount) == requested),
        None,
    )
    if tariff_price is None:
        tariffs = PARTNER_PROXY_TARIFFS[service]
        if requested < int(tariffs[0][0]):
            amount, price = tariffs[0]
            tariff_price = float(price) / int(amount) * requested
        elif requested > int(tariffs[-1][0]):
            amount, price = tariffs[-1]
            tariff_price = float(price) / int(amount) * requested
        else:
            lower = max((row for row in tariffs if int(row[0]) < requested), key=lambda row: int(row[0]))
            upper = min((row for row in tariffs if int(row[0]) > requested), key=lambda row: int(row[0]))
            progress = (requested - int(lower[0])) / (int(upper[0]) - int(lower[0]))
            tariff_price = float(lower[1]) + (float(upper[1]) - float(lower[1])) * progress
    sale_total = float(tariff_price) * (1.0 + get_current_proxy_markup(bot) / 100.0)
    return supplier_unit_price, round(max(sale_total, 0.01) + 1e-9, 2)


def accrue_proxy_partner_profit(
    bot: Bot | None,
    sale_price: float,
    supplier_cost: float,
    source_type: str,
    source_id: str | int,
) -> None:
    partner_bot = get_current_partner_bot(bot)
    if partner_bot is None:
        return
    profit = round(max(float(sale_price) - float(supplier_cost), 0.0), 6)
    if profit > 0:
        accrue_partner_earning_once(int(partner_bot["id"]), source_type, source_id, profit)


def build_partner_proxy_service_keyboard(service: str, bot: Bot | None = None) -> InlineKeyboardMarkup:
    markup_multiplier = 1 + get_current_proxy_markup(bot) / 100.0
    rows = [
        [InlineKeyboardButton(
            text=f"{amount} {PARTNER_PROXY_UNITS[service]} | {float(price) * markup_multiplier:g}$",
            callback_data=f"proxy_test:service_quantity:{service}:{amount}",
        )]
        for amount, price in PARTNER_PROXY_TARIFFS.get(service, ())
    ]
    rows.append([
        InlineKeyboardButton(
            text="✏️ Своё количество",
            callback_data=f"proxy_test:service_quantity_custom:{service}",
        )
    ])
    rows.append([InlineKeyboardButton(text=PROXY_BACK_BUTTON_TEXT, callback_data="proxy_test:home", icon_custom_emoji_id=PAYMENT_BACK_EMOJI_ID)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_partner_proxy_service_text(service: str) -> str:
    label = PARTNER_PROXY_LABELS.get(service, service)
    unit = PARTNER_PROXY_UNITS.get(service, "IPs")
    description = (
        "Прокси-сервис, предоставляющий стабильные и высокоскоростные IP-адреса. "
        "Тариф активируется промокодом после покупки."
    )
    return (
        f"⭐ <b>{label} | {unit}</b>\n\n"
        f"<b>Что такое {label}?</b>\n{description}\n\n"
        "<b>Выберите подходящий тарифный план:</b>"
    )


async def show_partner_proxy_checkout(target: Message, service: str, quantity: int) -> None:
    try:
        supplier_unit_price, total_price = await get_partner_proxy_quote(service, quantity, target.bot)
    except PartnerProxyApiError as error:
        await target.answer(str(error))
        return
    rows = [
        [InlineKeyboardButton(text="Списать с баланса", callback_data=f"partner_proxy_balance:{service}:{quantity}", icon_custom_emoji_id=PAYMENT_BALANCE_EMOJI_ID)],
        [InlineKeyboardButton(text="XROCKET", callback_data=f"partner_proxy_provider:xrocket:{service}:{quantity}", icon_custom_emoji_id=PAYMENT_XROCKET_EMOJI_ID)],
        [InlineKeyboardButton(text="LOLZ", callback_data=f"partner_proxy_provider:lolz:{service}:{quantity}", icon_custom_emoji_id=PAYMENT_LOLZ_EMOJI_ID)],
        [InlineKeyboardButton(text="Heleket", callback_data=f"partner_proxy_provider:heleket:{service}:{quantity}", icon_custom_emoji_id=PAYMENT_HELEKET_EMOJI_ID)],
        [InlineKeyboardButton(text="Cryptobot", callback_data=f"partner_proxy_provider:crystalpay:{service}:{quantity}", icon_custom_emoji_id=PAYMENT_CRYPTOBOT_EMOJI_ID)],
        [InlineKeyboardButton(text=PROXY_BACK_BUTTON_TEXT, callback_data=f"proxy_test:service:{service}", icon_custom_emoji_id=PAYMENT_BACK_EMOJI_ID)],
    ]
    unit = PARTNER_PROXY_UNITS.get(service, "IPs")
    label = PARTNER_PROXY_LABELS.get(service, service)
    await target.edit_text(
        f"✅ Вы выбрали <b>{quantity} {unit} | {total_price:g}$</b>\n\n"
        f"⭐ Сервис: <b>{label}</b>\n"
        f"🗃 Количество: <b>{quantity} {unit}</b>\n"
        f"💳 Итого к оплате: <b>{total_price:g}$</b>\n\n"
        "Выберите способ оплаты:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )


async def deliver_partner_proxy_purchase(target: Message, purchase: dict) -> tuple[bool, str]:
    service = str(purchase.get("partner_service") or "")
    quantity = int(purchase.get("partner_quantity") or 0)
    purchase_id = int(purchase["id"])
    try:
        created = await create_partner_proxy_order(service, quantity, f"sous-{purchase_id}-{uuid.uuid4().hex[:12]}")
        provider_order_id = int(created.get("id") or 0)
        await notify_partner_proxy_purchase_owner(target.bot, target, purchase)
        order = created
        for _ in range(20):
            if order.get("data"):
                break
            if provider_order_id <= 0:
                raise PartnerProxyApiError("Поставщик не вернул ID заказа")
            await asyncio.sleep(1)
            order = await check_partner_proxy_order(provider_order_id)
        delivery = str(order.get("data") or "").strip()
        if not delivery:
            mark_partner_proxy_delivery_pending(purchase_id, provider_order_id)
            asyncio.create_task(_wait_for_partner_proxy_delivery(target, purchase_id, provider_order_id, service, quantity))
            return True, "⏳ Заказ оплачен и отправлен поставщику. Прокси будут выданы в этом чате сразу после готовности."
        set_partner_proxy_delivery(purchase_id, provider_order_id, delivery)
        supplier_unit_price, _ = await get_partner_proxy_quote(
            service,
            quantity,
            None,
            require_available_balance=False,
        )
        accrue_proxy_partner_profit(
            target.bot, float(purchase.get("price_usd") or 0.0), supplier_unit_price * quantity,
            "partner_proxy", purchase_id,
        )
        await send_partner_proxy_delivery(target.bot, target.chat.id, service, quantity, delivery)
        return True, "✅ Заказ выполнен. Файл с прокси отправлен ниже."
    except PartnerProxyApiError as error:
        return False, str(error)


async def send_partner_proxy_delivery(bot: Bot, chat_id: int, service: str, quantity: int, delivery: str) -> None:
    guide_url = PARTNER_PROXY_GUIDES.get(service, "")
    guide_text = f"\n\n📖 <a href=\"{guide_url}\">Инструкция по активации</a>" if guide_url else ""
    await bot.send_document(
        chat_id,
        BufferedInputFile(delivery.encode("utf-8"), filename=f"{service}-{quantity}.txt"),
        caption=f"✅ <b>Прокси выданы</b>\nСервис: <b>{service}</b>\nКоличество: <b>{quantity} шт.</b>{guide_text}",
        parse_mode="HTML",
    )


async def _wait_for_partner_proxy_delivery(
    target: Message, purchase_id: int, provider_order_id: int, service: str, quantity: int
) -> None:
    for _ in range(120):
        try:
            order = await check_partner_proxy_order(provider_order_id)
            delivery = str(order.get("data") or "").strip()
            if delivery:
                set_partner_proxy_delivery(purchase_id, provider_order_id, delivery)
                purchase = get_maskify_proxy_purchase(purchase_id) or {}
                supplier_unit_price, _ = await get_partner_proxy_quote(
                    service,
                    quantity,
                    None,
                    require_available_balance=False,
                )
                accrue_proxy_partner_profit(
                    target.bot, float(purchase.get("price_usd") or 0.0), supplier_unit_price * quantity,
                    "partner_proxy", purchase_id,
                )
                await send_partner_proxy_delivery(target.bot, target.chat.id, service, quantity, delivery)
                return
        except PartnerProxyApiError:
            pass
        await asyncio.sleep(5)


@dp.callback_query(F.data.startswith("partner_proxy_balance:"))
async def partner_proxy_pay_from_balance(callback: CallbackQuery):
    try:
        _, service, quantity_text = (callback.data or "").split(":", 2)
        quantity = int(quantity_text)
        _, price = await get_partner_proxy_quote(service, quantity, callback.bot)
        price = apply_loyalty_discount(callback.from_user.id, price)
    except (ValueError, PartnerProxyApiError):
        await callback.answer("Не удалось рассчитать заказ.", show_alert=True)
        return
    async with PROXY_PURCHASE_LOCK:
        updated, error = adjust_user_balance(callback.from_user.id, -price)
        if error:
            profile = get_user_profile(callback.from_user.id) or {}
            await callback.answer(
                f"Недостаточно средств. Нужно ${price:.2f}, баланс ${float(profile.get('balance') or 0):.2f}.",
                show_alert=True,
            )
            return
        purchase_id = create_partner_proxy_purchase(callback.from_user.id, service, quantity, price)
        purchase = get_maskify_proxy_purchase(purchase_id) or {}
        await callback.message.edit_text("⏳ Оплата принята. Получаем прокси у поставщика…")
        success, text = await deliver_partner_proxy_purchase(callback.message, purchase)
        if not success:
            adjust_user_balance(callback.from_user.id, price)
            finish_maskify_proxy_purchase(purchase_id, "credited")
            text += f"\n\n${price:.2f} возвращены на внутренний баланс."
        await callback.message.edit_text(text)
    await callback.answer()


@dp.callback_query(F.data.startswith("partner_proxy_provider:"))
async def partner_proxy_create_invoice(callback: CallbackQuery):
    try:
        _, provider, service, quantity_text = (callback.data or "").split(":", 3)
        quantity = int(quantity_text)
        _, price = await get_partner_proxy_quote(service, quantity, callback.bot)
        price = apply_loyalty_discount(callback.from_user.id, price)
        purchase_id = create_partner_proxy_purchase(callback.from_user.id, service, quantity, price)
        bot_info = await callback.bot.get_me()
        redirect_url = f"https://t.me/{bot_info.username}" if bot_info.username else "https://t.me"
        description = f"Purchase {quantity} proxies {service}"
        if provider == "xrocket":
            invoice = await create_xrocket_invoice(
                purchase_id, price, description,
                payload_data={"partner_proxy_purchase_id": purchase_id, "user_id": callback.from_user.id},
            )
        elif provider == "lolz":
            invoice = await create_lolz_invoice(
                amount_usd=price,
                payment_id=f"partner-proxy-{purchase_id}-{callback.from_user.id}-{int(time.time())}",
                comment=description,
                success_url=redirect_url,
                additional_data=str(purchase_id),
            )
        elif provider == "heleket":
            invoice = await create_heleket_invoice(
                order_id=f"partner-proxy-{purchase_id}-{callback.from_user.id}-{int(time.time())}",
                amount_usd=price,
                description=description,
                success_url=redirect_url,
                additional_data=str(purchase_id),
            )
        elif provider == "crystalpay":
            invoice = await create_crystalpay_invoice(
                amount_usd=price,
                description=description,
                extra=f"partner_proxy:{purchase_id}:user:{callback.from_user.id}",
                redirect_url=redirect_url,
            )
        else:
            raise ValueError("Неизвестный способ оплаты")
        set_maskify_proxy_purchase_invoice(
            purchase_id, invoice["invoice_id"], invoice["pay_url"], provider, invoice.get("client_invoice_id")
        )
    except (ValueError, PartnerProxyApiError, XRocketError, LolzError, HeleketError, CrystalPayError):
        logger.exception("Failed to create partner proxy invoice")
        await callback.answer("Не удалось создать счёт.", show_alert=True)
        return
    await callback.message.edit_text(
        f"<b>Счёт на покупку прокси</b>\n\nСервис: <b>{service}</b>\nКоличество: <b>{quantity} шт.</b>\nСумма: <b>${price:.2f}</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Оплатить", url=invoice["pay_url"], icon_custom_emoji_id=PAYMENT_BUTTON_EMOJI_ID)],
            [InlineKeyboardButton(text="Проверить оплату", callback_data=f"partner_proxy_check:{purchase_id}", icon_custom_emoji_id=PAYMENT_CHECK_EMOJI_ID)],
            [InlineKeyboardButton(text=PROXY_BACK_BUTTON_TEXT, callback_data=f"proxy_test:service:{service}", icon_custom_emoji_id=PAYMENT_BACK_EMOJI_ID)],
        ]),
        parse_mode="HTML",
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("partner_proxy_check:"))
async def partner_proxy_check_invoice(callback: CallbackQuery):
    try:
        purchase_id = int((callback.data or "").split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Счёт не найден.", show_alert=True)
        return
    purchase = get_maskify_proxy_purchase(purchase_id)
    if not purchase or int(purchase["telegram_user_id"]) != callback.from_user.id:
        await callback.answer("Счёт не найден.", show_alert=True)
        return
    if purchase["status"] == "completed":
        await callback.answer("Заказ уже выполнен.", show_alert=True)
        return
    try:
        provider = str(purchase.get("payment_provider") or "crystalpay").lower()
        if provider == "xrocket":
            paid = is_xrocket_invoice_paid(await get_xrocket_invoice(str(purchase.get("invoice_id") or "")))
        elif provider == "lolz":
            paid = is_lolz_invoice_paid(await get_lolz_invoice(purchase.get("invoice_id"), purchase.get("client_invoice_id")))
        elif provider == "heleket":
            paid = is_heleket_invoice_paid(await get_heleket_payment(purchase.get("invoice_id"), purchase.get("client_invoice_id")))
        else:
            paid = is_crystalpay_invoice_paid(await get_crystalpay_invoice(str(purchase.get("invoice_id") or "")))
    except (XRocketError, LolzError, HeleketError, CrystalPayError):
        await callback.answer("Не удалось проверить оплату.", show_alert=True)
        return
    if not paid:
        await callback.answer("Оплата ещё не поступила.", show_alert=True)
        return
    if not claim_maskify_proxy_purchase(purchase_id, callback.from_user.id):
        await callback.answer("Счёт уже обрабатывается.", show_alert=True)
        return
    await callback.message.edit_text("⏳ Оплата подтверждена. Получаем прокси у поставщика…")
    success, text = await deliver_partner_proxy_purchase(callback.message, purchase)
    if not success:
        price = float(purchase.get("price_usd") or 0)
        adjust_user_balance(callback.from_user.id, price)
        finish_maskify_proxy_purchase(purchase_id, "credited")
        text += f"\n\n${price:.2f} зачислены на внутренний баланс."
    await callback.message.edit_text(text)
    await callback.answer()


def build_proxy_pool_text(pool: str, balance: float | None, settings: dict, bot: Bot | None = None) -> str:
    session_name = "По запросу" if settings["type"] == "rotating" else "Фиксированная"
    rotation = "При каждом запросе" if settings["type"] == "rotating" else f"Каждые {max(1, int(settings['sessionttl']) // 60)} минут"
    return (
        f"<b>{MASKIFY_POOLS[pool]}</b>\n\n"
        f"Доступный трафик: <b>{_traffic_text(balance)}</b>\n"
        f"Цена пополнения: <b>${get_proxy_traffic_sale_price(pool, 1, bot):g} за 1 GB</b>\n\n"
        "<b>География:</b>\n"
        f"<blockquote>Страна: {settings['country_name']}</blockquote>\n"
        "<b>Настройки:</b>\n"
        f"<blockquote>Сессия: {session_name}\nРотация: {rotation}</blockquote>\n"
        "<b>Прочее:</b>\n"
        f"<blockquote>Формат: {settings['format']}</blockquote>\n"
        "<b>Экспорт:</b>\n"
        f"<blockquote>Протокол подключения: {settings['protocol'].upper()}\nКоличество: {settings['quantity']} шт.</blockquote>"
        + ("\n<i>Резидентские прокси работают через HTTP CONNECT. Для HTTPS-сайтов используйте ту же HTTP-строку; не меняйте схему на https://.</i>" if pool == "residential" else "")
    )


async def render_proxy_pool(message: Message, user_id: int, pool: str) -> None:
    try:
        balance = await get_maskify_user_remaining_gb(user_id)
    except Exception:
        balance = None
    settings = get_proxy_settings(user_id, pool)
    await message.edit_text(
        build_proxy_pool_text(pool, balance, settings, message.bot),
        reply_markup=build_proxy_pool_keyboard(pool, message.bot),
        parse_mode="HTML",
    )


async def show_proxy_test_home(target: Message, user_id: int, *, edit: bool = False) -> None:
    text = await build_proxy_test_menu_text(user_id)
    if edit:
        await render_screen(target, text, reply_markup=build_proxy_test_menu_keyboard(target.bot), parse_mode="HTML")
    else:
        await target.answer(text, reply_markup=build_proxy_test_menu_keyboard(target.bot), parse_mode="HTML")


async def proxy_test_menu_command(message: Message, state: FSMContext):
    await state.clear()
    await show_proxy_test_home(message, message.from_user.id)


@dp.message(ProxyTestState.waiting_topup_gb)
async def proxy_test_receive_gb(message: Message, state: FSMContext):
    data = await state.get_data()
    pool = str(data.get("proxy_test_pool") or "")
    try:
        gb = float((message.text or "").replace(",", ".").strip())
    except ValueError:
        # Do not leave the global FSM stuck: otherwise every reply-menu action
        # is interpreted as a traffic amount until the process restarts.
        await state.clear()
        await message.answer("Ввод количества отменён.")
        return
    if pool not in MASKIFY_POOLS or gb <= 0 or gb > 10000:
        await message.answer("Введите количество от 0.1 до 10000 GB.")
        return
    gb = round(gb, 2)
    price = apply_loyalty_discount(
        message.from_user.id,
        get_proxy_traffic_sale_price(pool, gb, message.bot),
    )
    add_user(message.from_user.id)
    await state.clear()
    try:
        supplier_available_gb = await get_maskify_sellable_gb()
    except MaskifyError:
        supplier_available_gb = 0.0
    if gb > supplier_available_gb + 1e-9:
        await message.answer(
            f"Недостаточно доступного трафика. Можно купить: <b>{supplier_available_gb:g} GB</b>.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=PROXY_BACK_BUTTON_TEXT, callback_data=f"proxy_test:{pool}", icon_custom_emoji_id=PAYMENT_BACK_EMOJI_ID)]]
            ),
            parse_mode="HTML",
        )
        return
    await message.answer(
        f"<b>Покупка трафика</b>\n\n"
        f"Трафик: <b>{gb:g} GB</b>\n"
        f"К оплате: <b>${price:.2f}</b>\n"
        "Выберите способ оплаты:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Списать с баланса", callback_data=f"maskify_balance:{gb:g}", icon_custom_emoji_id=PAYMENT_BALANCE_EMOJI_ID)],
                [InlineKeyboardButton(text="XROCKET", callback_data=f"maskify_provider:xrocket:{gb:g}", icon_custom_emoji_id=PAYMENT_XROCKET_EMOJI_ID)],
                [InlineKeyboardButton(text="LOLZ", callback_data=f"maskify_provider:lolz:{gb:g}", icon_custom_emoji_id=PAYMENT_LOLZ_EMOJI_ID)],
                [InlineKeyboardButton(text="Heleket", callback_data=f"maskify_provider:heleket:{gb:g}", icon_custom_emoji_id=PAYMENT_HELEKET_EMOJI_ID)],
                [InlineKeyboardButton(text="Cryptobot", callback_data=f"maskify_provider:crystalpay:{gb:g}", icon_custom_emoji_id=PAYMENT_CRYPTOBOT_EMOJI_ID)],
                [InlineKeyboardButton(text=PROXY_BACK_BUTTON_TEXT, callback_data=f"proxy_test:{pool}", icon_custom_emoji_id=PAYMENT_BACK_EMOJI_ID)],
            ]
        ),
        parse_mode="HTML",
    )


async def deliver_maskify_traffic(
    target_message: Message,
    user_id: int,
    gb: float,
    price: float,
    purchase_id: int | None = None,
) -> tuple[bool, str]:
    try:
        supplier_available_gb = await get_maskify_sellable_gb()
    except MaskifyError:
        return False, "Не удалось проверить доступность трафика."
    if gb > supplier_available_gb + 1e-9:
        return False, f"Недостаточно доступного трафика. Можно купить: {supplier_available_gb:g} GB."
    try:
        user_traffic_remaining = await add_maskify_personal_user_traffic(user_id, gb)
    except Exception:
        return False, "Не удалось начислить трафик."
    await send_proxy_traffic_log(
        target_message.bot,
        event="Трафик успешно пополнен",
        user_id=user_id,
        pool="residential",
        gb=gb,
        price=price,
    )
    await notify_proxy_traffic_purchase_owner(
        target_message.bot,
        target_message,
        user_id=user_id,
        gb=gb,
        price=price,
        user_traffic_remaining=user_traffic_remaining,
    )
    base_cost = float(PROXY_TEST_GB_PRICES["residential"]) * float(gb)
    loyalty_multiplier = 1.0 - get_loyalty_discount_percent(user_id) / 100.0
    if purchase_id is not None:
        accrue_proxy_partner_profit(
            target_message.bot, price, base_cost * loyalty_multiplier, "maskify_traffic", purchase_id,
        )
    return True, (
        f"✅ Зачислено <b>{gb:g} GB</b>\n"
        f"Оплачено: <b>${price:.2f}</b>\n"
        f"Ваш трафик: <b>{user_traffic_remaining:g} GB</b>"
    )


@dp.callback_query(F.data.startswith("maskify_balance:"))
async def maskify_pay_from_balance(callback: CallbackQuery):
    try:
        gb = round(float((callback.data or "").split(":", 1)[1]), 2)
    except (ValueError, IndexError):
        await callback.answer("Некорректное количество.", show_alert=True)
        return
    price = apply_loyalty_discount(
        callback.from_user.id,
        get_proxy_traffic_sale_price("residential", gb, callback.bot),
    )
    async with PROXY_PURCHASE_LOCK:
        updated, error = adjust_user_balance(callback.from_user.id, -price)
        if error:
            profile = get_user_profile(callback.from_user.id) or {}
            await callback.answer(
                f"Недостаточно средств. Нужно ${price:.2f}, баланс ${float(profile.get('balance') or 0):.2f}.",
                show_alert=True,
            )
            return
        purchase_id = create_maskify_proxy_purchase(callback.from_user.id, gb, price)
        success, text = await deliver_maskify_traffic(callback.message, callback.from_user.id, gb, price, purchase_id)
        if not success:
            adjust_user_balance(callback.from_user.id, price)
            finish_maskify_proxy_purchase(purchase_id, "credited")
            text += " \u0421\u0440\u0435\u0434\u0441\u0442\u0432\u0430 \u0432\u043e\u0437\u0432\u0440\u0430\u0449\u0435\u043d\u044b \u043d\u0430 \u0431\u0430\u043b\u0430\u043d\u0441."
        else:
            finish_maskify_proxy_purchase(purchase_id, "completed")
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=PROXY_BACK_BUTTON_TEXT, callback_data="proxy_test:residential", icon_custom_emoji_id=PAYMENT_BACK_EMOJI_ID)
        ]]),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("maskify_provider:"))
async def maskify_create_invoice(callback: CallbackQuery):
    try:
        _, provider, gb_text = (callback.data or "").split(":", 2)
        gb = round(float(gb_text), 2)
    except (ValueError, IndexError):
        await callback.answer("Некорректное количество.", show_alert=True)
        return
    price = apply_loyalty_discount(
        callback.from_user.id,
        get_proxy_traffic_sale_price("residential", gb, callback.bot),
    )
    try:
        available = await get_maskify_sellable_gb()
        if gb > available + 1e-9:
            await callback.answer(f"Доступно для покупки: {available:g} GB.", show_alert=True)
            return
        purchase_id = create_maskify_proxy_purchase(callback.from_user.id, gb, price)
        bot_info = await callback.bot.get_me()
        redirect_url = f"https://t.me/{bot_info.username}" if bot_info.username else "https://t.me"
        description = f"Покупка {gb:g} GB резидентских прокси"
        if provider == "xrocket":
            invoice = await create_xrocket_invoice(
                purchase_id,
                price,
                description,
                payload_data={"maskify_proxy_purchase_id": purchase_id, "user_id": callback.from_user.id},
            )
        elif provider == "lolz":
            payment_id = f"maskify-{purchase_id}-{callback.from_user.id}-{int(time.time())}"
            invoice = await create_lolz_invoice(
                amount_usd=price,
                payment_id=payment_id,
                comment=description,
                success_url=redirect_url,
                additional_data=str(purchase_id),
            )
        elif provider == "heleket":
            merchant_order_id = f"maskify-{purchase_id}-{callback.from_user.id}-{int(time.time())}"
            invoice = await create_heleket_invoice(
                order_id=merchant_order_id,
                amount_usd=price,
                description=description,
                success_url=redirect_url,
                additional_data=str(purchase_id),
            )
        elif provider == "crystalpay":
            invoice = await create_crystalpay_invoice(
                amount_usd=price,
                description=description,
                extra=f"maskify_proxy:{purchase_id}:user:{callback.from_user.id}",
                redirect_url=redirect_url,
            )
        else:
            await callback.answer("Неизвестный способ оплаты.", show_alert=True)
            return
        set_maskify_proxy_purchase_invoice(
            purchase_id,
            invoice["invoice_id"],
            invoice["pay_url"],
            provider,
            invoice.get("client_invoice_id"),
        )
    except (XRocketError, LolzError, HeleketError, CrystalPayError, MaskifyError):
        await callback.answer("Не удалось создать счёт.", show_alert=True)
        return
    provider_labels = {"xrocket": "XROCKET", "lolz": "LOLZ", "heleket": "Heleket", "crystalpay": "Cryptobot"}
    await callback.message.edit_text(
        f"<b>Счёт на покупку прокси</b>\n\n"
        f"Трафик: <b>{gb:g} GB</b>\n"
        f"Сумма: <b>${price:.2f}</b>\n"
        f"Способ: <b>{provider_labels.get(provider, provider)}</b>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Оплатить", url=invoice["pay_url"], icon_custom_emoji_id=PAYMENT_BUTTON_EMOJI_ID)],
                [InlineKeyboardButton(text="Проверить оплату", callback_data=f"maskify_check:{purchase_id}", icon_custom_emoji_id=PAYMENT_CHECK_EMOJI_ID)],
                [InlineKeyboardButton(text=PROXY_BACK_BUTTON_TEXT, callback_data="proxy_test:residential", icon_custom_emoji_id=PAYMENT_BACK_EMOJI_ID)],
            ]
        ),
        parse_mode="HTML",
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("maskify_check:"))
async def maskify_check_invoice(callback: CallbackQuery):
    try:
        purchase_id = int((callback.data or "").split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Счёт не найден.", show_alert=True)
        return
    purchase = get_maskify_proxy_purchase(purchase_id)
    if not purchase or int(purchase["telegram_user_id"]) != callback.from_user.id:
        await callback.answer("Счёт не найден.", show_alert=True)
        return
    if purchase["status"] == "completed":
        await callback.answer("Этот счёт уже обработан.", show_alert=True)
        return
    try:
        provider = str(purchase.get("payment_provider") or "crystalpay").lower()
        if provider == "xrocket":
            invoice = await get_xrocket_invoice(str(purchase.get("invoice_id") or ""))
            paid = is_xrocket_invoice_paid(invoice)
        elif provider == "lolz":
            invoice = await get_lolz_invoice(
                invoice_id=purchase.get("invoice_id"),
                payment_id=purchase.get("client_invoice_id"),
            )
            paid = is_lolz_invoice_paid(invoice)
        elif provider == "heleket":
            invoice = await get_heleket_payment(
                invoice_id=purchase.get("invoice_id"),
                order_id=purchase.get("client_invoice_id"),
            )
            paid = is_heleket_invoice_paid(invoice)
        else:
            invoice = await get_crystalpay_invoice(str(purchase.get("invoice_id") or ""))
            paid = is_crystalpay_invoice_paid(invoice)
    except (XRocketError, LolzError, HeleketError, CrystalPayError):
        await callback.answer("Не удалось проверить оплату.", show_alert=True)
        return
    if not paid:
        await callback.answer("Оплата ещё не поступила.", show_alert=True)
        return
    if not claim_maskify_proxy_purchase(purchase_id, callback.from_user.id):
        await callback.answer("Счёт уже обрабатывается.", show_alert=True)
        return
    gb = float(purchase["gb"])
    price = float(purchase["price_usd"])
    async with PROXY_PURCHASE_LOCK:
        success, text = await deliver_maskify_traffic(callback.message, callback.from_user.id, gb, price, purchase_id)
        if success:
            finish_maskify_proxy_purchase(purchase_id, "completed")
        else:
            adjust_user_balance(callback.from_user.id, price)
            finish_maskify_proxy_purchase(purchase_id, "credited")
            text += f" Оплата <b>${price:.2f}</b> зачислена на ваш внутренний баланс."
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=PROXY_BACK_BUTTON_TEXT, callback_data="proxy_test:residential", icon_custom_emoji_id=PAYMENT_BACK_EMOJI_ID)
        ]]),
    )
    await callback.answer()


@dp.message(ProxyTestState.waiting_country_search)
async def proxy_test_country_search(message: Message, state: FSMContext):
    data = await state.get_data()
    pool = str(data.get("proxy_test_pool") or "")
    if pool not in MASKIFY_POOLS:
        await state.clear()
        return
    try:
        countries = await get_maskify_countries(pool)
    except Exception:
        countries = []
    results = search_countries(countries, message.text or "")[:20]
    if not results:
        await message.answer(
            "Ничего не найдено. Введите код или название страны, например: RU, Россия, Российская Федерация.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=PROXY_BACK_BUTTON_TEXT, callback_data=f"proxy_test:country:{pool}:0", icon_custom_emoji_id=PAYMENT_BACK_EMOJI_ID)]]),
        )
        return
    await state.clear()
    buttons = [[InlineKeyboardButton(text=f"{row['name']} · {row['code']}", callback_data=f"proxy_test:set:{pool}:country:{row['code']}")] for row in results]
    buttons.append([InlineKeyboardButton(text="🔎 Новый поиск", callback_data=f"proxy_test:country_search:{pool}")])
    buttons.append([InlineKeyboardButton(text=PROXY_BACK_BUTTON_TEXT, callback_data=f"proxy_test:country:{pool}:0", icon_custom_emoji_id=PAYMENT_BACK_EMOJI_ID)])
    await message.answer("Результаты поиска:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@dp.message(ProxyTestState.waiting_custom_quantity)
async def proxy_test_custom_quantity(message: Message, state: FSMContext):
    data = await state.get_data()
    pool = str(data.get("proxy_test_pool") or "")
    service = str(data.get("proxy_test_service") or "")
    try:
        quantity = int((message.text or "").strip())
    except ValueError:
        await message.answer("Введите целое число от 1 до 10000.")
        return
    if not 1 <= quantity <= 10000:
        await message.answer("Введите количество от 1 до 10000.")
        return
    if service in PARTNER_PROXY_SERVICES:
        await state.clear()
        try:
            supplier_unit_price, total_price = await get_partner_proxy_quote(service, quantity, message.bot)
        except PartnerProxyApiError as error:
            await message.answer(str(error))
            return
        rows = [
            [InlineKeyboardButton(text="Списать с баланса", callback_data=f"partner_proxy_balance:{service}:{quantity}", icon_custom_emoji_id=PAYMENT_BALANCE_EMOJI_ID)],
            [InlineKeyboardButton(text="XROCKET", callback_data=f"partner_proxy_provider:xrocket:{service}:{quantity}", icon_custom_emoji_id=PAYMENT_XROCKET_EMOJI_ID)],
            [InlineKeyboardButton(text="LOLZ", callback_data=f"partner_proxy_provider:lolz:{service}:{quantity}", icon_custom_emoji_id=PAYMENT_LOLZ_EMOJI_ID)],
            [InlineKeyboardButton(text="Heleket", callback_data=f"partner_proxy_provider:heleket:{service}:{quantity}", icon_custom_emoji_id=PAYMENT_HELEKET_EMOJI_ID)],
            [InlineKeyboardButton(text="Cryptobot", callback_data=f"partner_proxy_provider:crystalpay:{service}:{quantity}", icon_custom_emoji_id=PAYMENT_CRYPTOBOT_EMOJI_ID)],
        ]
        await message.answer(
            f"<b>Покупка прокси</b>\n\nСервис: <b>{service}</b>\nКоличество: <b>{quantity} шт.</b>\n"
            f"Цена поставщика: <b>${supplier_unit_price:.4f}/шт.</b>\nК оплате: <b>${total_price:.2f}</b>\n\nВыберите способ оплаты:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML",
        )
        return
    if pool not in MASKIFY_POOLS:
        await state.clear()
        await message.answer("Раздел покупки не найден.")
        return
    settings = get_proxy_settings(message.from_user.id, pool)
    settings["quantity"] = quantity
    save_proxy_settings(message.from_user.id, pool, settings)
    await state.clear()
    await message.answer(
        f"✅ Количество: <b>{quantity} шт.</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=PROXY_BACK_BUTTON_TEXT, callback_data=f"proxy_test:{pool}", icon_custom_emoji_id=PAYMENT_BACK_EMOJI_ID)]]),
        parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("proxy_test:"))
async def proxy_test_menu_callback(callback: CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split(":")
    action = parts[1] if len(parts) > 1 else ""
    user_id = callback.from_user.id
    if action == "home":
        await state.clear()
        await show_proxy_test_home(callback.message, user_id, edit=True)
    elif action in MASKIFY_POOLS:
        await state.clear()
        await render_proxy_pool(callback.message, user_id, action)
    elif action == "static":
        await state.clear()
        await callback.answer("Статичные IPv4 теперь открываются в этом меню. Раздел подключается.", show_alert=True)
    elif action == "topup":
        if len(parts) < 3 or parts[2] not in MASKIFY_POOLS:
            await callback.answer("Сначала выберите тип прокси.", show_alert=True)
        else:
            pool = parts[2]
            unit_price = get_proxy_traffic_sale_price(pool, 1, callback.bot)
            await state.set_state(ProxyTestState.waiting_topup_gb)
            await state.update_data(proxy_test_pool=pool)
            await callback.message.edit_text(
                f"<b>{MASKIFY_POOLS[pool]}</b>\n\nЦена: <b>${unit_price:g} за 1 GB</b>\n\nВведите количество гигабайтов:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=PROXY_BACK_BUTTON_TEXT, callback_data=f"proxy_test:{pool}", icon_custom_emoji_id=PAYMENT_BACK_EMOJI_ID)]]),
                parse_mode="HTML",
            )
    elif action == "country":
        await state.clear()
        pool = parts[2]; page = int(parts[3]) if len(parts) > 3 else 0
        try: countries = await get_maskify_countries(pool)
        except Exception: countries = []
        per_page = 8; rows = countries[page * per_page:(page + 1) * per_page]
        buttons = [[InlineKeyboardButton(text="🌍 Любая", callback_data=f"proxy_test:set:{pool}:country:_")]]
        buttons.append([InlineKeyboardButton(text="🔎 Поиск страны", callback_data=f"proxy_test:country_search:{pool}")])
        buttons += [[InlineKeyboardButton(text=x["name"], callback_data=f"proxy_test:set:{pool}:country:{x['code']}") ] for x in rows]
        nav=[]
        if page: nav.append(InlineKeyboardButton(text="‹", callback_data=f"proxy_test:country:{pool}:{page-1}"))
        if (page+1)*per_page < len(countries): nav.append(InlineKeyboardButton(text="›", callback_data=f"proxy_test:country:{pool}:{page+1}"))
        if nav: buttons.append(nav)
        buttons.append([InlineKeyboardButton(text=PROXY_BACK_BUTTON_TEXT, callback_data=f"proxy_test:{pool}", icon_custom_emoji_id=PAYMENT_BACK_EMOJI_ID)])
        await callback.message.edit_text("Выберите страну:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    elif action == "country_search":
        pool = parts[2]
        await state.set_state(ProxyTestState.waiting_country_search)
        await state.update_data(proxy_test_pool=pool)
        await callback.message.edit_text(
            "🔎 <b>Поиск страны</b>\n\nВведите код или название на русском либо английском.\nНапример: <code>RU</code>, <code>ру</code>, <code>Россия</code>, <code>Российская Федерация</code>.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=PROXY_BACK_BUTTON_TEXT, callback_data=f"proxy_test:country:{pool}:0", icon_custom_emoji_id=PAYMENT_BACK_EMOJI_ID)]]),
            parse_mode="HTML",
        )
    elif action == "session":
        pool=parts[2]; await callback.message.edit_text("Выберите режим IP:", reply_markup=build_choices_keyboard(pool,"type",[("Ротация по запросу","rotating"),("Фиксированная сессия","sticky")]))
    elif action == "rotation":
        pool=parts[2]; await callback.message.edit_text("Выберите время смены IP:", reply_markup=build_choices_keyboard(pool,"sessionttl",[("По запросу","0"),("5 минут","300"),("15 минут","900"),("30 минут","1800"),("60 минут","3600")]))
    elif action == "format":
        pool=parts[2]; await callback.message.edit_text("Выберите формат:", reply_markup=build_choices_keyboard(pool,"format",[
            ("login:password@hostname:port","uph"),
            ("hostname:port:login:password","hpu"),
            ("hostname:port@login:password","hpa"),
            ("protocol://login:password@hostname:port","url"),
        ]))
    elif action == "protocol":
        pool=parts[2]
        protocol_choices = [("HTTP (включая HTTPS-сайты)", "http")] if pool == "residential" else [("HTTP", "http"), ("SOCKS5", "socks5")]
        await callback.message.edit_text("Выберите протокол подключения:\n\nДля резидентских прокси доступен HTTP CONNECT; HTTPS — это протокол сайта, а не прокси.", reply_markup=build_choices_keyboard(pool,"protocol",protocol_choices))
    elif action == "quantity":
        pool=parts[2]
        choices=[(str(x),str(x)) for x in (5,25,50,100)]
        keyboard=build_choices_keyboard(pool,"quantity",choices)
        keyboard.inline_keyboard.insert(-1, [InlineKeyboardButton(text="✏️ Своё количество", callback_data=f"proxy_test:quantity_custom:{pool}")])
        await callback.message.edit_text("Выберите количество:", reply_markup=keyboard)
    elif action == "quantity_custom":
        pool=parts[2]
        await state.set_state(ProxyTestState.waiting_custom_quantity)
        await state.update_data(proxy_test_pool=pool)
        await callback.message.edit_text(
            "✏️ Введите своё количество прокси от 1 до 10000:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=PROXY_BACK_BUTTON_TEXT, callback_data=f"proxy_test:quantity:{pool}", icon_custom_emoji_id=PAYMENT_BACK_EMOJI_ID)]]),
        )
    elif action == "set":
        pool,key,value=parts[2],parts[3],parts[4]
        settings=get_proxy_settings(user_id,pool)
        if key == "country":
            settings["country"]="" if value=="_" else value
            if value == "_":
                settings["country_name"] = "Любая"
            else:
                try:
                    countries = await get_maskify_countries(pool)
                except Exception:
                    countries = []
                selected = next((row for row in countries if str(row.get("code", "")).upper() == value.upper()), None)
                settings["country_name"] = str(selected.get("name")) if selected else value.upper()
        elif key == "quantity":
            settings[key] = int(value)
        elif key == "sessionttl":
            settings[key] = int(value)
            settings["type"] = "rotating" if int(value) == 0 else "sticky"
        elif key == "type":
            settings[key] = value
            settings["sessionttl"] = 0 if value == "rotating" else max(300, int(settings.get("sessionttl") or 1800))
        elif key == "format":
            settings[key] = {
                "uph": "login:password@hostname:port",
                "hpu": "hostname:port:login:password",
                "hpa": "hostname:port@login:password",
                "url": "protocol://login:password@hostname:port",
            }.get(value, "login:password@hostname:port")
        else: settings[key]=value
        save_proxy_settings(user_id,pool,settings); await render_proxy_pool(callback.message,user_id,pool)
    elif action == "reset":
        pool=parts[2]
        default_format = "protocol://login:password@hostname:port" if pool == "residential" else "login:password@hostname:port"
        save_proxy_settings(user_id,pool,{"country":"","country_name":"Любая","type":"rotating","sessionttl":1800,"protocol":"http","format":default_format,"quantity":5})
        await render_proxy_pool(callback.message,user_id,pool)
    elif action == "password":
        await callback.answer("Для Maskify ключи выдаются в строках прокси; отдельный пароль не используется.", show_alert=True)
    elif action == "get":
        pool=parts[2]
        try:
            proxies = await generate_maskify_proxies(user_id, get_proxy_settings(user_id, pool))
            caption = f"Прокси · {MASKIFY_POOLS[pool]}"
            if pool == "residential":
                caption += "\nHTTP CONNECT: эту строку используйте и для HTTPS-сайтов. Не указывайте https:// перед адресом прокси."
            await callback.message.answer_document(
                document=BufferedInputFile(proxies.encode("utf-8"), filename=f"proxy-{pool}.txt"),
                caption=caption,
            )
        except Exception as exc: await callback.answer(str(exc),show_alert=True)
    elif action == "service":
        service = parts[2] if len(parts) > 2 else ""
        await state.clear()
        if service in PARTNER_PROXY_SUBMENUS:
            submenu_title, submenu_items = PARTNER_PROXY_SUBMENUS[service]
            await callback.message.edit_text(
                f"⭐ <b>{submenu_title}</b>\n\nВыберите нужный тип прокси:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    *[
                        [InlineKeyboardButton(text=label, callback_data=f"proxy_test:service:{item_service}")]
                        for label, item_service in submenu_items
                    ],
                    [InlineKeyboardButton(text=PROXY_BACK_BUTTON_TEXT, callback_data="proxy_test:home", icon_custom_emoji_id=PAYMENT_BACK_EMOJI_ID)],
                ]), parse_mode="HTML",
            )
        elif service in PARTNER_PROXY_SERVICES:
            await callback.message.edit_text(
                build_partner_proxy_service_text(service),
                reply_markup=build_partner_proxy_service_keyboard(service, callback.bot),
                parse_mode="HTML",
            )
        else:
            await callback.answer("Сервис не найден.", show_alert=True)
    elif action == "service_quantity":
        service = parts[2]; quantity = int(parts[3])
        await state.clear()
        await show_partner_proxy_checkout(callback.message, service, quantity)
    elif action == "service_quantity_custom":
        service = parts[2]
        await state.set_state(ProxyTestState.waiting_custom_quantity)
        await state.update_data(proxy_test_service=service)
        await callback.message.edit_text(f"Введите своё количество прокси для {service} (от 1 до 10000):", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=PROXY_BACK_BUTTON_TEXT, callback_data="proxy_test:home", icon_custom_emoji_id=PAYMENT_BACK_EMOJI_ID)]]))
    with contextlib.suppress(Exception):
        await callback.answer()


async def send_email_miniapp_link(message: Message) -> None:
    lang = get_event_language_code(message)
    bot_info = await message.bot.get_me()
    email_url = build_email_url(bot_info.username)
    if not email_url:
        await message.answer(tr(lang, "miniapp.email_url_missing"))
        return

    await message.answer(
        "👇",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=tr(lang, "miniapp.open_email"), web_app=WebAppInfo(url=email_url))]
            ]
        ),
    )


async def send_sms_miniapp_link(message: Message) -> None:
    bot_info = await message.bot.get_me()
    sms_url = build_sms_url(bot_info.username)
    if not sms_url:
        await message.answer("SMS Mini App временно недоступен.")
        return
    await message.answer(
        "📲 <b>SMS-активации</b>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="SMS", web_app=WebAppInfo(url=sms_url), icon_custom_emoji_id="5904248647972820334")]]
        ),
        parse_mode="HTML",
    )


async def help_command(message: Message):
    lang = get_event_language_code(message)
    await message.answer(
        "👇",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=tr(lang, "menu.support"), url="https://t.me/UniversallSupportBot?start=market")],
            ]
        ),
    )


async def open_miniapp_command(message: Message):
    await send_miniapp_link(message)


async def open_proxy_command(message: Message, state: FSMContext):
    await state.clear()
    await show_proxy_test_home(message, message.from_user.id)


async def open_store_command(message: Message):
    await send_miniapp_link(message)


async def open_email_command(message: Message):
    await send_email_miniapp_link(message)


async def open_sms_command(message: Message):
    await send_sms_miniapp_link(message)


async def open_partner_command(message: Message):
    lang = get_event_language_code(message)
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
        reply_markup=build_partner_program_keyboard(lang),
    )


async def open_partner_cabinet_command(message: Message):
    if not is_admin_user(message.from_user.id):
        return
    partner_bot = get_current_partner_bot(message.bot)
    if partner_bot is None or int(partner_bot.get("owner_id") or 0) != message.from_user.id:
        await message.answer("Партнёрский кабинет доступен только владельцу партнёрского бота.")
        return
    url = build_partner_url(partner_bot.get("bot_username"))
    if not url:
        await message.answer("URL Mini App ещё не настроен.")
        return
    await message.answer(
        "Откройте партнёрский кабинет:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🧭 Открыть кабинет", web_app=WebAppInfo(url=url))]
        ]),
    )

async def payment_reminder_worker(bot: Bot):
    while True:
        try:
            reminders = list_pending_invoice_reminders()
            for reminder in reminders:
                invoice_created_at = parse_datetime(reminder.get("invoice_created_at"))
                if invoice_created_at is None:
                    continue

                elapsed_seconds = (datetime.now() - invoice_created_at).total_seconds()
                payment_provider = str(reminder.get("payment_provider") or "xrocket").lower()
                try:
                    if reminder["entity_type"] == "order":
                        paid_order = await ensure_order_paid(
                            int(reminder["id"]),
                            default_bot=bot,
                            buyer_message=None,
                            payment_invoice=reminder,
                        )
                        if paid_order is not None and paid_order.get("status") != "waiting_payment":
                            await continue_order_fulfillment(
                                int(reminder["id"]),
                                default_bot=bot,
                                notify_chat_id=int(reminder["user_id"]),
                                buyer_message=None,
                                status_message=None,
                            )
                            mark_invoice_reminder_sent(reminder["entity_type"], reminder["id"])
                            continue

                    if reminder["entity_type"] == "topup":
                        if payment_provider == "lolz":
                            invoice = await get_lolz_invoice(
                                invoice_id=reminder.get("invoice_id"),
                                payment_id=reminder.get("client_invoice_id"),
                            )
                            paid = is_lolz_invoice_paid(invoice)
                        elif payment_provider == "heleket":
                            invoice = await get_heleket_payment(
                                invoice_id=reminder.get("invoice_id"),
                                order_id=reminder.get("client_invoice_id"),
                            )
                            paid = is_heleket_invoice_paid(invoice)
                        elif payment_provider == "crystalpay":
                            invoice = await get_crystalpay_invoice(str(reminder.get("invoice_id") or ""))
                            paid = is_crystalpay_invoice_paid(invoice)
                        else:
                            invoice = await get_xrocket_invoice(reminder["invoice_id"])
                            paid = is_xrocket_invoice_paid(invoice)
                        if paid:
                            complete_topup_payment(reminder["id"])
                            mark_invoice_reminder_sent(reminder["entity_type"], reminder["id"])
                            continue
                except (XRocketError, LolzError, HeleketError, CrystalPayError):
                    continue

                if elapsed_seconds < PAYMENT_REMINDER_DELAY_SECONDS:
                    continue

                if int(reminder.get("reminder_sent") or 0) != 0:
                    continue

                try:
                    await bot.send_message(
                        chat_id=reminder["user_id"],
                        text=(
                            "⏳ <b>Счёт ещё не оплачен</b>\n\n"
                            f"На оплату осталось {PAYMENT_REMINDER_REMAINING_MINUTES} минут."
                        ),
                        parse_mode="HTML",
                    )
                    mark_invoice_reminder_sent(reminder["entity_type"], reminder["id"])
                except Exception:
                    continue
        except Exception:
            pass

        await asyncio.sleep(PAYMENT_REMINDER_CHECK_INTERVAL_SECONDS)


async def market_delivery_recovery_worker(bot: Bot):
    while True:
        try:
            await resume_pending_market_deliveries(bot)
        except Exception:
            pass

        await asyncio.sleep(MARKET_DELIVERY_RECOVERY_INTERVAL_SECONDS)


async def proxy_traffic_recovery_worker(bot: Bot):
    while True:
        # Maskify purchases are completed directly by the payment flow.
        await asyncio.sleep(PROXY_TRAFFIC_RETRY_INTERVAL_SECONDS)


async def supplier_balance_monitor_worker(bot: Bot):
    """Notify the main bot only for confirmed low supplier balances."""
    while True:
        try:
            market_rate = await refresh_market_rub_per_usdt()
        except Exception:
            market_rate = 0.0
        checks = (
            ("djekxa", "Djekxa", MARKET_BALANCE_ALERT_USD * market_rate, "RUB", get_market_balance_rub),
            ("maskify", "Maskify", MASKIFY_BALANCE_ALERT_GB, "GB", lambda: get_maskify_reseller_account()),
            ("sms", "Greedy SMS", SMS_BALANCE_ALERT_USD, "USDT", get_provider_balance_usd),
        )
        for key, label, threshold, unit, fetcher in checks:
            try:
                result = await fetcher()
                balance = float(result.get("gb_available") or 0.0) if isinstance(result, dict) else float(result)
            except (MarketProviderError, MaskifyError, GreedySmsError, ValueError, TypeError):
                # Upstream availability is not a balance signal.
                continue
            except Exception:
                logger.exception("Supplier balance check failed: %s", key)
                continue

            if threshold <= 0 or balance >= threshold:
                _supplier_low_balance_alerts.pop(key, None)
                continue
            now = time.monotonic()
            if now - _supplier_low_balance_alerts.get(key, 0.0) < SUPPLIER_BALANCE_ALERT_REPEAT_SECONDS:
                continue
            try:
                await bot.send_message(
                    ADMIN_ID,
                    f"⚠️ Низкий баланс поставщика\n\n{label}: <b>{balance:g} {unit}</b>\nПорог: {threshold:g} {unit}",
                    parse_mode="HTML",
                )
                _supplier_low_balance_alerts[key] = now
            except Exception:
                logger.exception("Could not send supplier balance alert: %s", key)
        await asyncio.sleep(max(SUPPLIER_BALANCE_CHECK_INTERVAL_SECONDS, 60))


async def main():
    create_db()
    partner_runtime = PartnerBotsRuntime(dp, bot)
    set_partner_runtime(partner_runtime)
    miniapp_server = MiniAppServer(bot)
    await miniapp_server.startup()
    await ensure_miniapp_commands(bot)
    await configure_bot_menu_button(bot, None, None)
    miniapp_app = miniapp_server.build_app()
    miniapp_runner = web.AppRunner(miniapp_app)
    await miniapp_runner.setup()
    miniapp_site = web.TCPSite(miniapp_runner, MINIAPP_HOST, MINIAPP_PORT)
    await miniapp_site.start()
    await partner_runtime.load_active_bots()
    await resume_pending_market_deliveries(bot)
    reminder_task = asyncio.create_task(payment_reminder_worker(bot))
    delivery_recovery_task = asyncio.create_task(market_delivery_recovery_worker(bot))
    proxy_traffic_task = asyncio.create_task(proxy_traffic_recovery_worker(bot))
    supplier_balance_task = asyncio.create_task(supplier_balance_monitor_worker(bot))
    try:
        await dp.start_polling(bot)
    finally:
        reminder_task.cancel()
        delivery_recovery_task.cancel()
        proxy_traffic_task.cancel()
        supplier_balance_task.cancel()
        await partner_runtime.shutdown()
        await miniapp_runner.cleanup()
        with contextlib.suppress(asyncio.CancelledError):
            await reminder_task
        with contextlib.suppress(asyncio.CancelledError):
            await delivery_recovery_task
        with contextlib.suppress(asyncio.CancelledError):
            await proxy_traffic_task
        with contextlib.suppress(asyncio.CancelledError):
            await supplier_balance_task


if __name__ == "__main__":
    asyncio.run(main())
