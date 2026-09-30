import asyncio
import hashlib
import hmac
import html
import io
import json
import logging
import mimetypes
import os
import re
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_UP
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

from aiohttp import web
from aiogram import Bot
from aiogram.types import BotCommand, MenuButtonCommands

from database.data import (
    PARTNER_MIN_WITHDRAW_AMOUNT,
    activate_promo_code,
    create_promo_code,
    add_user,
    adjust_user_balance,
    cancel_email_activation_and_refund,
    charge_user_balance_for_order,
    complete_order,
    complete_market_order,
    complete_topup_payment,
    count_user_orders,
    count_user_orders_any_status,
    count_email_activations,
    create_topup,
    create_order,
    create_web_account,
    create_web_session,
    create_sales_log_event,
    credit_order_to_user_balance,
    get_crm_dashboard_stats,
    get_api_account,
    get_api_account_by_key,
    get_web_account_by_session,
    get_partner_bot_by_token,
    get_partner_bot_by_username,
    get_partner_owner_summary,
    list_partner_bots_by_owner,
    get_partner_bot_stats,
    get_partner_bot_available_balance,
    list_partner_bot_user_ids,
    create_partner_payout_request,
    list_recent_promo_codes,
    update_partner_bot_markup,
    update_partner_bot_margin,
    update_partner_franchise_setting,
    get_order,
    get_email_activation,
    get_topup,
    get_user_profile,
    get_loyalty_discount_percent,
    list_proxy_catalog_fallback_items,
    list_email_activations,
    list_recent_unavailable_email_domains,
    list_user_orders,
    list_user_orders_any_status,
    list_user_topups,
    mark_sales_log_event_failed,
    mark_sales_log_event_sent,
    mark_invoice_reminder_sent,
    mark_order_paid,
    mark_order_delivery_pending,
    parse_datetime,
    set_proxy_order_provider_reference,
    authenticate_web_account,
    delete_web_session,
    fail_email_activation_and_refund,
    finalize_email_activation,
    reserve_email_activation,
    update_email_activation_message,
    update_order_invoice,
    update_order_supplier_data,
    update_topup_invoice,
)
from anymessage_service import (
    AnyMessageAPIError,
    cancel_email,
    get_anymessage_error_message,
    get_email_domains,
    get_email_message,
    normalize_target_site,
    order_email,
    reorder_email,
)
from services import (
    CrystalPayError,
    PartnerProxyApiError,
    MarketProviderError,
    HeleketError,
    LolzError,
    PROXY_GENERIC_ERROR_CODE,
    PROXY_PROVIDER_CREATE_ATTEMPTS,
    PROXY_PROVIDER_CREATE_RETRY_DELAY_SECONDS,
    PROXY_PROVIDER_DETAILS_POLL_ATTEMPTS,
    PROXY_PROVIDER_DETAILS_POLL_INTERVAL_SECONDS,
    PROXY_PROVIDER_ORDER_DURATION_DAYS,
    ProxyProviderError,
    XRocketError,
    check_proxy_provider_purchase_availability,
    check_partner_proxy_order,
    create_proxy_provider_order,
    create_partner_proxy_order,
    create_market_order,
    create_heleket_invoice,
    create_crystalpay_invoice,
    create_lolz_invoice,
    create_xrocket_invoice,
    download_text_file,
    get_heleket_payment,
    get_crystalpay_invoice,
    get_lolz_invoice,
    get_xrocket_invoice,
    ensure_proxy_endpoint_scheme,
    format_proxy_delivery,
    format_proxy_endpoint,
    filter_working_proxy_details,
    get_cached_market_rub_per_usdt,
    get_market_order,
    get_market_order_file_url,
    get_market_product,
    get_market_categories,
    get_market_products,
    get_proxy_provider_order,
    get_partner_proxy_account_info,
    get_static_proxy_categories,
    get_static_proxy_categories_snapshot,
    is_heleket_invoice_paid,
    is_crystalpay_invoice_paid,
    is_lolz_invoice_paid,
    is_market_order_creation_rejected,
    is_market_order_ready,
    is_market_email_category,
    is_xrocket_invoice_paid,
)
from sms_service import (
    GreedySmsError,
    finalize_activation as finalize_sms_activation,
    get_activation as get_sms_activation,
    get_countries as get_sms_countries,
    get_number as get_sms_number,
    get_prices as get_sms_prices,
    get_provider_status as get_sms_provider_status,
    get_services as get_sms_services,
    init_sms_db,
    list_activations as list_sms_activations,
    mark_finished as mark_sms_finished,
    refund_activation as refund_sms_activation,
    reserve_activation as reserve_sms_activation,
    sale_price_usd as calculate_sms_sale_price,
    set_provider_status as set_sms_provider_status,
    update_activation_status as update_sms_activation_status,
)
from favorites_service import (
    init_favorites_db,
    list_favorite_product_ids,
    set_product_favorite,
)
from maskify_service import (
    MaskifyError,
    add_maskify_personal_user_traffic,
    create_api_residential_client,
    create_maskify_proxy_purchase,
    create_partner_proxy_purchase,
    credit_api_residential_pool,
    delete_api_webhook,
    dispatch_api_webhook,
    generate_api_residential_client_proxies,
    generate_maskify_proxies,
    get_api_residential_client_live,
    get_api_residential_client_by_key,
    get_api_residential_traffic,
    get_api_webhook,
    get_maskify_proxy_purchase,
    get_maskify_proxy_purchase_by_request,
    get_maskify_sellable_gb,
    finish_maskify_proxy_purchase,
    mark_partner_proxy_delivery_pending,
    save_api_webhook,
    set_maskify_residential_delivery,
    set_partner_proxy_delivery,
    transfer_api_residential_traffic,
    validate_api_webhook_url,
)


logger = logging.getLogger(__name__)


MINIAPP_BASE_URL = os.getenv("MINIAPP_BASE_URL", "").strip().rstrip("/")
# Optional split-domain routing. Leave empty to keep the current domain until
# DNS and nginx are switched; partner links will then use MINIAPP_BASE_URL.
MINIAPP_PARTNER_BASE_URL = os.getenv("MINIAPP_PARTNER_BASE_URL", "").strip().rstrip("/")
MINIAPP_HOST = os.getenv("MINIAPP_HOST", "127.0.0.1").strip()
MINIAPP_PORT = int(os.getenv("MINIAPP_PORT", "8081") or 8081)
MINIAPP_MAIN_BRAND_NAME = os.getenv("MINIAPP_MAIN_BRAND_NAME", "SOUS MARKET").strip() or "SOUS MARKET"
MINIAPP_SUPPORT_URL = os.getenv("MINIAPP_SUPPORT_URL", "https://t.me/UniversallSupportBot?start=market").strip() or "https://t.me/UniversallSupportBot?start=market"
CRM_ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
PUBLIC_API_MARKUP_PERCENT = 35.0
PUBLIC_API_RESIDENTIAL_PRICE_PER_GB = 0.50
# Proxy-service plans in API are 10% cheaper than the same plans in the shop.
# Residential traffic keeps its explicitly configured $0.50/GB price.
PUBLIC_API_PROXY_SERVICE_DISCOUNT_PERCENT = 10.0
PUBLIC_API_PROXY_SERVICE_TARIFFS = {
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
PUBLIC_API_PROXY_SERVICE_UNITS = {
    "piaproxy": "IP",
    "piaproxy_gb": "GB",
    "piaproxy_long_acting": "IP",
    "922proxy": "IP",
    "922proxy_gb": "GB",
    "abcproxy": "IP",
    "abcproxy_gb": "GB",
    "9proxy": "IP",
    "9proxy_gb": "GB",
    "proxy001_gb": "GB",
    "711proxy": "IP",
    "cliproxy": "IP",
    "lokiproxy": "IP",
    "residential": "GB",
}


def get_public_api_proxy_service_total(service: str, quantity: int) -> float:
    """Calculate the shop volume tariff with the API-only 10% discount."""
    requested = int(quantity)
    tariffs = PUBLIC_API_PROXY_SERVICE_TARIFFS.get(str(service))
    if tariffs is None or not 1 <= requested <= 10000:
        raise ValueError("Unsupported service or quantity")
    exact_price = next(
        (float(price) for amount, price in tariffs if int(amount) == requested),
        None,
    )
    if exact_price is not None:
        total = exact_price
    elif requested < int(tariffs[0][0]):
        amount, price = tariffs[0]
        total = float(price) / int(amount) * requested
    elif requested > int(tariffs[-1][0]):
        amount, price = tariffs[-1]
        total = float(price) / int(amount) * requested
    else:
        lower = max((row for row in tariffs if int(row[0]) < requested), key=lambda row: int(row[0]))
        upper = min((row for row in tariffs if int(row[0]) > requested), key=lambda row: int(row[0]))
        progress = (requested - int(lower[0])) / (int(upper[0]) - int(lower[0]))
        total = float(lower[1]) + (float(upper[1]) - float(lower[1])) * progress
    discounted_total = total * (1 - PUBLIC_API_PROXY_SERVICE_DISCOUNT_PERCENT / 100)
    return round(max(discounted_total, 0.01) + 1e-9, 2)


def get_public_api_proxy_services() -> list[dict[str, Any]]:
    """Return only the public proxy-service contract exposed by API v1."""
    return [
        {
            "service": "residential",
            "unit": "GB",
            "price": PUBLIC_API_RESIDENTIAL_PRICE_PER_GB,
            "min_quantity": 1,
            "delivery": "instant",
            "settings": {
                "rotation": ["rotating", "sticky"],
                "session_ttl_seconds": [0, 300, 900, 1800, 3600],
                "country": "ISO 3166-1 alpha-2, for example UA",
                "protocol": ["http", "socks5"],
                "format": [
                    "login:password@hostname:port",
                    "hostname:port:login:password",
                    "hostname:port@login:password",
                    "protocol://login:password@hostname:port",
                ],
            },
        },
        *[
            {
                "service": service,
                "unit": PUBLIC_API_PROXY_SERVICE_UNITS[service],
                "price": get_public_api_proxy_service_total(service, 1),
                "min_quantity": 1,
                "delivery": "instant_or_pending",
                "pricing": "volume_tariffs",
                "tariffs": [
                    {
                        "quantity": int(quantity),
                        "total_price": get_public_api_proxy_service_total(service, int(quantity)),
                        "unit_price": round(
                            get_public_api_proxy_service_total(service, int(quantity)) / int(quantity),
                            6,
                        ),
                    }
                    for quantity, total_price in tariffs
                ],
            }
            for service, tariffs in PUBLIC_API_PROXY_SERVICE_TARIFFS.items()
        ],
    ]
PROXY_BASE_MARKUP_PERCENT = 15
SMS_BASE_MARKUP_PERCENT = float(os.getenv("GREEDY_SMS_MARKUP_PERCENT", "50") or 50)
PROXY_PRICE_USD = float(os.getenv("PROXY_PRICE_USD", "0.6") or 0.6)
ANYMESSAGE_MARKUP_PERCENT = float(os.getenv("ANYMESSAGE_MARKUP_PERCENT", "50") or 50)
ANYMESSAGE_UNAVAILABLE_COOLDOWN_SECONDS = int(
    os.getenv("ANYMESSAGE_UNAVAILABLE_COOLDOWN_SECONDS", "1800") or 1800
)
MINIAPP_ALLOW_UNSAFE_DEV_AUTH = os.getenv("MINIAPP_ALLOW_UNSAFE_DEV_AUTH", "0").strip() == "1"
MINIAPP_DEV_USER_ID = int(os.getenv("MINIAPP_DEV_USER_ID", "0") or 0)
MINIAPP_INITDATA_MAX_AGE_SECONDS = int(
    os.getenv(
        "MINIAPP_INITDATA_MAX_AGE_SECONDS",
        os.getenv("MINIAPP_INIT_DATA_MAX_AGE_SECONDS", "3600"),
    )
    or 3600
)
REFERRAL_PERCENT = 50.0
MARKET_MARKUP_PERCENT = int(float(os.getenv("MARKET_MARKUP_PERCENT", "15") or 15))
MARKET_PRODUCTS_PAGE_SIZE = int(os.getenv("MARKET_PRODUCTS_PAGE_SIZE", "6") or 6)
MARKET_SITE_DIR = Path(__file__).resolve().parent / "market"
MARKET_SITE_INDEX_PATH = MARKET_SITE_DIR / "index.html"
MARKET_SESSION_COOKIE = "sous_market_session"
MINIAPP_STATIC_DIR = Path(__file__).resolve().parent / "miniapp_static"
MINIAPP_RULES_TEXT = (
    "1. Все покупки проверяйте сразу после выдачи.\n"
    "2. Возврат возможен только при проблеме с выдачей товара.\n"
    "3. Перед оплатой внимательно проверяйте категорию и количество.\n"
    "4. Поддержка отвечает через текущего бота или франшизу."
)
MINIAPP_MARKET_ORDER_POLL_ATTEMPTS = int(os.getenv("MARKET_ORDER_POLL_ATTEMPTS", "300") or 300)
MINIAPP_MARKET_ORDER_POLL_INTERVAL_SECONDS = max(0.25, float(os.getenv("MARKET_ORDER_POLL_INTERVAL_SECONDS", "0.5") or 0.5))
MINIAPP_TOPUP_POLL_INTERVAL_SECONDS = max(1.0, float(os.getenv("MINIAPP_TOPUP_POLL_INTERVAL_SECONDS", "2") or 2))
MINIAPP_TOPUP_POLL_ATTEMPTS = max(1, int(os.getenv("MINIAPP_TOPUP_POLL_ATTEMPTS", "900") or 900))
MINIAPP_INDEX_TEMPLATE_PATH = MINIAPP_STATIC_DIR / "index.html"
MINIAPP_STYLES_PATH = MINIAPP_STATIC_DIR / "styles.css"
MINIAPP_APP_JS_PATH = MINIAPP_STATIC_DIR / "app.js"
CRM_STATIC_DIR = Path(__file__).resolve().parent / "crm_static"
CRM_INDEX_TEMPLATE_PATH = CRM_STATIC_DIR / "index.html"
CRM_STYLES_PATH = CRM_STATIC_DIR / "styles.css"
CRM_APP_JS_PATH = CRM_STATIC_DIR / "app.js"
PROXY_STATIC_DIR = Path(__file__).resolve().parent / "proxy_static"
PROXY_INDEX_TEMPLATE_PATH = PROXY_STATIC_DIR / "index.html"
PROXY_STYLES_PATH = PROXY_STATIC_DIR / "styles.css"
PROXY_APP_JS_PATH = PROXY_STATIC_DIR / "app.js"
EMAIL_STATIC_DIR = Path(__file__).resolve().parent / "email_static"
EMAIL_INDEX_TEMPLATE_PATH = EMAIL_STATIC_DIR / "index.html"
EMAIL_STYLES_PATH = EMAIL_STATIC_DIR / "styles.css"
EMAIL_APP_JS_PATH = EMAIL_STATIC_DIR / "app.js"
SMS_STATIC_DIR = Path(__file__).resolve().parent / "sms_static"
PARTNER_STATIC_DIR = Path(__file__).resolve().parent / "partner_static"
PARTNER_INDEX_PATH = PARTNER_STATIC_DIR / "index.html"
PARTNER_STYLES_PATH = PARTNER_STATIC_DIR / "styles.css"
PARTNER_APP_JS_PATH = PARTNER_STATIC_DIR / "app.js"
PARTNER_REFERENCE_BOTS_DIR = PARTNER_STATIC_DIR / "reference" / "bots"
SMS_INDEX_TEMPLATE_PATH = SMS_STATIC_DIR / "index.html"
SMS_STYLES_PATH = SMS_STATIC_DIR / "styles.css"
SMS_APP_JS_PATH = SMS_STATIC_DIR / "app.js"
SALES_LOG_CHANNEL_ID = int(os.getenv("SALES_LOG_CHANNEL_ID", "-1003983969799") or -1003983969799)
SALES_LOG_HEADER = os.getenv("SALES_LOG_HEADER", "🏷 [LOG] SOUS FRAN").strip() or "🏷 [LOG] SOUS FRAN"
MINIAPP_ENTRY_COMMANDS = (
    ("start", "Главное меню"),
    ("admin", "Админ-панель"),
    ("money", "Реферальный конкурс"),
)


@dataclass
class MiniAppBotContext:
    bot_token: str
    bot_username: str | None
    brand_name: str
    brand_badge: str
    brand_avatar_url: str | None
    subtitle: str | None
    support_url: str | None
    is_partner: bool
    partner_bot: dict | None
    margin_percentage: int
    referral_percent: float


def can_configure_miniapp_menu() -> bool:
    return bool(MINIAPP_BASE_URL)


def normalize_bot_slug(bot_username: str | None) -> str:
    normalized = (bot_username or "main").strip().lstrip("@").lower()
    return normalized or "main"


def build_miniapp_url(bot_username: str | None) -> str | None:
    if not MINIAPP_BASE_URL:
        return None

    bot_slug = normalize_bot_slug(bot_username)
    return f"{MINIAPP_BASE_URL}/miniapp/{bot_slug}/"


def build_partner_url(bot_username: str | None) -> str | None:
    base_url = MINIAPP_PARTNER_BASE_URL or MINIAPP_BASE_URL
    if not base_url:
        return None
    return f"{base_url}/partner/{normalize_bot_slug(bot_username)}/"


def build_proxy_url(bot_username: str | None) -> str | None:
    if not MINIAPP_BASE_URL:
        return None

    bot_slug = normalize_bot_slug(bot_username)
    return f"{MINIAPP_BASE_URL}/proxy/{bot_slug}/"


def build_email_url(bot_username: str | None) -> str | None:
    if not MINIAPP_BASE_URL:
        return None

    bot_slug = normalize_bot_slug(bot_username)
    return f"{MINIAPP_BASE_URL}/email/{bot_slug}/"


def build_sms_url(bot_username: str | None) -> str | None:
    if not MINIAPP_BASE_URL:
        return None
    bot_slug = normalize_bot_slug(bot_username)
    return f"{MINIAPP_BASE_URL}/sms/{bot_slug}/"


def build_crm_url() -> str | None:
    if not MINIAPP_BASE_URL:
        return None
    return f"{MINIAPP_BASE_URL}/crm/"


def _clean_brand_piece(value: str) -> str:
    normalized = re.sub(r"(?<=[a-zа-я])(?=[A-ZА-Я])", " ", value)
    normalized = re.sub(r"[_\-]+", " ", normalized)
    normalized = re.sub(r"market$", " market", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def format_brand_name_from_username(bot_username: str | None) -> str:
    raw = (bot_username or "").strip().lstrip("@")
    if not raw:
        return MINIAPP_MAIN_BRAND_NAME

    raw = re.sub(r"bot$", "", raw, flags=re.IGNORECASE)
    raw = _clean_brand_piece(raw)
    if not raw:
        return MINIAPP_MAIN_BRAND_NAME
    return raw.upper()


def build_brand_badge(brand_name: str) -> str:
    letters = [chunk[0] for chunk in brand_name.split() if chunk]
    if not letters:
        return "SM"
    return "".join(letters[:2]).upper()


def get_partner_shop_markup(partner_bot: dict | None) -> int:
    if partner_bot is None:
        return MARKET_MARKUP_PERCENT

    raw_value = partner_bot.get("shop_markup_percentage")
    if raw_value is None or raw_value == "":
        return MARKET_MARKUP_PERCENT

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


def calculate_sale_price_from_base_usdt(base_price_usdt: float, margin_percentage: int) -> float:
    raw_amount = float(base_price_usdt or 0.0) * (1 + max(margin_percentage, 0) / 100)
    if raw_amount <= 0:
        return 0.0
    cents = int(raw_amount * 100)
    if cents < raw_amount * 100:
        cents += 1
    return max(cents / 100, 0.01)


def convert_rub_to_usdt(amount_rub: float, margin_percentage: int) -> float:
    rate = get_cached_market_rub_per_usdt()
    base_amount = float(amount_rub or 0.0) / rate
    return calculate_sale_price_from_base_usdt(base_amount, margin_percentage)


def calculate_email_sale_price(supplier_price_usd: float, markup_percent: float | None = None) -> float:
    base = Decimal(str(max(float(supplier_price_usd or 0.0), 0.0)))
    effective_markup = ANYMESSAGE_MARKUP_PERCENT if markup_percent is None else max(float(markup_percent), 0.0)
    multiplier = Decimal("1") + (Decimal(str(effective_markup)) / Decimal("100"))
    return float((base * multiplier).quantize(Decimal("0.000001"), rounding=ROUND_UP))


def calculate_partner_profit_share_amount(
    purchase_unit_price: float,
    quantity: int,
    margin_percentage: int,
) -> float:
    clean_profit_usdt = float(purchase_unit_price or 0.0) * max(margin_percentage, 0) / 100 * max(quantity, 0)
    return round(clean_profit_usdt, 2)


def clean_html_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value or ""))).strip()


def extract_email_message_links(value: str) -> list[str]:
    source = html.unescape(str(value or ""))
    candidates = re.findall(r"href\s*=\s*['\"]([^'\"]+)['\"]", source, flags=re.IGNORECASE)
    candidates.extend(re.findall(r"https?://[^\s<>\"']+", source, flags=re.IGNORECASE))
    links: list[str] = []
    for candidate in candidates:
        normalized = str(candidate or "").strip().rstrip(".,);]")
        if normalized.lower().startswith(("http://", "https://")) and normalized not in links:
            links.append(normalized)
    return links[:10]


def get_order_status_label(status: str) -> str:
    labels = {
        "draft": "Черновик",
        "waiting_payment": "Ожидает оплату",
        "paid": "Оплачен",
        "delivery_pending": "Ожидание выдачи",
        "delivered": "Выдан",
        "credited": "Возврат на баланс",
        "proxy_balance_error": "Ошибка поставщика",
        "delivery_error": "Ошибка выдачи",
    }
    return labels.get(status, status)


def wrap_sales_log(text: str) -> str:
    return f"{SALES_LOG_HEADER}\n\n{text}"


def split_sales_log_message(text: str, chunk_size: int = 3500) -> list[str]:
    wrapped_text = wrap_sales_log(text)
    if len(wrapped_text) <= chunk_size:
        return [wrapped_text]
    return [wrapped_text[index:index + chunk_size] for index in range(0, len(wrapped_text), chunk_size)]


def _build_data_check_string(items: dict[str, str]) -> str:
    return "\n".join(f"{key}={value}" for key, value in sorted(items.items()))


def verify_telegram_webapp_init_data(init_data: str, bot_token: str) -> dict[str, Any]:
    parsed_items = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed_items.pop("hash", "")
    if not received_hash:
        raise web.HTTPUnauthorized(text=json.dumps({"error": "Missing initData hash"}), content_type="application/json")

    data_check_string = _build_data_check_string(parsed_items)
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated_hash, received_hash):
        raise web.HTTPUnauthorized(text=json.dumps({"error": "Invalid initData hash"}), content_type="application/json")

    user_raw = parsed_items.get("user")
    if not user_raw:
        raise web.HTTPUnauthorized(text=json.dumps({"error": "User payload missing"}), content_type="application/json")

    try:
        user_payload = json.loads(user_raw)
    except json.JSONDecodeError as error:
        raise web.HTTPUnauthorized(
            text=json.dumps({"error": "Invalid user payload"}),
            content_type="application/json",
        ) from error

    auth_date_raw = parsed_items.get("auth_date")
    try:
        auth_date = int(auth_date_raw or 0)
    except (TypeError, ValueError) as error:
        raise web.HTTPUnauthorized(
            text=json.dumps({"error": "Invalid auth_date"}),
            content_type="application/json",
        ) from error

    now_timestamp = int(time.time())
    if auth_date <= 0 or auth_date > now_timestamp + 300:
        raise web.HTTPUnauthorized(
            text=json.dumps({"error": "Invalid auth_date timestamp"}),
            content_type="application/json",
        )
    if MINIAPP_INITDATA_MAX_AGE_SECONDS > 0 and now_timestamp - auth_date > MINIAPP_INITDATA_MAX_AGE_SECONDS:
        raise web.HTTPUnauthorized(
            text=json.dumps({"error": "Telegram initData expired. Reopen the mini app from the bot."}),
            content_type="application/json",
        )

    return {
        "user": user_payload,
        "auth_date": str(auth_date),
        "query_id": parsed_items.get("query_id"),
    }


class MiniAppServer:
    def __init__(self, source_bot: Bot):
        self.source_bot = source_bot
        self.main_bot_username: str | None = None
        self._order_fulfillment_locks: dict[int, asyncio.Lock] = {}
        self._market_order_tasks: dict[int, asyncio.Task] = {}
        self._topup_payment_tasks: dict[int, asyncio.Task] = {}
        self._api_proxy_locks: dict[tuple[int, str], asyncio.Lock] = {}
        self._bot_avatar_cache: dict[str, tuple[float, bytes | None, str | None]] = {}
        self._miniapp_log_cooldowns: dict[tuple[str, str, int], float] = {}

    async def startup(self) -> None:
        init_sms_db()
        init_favorites_db()
        me = await self.source_bot.get_me()
        self.main_bot_username = me.username or None
        await ensure_miniapp_commands(self.source_bot)
        await configure_bot_menu_button(self.source_bot, self.main_bot_username, MINIAPP_MAIN_BRAND_NAME)

    async def _load_bot_avatar(
        self,
        bot_token: str,
        bot_username: str | None,
    ) -> tuple[bytes | None, str | None]:
        cache_key = normalize_bot_slug(bot_username)
        cached_entry = self._bot_avatar_cache.get(cache_key)
        now = time.time()
        if cached_entry and cached_entry[0] > now:
            return cached_entry[1], cached_entry[2]

        target_bot: Bot | None = None
        should_close = False
        avatar_data: bytes | None = None
        content_type: str | None = None
        try:
            if bot_token == self.source_bot.token:
                target_bot = self.source_bot
            else:
                target_bot = Bot(token=bot_token)
                should_close = True

            me = await target_bot.get_me()
            photos = await target_bot.get_user_profile_photos(me.id, limit=1)
            if photos.total_count and photos.photos:
                file_id = photos.photos[0][-1].file_id
                file = await target_bot.get_file(file_id)
                if file.file_path:
                    destination = io.BytesIO()
                    await target_bot.download(file, destination=destination)
                    avatar_data = destination.getvalue() or None
                    content_type = mimetypes.guess_type(file.file_path)[0] or "image/jpeg"
        except Exception:
            avatar_data = None
            content_type = None
        finally:
            if should_close and target_bot is not None:
                await target_bot.session.close()

        self._bot_avatar_cache[cache_key] = (now + 900, avatar_data, content_type)
        return avatar_data, content_type

    async def _resolve_bot_avatar_url(self, bot_token: str, bot_username: str | None) -> str | None:
        avatar_data, _ = await self._load_bot_avatar(bot_token, bot_username)
        if not avatar_data:
            return None
        return f"/miniapp/{normalize_bot_slug(bot_username)}/avatar"

    async def handle_bot_avatar(self, request: web.Request) -> web.Response:
        bot_slug = normalize_bot_slug(request.match_info.get("bot_slug"))
        main_username = normalize_bot_slug(self.main_bot_username)

        if bot_slug in {"main", main_username}:
            bot_token = self.source_bot.token
            bot_username = self.main_bot_username
        else:
            partner_bot = get_partner_bot_by_username(bot_slug)
            if partner_bot is None:
                raise web.HTTPNotFound()
            bot_token = str(partner_bot["bot_token"])
            bot_username = str(partner_bot.get("bot_username") or bot_slug)

        avatar_data, content_type = await self._load_bot_avatar(bot_token, bot_username)
        if not avatar_data:
            raise web.HTTPNotFound()

        return web.Response(
            body=avatar_data,
            content_type=content_type or "image/jpeg",
            headers={
                "Cache-Control": "public, max-age=900",
                "X-Content-Type-Options": "nosniff",
            },
        )

    async def resolve_bot_context(self, bot_slug: str | None) -> MiniAppBotContext:
        normalized_slug = normalize_bot_slug(bot_slug)
        main_username = (self.main_bot_username or "").strip().lower()

        if not normalized_slug or normalized_slug == "main" or (main_username and normalized_slug == main_username):
            return MiniAppBotContext(
                bot_token=self.source_bot.token,
                bot_username=self.main_bot_username,
                brand_name=MINIAPP_MAIN_BRAND_NAME,
                brand_badge=build_brand_badge(MINIAPP_MAIN_BRAND_NAME),
                brand_avatar_url=await self._resolve_bot_avatar_url(self.source_bot.token, self.main_bot_username),
                subtitle=(f"@{self.main_bot_username}" if self.main_bot_username else None),
                support_url=MINIAPP_SUPPORT_URL,
                is_partner=False,
                partner_bot=None,
                margin_percentage=MARKET_MARKUP_PERCENT,
                referral_percent=REFERRAL_PERCENT,
            )

        partner_bot = get_partner_bot_by_username(normalized_slug)
        if partner_bot is None:
            raise web.HTTPNotFound(
                text=json.dumps({"error": "Bot context not found"}),
                content_type="application/json",
            )

        bot_username = partner_bot.get("bot_username") or normalized_slug
        brand_name = format_brand_name_from_username(bot_username)
        return MiniAppBotContext(
            bot_token=partner_bot["bot_token"],
            bot_username=bot_username,
            brand_name=brand_name,
            brand_badge=build_brand_badge(brand_name),
            brand_avatar_url=await self._resolve_bot_avatar_url(partner_bot["bot_token"], bot_username),
            subtitle=f"@{bot_username}",
            support_url=MINIAPP_SUPPORT_URL,
            is_partner=True,
            partner_bot=partner_bot,
                margin_percentage=get_partner_shop_markup(partner_bot) + get_partner_category_markup(partner_bot, "goods"),
            referral_percent=(
                float(partner_bot.get("referral_percent") or REFERRAL_PERCENT)
                if int(partner_bot.get("referral_enabled") or 0) == 1
                else 0.0
            ),
        )

    def get_request_bot_slug(self, request: web.Request) -> str | None:
        path_slug = request.match_info.get("bot_slug")
        if path_slug:
            return normalize_bot_slug(path_slug)
        legacy_slug = request.query.get("bot")
        if legacy_slug:
            return normalize_bot_slug(legacy_slug)
        return self.main_bot_username

    def can_use_unsafe_dev_auth(self, request: web.Request) -> bool:
        if not MINIAPP_ALLOW_UNSAFE_DEV_AUTH or MINIAPP_DEV_USER_ID <= 0:
            return False

        host = (request.host or "").split(":", 1)[0].lower()
        request_ip = (request.remote or "").strip().lower()
        if host not in {"127.0.0.1", "localhost"}:
            return False
        return request_ip in {"127.0.0.1", "::1", "localhost", ""}

    def _format_shop_name(self, bot_context: MiniAppBotContext) -> str:
        username = (bot_context.bot_username or "").strip()
        if username:
            return f"{bot_context.brand_name} (@{username})"
        return bot_context.brand_name

    def _format_user_label(self, user_payload: dict[str, Any], user_id: int) -> str:
        username = str(user_payload.get("username") or "").strip()
        if username:
            return f"@{username}"

        first_name = str(user_payload.get("first_name") or "").strip()
        last_name = str(user_payload.get("last_name") or "").strip()
        full_name = " ".join(part for part in (first_name, last_name) if part).strip()
        if full_name:
            return full_name

        return f"ID {user_id}"

    def _extract_request_ip(self, request: web.Request) -> str:
        forwarded_for = (request.headers.get("X-Forwarded-For") or "").strip()
        if forwarded_for:
            return forwarded_for.split(",", 1)[0].strip()
        real_ip = (request.headers.get("X-Real-IP") or "").strip()
        if real_ip:
            return real_ip
        return str(request.remote or "-")

    def _should_skip_miniapp_log(self, event_key: tuple[str, str, int], cooldown_seconds: int) -> bool:
        if cooldown_seconds <= 0:
            return False

        now = time.time()
        expires_at = self._miniapp_log_cooldowns.get(event_key, 0.0)
        if expires_at > now:
            return True

        self._miniapp_log_cooldowns[event_key] = now + cooldown_seconds
        if len(self._miniapp_log_cooldowns) > 2048:
            self._miniapp_log_cooldowns = {
                key: value for key, value in self._miniapp_log_cooldowns.items() if value > now
            }
        return False

    async def _send_sales_log_message(self, event_id: int, text: str) -> None:
        if SALES_LOG_CHANNEL_ID == 0:
            mark_sales_log_event_failed(event_id, "Sales log skipped: SALES_LOG_CHANNEL_ID is 0")
            return

        try:
            last_message_id: int | None = None
            for chunk in split_sales_log_message(text):
                sent_message = await self.source_bot.send_message(SALES_LOG_CHANNEL_ID, chunk)
                last_message_id = getattr(sent_message, "message_id", None)
            mark_sales_log_event_sent(event_id, last_message_id)
        except Exception as error:
            mark_sales_log_event_failed(event_id, str(error))
            print(f"[miniapp-log] failed to send log event #{event_id}: {error}")

    async def _log_miniapp_user_event(
        self,
        *,
        request: web.Request,
        bot_context: MiniAppBotContext,
        user_payload: dict[str, Any],
        profile: dict[str, Any],
        event_label: str,
        app_scope: str,
        extra_lines: list[str] | None = None,
        cooldown_seconds: int = 0,
    ) -> None:
        user_id = int(profile.get("user_id") or user_payload.get("id") or 0)
        bot_slug = normalize_bot_slug(bot_context.bot_username)
        event_key = (app_scope, bot_slug, user_id)
        if self._should_skip_miniapp_log(event_key, cooldown_seconds):
            return

        lines = [
            f"🏪 Шоп: {self._format_shop_name(bot_context)}",
            "📱 Лог mini app",
            f"📌 Событие: {event_label}",
            f"🧩 Раздел: {app_scope}",
            f"👤 Пользователь: {self._format_user_label(user_payload, user_id)}",
            f"🆔 User ID: {user_id}",
            f"🤝 Партнерский бот: {'Да' if bot_context.is_partner else 'Нет'}",
            f"🌐 IP: {self._extract_request_ip(request)}",
            f"🖥 User-Agent: {str(request.headers.get('User-Agent') or '-').strip()[:500]}",
            # Do not copy query strings into sales logs: they can contain
            # credentials accidentally supplied by an API client.
            f"🛣 Путь: {request.path}",
        ]
        if extra_lines:
            lines.extend([line for line in extra_lines if line])
        event_text = "\n".join(lines)
        event_id = create_sales_log_event(
            "miniapp",
            event_text,
            user_id=user_id,
            shop_name=self._format_shop_name(bot_context),
            event_label=event_label,
        )
        await self._send_sales_log_message(event_id, event_text)

    def _schedule_miniapp_user_event_log(
        self,
        *,
        request: web.Request,
        bot_context: MiniAppBotContext,
        user_payload: dict[str, Any],
        profile: dict[str, Any],
        event_label: str,
        app_scope: str,
        extra_lines: list[str] | None = None,
        cooldown_seconds: int = 0,
    ) -> None:
        asyncio.create_task(
            self._log_miniapp_user_event(
                request=request,
                bot_context=bot_context,
                user_payload=user_payload,
                profile=profile,
                event_label=event_label,
                app_scope=app_scope,
                extra_lines=extra_lines,
                cooldown_seconds=cooldown_seconds,
            ),
            name=f"miniapp-user-log-{app_scope}-{int(profile.get('user_id') or user_payload.get('id') or 0)}",
        )

    def _get_promocode_error_message(self, error_code: str | None) -> str:
        messages = {
            "invalid_code": "Введите корректный промокод.",
            "user_not_found": "Пользователь не найден.",
            "promo_not_found": "Промокод не найден или уже отключён.",
            "limit_reached": "У этого промокода закончился лимит активаций.",
            "already_used": "Вы уже активировали этот промокод.",
            "discount_already_active": "У вас уже есть активная скидка по другому промокоду.",
            "invalid_type": "У этого промокода неподдерживаемый тип награды.",
        }
        return messages.get(error_code or "", "Не удалось активировать промокод.")

    async def _handle_promocode_activation(self, request: web.Request, app_scope: str) -> web.Response:
        bot_context, user_payload, profile = await self._resolve_request_context(request)
        try:
            payload = await request.json()
        except json.JSONDecodeError as error:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "Invalid JSON payload"}),
                content_type="application/json",
            ) from error

        code = str(payload.get("code") or "").strip()
        activation_result, error_code = activate_promo_code(int(profile["user_id"]), code)
        if error_code is not None or activation_result is None:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": self._get_promocode_error_message(error_code)}),
                content_type="application/json",
            )

        promo_code = activation_result.get("promo_code") or {}
        reward_type = str(promo_code.get("reward_type") or "").strip().lower()
        reward_value = round(float(promo_code.get("reward_value") or 0.0), 2)
        updated_profile = get_user_profile(int(profile["user_id"])) or profile

        if reward_type == "balance":
            success_message = f"Промокод {promo_code.get('code') or code} активирован. Баланс пополнен на {reward_value:.2f} $."
        else:
            success_message = (
                f"Промокод {promo_code.get('code') or code} активирован. "
                f"Скидка {reward_value:.2f}% применится к следующему заказу."
            )

        self._schedule_miniapp_user_event_log(
            request=request,
            bot_context=bot_context,
            user_payload=user_payload,
            profile=updated_profile,
            event_label="Активация промокода",
            app_scope=app_scope,
            extra_lines=[
                f"🎟 Код: {promo_code.get('code') or code}",
                f"🎁 Тип: {'Баланс' if reward_type == 'balance' else 'Скидка'}",
                f"💰 Значение: {reward_value:.2f}{' $' if reward_type == 'balance' else '%'}",
            ],
        )
        return web.json_response(
            {
                "ok": True,
                "message": success_message,
                "profile": self._serialize_profile(updated_profile, bot_context),
                "promo_code": {
                    "code": promo_code.get("code") or code,
                    "reward_type": reward_type,
                    "reward_value": reward_value,
                },
            }
        )

    async def _resolve_request_context(self, request: web.Request) -> tuple[MiniAppBotContext, dict, dict]:
        bot_context = await self.resolve_bot_context(self.get_request_bot_slug(request))
        if request.path.startswith("/market/api/"):
            web_account = get_web_account_by_session(request.cookies.get(MARKET_SESSION_COOKIE, ""))
            if web_account is not None:
                web_user_id = int(web_account["user_id"])
                profile = get_user_profile(web_user_id)
                if profile is None:
                    raise web.HTTPUnauthorized(
                        text=json.dumps({"error": "Web account not found"}),
                        content_type="application/json",
                    )
                web_context = MiniAppBotContext(
                    bot_token=bot_context.bot_token,
                    bot_username="__market_site__",
                    brand_name=bot_context.brand_name,
                    brand_badge=bot_context.brand_badge,
                    brand_avatar_url=bot_context.brand_avatar_url,
                    subtitle=bot_context.subtitle,
                    support_url=bot_context.support_url,
                    is_partner=False,
                    partner_bot=None,
                    margin_percentage=int(PUBLIC_API_MARKUP_PERCENT),
                    referral_percent=0.0,
                )
                return web_context, {
                    "id": web_user_id,
                    "owner_id": web_user_id,
                    "username": web_account.get("username") or "market_user",
                    "web_account_id": int(web_account["id"]),
                }, profile
            init_data = request.headers.get("X-Telegram-Init-Data", "").strip()
            if init_data:
                auth_payload = verify_telegram_webapp_init_data(init_data, self.source_bot.token)
                user_payload = auth_payload["user"]
                owner_user_id = int(user_payload["id"])
                api_account = get_api_account(owner_user_id)
                if api_account is None or str(api_account.get("status") or "") != "approved":
                    raise web.HTTPForbidden(
                        text=json.dumps({"error": "Доступ к API не одобрен"}),
                        content_type="application/json",
                    )
                api_user_id = int(api_account.get("api_user_id") or 0)
                profile = get_user_profile(api_user_id)
                if api_user_id == 0 or profile is None:
                    raise web.HTTPUnauthorized(
                        text=json.dumps({"error": "API account not found"}),
                        content_type="application/json",
                    )
                api_context = MiniAppBotContext(
                    bot_token=bot_context.bot_token,
                    bot_username="__market_webapp__",
                    brand_name=bot_context.brand_name,
                    brand_badge=bot_context.brand_badge,
                    brand_avatar_url=bot_context.brand_avatar_url,
                    subtitle=bot_context.subtitle,
                    support_url=bot_context.support_url,
                    is_partner=False,
                    partner_bot=None,
                    margin_percentage=int(PUBLIC_API_MARKUP_PERCENT),
                    referral_percent=0.0,
                )
                return api_context, {**user_payload, "owner_id": owner_user_id}, profile

        if request.path.startswith("/api/v1/") or request.path.startswith("/market/api/"):
            authorization = request.headers.get("Authorization", "").strip()
            supplied_key = request.headers.get("X-API-Key", "").strip()
            if authorization.lower().startswith("bearer "):
                supplied_key = authorization[7:].strip()
            api_account = get_api_account_by_key(supplied_key) if supplied_key else None
            if api_account is None:
                raise web.HTTPUnauthorized(
                    text=json.dumps({"error": "Invalid API key"}),
                    content_type="application/json",
                )
            api_user_id = int(api_account.get("api_user_id") or 0)
            profile = get_user_profile(api_user_id)
            if profile is None:
                raise web.HTTPUnauthorized(
                    text=json.dumps({"error": "API account not found"}),
                    content_type="application/json",
                )
            api_context = MiniAppBotContext(
                bot_token=bot_context.bot_token,
                bot_username="__public_api__",
                brand_name=bot_context.brand_name,
                brand_badge=bot_context.brand_badge,
                brand_avatar_url=bot_context.brand_avatar_url,
                subtitle=bot_context.subtitle,
                support_url="https://t.me/UniversallSupportBot?start=bot",
                is_partner=False,
                partner_bot=None,
                margin_percentage=int(PUBLIC_API_MARKUP_PERCENT),
                referral_percent=0.0,
            )
            return api_context, {"id": api_user_id, "owner_id": int(api_account["user_id"]), "username": "api_client"}, profile
        init_data = request.headers.get("X-Telegram-Init-Data", "").strip()

        if not init_data and self.can_use_unsafe_dev_auth(request):
            add_user(MINIAPP_DEV_USER_ID)
            profile = get_user_profile(MINIAPP_DEV_USER_ID)
            return (
                bot_context,
                {"id": MINIAPP_DEV_USER_ID, "username": "dev_user", "first_name": "Dev"},
                profile or {},
            )

        if not init_data:
            raise web.HTTPUnauthorized(
                text=json.dumps({"error": "Telegram initData required"}),
                content_type="application/json",
            )

        auth_payload = verify_telegram_webapp_init_data(init_data, bot_context.bot_token)
        user_payload = auth_payload["user"]
        user_id = int(user_payload["id"])
        add_user(user_id)
        profile = get_user_profile(user_id)
        if profile is None:
            raise web.HTTPUnauthorized(
                text=json.dumps({"error": "User profile not found"}),
                content_type="application/json",
            )
        # A franchise cabinet is opened from SousPartnersBot, while the
        # dashboard belongs to the user's connected bot. Telegram signs the
        # initData with the launcher token, so switch the data context only
        # after validating that signature.
        cabinet_slug = str(request.query.get("cabinet_bot") or "").strip()
        if (
            cabinet_slug
            and bot_context.partner_bot is not None
            and str(bot_context.bot_username or "").casefold() == "souspartnersbot"
        ):
            target_context = await self.resolve_bot_context(cabinet_slug)
            if target_context.partner_bot is not None:
                bot_context = target_context
        return bot_context, user_payload, profile

    async def _resolve_admin_request_context(self, request: web.Request) -> tuple[dict, dict]:
        init_data = request.headers.get("X-Telegram-Init-Data", "").strip()

        if CRM_ADMIN_ID <= 0:
            raise web.HTTPServiceUnavailable(
                text=json.dumps({"error": "CRM admin is not configured"}),
                content_type="application/json",
            )

        if not init_data and self.can_use_unsafe_dev_auth(request):
            add_user(MINIAPP_DEV_USER_ID)
            profile = get_user_profile(MINIAPP_DEV_USER_ID)
            if MINIAPP_DEV_USER_ID != CRM_ADMIN_ID or profile is None:
                raise web.HTTPForbidden(
                    text=json.dumps({"error": "Access denied"}),
                    content_type="application/json",
                )
            return (
                {"id": MINIAPP_DEV_USER_ID, "username": "dev_user", "first_name": "Dev"},
                profile,
            )

        if not init_data:
            raise web.HTTPUnauthorized(
                text=json.dumps({"error": "Telegram initData required"}),
                content_type="application/json",
            )

        auth_payload = verify_telegram_webapp_init_data(init_data, self.source_bot.token)
        user_payload = auth_payload["user"]
        user_id = int(user_payload["id"])
        if user_id != CRM_ADMIN_ID:
            raise web.HTTPForbidden(
                text=json.dumps({"error": "Access denied"}),
                content_type="application/json",
            )

        add_user(user_id)
        profile = get_user_profile(user_id)
        if profile is None:
            raise web.HTTPUnauthorized(
                text=json.dumps({"error": "User profile not found"}),
                content_type="application/json",
            )
        return user_payload, profile

    @web.middleware
    async def security_headers_middleware(self, request: web.Request, handler):
        response = await handler(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        if request.path.startswith("/market"):
            response.headers.setdefault("Cache-Control", "no-store, no-cache, must-revalidate, private")
        if (
            request.path.startswith("/miniapp/")
            or request.path.startswith("/proxy/")
            or request.path.startswith("/email/")
            or request.path.startswith("/sms/")
            or request.path.startswith("/crm/")
        ):
            response.headers.setdefault(
                "Content-Security-Policy",
                (
                    "default-src 'self'; "
                    "script-src 'self' https://telegram.org https://*.telegram.org; "
                    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                    "font-src 'self' https://fonts.gstatic.com; "
                    "img-src 'self' data: https:; "
                    "connect-src 'self'; "
                    "frame-src https://telegram.org https://*.telegram.org; "
                    "frame-ancestors 'none'; base-uri 'none'; form-action 'none'; object-src 'none'"
                ),
            )
            if (
                "/api/" in request.path
                or request.path.endswith("/miniapp/")
                or request.path.endswith("/proxy/")
                or request.path.endswith("/email/")
                or request.path.endswith("/sms/")
                or request.path.endswith("/crm/")
                or request.path.count("/miniapp/") == 1
                or request.path.count("/proxy/") == 1
                or request.path.count("/email/") == 1
                or request.path.count("/sms/") == 1
                or request.path.count("/crm/") == 1
            ):
                response.headers.setdefault("Cache-Control", "no-store, no-cache, must-revalidate, private")
        return response

    @web.middleware
    async def safe_error_middleware(self, request: web.Request, handler):
        """Never return framework/provider tracebacks to a public client."""
        try:
            return await handler(request)
        except web.HTTPException:
            raise
        except Exception:
            logger.exception("Unhandled miniapp request error: %s %s", request.method, request.path)
            return web.json_response({"error": "Internal server error"}, status=500)

    async def handle_market_site_index(self, _: web.Request) -> web.Response:
        """Serve the standalone web storefront from the repository root."""
        if not MARKET_SITE_INDEX_PATH.is_file():
            raise web.HTTPNotFound(text="Market site is not installed")
        bot_username = (self.main_bot_username or "MarketPlaceABot").strip().lstrip("@")
        return web.Response(
            text=MARKET_SITE_INDEX_PATH.read_text(encoding="utf-8").replace(
                "__MARKET_BOT_USERNAME__", bot_username
            ),
            content_type="text/html",
            charset="utf-8",
        )

    async def handle_market_site_session(self, request: web.Request) -> web.Response:
        """Validate an API key without ever placing it in a URL or cookie."""
        _, user_payload, profile = await self._resolve_request_context(request)
        return web.json_response(
            {
                "user_id": int(user_payload.get("owner_id") or user_payload["id"]),
                "username": str(user_payload.get("username") or ""),
                "balance": round(float(profile.get("balance") or 0.0), 6),
                "currency": "USDT",
            }
        )

    @staticmethod
    def _set_market_session_cookie(response: web.StreamResponse, raw_token: str) -> None:
        response.set_cookie(
            MARKET_SESSION_COOKIE,
            raw_token,
            max_age=30 * 24 * 60 * 60,
            httponly=True,
            secure=True,
            samesite="Lax",
            path="/",
        )

    async def handle_market_site_register(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except json.JSONDecodeError as error:
            raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid JSON"}), content_type="application/json") from error
        email = str(body.get("email") or "").strip().casefold()
        username = str(body.get("username") or "").strip()
        password = str(body.get("password") or "")
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or len(email) > 254:
            raise web.HTTPBadRequest(text=json.dumps({"error": "Введите корректную почту"}), content_type="application/json")
        if not 2 <= len(username) <= 40:
            raise web.HTTPBadRequest(text=json.dumps({"error": "Имя должно содержать 2–40 символов"}), content_type="application/json")
        if len(password) < 8 or len(password) > 200:
            raise web.HTTPBadRequest(text=json.dumps({"error": "Пароль должен содержать минимум 8 символов"}), content_type="application/json")
        account, error_code = create_web_account(email, username, password)
        if account is None:
            raise web.HTTPConflict(text=json.dumps({"error": "Аккаунт с такой почтой уже существует"}), content_type="application/json")
        raw_token = create_web_session(int(account["id"]))
        profile = get_user_profile(int(account["user_id"])) or {}
        response = web.json_response({
            "user_id": int(account["user_id"]), "username": account["username"],
            "email": account["email"], "balance": round(float(profile.get("balance") or 0.0), 6), "currency": "USDT",
        })
        self._set_market_session_cookie(response, raw_token)
        return response

    async def handle_market_site_login(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except json.JSONDecodeError as error:
            raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid JSON"}), content_type="application/json") from error
        account = authenticate_web_account(str(body.get("email") or ""), str(body.get("password") or ""))
        if account is None:
            raise web.HTTPUnauthorized(text=json.dumps({"error": "Неверная почта или пароль"}), content_type="application/json")
        raw_token = create_web_session(int(account["id"]))
        profile = get_user_profile(int(account["user_id"])) or {}
        response = web.json_response({
            "user_id": int(account["user_id"]), "username": account["username"],
            "email": account["email"], "balance": round(float(profile.get("balance") or 0.0), 6), "currency": "USDT",
        })
        self._set_market_session_cookie(response, raw_token)
        return response

    async def handle_market_site_logout(self, request: web.Request) -> web.Response:
        delete_web_session(request.cookies.get(MARKET_SESSION_COOKIE, ""))
        response = web.json_response({"ok": True})
        response.del_cookie(MARKET_SESSION_COOKIE, path="/")
        return response

    async def handle_market_site_categories(self, request: web.Request) -> web.Response:
        try:
            categories = await get_market_categories()
        except MarketProviderError as error:
            raise web.HTTPBadGateway(
                text=json.dumps({"error": "Не удалось загрузить категории"}),
                content_type="application/json",
            ) from error
        return web.json_response({"items": categories})

    async def handle_market_site_products(self, request: web.Request) -> web.Response:
        """Public Djekxa catalogue endpoint for the standalone web market."""
        category_id = int(request.query.get("category_id", "0") or 0)
        page = max(int(request.query.get("page", "1") or 1), 1)
        if category_id <= 0:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "category_id is required"}),
                content_type="application/json",
            )
        payload = await get_market_products(category_id, page=page, per_page=100)
        items = []
        for product in payload.get("items", []):
            items.append(
                {
                    "id": int(product["id"]),
                    "title": product.get("title") or "Товар",
                    "description": clean_html_text(product.get("description") or ""),
                    "quantity": int(product.get("quantity") or 0),
                    "price_usdt": round(
                        convert_rub_to_usdt(product.get("price", 0), int(PUBLIC_API_MARKUP_PERCENT)),
                        2,
                    ),
                    "attributes": [
                        row.get("value")
                        for row in product.get("attributes", [])
                        if row.get("value")
                    ][:8],
                }
            )
        meta = payload.get("meta", {}) or {}
        return web.json_response(
            {
                "items": items,
                "page": int(meta.get("current_page") or page),
                "last_page": int(meta.get("last_page") or 1),
                "total": int(meta.get("total") or len(items)),
            }
        )

    async def handle_market_site_topup(self, request: web.Request) -> web.Response:
        """Reuse the regular top-up flow while keeping the public API account isolated."""
        return await self.handle_create_topup(request)

    def _serialize_profile(self, profile: dict, bot_context: MiniAppBotContext) -> dict[str, Any]:
        referral_code = profile.get("referral_code") or profile["user_id"]
        referral_link = (
            f"https://t.me/{bot_context.bot_username}?start=r_{referral_code}"
            if bot_context.bot_username
            else ""
        )
        return {
            "user_id": int(profile["user_id"]),
            "balance": round(float(profile.get("balance") or 0.0), 2),
            "purchases_count": int(profile.get("purchases_count") or 0),
            "purchases_total": round(float(profile.get("purchases_total") or 0.0), 2),
            "topups_total": round(float(profile.get("topups_total") or 0.0), 2),
            "referrals_count": int(profile.get("referrals_count") or 0),
            "referral_earnings": round(float(profile.get("referral_earnings") or 0.0), 2),
            "registered_at": profile.get("registered_at") or "",
            "referral_link": referral_link,
            "referral_percent": round(float(bot_context.referral_percent), 2),
            "discount_percent": round(float(profile.get("active_discount_percent") or 0.0), 2),
            "discount_code": profile.get("active_discount_code") or "",
        }

    def _serialize_email_profile(self, profile: dict, bot_context: MiniAppBotContext) -> dict[str, Any]:
        payload = self._serialize_profile(profile, bot_context)
        payload["balance"] = round(float(profile.get("balance") or 0.0), 6)
        payload["email_activations_count"] = count_email_activations(int(profile["user_id"]))
        return payload

    def _serialize_email_activation(self, activation: dict) -> dict[str, Any]:
        status = str(activation.get("status") or "ordering")
        raw_message = str(activation.get("message_text") or "")
        status_labels = {
            "ordering": "Оформляется",
            "waiting": "Ожидает письмо",
            "received": "Письмо получено",
            "canceled": "Отменена",
            "failed": "Ошибка",
        }
        return {
            "id": int(activation["id"]),
            "site": str(activation.get("site") or ""),
            "domain": str(activation.get("domain") or ""),
            "email": str(activation.get("email") or ""),
            "price": round(float(activation.get("sale_price_usd") or 0.0), 6),
            "status": status,
            "status_label": status_labels.get(status, status),
            "code": str(activation.get("message_code") or ""),
            "message": clean_html_text(raw_message)[:5000],
            "links": extract_email_message_links(raw_message),
            "error": get_anymessage_error_message(str(activation.get("error_code") or "")) if activation.get("error_code") else "",
            "refunded": bool(int(activation.get("refunded") or 0)),
            "can_reorder": bool(activation.get("provider_activation_id")) and status in {"received", "canceled"},
            "created_at": str(activation.get("created_at") or ""),
            "updated_at": str(activation.get("updated_at") or ""),
        }

    def _filter_available_email_domains(self, site: str, domains: list[dict[str, Any]]) -> list[dict[str, Any]]:
        blocked_domains = list_recent_unavailable_email_domains(
            site,
            cooldown_seconds=ANYMESSAGE_UNAVAILABLE_COOLDOWN_SECONDS,
        )
        return [
            row
            for row in domains
            if int(row.get("count") or 0) > 0 and str(row.get("domain") or "").strip().lower() not in blocked_domains
        ]

    def _serialize_orders(self, orders: list[dict]) -> list[dict[str, Any]]:
        serialized: list[dict[str, Any]] = []
        for order in orders:
            supplier_order_number = str(order.get("supplier_order_number") or "").strip()
            serialized.append(
                {
                    "id": int(order["id"]),
                    "order_number": (
                        supplier_order_number
                        if str(order.get("proxy_kind") or "") == "account"
                        else str(order["id"])
                    ),
                    "title": order.get("product_title") or order.get("protocol") or "Товар",
                    "category_name": order.get("category_name") or "",
                    "quantity": int(order.get("quantity") or 0),
                    "total_price": round(float(order.get("total_price") or 0.0), 2),
                    "status": str(order.get("status") or ""),
                    "status_label": get_order_status_label(str(order.get("status") or "")),
                    "delivery_preview": (order.get("delivery_text") or "")[:160],
                    "can_download": bool(order.get("delivery_text")),
                    "supplier_order_number": order.get("supplier_order_number") or "",
                    "created_at": order.get("created_at") or "",
                    "is_account_order": str(order.get("proxy_kind") or "") == "account",
                }
            )
        return serialized

    def _serialize_order_detail(self, order: dict) -> dict[str, Any]:
        supplier_order_number = str(order.get("supplier_order_number") or "").strip()
        return {
            "id": int(order["id"]),
            "order_number": (
                supplier_order_number
                if str(order.get("proxy_kind") or "") == "account"
                else str(order["id"])
            ),
            "title": order.get("product_title") or order.get("protocol") or "Товар",
            "category_name": order.get("category_name") or "",
            "quantity": int(order.get("quantity") or 0),
            "unit_price": round(float(order.get("unit_price") or 0.0), 2),
            "total_price": round(float(order.get("total_price") or 0.0), 2),
            "status": str(order.get("status") or ""),
            "status_label": get_order_status_label(str(order.get("status") or "")),
            "delivery_preview": (order.get("delivery_text") or "")[:160],
            "delivery_text": order.get("delivery_text") or "",
            "can_download": bool(order.get("delivery_text")),
            "supplier_order_number": order.get("supplier_order_number") or "",
            "created_at": order.get("created_at") or "",
            "updated_at": order.get("updated_at") or "",
            "is_account_order": str(order.get("proxy_kind") or "") == "account",
        }

    def _serialize_transactions(self, orders: list[dict], topups: list[dict]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for order in orders:
            created_at = order.get("created_at")
            parsed_created_at = parse_datetime(created_at)
            rows.append(
                {
                    "id": f"order-{order['id']}",
                    "kind": "order",
                    "title": order.get("product_title") or order.get("protocol") or "Покупка",
                    "amount": round(float(order.get("total_price") or 0.0), 2),
                    "amount_prefix": "-",
                    "created_at": created_at or "",
                    "sort_key": parsed_created_at.timestamp() if parsed_created_at is not None else 0,
                    "subtitle": order.get("category_name") or "",
                }
            )
        for topup in topups:
            created_at = topup.get("created_at")
            parsed_created_at = parse_datetime(created_at)
            rows.append(
                {
                    "id": f"topup-{topup['id']}",
                    "kind": "topup",
                    "title": f"Пополнение #{topup['id']}",
                    "amount": round(float(topup.get("amount") or 0.0), 2),
                    "amount_prefix": "+",
                    "created_at": created_at or "",
                    "sort_key": parsed_created_at.timestamp() if parsed_created_at is not None else 0,
                    "subtitle": str(topup.get("payment_provider") or "balance").upper(),
                }
            )
        rows.sort(key=lambda item: item["sort_key"] or 0, reverse=True)
        for row in rows:
            row.pop("sort_key", None)
        return rows[:30]

    def _resolve_category_name(self, categories: list[dict], category_id: int, fallback: str = "") -> str:
        for category in categories:
            if int(category.get("id") or 0) == int(category_id):
                return str(category.get("name") or fallback or "Категория")
            for child in category.get("children", []) or []:
                if int(child.get("id") or 0) == int(category_id):
                    parent_name = str(category.get("name") or "").strip()
                    child_name = str(child.get("name") or fallback or "Категория").strip()
                    return f"{parent_name} / {child_name}" if parent_name else child_name
        return fallback or "Категория"

    def _build_asset_version(self, paths: tuple[Path, ...]) -> str:
        latest_mtime_ns = 0
        for path in paths:
            try:
                latest_mtime_ns = max(latest_mtime_ns, path.stat().st_mtime_ns)
            except FileNotFoundError:
                continue
        return str(latest_mtime_ns or int(time.time() * 1_000_000_000))

    def _build_index_html(self, template_path: Path, asset_paths: tuple[Path, ...]) -> str:
        template = template_path.read_text(encoding="utf-8")
        return template.replace("__ASSET_VERSION__", self._build_asset_version(asset_paths))

    def _build_static_file_response(self, path: Path) -> web.FileResponse:
        response = web.FileResponse(path)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    def _get_order_fulfillment_lock(self, order_id: int) -> asyncio.Lock:
        lock = self._order_fulfillment_locks.get(order_id)
        if lock is None:
            lock = asyncio.Lock()
            self._order_fulfillment_locks[order_id] = lock
        return lock

    async def _prepare_proxy_delivery_details(self, provider_order: dict, protocol: str) -> list[dict]:
        """Normalize provider details in one place for immediate delivery."""
        details = list(provider_order.get("details") or [])
        if not details:
            return []
        return await filter_working_proxy_details(details, protocol)

    def _schedule_market_order_fulfillment(self, order_id: int) -> bool:
        existing_task = self._market_order_tasks.get(order_id)
        if existing_task is not None and not existing_task.done():
            return False

        async def runner() -> None:
            try:
                await self._fulfill_market_order_for_miniapp(order_id)
            finally:
                self._market_order_tasks.pop(order_id, None)

        self._market_order_tasks[order_id] = asyncio.create_task(
            runner(),
            name=f"miniapp-market-order-{order_id}",
        )
        return True

    async def _poll_topup_payment(self, topup_id: int) -> None:
        for attempt in range(MINIAPP_TOPUP_POLL_ATTEMPTS):
            topup = get_topup(topup_id)
            if topup is None or str(topup.get("status") or "") == "paid":
                return

            invoice_id = str(topup.get("xrocket_invoice_id") or "").strip()
            provider = str(topup.get("payment_provider") or "xrocket").strip().lower()
            if invoice_id:
                try:
                    if provider == "lolz":
                        paid = is_lolz_invoice_paid(await get_lolz_invoice(invoice_id=invoice_id))
                    elif provider == "heleket":
                        paid = is_heleket_invoice_paid(await get_heleket_payment(invoice_id=invoice_id))
                    elif provider == "crystalpay":
                        paid = is_crystalpay_invoice_paid(await get_crystalpay_invoice(invoice_id))
                    else:
                        paid = is_xrocket_invoice_paid(await get_xrocket_invoice(invoice_id))
                    if paid:
                        complete_topup_payment(topup_id)
                        return
                except (XRocketError, LolzError, HeleketError, CrystalPayError):
                    pass

            if attempt + 1 < MINIAPP_TOPUP_POLL_ATTEMPTS:
                await asyncio.sleep(MINIAPP_TOPUP_POLL_INTERVAL_SECONDS)

    def _schedule_topup_payment_poll(self, topup_id: int) -> bool:
        existing_task = self._topup_payment_tasks.get(topup_id)
        if existing_task is not None and not existing_task.done():
            return False

        async def runner() -> None:
            try:
                await self._poll_topup_payment(topup_id)
            finally:
                self._topup_payment_tasks.pop(topup_id, None)

        self._topup_payment_tasks[topup_id] = asyncio.create_task(
            runner(),
            name=f"miniapp-topup-payment-{topup_id}",
        )
        return True

    def _get_proxy_purchase_unit_price(self, provider_price_rub: float) -> float:
        rate = get_cached_market_rub_per_usdt()
        base_amount = float(provider_price_rub or 0.0) / rate
        return max(round(base_amount, 4), 0.01)

    def _get_proxy_sale_unit_price(self, bot_context: MiniAppBotContext, provider_price_rub: float) -> float:
        purchase_unit_price = self._get_proxy_purchase_unit_price(provider_price_rub)
        if bot_context.bot_username == "__public_api__":
            return calculate_sale_price_from_base_usdt(purchase_unit_price, int(PUBLIC_API_MARKUP_PERCENT))
        partner_bot = bot_context.partner_bot
        franchise_markup_percentage = get_partner_category_markup(partner_bot, "proxy")
        return calculate_sale_price_from_base_usdt(
            purchase_unit_price,
            PROXY_BASE_MARKUP_PERCENT + franchise_markup_percentage,
        )

    def _build_proxy_catalog_fallback(self) -> list[dict]:
        categories_by_id: dict[int, dict[str, Any]] = {}
        for row in list_proxy_catalog_fallback_items():
            category_id = int(row.get("category_id") or 0)
            item_id = int(row.get("item_id") or 0)
            if category_id <= 0 or item_id <= 0:
                continue

            category_name = str(row.get("category_name") or "Страна").strip() or "Страна"
            category = categories_by_id.setdefault(
                category_id,
                {
                    "id": category_id,
                    "name": {"ru": category_name, "en": category_name},
                    "countryCode": "",
                    "image": "",
                    "quality": "BASIC",
                    "available": False,
                    "price": 0.0,
                    "items": [],
                },
            )
            category["items"].append(
                {
                    "id": item_id,
                    "name": str(row.get("protocol") or "HTTP"),
                    "itemProductsCount": 0,
                }
            )

        return list(categories_by_id.values())

    async def _load_proxy_catalog_state(self) -> tuple[list[dict], bool, str]:
        categories, catalog_live = await get_static_proxy_categories_snapshot()
        if categories:
            return (
                categories,
                catalog_live,
                "" if catalog_live else "Провайдер временно не отвечает. Показываю последний доступный каталог.",
            )

        fallback_categories = self._build_proxy_catalog_fallback()
        if fallback_categories:
            return (
                fallback_categories,
                False,
                "Провайдер временно не отвечает. Показываю последний известный список стран и протоколов.",
            )

        return [], False, "Прокси временно недоступны. Попробуйте позже."

    def _build_proxy_type_payloads(self, categories: list[dict], catalog_available: bool) -> list[dict[str, Any]]:
        quality_meta: dict[str, dict[str, str]] = {
            "BASIC": {
                "id": "basic",
                "title": "Basic",
                "accent": "globe",
                "description": "Базовые прокси",
                "badge": "",
            },
            "PRIVATE": {
                "id": "private",
                "title": "Private",
                "accent": "shield",
                "description": "Персональные прокси",
                "badge": "",
            },
            "DEDICATED": {
                "id": "dedicated",
                "title": "Dedicated",
                "accent": "diamond",
                "description": "Максимальная приватность",
                "badge": "",
            },
        }
        quality_order = ("BASIC", "PRIVATE", "DEDICATED")
        types: list[dict[str, Any]] = []
        for quality in quality_order:
            matching_categories = [
                category
                for category in categories
                if str(category.get("quality") or "BASIC").upper() == quality
            ]
            if not matching_categories:
                continue

            meta = quality_meta[quality]
            types.append(
                {
                    "id": meta["id"],
                    "title": meta["title"],
                    "accent": meta["accent"],
                    "description": meta["description"],
                    "available": catalog_available,
                    "badge": meta["badge"],
                    "quality": quality,
                }
            )
        return types

    def _create_proxy_order_from_selection(
        self,
        user_id: int,
        bot_context: MiniAppBotContext,
        category_id: int,
        category_name: str,
        item_id: int,
        protocol: str,
        quantity: int,
        purchase_unit_price: float,
    ) -> int:
        partner_bot = bot_context.partner_bot
        is_public_api = bot_context.bot_username == "__public_api__"
        partner_margin_percentage = get_partner_category_markup(partner_bot, "proxy")
        effective_markup = int(PUBLIC_API_MARKUP_PERCENT) if is_public_api else PROXY_BASE_MARKUP_PERCENT + partner_margin_percentage
        sale_unit_price = calculate_sale_price_from_base_usdt(
            purchase_unit_price,
            effective_markup,
        )
        return create_order(
            user_id=user_id,
            proxy_kind="static",
            category_id=category_id,
            category_name=category_name,
            item_id=item_id,
            protocol=protocol,
            quantity=quantity,
            unit_price=sale_unit_price,
            total_price=round(sale_unit_price * quantity, 2),
            partner_bot_id=(int(partner_bot["id"]) if partner_bot else None),
            purchase_unit_price=purchase_unit_price,
            partner_margin_percentage=partner_margin_percentage,
            partner_profit_amount=(
                calculate_partner_profit_share_amount(purchase_unit_price, quantity, partner_margin_percentage)
                if partner_bot
                else 0.0
            ),
        )

    def _format_proxy_manager_expiry(self, value: str | None, fallback_created_at: str | None = None) -> str:
        if value:
            normalized = str(value).replace("Z", "+00:00")
            try:
                parsed = datetime.fromisoformat(normalized)
                return parsed.strftime("%H:%M %d-%m-%Y")
            except ValueError:
                pass

        fallback_dt = parse_datetime(fallback_created_at)
        if fallback_dt is not None:
            return (fallback_dt + timedelta(days=PROXY_PROVIDER_ORDER_DURATION_DAYS)).strftime("%H:%M %d-%m-%Y")
        return ""

    def _parse_proxy_delivery_lines(self, delivery_text: str) -> list[str]:
        return [line.strip() for line in str(delivery_text or "").splitlines() if line.strip()]

    async def _build_proxy_manager_entries(self, user_id: int) -> list[dict[str, Any]]:
        orders = list_user_orders_any_status(user_id, limit=200, offset=0)
        proxy_orders = [
            order
            for order in orders
            if str(order.get("proxy_kind") or "") == "static" and str(order.get("status") or "") in {"delivered", "paid", "delivery_pending"}
        ]
        entries: list[dict[str, Any]] = []

        for order in proxy_orders:
            provider_order_id = int(order.get("provider_order_id") or 0)
            provider_order: dict[str, Any] | None = None
            if provider_order_id > 0:
                try:
                    provider_order = await get_proxy_provider_order(provider_order_id)
                except ProxyProviderError:
                    provider_order = None

            provider_details = list((provider_order or {}).get("details") or [])
            if provider_details:
                for index, detail in enumerate(provider_details, start=1):
                    endpoint = format_proxy_endpoint(detail, str(order.get("protocol") or "HTTP"))
                    entries.append(
                        {
                            "id": f"{order['id']}-{index}",
                            "order_id": int(order["id"]),
                            "country_name": str(order.get("category_name") or "Страна"),
                            "protocol": str(order.get("protocol") or "HTTP"),
                            "status": str(order.get("status") or ""),
                            "status_label": get_order_status_label(str(order.get("status") or "")),
                            "endpoint": endpoint,
                            "expires_at": self._format_proxy_manager_expiry(detail.get("expiresAt"), order.get("created_at")),
                            "duration_days": int(detail.get("duration") or PROXY_PROVIDER_ORDER_DURATION_DAYS),
                            "created_at": order.get("created_at") or "",
                        }
                    )
                continue

            delivery_lines = self._parse_proxy_delivery_lines(order.get("delivery_text") or "")
            if not delivery_lines:
                delivery_lines = [""]

            for index, line in enumerate(delivery_lines, start=1):
                entries.append(
                    {
                        "id": f"{order['id']}-{index}",
                        "order_id": int(order["id"]),
                        "country_name": str(order.get("category_name") or "Страна"),
                        "protocol": str(order.get("protocol") or "HTTP"),
                        "status": str(order.get("status") or ""),
                        "status_label": get_order_status_label(str(order.get("status") or "")),
                        "endpoint": ensure_proxy_endpoint_scheme(line, str(order.get("protocol") or "HTTP")),
                        "expires_at": self._format_proxy_manager_expiry(None, order.get("created_at")),
                        "duration_days": PROXY_PROVIDER_ORDER_DURATION_DAYS,
                        "created_at": order.get("created_at") or "",
                    }
                )

        entries.sort(key=lambda item: (item.get("expires_at") or "", item.get("order_id") or 0), reverse=True)
        return entries

    def _create_market_order_from_product(
        self,
        user_id: int,
        bot_context: MiniAppBotContext,
        category_name: str,
        product: dict,
        quantity: int,
    ) -> int:
        purchase_unit_price = round(float(product.get("price", 0) or 0) / get_cached_market_rub_per_usdt(), 4)
        unit_price = convert_rub_to_usdt(product.get("price", 0), bot_context.margin_percentage)
        partner_bot = bot_context.partner_bot
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

    async def _fulfill_proxy_order_for_miniapp(self, order_id: int) -> dict | None:
        async with self._get_order_fulfillment_lock(order_id):
            order = get_order(order_id)
            if order is None:
                return None
            if order.get("status") in {"delivered", "credited"}:
                return order

            provider_order_id = int(order.get("provider_order_id") or 0)
            try:
                if provider_order_id <= 0:
                    created_order: dict[str, Any] | None = None
                    last_create_error: ProxyProviderError | None = None
                    for attempt in range(PROXY_PROVIDER_CREATE_ATTEMPTS):
                        try:
                            created_order = await create_proxy_provider_order(
                                category_id=int(order["category_id"]),
                                item_id=int(order["item_id"]),
                                count=int(order["quantity"]),
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
                    if provider_order_id <= 0:
                        raise ValueError("Proxy provider order id is missing")
                    set_proxy_order_provider_reference(order_id, provider_order_id, status="delivery_pending")
                    order = get_order(order_id) or order
                    if provider_order.get("details"):
                        protocol = str(order.get("protocol") or "HTTP")
                        provider_order["details"] = await self._prepare_proxy_delivery_details(provider_order, protocol)
                    if provider_order.get("details"):
                        delivery_text = format_proxy_delivery(provider_order, protocol)
                        complete_order(order_id, provider_order_id, delivery_text)
                        return get_order(order_id)

                last_provider_order_data: dict[str, Any] | None = None
                for attempt in range(PROXY_PROVIDER_DETAILS_POLL_ATTEMPTS):
                    try:
                        provider_order_data = await get_proxy_provider_order(provider_order_id)
                        last_provider_order_data = provider_order_data
                        if provider_order_data.get("details"):
                            protocol = str(order.get("protocol") or "HTTP")
                            provider_order_data["details"] = await self._prepare_proxy_delivery_details(
                                provider_order_data,
                                protocol,
                            )
                        if provider_order_data.get("details"):
                            delivery_text = format_proxy_delivery(provider_order_data, protocol)
                            complete_order(order_id, provider_order_id, delivery_text)
                            return get_order(order_id)
                    except ProxyProviderError:
                        if attempt + 1 >= PROXY_PROVIDER_DETAILS_POLL_ATTEMPTS:
                            break
                    await asyncio.sleep(PROXY_PROVIDER_DETAILS_POLL_INTERVAL_SECONDS)

                pending_order = get_order(order_id)
                if pending_order is not None and str(pending_order.get("status") or "") not in {"delivered", "credited"}:
                    mark_order_delivery_pending(order_id)
                    return get_order(order_id)
                return pending_order
            except (ProxyProviderError, ValueError):
                if provider_order_id > 0:
                    pending_order = get_order(order_id)
                    if pending_order is not None and str(pending_order.get("status") or "") not in {"delivered", "credited"}:
                        mark_order_delivery_pending(order_id)
                        return get_order(order_id)
                    return pending_order
                updated_order = credit_order_to_user_balance(order_id)
                if updated_order is not None:
                    updated_order["_miniapp_error"] = PROXY_GENERIC_ERROR_CODE
                return updated_order

    async def _fulfill_market_order_for_miniapp(self, order_id: int) -> dict | None:
        async with self._get_order_fulfillment_lock(order_id):
            order = get_order(order_id)
            if order is None:
                return None
            if order.get("status") in {"delivered", "credited"}:
                return order

            try:
                if not order.get("supplier_order_uuid"):
                    # The stock was validated during checkout. Creating the supplier
                    # order immediately avoids an extra provider round-trip.
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

                last_supplier_order = None
                for attempt in range(MINIAPP_MARKET_ORDER_POLL_ATTEMPTS):
                    supplier_order = await get_market_order(order["supplier_order_uuid"])
                    last_supplier_order = supplier_order

                    if is_market_order_ready(supplier_order):
                        file_url = get_market_order_file_url(supplier_order)
                        if not file_url:
                            raise MarketProviderError("Поставщик не вернул ссылку на файл")
                        delivery_text = await download_text_file(file_url)
                        # Account files are delivered as marketplace goods. Proxy
                        # reachability checks are handled in the proxy flow only.
                        complete_market_order(
                            order_id,
                            supplier_order_uuid=supplier_order["uuid"],
                            supplier_order_number=supplier_order.get("order_number"),
                            delivery_text=delivery_text,
                        )
                        return get_order(order_id)

                    if str(supplier_order.get("status", "")).lower() in {"uncompleted", "cancelled"}:
                        return credit_order_to_user_balance(order_id)

                    if attempt + 1 < MINIAPP_MARKET_ORDER_POLL_ATTEMPTS:
                        await asyncio.sleep(MINIAPP_MARKET_ORDER_POLL_INTERVAL_SECONDS)

                update_order_supplier_data(
                    order_id,
                    supplier_order_uuid=order["supplier_order_uuid"],
                    supplier_order_number=(last_supplier_order or {}).get("order_number"),
                )
                return get_order(order_id)
            except MarketProviderError as error:
                # The request may fail after the supplier has already accepted and
                # charged the order.  Keep it pending for reconciliation/retry and
                # refund only explicit cancelled/uncompleted supplier statuses.
                updated_order = get_order(order_id)
                if not (updated_order or {}).get("supplier_order_uuid") and is_market_order_creation_rejected(error):
                    return credit_order_to_user_balance(order_id)
                if updated_order is not None:
                    updated_order["_miniapp_error"] = str(error)
                return updated_order

    async def _create_market_supplier_reference(self, order_id: int) -> dict | None:
        """Create a DJEKXA order and persist its customer-facing number."""
        order = get_order(order_id)
        if order is None or order.get("supplier_order_uuid"):
            return order
        supplier_order = await create_market_order(
            int(order["item_id"]),
            int(order["quantity"]),
            product_prevalidated=True,
        )
        supplier_order_uuid = str(supplier_order.get("uuid") or "").strip()
        if not supplier_order_uuid:
            raise MarketProviderError("Поставщик не вернул UUID заказа")
        update_order_supplier_data(
            order_id,
            supplier_order_uuid=supplier_order_uuid,
            supplier_order_number=str(supplier_order.get("order_number") or "").strip() or None,
        )
        return get_order(order_id)

    async def handle_index(self, _: web.Request) -> web.Response:
        response = web.Response(
            text=self._build_index_html(
                MINIAPP_INDEX_TEMPLATE_PATH,
                (MINIAPP_INDEX_TEMPLATE_PATH, MINIAPP_STYLES_PATH, MINIAPP_APP_JS_PATH),
            ),
            content_type="text/html",
            charset="utf-8",
        )
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    async def handle_partner_index(self, request: web.Request) -> web.Response:
        """Owner-facing partner-program cabinet Mini App."""
        bot_context = await self.resolve_bot_context(self.get_request_bot_slug(request))
        if bot_context.partner_bot is None:
            raise web.HTTPNotFound(text="Partner cabinet is available only for partner bots")
        response = web.Response(
            text=self._build_index_html(
                PARTNER_INDEX_PATH,
                (PARTNER_INDEX_PATH, PARTNER_STYLES_PATH, PARTNER_APP_JS_PATH),
            ),
            content_type="text/html",
            charset="utf-8",
        )
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
        return response

    async def handle_partner_styles(self, _: web.Request) -> web.FileResponse:
        return self._build_static_file_response(PARTNER_STYLES_PATH)

    async def handle_partner_app_script(self, _: web.Request) -> web.FileResponse:
        return self._build_static_file_response(PARTNER_APP_JS_PATH)

    async def handle_partner_reference_bot_asset(self, request: web.Request) -> web.FileResponse:
        asset_name = str(request.match_info.get("asset_name") or "")
        if not re.fullmatch(r"ref-[0-9]{2}\.png", asset_name):
            raise web.HTTPNotFound()
        asset_path = PARTNER_REFERENCE_BOTS_DIR / asset_name
        if not asset_path.is_file():
            raise web.HTTPNotFound()
        return self._build_static_file_response(asset_path)

    async def _resolve_partner_owner_optional(self, request: web.Request) -> tuple[dict | None, dict]:
        context, user_payload, _ = await self._resolve_request_context(request)
        partner_bot = context.partner_bot
        user_id = int(user_payload.get("id") or 0)
        # When the cabinet is launched from SousPartnersBot, the launcher
        # context can remain in the request even though the user is opening
        # their own connected bot. Resolve that ownership explicitly.
        cabinet_slug = str(request.query.get("cabinet_bot") or "").strip()
        candidate = get_partner_bot_by_username(cabinet_slug) if cabinet_slug else None
        # A cabinet opened from the Bots tab may switch the target bot while
        # keeping the same Telegram Web App signature. The target is accepted
        # only when it belongs to the authenticated Telegram user.
        if candidate is not None and int(candidate.get("owner_id") or 0) == user_id:
            partner_bot = candidate
        elif partner_bot is None or int(partner_bot.get("owner_id") or 0) != user_id:
            owned = list_partner_bots_by_owner(user_id)
            candidate = owned[0] if owned else None
            if candidate is not None and int(candidate.get("owner_id") or 0) == user_id:
                partner_bot = candidate
        if partner_bot is not None and int(partner_bot.get("owner_id") or 0) != user_id:
            partner_bot = None
        return partner_bot, user_payload

    async def _resolve_partner_owner(self, request: web.Request) -> tuple[dict, dict]:
        partner_bot, user_payload = await self._resolve_partner_owner_optional(request)
        if partner_bot is None:
            raise web.HTTPForbidden(text=json.dumps({"error": "Partner owner access required"}), content_type="application/json")
        return partner_bot, user_payload

    async def handle_partner_dashboard(self, request: web.Request) -> web.Response:
        partner_bot, user_payload = await self._resolve_partner_owner_optional(request)
        stats = get_partner_bot_stats(int(partner_bot["id"])) if partner_bot is not None else {
            "total_users": 0,
            "delivered_revenue": 0.0,
            "profit": 0.0,
        }
        available = get_partner_bot_available_balance(partner_bot) if partner_bot is not None else 0.0
        owner_profile = get_user_profile(int(user_payload["id"])) or {}
        referral_code = str(owner_profile.get("referral_code") or user_payload["id"]).strip()
        bot_username = str((partner_bot or {}).get("bot_username") or "").strip().lstrip("@")
        partner_entry_username = str(
            os.getenv("PARTNER_MAIN_BOT_USERNAME")
            or os.getenv("MAIN_BOT_USERNAME")
            or "SousPartnersBot"
        ).strip().lstrip("@")
        owned_bots = []
        for owned_bot in list_partner_bots_by_owner(int(user_payload["id"])):
            owned_stats = get_partner_bot_stats(int(owned_bot["id"]))
            owned_bots.append(
                {
                    "id": int(owned_bot["id"]),
                    "username": str(owned_bot.get("bot_username") or "").lstrip("@"),
                    "active": bool(int(owned_bot.get("is_active") or 0)),
                    "earned": round(float(owned_bot.get("partner_earnings") or 0.0), 2),
                    "users": int(owned_stats.get("total_users") or 0),
                    "selected": partner_bot is not None and int(owned_bot["id"]) == int(partner_bot["id"]),
                }
            )
        return web.json_response({
            "has_bot": partner_bot is not None,
            "bot": {"id": int(partner_bot["id"]) if partner_bot is not None else None, "username": partner_bot.get("bot_username") if partner_bot is not None else None, "owner_id": int(user_payload["id"]), "active": bool(int(partner_bot.get("is_active") or 0)) if partner_bot is not None else False},
            "available_balance": round(float(available), 2),
            "currency": "USDT",
            "payout_minimum": round(float(PARTNER_MIN_WITHDRAW_AMOUNT), 2),
            "earned": round(float(partner_bot.get("partner_earnings") or 0.0), 2) if partner_bot is not None else 0.0,
            "withdrawn": round(float(partner_bot.get("partner_withdrawn") or 0.0), 2) if partner_bot is not None else 0.0,
            "markup": int(partner_bot.get("margin_percentage") or 0) if partner_bot is not None else 0,
            "markups": {
                "goods": get_partner_category_markup(partner_bot, "goods") if partner_bot is not None else 0,
                "proxy": get_partner_category_markup(partner_bot, "proxy") if partner_bot is not None else 0,
                "sms": get_partner_category_markup(partner_bot, "sms") if partner_bot is not None else 0,
            },
            "stats": stats,
            "referral": {
                "enabled": bool(int(partner_bot.get("referral_enabled") or 0)) if partner_bot is not None else False,
                "percent": float(partner_bot.get("referral_percent") or 0) if partner_bot is not None else 0.0,
                "link": f"https://t.me/{bot_username}?start=r_{referral_code}" if bot_username else "",
                # This action must enter the token-connection flow. The old
                # referral payload only reopened /start and never put the
                # user into PartnerBotState.waiting_bot_token.
                "create_bot_link": f"https://t.me/{partner_entry_username}?start=create_bot_r_{referral_code}",
            },
            "subscription": {"enabled": bool(int(partner_bot.get("subscription_enabled") or 0)) if partner_bot is not None else False, "channel_url": partner_bot.get("subscription_channel_url") or "" if partner_bot is not None else ""},
            "links": {"support": partner_bot.get("franchise_support_url") or "" if partner_bot is not None else "", "news": partner_bot.get("franchise_news_url") or "" if partner_bot is not None else ""},
            "franchise": {"create_button_visible": int(partner_bot.get("franchise_hide_create") or 0) == 0 if partner_bot is not None else True},
            "bots": owned_bots,
        })

    async def handle_partner_create_bot(self, request: web.Request) -> web.Response:
        """Connect a partner bot directly from the Telegram Mini App."""
        _, user_payload, _ = await self._resolve_request_context(request)
        user_id = int(user_payload.get("id") or 0)
        try:
            body = await request.json()
            token = str(body.get("token") or "").strip()
        except (TypeError, json.JSONDecodeError):
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "Введите токен бота"}),
                content_type="application/json",
            )

        if not re.fullmatch(r"\d{8,12}:[A-Za-z0-9_-]{30,}", token):
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "Токен выглядит некорректно"}),
                content_type="application/json",
            )

        existing_partner = get_partner_bot_by_token(token)
        owner_summary = get_partner_owner_summary(user_id)
        is_current_active_bot = bool(
            existing_partner
            and int(existing_partner.get("owner_id") or 0) == user_id
            and int(existing_partner.get("is_active") or 0) == 1
        )
        if int(owner_summary.get("total_bots") or 0) >= 2 and not is_current_active_bot:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "Можно подключить не больше двух ботов"}),
                content_type="application/json",
            )

        from partner_runtime import get_partner_runtime

        runtime = get_partner_runtime()
        if runtime is None:
            raise web.HTTPServiceUnavailable(
                text=json.dumps({"error": "Подключение ботов временно недоступно"}),
                content_type="application/json",
            )

        try:
            partner_bot = await runtime.onboard_partner_bot(
                owner_id=user_id,
                bot_token=token,
                margin_percentage=int(float(os.getenv("PARTNER_DEFAULT_MARGIN_PERCENT", "50") or 50)),
            )
        except ValueError as error:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": str(error)}),
                content_type="application/json",
            ) from error
        except Exception as error:
            logger.exception("Mini App partner bot onboarding failed for user_id=%s", user_id)
            raise web.HTTPBadGateway(
                text=json.dumps({"error": "Не удалось подключить бота. Проверьте токен."}),
                content_type="application/json",
            ) from error

        return web.json_response(
            {
                "ok": True,
                "bot": {
                    "id": int(partner_bot["id"]),
                    "username": str(partner_bot.get("bot_username") or "").lstrip("@"),
                    "active": bool(int(partner_bot.get("is_active") or 0)),
                },
            }
        )

    async def handle_partner_settings(self, request: web.Request) -> web.Response:
        partner_bot, user_payload = await self._resolve_partner_owner(request)
        try:
            body = await request.json()
            kind = str(body.get("kind") or "").strip().lower()
            value = int(body.get("value"))
        except (ValueError, TypeError, json.JSONDecodeError):
            raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid settings"}), content_type="application/json")
        if kind not in {"goods", "proxy", "sms"} or not 0 <= value <= 500:
            raise web.HTTPBadRequest(text=json.dumps({"error": "Unsupported markup"}), content_type="application/json")
        if not update_partner_bot_markup(int(partner_bot["id"]), int(user_payload["id"]), kind, value):
            raise web.HTTPForbidden(text=json.dumps({"error": "Unable to update markup"}), content_type="application/json")
        return web.json_response({"ok": True, "kind": kind, "value": value})

    async def handle_partner_config(self, request: web.Request) -> web.Response:
        partner_bot, user_payload = await self._resolve_partner_owner(request)
        try:
            body = await request.json()
            field = str(body.get("field") or "").strip()
            value = body.get("value")
        except (TypeError, json.JSONDecodeError):
            raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid configuration"}), content_type="application/json")
        if field in {"subscription_enabled", "referral_enabled", "is_active", "franchise_hide_create"}:
            if isinstance(value, str):
                value = value.strip().lower() in {"1", "true", "yes", "on"}
            value = 1 if bool(value) else 0
        elif field == "referral_percent":
            try:
                value = max(0, min(float(value), 100))
            except (TypeError, ValueError):
                raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid percentage"}), content_type="application/json")
        elif field not in {"subscription_channel_id", "subscription_channel_url", "franchise_support_url", "franchise_news_url", "franchise_usage_url", "franchise_privacy_url", "franchise_info_brand"}:
            raise web.HTTPBadRequest(text=json.dumps({"error": "Unsupported configuration"}), content_type="application/json")
        if not update_partner_franchise_setting(int(partner_bot["id"]), field, value):
            raise web.HTTPForbidden(text=json.dumps({"error": "Unable to update configuration"}), content_type="application/json")
        return web.json_response({"ok": True, "field": field, "value": value})

    async def handle_partner_action(self, request: web.Request) -> web.Response:
        partner_bot, user_payload = await self._resolve_partner_owner(request)
        try:
            body = await request.json()
            action = str(body.get("action") or "").strip().lower()
        except (TypeError, json.JSONDecodeError):
            raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid action"}), content_type="application/json")
        if action != "toggle":
            raise web.HTTPBadRequest(text=json.dumps({"error": "Unsupported action"}), content_type="application/json")
        active = not bool(int(partner_bot.get("is_active") or 0))
        if not update_partner_franchise_setting(int(partner_bot["id"]), "is_active", 1 if active else 0):
            raise web.HTTPForbidden(text=json.dumps({"error": "Unable to change bot state"}), content_type="application/json")
        try:
            # The database flag alone does not stop a running polling task.
            # Apply the state change to the live partner runtime as well.
            from partner_runtime import get_partner_runtime
            runtime = get_partner_runtime()
            if runtime is not None:
                await runtime.set_bot_active(partner_bot, active)
        except Exception as error:
            update_partner_franchise_setting(int(partner_bot["id"]), "is_active", 0 if active else 1)
            raise web.HTTPServiceUnavailable(
                text=json.dumps({"error": "Не удалось применить состояние бота"}),
                content_type="application/json",
            ) from error
        return web.json_response({"ok": True, "active": active})

    async def handle_partner_broadcast(self, request: web.Request) -> web.Response:
        partner_bot, _ = await self._resolve_partner_owner(request)
        try:
            body = await request.json()
            text = str(body.get("text") or "").strip()
        except (TypeError, json.JSONDecodeError):
            raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid message"}), content_type="application/json")
        if not text or len(text) > 4000:
            raise web.HTTPBadRequest(text=json.dumps({"error": "Message must be 1-4000 characters"}), content_type="application/json")
        target = Bot(token=str(partner_bot["bot_token"]))
        sent = 0
        try:
            for user_id in list_partner_bot_user_ids(int(partner_bot["id"])):
                try:
                    await target.send_message(user_id, text)
                    sent += 1
                except Exception:
                    continue
        finally:
            await target.session.close()
        return web.json_response({"ok": True, "sent": sent})

    async def handle_partner_promo(self, request: web.Request) -> web.Response:
        partner_bot, user_payload = await self._resolve_partner_owner(request)
        try:
            body = await request.json()
            code = str(body.get("code") or "").strip()
            reward_type = str(body.get("reward_type") or "balance").strip().lower()
            value = float(body.get("value"))
            limit = body.get("limit")
            max_activations = int(limit) if limit not in (None, "") else None
        except (TypeError, ValueError, json.JSONDecodeError):
            raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid promo data"}), content_type="application/json")
        promo, error = create_promo_code(code, reward_type, value, int(user_payload["id"]), max_activations)
        if promo is None:
            raise web.HTTPBadRequest(text=json.dumps({"error": error or "Unable to create promo"}), content_type="application/json")
        return web.json_response({"ok": True, "promo": promo})

    async def handle_partner_payout(self, request: web.Request) -> web.Response:
        partner_bot, user_payload = await self._resolve_partner_owner(request)
        try:
            body = await request.json()
            amount = float(body.get("amount"))
            destination = str(body.get("destination") or "").strip()
        except (TypeError, ValueError, json.JSONDecodeError):
            raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid payout data"}), content_type="application/json")
        request_id = create_partner_payout_request(int(partner_bot["id"]), int(user_payload["id"]), amount, "USDT", destination)
        if request_id is None:
            raise web.HTTPBadRequest(
                text=json.dumps(
                    {
                        "error": (
                            f"Минимум {PARTNER_MIN_WITHDRAW_AMOUNT:g} USDT, "
                            "сумма не может превышать доступный баланс"
                        )
                    }
                ),
                content_type="application/json",
            )
        return web.json_response({"ok": True, "request_id": request_id, "status": "pending"})

    async def handle_styles(self, _: web.Request) -> web.FileResponse:
        return self._build_static_file_response(MINIAPP_STYLES_PATH)

    async def handle_app_script(self, _: web.Request) -> web.FileResponse:
        return self._build_static_file_response(MINIAPP_APP_JS_PATH)

    async def handle_proxy_index(self, _: web.Request) -> web.Response:
        response = web.Response(
            text=self._build_index_html(
                PROXY_INDEX_TEMPLATE_PATH,
                (PROXY_INDEX_TEMPLATE_PATH, PROXY_STYLES_PATH, PROXY_APP_JS_PATH),
            ),
            content_type="text/html",
            charset="utf-8",
        )
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    async def handle_proxy_styles(self, _: web.Request) -> web.FileResponse:
        return self._build_static_file_response(PROXY_STYLES_PATH)

    async def handle_proxy_app_script(self, _: web.Request) -> web.FileResponse:
        return self._build_static_file_response(PROXY_APP_JS_PATH)

    async def handle_email_index(self, _: web.Request) -> web.Response:
        response = web.Response(
            text=self._build_index_html(
                EMAIL_INDEX_TEMPLATE_PATH,
                (EMAIL_INDEX_TEMPLATE_PATH, EMAIL_STYLES_PATH, EMAIL_APP_JS_PATH),
            ),
            content_type="text/html",
            charset="utf-8",
        )
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    async def handle_email_styles(self, _: web.Request) -> web.FileResponse:
        return self._build_static_file_response(EMAIL_STYLES_PATH)

    async def handle_email_app_script(self, _: web.Request) -> web.FileResponse:
        return self._build_static_file_response(EMAIL_APP_JS_PATH)

    async def handle_sms_index(self, _: web.Request) -> web.Response:
        response = web.Response(
            text=self._build_index_html(
                SMS_INDEX_TEMPLATE_PATH,
                (SMS_INDEX_TEMPLATE_PATH, SMS_STYLES_PATH, SMS_APP_JS_PATH),
            ),
            content_type="text/html",
            charset="utf-8",
        )
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
        return response

    async def handle_sms_styles(self, _: web.Request) -> web.FileResponse:
        return self._build_static_file_response(SMS_STYLES_PATH)

    async def handle_sms_app_script(self, _: web.Request) -> web.FileResponse:
        return self._build_static_file_response(SMS_APP_JS_PATH)

    @staticmethod
    def _serialize_sms_activation(row: dict) -> dict[str, Any]:
        cancel_after_seconds = 0
        try:
            created_at = datetime.fromisoformat(str(row.get("created_at") or ""))
            cancel_after_seconds = max(0, int((created_at + timedelta(minutes=2) - datetime.now()).total_seconds()) + 1)
        except ValueError:
            pass
        return {
            "id": int(row["id"]),
            "service_id": str(row.get("service_id") or ""),
            "service_name": str(row.get("service_name") or ""),
            "country_id": int(row.get("country_id") or 0),
            "country_name": str(row.get("country_name") or ""),
            "phone": str(row.get("phone") or ""),
            "sale_price_usd": round(float(row.get("sale_price_usd") or 0), 4),
            "status": str(row.get("status") or ""),
            "sms_code": str(row.get("sms_code") or ""),
            "provider_status": str(row.get("provider_status") or ""),
            "created_at": str(row.get("created_at") or ""),
            "refunded": bool(row.get("refunded")),
            "cancel_after_seconds": cancel_after_seconds,
        }

    async def handle_sms_bootstrap(self, request: web.Request) -> web.Response:
        bot_context, user_payload, profile = await self._resolve_request_context(request)
        provider_error = ""
        results = await asyncio.gather(
            get_sms_services(page=1, page_size=1000),
            get_sms_countries(page=1, page_size=250),
            return_exceptions=True,
        )
        services_result, countries_result = results
        if isinstance(services_result, GreedySmsError):
            provider_error = services_result.public_message
            services_payload = {"services": []}
        else:
            services_payload = services_result
        if isinstance(countries_result, GreedySmsError):
            provider_error = provider_error or countries_result.public_message
            countries_payload = {"countries": []}
        else:
            countries_payload = countries_result
        return web.json_response({
            "profile": self._serialize_profile(profile, bot_context),
            "user": {
                "id": int(user_payload["id"]),
                "first_name": str(user_payload.get("first_name") or ""),
                "last_name": str(user_payload.get("last_name") or ""),
                "username": str(user_payload.get("username") or ""),
            },
            "support_url": bot_context.support_url or "",
            "services": services_payload.get("services", []),
            "countries": countries_payload.get("countries", []),
            "activations": [self._serialize_sms_activation(row) for row in list_sms_activations(int(profile["user_id"]))],
            "provider_error": provider_error,
        })

    async def handle_sms_prices(self, request: web.Request) -> web.Response:
        bot_context, _, _ = await self._resolve_request_context(request)
        service = str(request.query.get("service") or "").strip()
        try:
            country = int(request.query.get("country") or 0)
        except ValueError:
            country = 0
        # Greedy country IDs are positive. In particular, 0 is not an
        # "all countries" sentinel and produces OFFER_NOT_FOUND upstream.
        if not service or country <= 0:
            raise web.HTTPBadRequest(text=json.dumps({"error": "Выберите сервис и страну"}), content_type="application/json")
        try:
            payload = await get_sms_prices(service, country)
        except GreedySmsError as error:
            if error.offer_unavailable:
                return web.json_response({"prices": []})
            raise web.HTTPBadGateway(text=json.dumps({"error": error.public_message}), content_type="application/json")
        prices: list[dict[str, Any]] = []
        for country_row in payload.get("countries", []):
            if int(country_row.get("country") or 0) != country:
                continue
            for service_row in country_row.get("services", []):
                if str(service_row.get("name")) != service:
                    continue
                price_range = service_row.get("priceRange") or [{
                    "providerId": 0,
                    "price": service_row.get("price"),
                    "count": service_row.get("count"),
                }]
                for offer in price_range:
                    supplier_price = float(offer.get("price") or 0)
                    count = int(offer.get("count") or 0)
                    if supplier_price > 0 and count > 0:
                        price_item = {
                            "provider_id": int(offer.get("providerId") or 0),
                            "sale_price_usd": calculate_sms_sale_price(
                                supplier_price,
                                PUBLIC_API_MARKUP_PERCENT
                                if request.path.startswith("/api/v1/")
                                else SMS_BASE_MARKUP_PERCENT + get_partner_category_markup(bot_context.partner_bot, "sms"),
                            ),
                            "count": count,
                        }
                        if not request.path.startswith("/api/v1/"):
                            price_item["supplier_price_rub"] = round(supplier_price, 2)
                        prices.append(price_item)
        prices.sort(key=lambda item: float(item.get("supplier_price_rub", item["sale_price_usd"])))
        return web.json_response({"prices": prices})

    async def handle_sms_order(self, request: web.Request) -> web.Response:
        bot_context, _, profile = await self._resolve_request_context(request)
        body = await request.json()
        try:
            service = str(body["service"]).strip()
            country = int(body["country"])
            provider_id = int(body["provider_id"])
            quoted_price = round(float(body.get("supplier_price_rub") or 0), 2)
            request_key = str(body["request_key"]).strip()
        except (KeyError, TypeError, ValueError):
            raise web.HTTPBadRequest(text=json.dumps({"error": "Некорректные параметры заказа"}), content_type="application/json")
        if not service or country <= 0 or provider_id < 0:
            raise web.HTTPBadRequest(text=json.dumps({"error": "Некорректные параметры заказа"}), content_type="application/json")
        if not request_key or len(request_key) > 100:
            raise web.HTTPBadRequest(text=json.dumps({"error": "Некорректный ключ заказа"}), content_type="application/json")
        try:
            live = await get_sms_prices(service, country)
        except GreedySmsError as error:
            if error.offer_unavailable:
                raise web.HTTPConflict(
                    text=json.dumps({"error": error.public_message}),
                    content_type="application/json",
                )
            raise web.HTTPBadGateway(text=json.dumps({"error": error.public_message}), content_type="application/json")
        live_offer = None
        for country_row in live.get("countries", []):
            if int(country_row.get("country") or 0) != country:
                continue
            for service_row in country_row.get("services", []):
                if str(service_row.get("name")) == service:
                    for offer in service_row.get("priceRange") or []:
                        if int(offer.get("providerId") or 0) == provider_id and int(offer.get("count") or 0) > 0:
                            live_offer = offer
                            break
        if not live_offer:
            raise web.HTTPConflict(text=json.dumps({"error": "Тариф больше недоступен. Обновите список."}), content_type="application/json")
        supplier_price = round(float(live_offer["price"]), 2)
        if not request.path.startswith("/api/v1/") and supplier_price > quoted_price:
            raise web.HTTPConflict(text=json.dumps({"error": "Цена изменилась. Обновите список тарифов."}), content_type="application/json")
        sms_markup = (
            PUBLIC_API_MARKUP_PERCENT
            if request.path.startswith("/api/v1/")
            else SMS_BASE_MARKUP_PERCENT + get_partner_category_markup(bot_context.partner_bot, "sms")
        )
        sale_price = calculate_sms_sale_price(supplier_price, sms_markup)
        activation, reserve_error, created = reserve_sms_activation(
            user_id=int(profile["user_id"]), request_key=request_key,
            partner_bot_id=(int(bot_context.partner_bot["id"]) if bot_context.partner_bot else None),
            service_id=service, service_name=str(body.get("service_name") or service),
            country_id=country, country_name=str(body.get("country_name") or country),
            provider_id=provider_id, supplier_price_rub=supplier_price, sale_price=sale_price,
        )
        if reserve_error == "insufficient_balance":
            raise web.HTTPPaymentRequired(text=json.dumps({"error": "Недостаточно средств на балансе"}), content_type="application/json")
        if activation is None:
            raise web.HTTPConflict(text=json.dumps({"error": "Не удалось создать активацию"}), content_type="application/json")
        if created:
            try:
                provider_data = await get_sms_number(service, country, provider_id, supplier_price)
                activation = finalize_sms_activation(int(activation["id"]), provider_data)
            except GreedySmsError as error:
                refund_sms_activation(int(activation["id"]), "provider_error")
                if error.offer_unavailable:
                    raise web.HTTPConflict(
                        text=json.dumps({"error": error.public_message}),
                        content_type="application/json",
                    )
                raise web.HTTPBadGateway(text=json.dumps({"error": error.public_message}), content_type="application/json")
        updated_profile = get_user_profile(int(profile["user_id"])) or profile
        return web.json_response({
            "activation": self._serialize_sms_activation(activation),
            "profile": self._serialize_profile(updated_profile, bot_context),
        })

    async def handle_sms_activations(self, request: web.Request) -> web.Response:
        bot_context, _, profile = await self._resolve_request_context(request)
        return web.json_response({
            "items": [self._serialize_sms_activation(row) for row in list_sms_activations(int(profile["user_id"]))],
            "profile": self._serialize_profile(profile, bot_context),
        })

    async def handle_sms_activation(self, request: web.Request) -> web.Response:
        _, _, profile = await self._resolve_request_context(request)
        row = get_sms_activation(int(request.match_info["activation_id"]), int(profile["user_id"]))
        if not row:
            raise web.HTTPNotFound(text=json.dumps({"error": "Активация не найдена"}), content_type="application/json")
        return web.json_response({"activation": self._serialize_sms_activation(row)})

    async def handle_sms_check(self, request: web.Request) -> web.Response:
        bot_context, _, profile = await self._resolve_request_context(request)
        row = get_sms_activation(int(request.match_info["activation_id"]), int(profile["user_id"]))
        if not row or not row.get("provider_activation_id"):
            raise web.HTTPNotFound(text=json.dumps({"error": "Активация не найдена"}), content_type="application/json")
        if row["status"] in {"canceled", "finished"}:
            return web.json_response({"activation": self._serialize_sms_activation(row)})
        try:
            payload = await get_sms_provider_status(int(row["provider_activation_id"]))
            row = update_sms_activation_status(int(row["id"]), str(payload.get("status") or ""))
        except GreedySmsError as error:
            raise web.HTTPBadGateway(text=json.dumps({"error": error.public_message}), content_type="application/json")
        return web.json_response({
            "activation": self._serialize_sms_activation(row),
            "profile": self._serialize_profile(get_user_profile(int(profile["user_id"])) or profile, bot_context),
        })

    async def handle_sms_new_code(self, request: web.Request) -> web.Response:
        _, _, profile = await self._resolve_request_context(request)
        row = get_sms_activation(int(request.match_info["activation_id"]), int(profile["user_id"]))
        if not row or not row.get("provider_activation_id"):
            raise web.HTTPNotFound(text=json.dumps({"error": "Активация не найдена"}), content_type="application/json")
        try:
            await set_sms_provider_status(int(row["provider_activation_id"]), "NewCode")
            row = update_sms_activation_status(int(row["id"]), "STATUS_WAIT_CODE")
        except GreedySmsError as error:
            raise web.HTTPBadGateway(text=json.dumps({"error": error.public_message}), content_type="application/json")
        return web.json_response({"activation": self._serialize_sms_activation(row)})

    async def handle_sms_cancel(self, request: web.Request) -> web.Response:
        bot_context, _, profile = await self._resolve_request_context(request)
        row = get_sms_activation(int(request.match_info["activation_id"]), int(profile["user_id"]))
        if not row or not row.get("provider_activation_id"):
            raise web.HTTPNotFound(text=json.dumps({"error": "Активация не найдена"}), content_type="application/json")
        if row["status"] == "code_received":
            raise web.HTTPConflict(text=json.dumps({"error": "После получения SMS отмена недоступна"}), content_type="application/json")
        try:
            created_at = datetime.fromisoformat(str(row.get("created_at") or ""))
            remaining = int((created_at + timedelta(minutes=2) - datetime.now()).total_seconds()) + 1
        except ValueError:
            remaining = 0
        if remaining > 0:
            minutes, seconds = divmod(remaining, 60)
            wait_text = f"{minutes} мин {seconds} сек" if minutes else f"{seconds} сек"
            raise web.HTTPConflict(
                text=json.dumps({"error": f"Отмена будет доступна через {wait_text}."}),
                content_type="application/json",
            )
        refunded = bool(row.get("refunded"))
        if row["status"] != "canceled":
            try:
                await set_sms_provider_status(int(row["provider_activation_id"]), "Cancel")
            except GreedySmsError as error:
                raise web.HTTPConflict(text=json.dumps({"error": error.public_message}), content_type="application/json")
            row, refunded = refund_sms_activation(int(row["id"]), "canceled")
        updated_profile = get_user_profile(int(profile["user_id"])) or profile
        return web.json_response({
            "activation": self._serialize_sms_activation(row or {}),
            "refunded": refunded,
            "profile": self._serialize_profile(updated_profile, bot_context),
        })

    async def handle_crm_index(self, _: web.Request) -> web.Response:
        response = web.Response(
            text=self._build_index_html(
                CRM_INDEX_TEMPLATE_PATH,
                (CRM_INDEX_TEMPLATE_PATH, CRM_STYLES_PATH, CRM_APP_JS_PATH),
            ),
            content_type="text/html",
            charset="utf-8",
        )
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    async def handle_crm_styles(self, _: web.Request) -> web.FileResponse:
        return self._build_static_file_response(CRM_STYLES_PATH)

    async def handle_crm_app_script(self, _: web.Request) -> web.FileResponse:
        return self._build_static_file_response(CRM_APP_JS_PATH)

    async def handle_crm_bootstrap(self, request: web.Request) -> web.Response:
        user_payload, profile = await self._resolve_admin_request_context(request)
        return web.json_response(
            {
                "admin": {
                    "id": int(user_payload["id"]),
                    "username": user_payload.get("username") or "",
                    "first_name": user_payload.get("first_name") or "",
                    "last_name": user_payload.get("last_name") or "",
                    "balance": round(float(profile.get("balance") or 0.0), 2),
                    "registered_at": profile.get("registered_at") or "",
                },
                "bot": {
                    "username": self.main_bot_username or "",
                    "crm_url": build_crm_url() or "",
                },
                "dashboard": get_crm_dashboard_stats(),
            }
        )

    async def handle_proxy_bootstrap(self, request: web.Request) -> web.Response:
        bot_context, user_payload, profile = await self._resolve_request_context(request)
        self._schedule_miniapp_user_event_log(
            request=request,
            bot_context=bot_context,
            user_payload=user_payload,
            profile=profile,
            event_label="Открытие proxy mini app",
            app_scope="proxy",
            extra_lines=[
                f"💼 Баланс: {round(float(profile.get('balance') or 0.0), 2):.2f} $",
            ],
            cooldown_seconds=300,
        )
        all_orders = list_user_orders_any_status(int(profile["user_id"]), limit=30, offset=0)
        proxy_orders = [order for order in all_orders if str(order.get("proxy_kind") or "") == "static"]
        payload = {
            "brand": {
                "name": bot_context.brand_name,
                "badge": bot_context.brand_badge,
                "avatar_url": bot_context.brand_avatar_url or "",
                "subtitle": bot_context.subtitle or "",
            },
            "bot": {
                "username": bot_context.bot_username or "",
                "is_partner": bot_context.is_partner,
                "support_url": bot_context.support_url or "",
                "menu_url": build_proxy_url(bot_context.bot_username) or "",
            },
            "user": {
                "id": int(user_payload["id"]),
                "username": user_payload.get("username") or "",
                "first_name": user_payload.get("first_name") or "",
                "last_name": user_payload.get("last_name") or "",
            },
            "profile": self._serialize_profile(profile, bot_context),
            "orders": self._serialize_orders(proxy_orders[:12]),
            "orders_count": len(proxy_orders),
            "support_url": bot_context.support_url or "",
            "proxy_unit_price": None,
            "catalog_available": True,
            "catalog_message": "",
        }
        return web.json_response(payload)

    async def handle_proxy_catalog(self, request: web.Request) -> web.Response:
        bot_context, _, _ = await self._resolve_request_context(request)
        categories, catalog_available, catalog_message = await self._load_proxy_catalog_state()
        quality_titles = {
            "BASIC": "Basic",
            "PRIVATE": "Private",
            "DEDICATED": "Dedicated",
        }

        serialized_categories: list[dict[str, Any]] = []
        for category in categories:
            provider_price = float(category.get("price") or 0.0)
            quality = str(category.get("quality") or "BASIC").upper()
            serialized_categories.append(
                {
                    "id": int(category["id"]),
                    "country_name": str(category.get("name", {}).get("ru") or "Страна"),
                    "country_code": str(category.get("countryCode") or "").upper(),
                    "country_label": str(category.get("name", {}).get("en") or ""),
                    "image": str(category.get("image") or ""),
                    "quality": quality,
                    "type_id": quality.lower(),
                    "type_title": quality_titles.get(quality, quality.title()),
                    **(
                        {}
                        if request.path.startswith("/api/v1/")
                        else {"provider_price": round(provider_price, 2)}
                    ),
                    "sale_unit_price": (
                        round(self._get_proxy_sale_unit_price(bot_context, provider_price), 2)
                        if catalog_available
                        else None
                    ),
                    "available": bool(category.get("available")) and catalog_available,
                    "protocols": [
                        {
                            "id": int(item["id"]),
                            "name": str(item.get("name") or "HTTP"),
                            "stock": int(item.get("itemProductsCount") or 0) if catalog_available else 0,
                        }
                        for item in (category.get("items") or [])
                    ],
                }
            )

        return web.json_response(
            {
                "types": self._build_proxy_type_payloads(categories, catalog_available),
                "countries": serialized_categories,
                "proxy_unit_price": (
                    round(
                        min(
                            (self._get_proxy_sale_unit_price(bot_context, float(category.get("price") or 0.0)) for category in categories),
                            default=0.0,
                        ),
                        2,
                    )
                    if catalog_available and categories
                    else None
                ),
                "catalog_available": catalog_available,
                "catalog_message": catalog_message,
            }
        )

    async def handle_email_bootstrap(self, request: web.Request) -> web.Response:
        bot_context, user_payload, profile = await self._resolve_request_context(request)
        activations = list_email_activations(int(profile["user_id"]), limit=20)
        self._schedule_miniapp_user_event_log(
            request=request,
            bot_context=bot_context,
            user_payload=user_payload,
            profile=profile,
            event_label="Открытие email mini app",
            app_scope="email",
            extra_lines=[f"💼 Баланс: {float(profile.get('balance') or 0.0):.4f} $"],
            cooldown_seconds=300,
        )
        return web.json_response(
            {
                "brand": {
                    "name": bot_context.brand_name,
                    "badge": bot_context.brand_badge,
                    "avatar_url": bot_context.brand_avatar_url or "",
                    "subtitle": bot_context.subtitle or "",
                },
                "bot": {
                    "username": bot_context.bot_username or "",
                    "support_url": bot_context.support_url or "",
                },
                "user": {
                    "id": int(user_payload["id"]),
                    "username": user_payload.get("username") or "",
                    "first_name": user_payload.get("first_name") or "",
                    "last_name": user_payload.get("last_name") or "",
                },
                "profile": self._serialize_email_profile(profile, bot_context),
                "activations": [self._serialize_email_activation(row) for row in activations],
            }
        )

    async def handle_email_domains(self, request: web.Request) -> web.Response:
        await self._resolve_request_context(request)
        try:
            site = normalize_target_site(str(request.query.get("site") or ""))
            domains = self._filter_available_email_domains(site, await get_email_domains(site))
        except ValueError as error:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": str(error)}),
                content_type="application/json",
            ) from error
        except AnyMessageAPIError as error:
            raise web.HTTPBadGateway(
                text=json.dumps({"error": get_anymessage_error_message(error.code)}),
                content_type="application/json",
            ) from error

        items = [
            {
                "domain": row["domain"],
                "count": int(row["count"]),
                "price": calculate_email_sale_price(
                    float(row["supplier_price"]),
                    PUBLIC_API_MARKUP_PERCENT if request.path.startswith("/api/v1/") else None,
                ),
                "available": int(row["count"]) > 0,
            }
            for row in domains
            if int(row["count"]) > 0
        ]
        items.sort(key=lambda row: (float(row["price"]), row["domain"]))
        return web.json_response({"site": site, "items": items})

    async def handle_email_order(self, request: web.Request) -> web.Response:
        bot_context, user_payload, profile = await self._resolve_request_context(request)
        try:
            payload = await request.json()
        except json.JSONDecodeError as error:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "Некорректный запрос."}),
                content_type="application/json",
            ) from error

        request_key = str(payload.get("request_id") or "").strip()
        domain = str(payload.get("domain") or "").strip().lower()
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", request_key):
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "Обновите страницу и попробуйте ещё раз."}),
                content_type="application/json",
            )
        try:
            site = normalize_target_site(str(payload.get("site") or ""))
            domains = self._filter_available_email_domains(site, await get_email_domains(site))
        except ValueError as error:
            raise web.HTTPBadRequest(text=json.dumps({"error": str(error)}), content_type="application/json") from error
        except AnyMessageAPIError as error:
            raise web.HTTPBadGateway(
                text=json.dumps({"error": get_anymessage_error_message(error.code)}),
                content_type="application/json",
            ) from error

        selected = next((row for row in domains if row["domain"] == domain), None)
        if selected is None or int(selected["count"]) <= 0:
            raise web.HTTPConflict(
                text=json.dumps(
                    {
                        "error": "Этот домен только что стал недоступен и удалён из списка.",
                        "code": "domain_unavailable",
                        "domain": domain,
                    }
                ),
                content_type="application/json",
            )
        supplier_price = float(selected["supplier_price"])
        sale_price = calculate_email_sale_price(
            supplier_price,
            PUBLIC_API_MARKUP_PERCENT if request.path.startswith("/api/v1/") else None,
        )
        partner_bot_id = int(bot_context.partner_bot["id"]) if bot_context.partner_bot else None
        activation, reserve_error, created = reserve_email_activation(
            user_id=int(profile["user_id"]),
            request_key=request_key,
            site=site,
            domain=domain,
            supplier_price_usd=supplier_price,
            sale_price_usd=sale_price,
            partner_bot_id=partner_bot_id,
        )
        if reserve_error == "insufficient_balance":
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "Недостаточно средств на балансе."}),
                content_type="application/json",
            )
        if reserve_error or activation is None:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "Не удалось зарезервировать оплату."}),
                content_type="application/json",
            )
        if not created:
            updated_profile = get_user_profile(int(profile["user_id"])) or profile
            return web.json_response(
                {
                    "activation": self._serialize_email_activation(activation),
                    "profile": self._serialize_email_profile(updated_profile, bot_context),
                    "duplicate": True,
                }
            )

        try:
            provider_order = await order_email(site, domain)
            activation = finalize_email_activation(
                int(activation["id"]),
                provider_order["activation_id"],
                provider_order["email"],
            )
        except AnyMessageAPIError as error:
            fail_email_activation_and_refund(int(activation["id"]), error.code)
            raise web.HTTPBadGateway(
                text=json.dumps(
                    {
                        "error": get_anymessage_error_message(error.code),
                        "code": "domain_unavailable" if error.code == "no emails" else error.code,
                        "domain": domain if error.code == "no emails" else "",
                    }
                ),
                content_type="application/json",
            ) from error
        if activation is None:
            raise web.HTTPInternalServerError(
                text=json.dumps({"error": "Заказ создан, но не сохранился. Обратитесь в поддержку."}),
                content_type="application/json",
            )

        updated_profile = get_user_profile(int(profile["user_id"])) or profile
        self._schedule_miniapp_user_event_log(
            request=request,
            bot_context=bot_context,
            user_payload=user_payload,
            profile=updated_profile,
            event_label="Покупка email активации",
            app_scope="email",
            extra_lines=[
                f"🌐 Сайт: {site}",
                f"✉️ Домен: {domain}",
                f"💰 Цена: {sale_price:.6f} $",
                f"🆔 Activation ID: {activation.get('provider_activation_id') or '-'}",
            ],
        )
        return web.json_response(
            {
                "activation": self._serialize_email_activation(activation),
                "profile": self._serialize_email_profile(updated_profile, bot_context),
            }
        )

    async def handle_email_activations(self, request: web.Request) -> web.Response:
        bot_context, _, profile = await self._resolve_request_context(request)
        items = list_email_activations(int(profile["user_id"]), limit=50)
        return web.json_response(
            {
                "items": [self._serialize_email_activation(row) for row in items],
                "profile": self._serialize_email_profile(profile, bot_context),
            }
        )

    async def handle_email_activation(self, request: web.Request) -> web.Response:
        bot_context, _, profile = await self._resolve_request_context(request)
        activation = get_email_activation(int(request.match_info["activation_id"]), int(profile["user_id"]))
        if activation is None:
            raise web.HTTPNotFound(text=json.dumps({"error": "Активация не найдена."}), content_type="application/json")
        # Older rows stored only stripped text, which removed URLs hidden in
        # HTML buttons. Refresh once from the provider when such a letter is
        # reopened so login links (Groq and similar) become available again.
        if (
            str(activation.get("status") or "") == "received"
            and not extract_email_message_links(str(activation.get("message_text") or ""))
            and str(activation.get("provider_activation_id") or "").strip()
        ):
            try:
                refreshed = await get_email_message(str(activation["provider_activation_id"]))
                if refreshed.get("status") == "received" and refreshed.get("message"):
                    activation = update_email_activation_message(
                        int(activation["id"]),
                        str(refreshed.get("value") or activation.get("message_code") or ""),
                        str(refreshed.get("message") or "")[:20000],
                    ) or activation
            except AnyMessageAPIError:
                pass
        return web.json_response(
            {
                "activation": self._serialize_email_activation(activation),
                "profile": self._serialize_email_profile(profile, bot_context),
            }
        )

    async def handle_email_check(self, request: web.Request) -> web.Response:
        bot_context, _, profile = await self._resolve_request_context(request)
        activation = get_email_activation(int(request.match_info["activation_id"]), int(profile["user_id"]))
        if activation is None:
            raise web.HTTPNotFound(text=json.dumps({"error": "Активация не найдена."}), content_type="application/json")
        if str(activation.get("status") or "") == "received":
            return web.json_response({"activation": self._serialize_email_activation(activation)})
        if str(activation.get("status") or "") in {"canceled", "failed"}:
            raise web.HTTPConflict(text=json.dumps({"error": "Эта активация уже завершена."}), content_type="application/json")
        provider_id = str(activation.get("provider_activation_id") or "").strip()
        if not provider_id:
            raise web.HTTPConflict(text=json.dumps({"error": "Заказ ещё оформляется."}), content_type="application/json")
        try:
            result = await get_email_message(provider_id)
        except AnyMessageAPIError as error:
            raise web.HTTPBadGateway(
                text=json.dumps({"error": get_anymessage_error_message(error.code)}),
                content_type="application/json",
            ) from error
        if result["status"] == "received":
            activation = update_email_activation_message(
                int(activation["id"]),
                str(result.get("value") or ""),
                str(result.get("message") or "")[:20000],
            ) or activation
        elif result["status"] == "canceled":
            activation, _ = cancel_email_activation_and_refund(int(activation["id"]))
        updated_profile = get_user_profile(int(profile["user_id"])) or profile
        return web.json_response(
            {
                "activation": self._serialize_email_activation(activation or {}),
                "profile": self._serialize_email_profile(updated_profile, bot_context),
            }
        )

    async def handle_email_cancel(self, request: web.Request) -> web.Response:
        bot_context, _, profile = await self._resolve_request_context(request)
        activation = get_email_activation(int(request.match_info["activation_id"]), int(profile["user_id"]))
        if activation is None:
            raise web.HTTPNotFound(text=json.dumps({"error": "Активация не найдена."}), content_type="application/json")
        if str(activation.get("status") or "") == "received":
            raise web.HTTPConflict(text=json.dumps({"error": "Письмо уже получено, отмена недоступна."}), content_type="application/json")
        provider_id = str(activation.get("provider_activation_id") or "").strip()
        if provider_id and str(activation.get("status") or "") != "canceled":
            try:
                await cancel_email(provider_id)
            except AnyMessageAPIError as error:
                if error.code not in {"activation canceled", "activation already canceled"}:
                    raise web.HTTPBadGateway(
                        text=json.dumps({"error": get_anymessage_error_message(error.code)}),
                        content_type="application/json",
                    ) from error
        activation, cancel_error = cancel_email_activation_and_refund(int(activation["id"]))
        if cancel_error == "already_received":
            raise web.HTTPConflict(text=json.dumps({"error": "Письмо уже получено."}), content_type="application/json")
        updated_profile = get_user_profile(int(profile["user_id"])) or profile
        return web.json_response(
            {
                "activation": self._serialize_email_activation(activation or {}),
                "profile": self._serialize_email_profile(updated_profile, bot_context),
            }
        )

    async def handle_email_reorder(self, request: web.Request) -> web.Response:
        bot_context, user_payload, profile = await self._resolve_request_context(request)
        original = get_email_activation(int(request.match_info["activation_id"]), int(profile["user_id"]))
        if original is None:
            raise web.HTTPNotFound(
                text=json.dumps({"error": "Активация не найдена."}),
                content_type="application/json",
            )
        if str(original.get("status") or "") not in {"received", "canceled"}:
            raise web.HTTPConflict(
                text=json.dumps({"error": "Повторный заказ доступен после завершения активации."}),
                content_type="application/json",
            )
        provider_id = str(original.get("provider_activation_id") or "").strip()
        if not provider_id:
            raise web.HTTPConflict(
                text=json.dumps({"error": "Для этой активации повторный заказ недоступен."}),
                content_type="application/json",
            )
        try:
            payload = await request.json()
        except json.JSONDecodeError as error:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "Некорректный запрос."}),
                content_type="application/json",
            ) from error
        request_key = str(payload.get("request_id") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", request_key):
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "Обновите страницу и попробуйте ещё раз."}),
                content_type="application/json",
            )

        site = str(original.get("site") or "").strip()
        domain = str(original.get("domain") or "").strip().lower()
        try:
            domains = self._filter_available_email_domains(site, await get_email_domains(site))
        except AnyMessageAPIError as error:
            raise web.HTTPBadGateway(
                text=json.dumps({"error": get_anymessage_error_message(error.code)}),
                content_type="application/json",
            ) from error
        selected = next((row for row in domains if row["domain"] == domain), None)
        if selected is None or int(selected["count"]) <= 0:
            raise web.HTTPConflict(
                text=json.dumps(
                    {
                        "error": "Этот домен сейчас недоступен и удалён из списка.",
                        "code": "domain_unavailable",
                        "domain": domain,
                    }
                ),
                content_type="application/json",
            )

        supplier_price = float(selected["supplier_price"])
        sale_price = calculate_email_sale_price(
            supplier_price,
            PUBLIC_API_MARKUP_PERCENT if request.path.startswith("/api/v1/") else None,
        )
        partner_bot_id = int(bot_context.partner_bot["id"]) if bot_context.partner_bot else None
        activation, reserve_error, created = reserve_email_activation(
            user_id=int(profile["user_id"]),
            request_key=request_key,
            site=site,
            domain=domain,
            supplier_price_usd=supplier_price,
            sale_price_usd=sale_price,
            partner_bot_id=partner_bot_id,
        )
        if reserve_error == "insufficient_balance":
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "Недостаточно средств на балансе."}),
                content_type="application/json",
            )
        if reserve_error or activation is None:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "Не удалось зарезервировать оплату."}),
                content_type="application/json",
            )
        if not created:
            updated_profile = get_user_profile(int(profile["user_id"])) or profile
            return web.json_response(
                {
                    "activation": self._serialize_email_activation(activation),
                    "profile": self._serialize_email_profile(updated_profile, bot_context),
                    "duplicate": True,
                }
            )

        try:
            provider_order = await reorder_email(provider_id)
            activation = finalize_email_activation(
                int(activation["id"]),
                provider_order["activation_id"],
                provider_order["email"],
            )
        except AnyMessageAPIError as error:
            fail_email_activation_and_refund(int(activation["id"]), error.code)
            raise web.HTTPBadGateway(
                text=json.dumps(
                    {
                        "error": get_anymessage_error_message(error.code),
                        "code": "domain_unavailable" if error.code == "no emails" else error.code,
                        "domain": domain if error.code == "no emails" else "",
                    }
                ),
                content_type="application/json",
            ) from error
        if activation is None:
            raise web.HTTPInternalServerError(
                text=json.dumps({"error": "Повторный заказ создан, но не сохранился. Обратитесь в поддержку."}),
                content_type="application/json",
            )

        updated_profile = get_user_profile(int(profile["user_id"])) or profile
        self._schedule_miniapp_user_event_log(
            request=request,
            bot_context=bot_context,
            user_payload=user_payload,
            profile=updated_profile,
            event_label="Повторная email активация",
            app_scope="email",
            extra_lines=[
                f"↻ Исходная активация: #{int(original['id'])}",
                f"🌐 Сайт: {site}",
                f"✉️ Домен: {domain}",
                f"💰 Цена: {sale_price:.6f} $",
            ],
        )
        return web.json_response(
            {
                "activation": self._serialize_email_activation(activation),
                "profile": self._serialize_email_profile(updated_profile, bot_context),
            }
        )

    async def handle_proxy_orders(self, request: web.Request) -> web.Response:
        _, _, profile = await self._resolve_request_context(request)
        page = max(int(request.query.get("page", "1") or 1), 1)
        per_page = max(min(int(request.query.get("per_page", "20") or 20), 50), 1)
        offset = (page - 1) * per_page
        orders = list_user_orders_any_status(int(profile["user_id"]), limit=100, offset=0)
        proxy_orders = [order for order in orders if str(order.get("proxy_kind") or "") == "static"]
        slice_items = proxy_orders[offset: offset + per_page]
        return web.json_response(
            {
                "items": self._serialize_orders(slice_items),
                "page": page,
                "per_page": per_page,
                "total": len(proxy_orders),
            }
        )

    async def handle_proxy_manager(self, request: web.Request) -> web.Response:
        _, _, profile = await self._resolve_request_context(request)
        entries = await self._build_proxy_manager_entries(int(profile["user_id"]))
        return web.json_response({"items": entries})

    async def handle_proxy_order_detail(self, request: web.Request) -> web.Response:
        _, _, profile = await self._resolve_request_context(request)
        order_id = int(request.match_info["order_id"])
        order = get_order(order_id)
        if order is None or int(order.get("user_id") or 0) != int(profile["user_id"]):
            raise web.HTTPNotFound(
                text=json.dumps({"error": "Order not found"}),
                content_type="application/json",
            )
        if str(order.get("proxy_kind") or "") != "static":
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "Only proxy orders are available in proxy mini app"}),
                content_type="application/json",
            )
        return web.json_response({"order": self._serialize_order_detail(order)})

    async def handle_proxy_order_refresh(self, request: web.Request) -> web.Response:
        bot_context, _, profile = await self._resolve_request_context(request)
        order_id = int(request.match_info["order_id"])
        order = get_order(order_id)
        if order is None or int(order.get("user_id") or 0) != int(profile["user_id"]):
            raise web.HTTPNotFound(
                text=json.dumps({"error": "Order not found"}),
                content_type="application/json",
            )
        if str(order.get("proxy_kind") or "") != "static":
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "Only proxy orders can be refreshed in proxy mini app"}),
                content_type="application/json",
            )
        if str(order.get("status") or "") == "waiting_payment":
            provider = str(order.get("payment_provider") or "xrocket").strip().lower()
            invoice_id = str(order.get("xrocket_invoice_id") or "").strip()
            client_invoice_id = str(order.get("xrocket_client_invoice_id") or "").strip()
            try:
                if provider == "lolz":
                    paid = is_lolz_invoice_paid(
                        await get_lolz_invoice(invoice_id=invoice_id, payment_id=client_invoice_id)
                    )
                elif provider == "heleket":
                    paid = is_heleket_invoice_paid(
                        await get_heleket_payment(invoice_id=invoice_id, order_id=client_invoice_id)
                    )
                elif provider == "crystalpay":
                    paid = is_crystalpay_invoice_paid(await get_crystalpay_invoice(invoice_id))
                else:
                    paid = is_xrocket_invoice_paid(await get_xrocket_invoice(invoice_id or client_invoice_id))
                if paid:
                    mark_order_paid(order_id)
                    order = get_order(order_id) or order
            except (XRocketError, LolzError, HeleketError, CrystalPayError):
                pass
        if str(order.get("status") or "") in {"paid", "delivery_pending"}:
            await self._fulfill_proxy_order_for_miniapp(order_id)
        refreshed_order = get_order(order_id)
        if refreshed_order is None:
            raise web.HTTPNotFound(
                text=json.dumps({"error": "Order not found"}),
                content_type="application/json",
            )
        updated_profile = get_user_profile(int(profile["user_id"])) or profile
        return web.json_response(
            {
                "order": self._serialize_order_detail(refreshed_order),
                "profile": self._serialize_profile(updated_profile, bot_context),
            }
        )

    async def handle_proxy_balance_checkout(self, request: web.Request) -> web.Response:
        bot_context, _, profile = await self._resolve_request_context(request)
        try:
            payload = await request.json()
        except json.JSONDecodeError as error:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "Invalid JSON payload"}),
                content_type="application/json",
            ) from error

        raw_items = payload.get("items")
        if not isinstance(raw_items, list) or not raw_items:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "Cart is empty"}),
                content_type="application/json",
            )
        payment_provider = str(payload.get("provider") or "balance").strip().lower()
        if payment_provider not in {"balance", "crystalpay"}:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "Unsupported payment provider"}),
                content_type="application/json",
            )

        categories, catalog_available, catalog_message = await self._load_proxy_catalog_state()
        if not catalog_available:
            raise web.HTTPBadGateway(
                text=json.dumps({"error": catalog_message or "Покупка прокси временно недоступна."}),
                content_type="application/json",
            )

        categories_by_id = {int(category["id"]): category for category in categories}
        prepared_items: list[dict[str, Any]] = []
        estimated_total = 0.0
        provider_total_rub = 0.0

        for raw_item in raw_items[:20]:
            if not isinstance(raw_item, dict):
                continue
            category_id = int(raw_item.get("category_id") or 0)
            item_id = int(raw_item.get("item_id") or 0)
            quantity = int(raw_item.get("quantity") or 0)
            category = categories_by_id.get(category_id)
            if category is None or item_id <= 0 or quantity <= 0:
                continue

            protocol = next((item for item in (category.get("items") or []) if int(item.get("id") or 0) == item_id), None)
            if protocol is None:
                continue

            if quantity > max(int(protocol.get("itemProductsCount") or 0), 0):
                raise web.HTTPBadRequest(
                    text=json.dumps({"error": f"Недостаточно остатка для {protocol.get('name') or 'протокола'}"}),
                    content_type="application/json",
                )

            provider_price = float(category.get("price") or 0.0)
            unit_price = self._get_proxy_sale_unit_price(bot_context, provider_price)
            purchase_unit_price = self._get_proxy_purchase_unit_price(provider_price)
            estimated_total += round(unit_price * quantity, 2)
            provider_total_rub += provider_price * quantity
            prepared_items.append(
                {
                    "category_id": category_id,
                    "category_name": str(category.get("name", {}).get("ru") or "Страна"),
                    "item_id": item_id,
                    "protocol": str(protocol.get("name") or "HTTP"),
                    "purchase_unit_price": purchase_unit_price,
                    "quantity": quantity,
                }
            )

        if not prepared_items:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "Cart is empty"}),
                content_type="application/json",
            )
        if payment_provider != "balance" and len(prepared_items) != 1:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "Оплата счётом доступна только для одного выбора прокси за раз"}),
                content_type="application/json",
            )

        purchase_available, purchase_message = await check_proxy_provider_purchase_availability(provider_total_rub)
        if not purchase_available:
            raise web.HTTPBadGateway(
                text=json.dumps({"error": purchase_message or "Покупка прокси временно недоступна."}),
                content_type="application/json",
            )

        user_id = int(profile["user_id"])
        loyalty_multiplier = 1 - get_loyalty_discount_percent(user_id) / 100
        estimated_total = round(estimated_total * loyalty_multiplier, 2)
        if payment_provider == "crystalpay":
            item = prepared_items[0]
            order_id = self._create_proxy_order_from_selection(
                user_id=user_id,
                bot_context=bot_context,
                category_id=item["category_id"],
                category_name=item["category_name"],
                item_id=item["item_id"],
                protocol=item["protocol"],
                quantity=item["quantity"],
                purchase_unit_price=item["purchase_unit_price"],
            )
            created_order = get_order(order_id)
            amount = round(float((created_order or {}).get("total_price") or estimated_total), 2)
            success_url = f"https://t.me/{bot_context.bot_username}" if bot_context.bot_username else "https://t.me"
            try:
                invoice = await create_crystalpay_invoice(
                    amount_usd=amount,
                    description=f"SOUS MARKET proxy order #{order_id}",
                    extra=f"order:{order_id}:user:{user_id}:proxy",
                    redirect_url=success_url,
                )
            except CrystalPayError as error:
                raise web.HTTPBadGateway(
                    text=json.dumps({"error": str(error)}),
                    content_type="application/json",
                ) from error

            update_order_invoice(
                order_id=order_id,
                client_invoice_id=str(invoice.get("client_invoice_id") or invoice["invoice_id"]),
                invoice_id=invoice["invoice_id"],
                pay_url=invoice["pay_url"],
                payment_asset=invoice.get("currency") or "USD",
                payment_provider="crystalpay",
            )
            order = get_order(order_id) or created_order
            updated_profile = get_user_profile(user_id) or profile
            self._schedule_miniapp_user_event_log(
                request=request,
                bot_context=bot_context,
                user_payload={"id": user_id},
                profile=updated_profile,
                event_label="Создание proxy счёта",
                app_scope="proxy",
                extra_lines=[
                    f"🆔 Order ID: {order_id}",
                    f"💰 Сумма: {amount:.2f} $",
                    "💱 Провайдер: CryptoBot",
                    f"📌 Статус: {(order or {}).get('status') or 'waiting_payment'}",
                ],
            )
            return web.json_response(
                {
                    "orders": [self._serialize_order_detail(order)] if order else [],
                    "profile": self._serialize_profile(updated_profile, bot_context),
                    "provider": "crystalpay",
                    "provider_label": "CryptoBot",
                    "pay_url": invoice["pay_url"],
                    "invoice_id": invoice["invoice_id"],
                    "invoice_currency": invoice.get("currency"),
                    "invoice_amount": invoice.get("amount"),
                    "status": (order or {}).get("status") or "waiting_payment",
                }
            )

        current_balance = round(float(profile.get("balance") or 0.0), 2)
        if current_balance < round(estimated_total, 2):
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "Недостаточно баланса"}),
                content_type="application/json",
            )

        order_ids: list[int] = []
        for item in prepared_items:
            order_ids.append(
                self._create_proxy_order_from_selection(
                    user_id=user_id,
                    bot_context=bot_context,
                    category_id=item["category_id"],
                    category_name=item["category_name"],
                    item_id=item["item_id"],
                    protocol=item["protocol"],
                    quantity=item["quantity"],
                    purchase_unit_price=item["purchase_unit_price"],
                )
            )

        results: list[dict[str, Any]] = []
        for order_id in order_ids:
            charged_order, error_code = charge_user_balance_for_order(order_id)
            if error_code or charged_order is None:
                raise web.HTTPBadRequest(
                    text=json.dumps({"error": "Не удалось списать баланс для оформления заказа"}),
                    content_type="application/json",
                )
            final_order = await self._fulfill_proxy_order_for_miniapp(order_id)
            if final_order is not None:
                results.append(self._serialize_order_detail(final_order))

        updated_profile = get_user_profile(user_id) or profile
        self._schedule_miniapp_user_event_log(
            request=request,
            bot_context=bot_context,
            user_payload={"id": user_id},
            profile=updated_profile,
            event_label="Оплата proxy корзины",
            app_scope="proxy",
            extra_lines=[
                f"🧾 Заказов: {len(results)}",
                f"💰 Списано: {round(sum(float(order.get('total_price') or 0.0) for order in results), 2):.2f} $",
                f"🆔 Order IDs: {', '.join(str(order.get('id')) for order in results) or '-'}",
            ],
        )
        return web.json_response(
            {
                "orders": results,
                "profile": self._serialize_profile(updated_profile, bot_context),
                "balance_charged": round(sum(float(order.get("total_price") or 0.0) for order in results), 2),
            }
        )

    async def handle_bootstrap(self, request: web.Request) -> web.Response:
        bot_context, user_payload, profile = await self._resolve_request_context(request)
        self._schedule_miniapp_user_event_log(
            request=request,
            bot_context=bot_context,
            user_payload=user_payload,
            profile=profile,
            event_label="Открытие mini app",
            app_scope="store",
            extra_lines=[
                f"💼 Баланс: {round(float(profile.get('balance') or 0.0), 2):.2f} $",
            ],
            cooldown_seconds=300,
        )
        # The customer order history must only contain successfully delivered
        # purchases. Pending fulfillment stays internal and is retried by the
        # delivery worker instead of being presented as a completed order.
        orders = list_user_orders(int(profile["user_id"]), limit=10, offset=0)
        topups = list_user_topups(int(profile["user_id"]), limit=10, offset=0)
        payload = {
            "brand": {
                "name": bot_context.brand_name,
                "badge": bot_context.brand_badge,
                "avatar_url": bot_context.brand_avatar_url or "",
                "subtitle": bot_context.subtitle or "",
            },
            "bot": {
                "username": bot_context.bot_username or "",
                "is_partner": bot_context.is_partner,
                "support_url": bot_context.support_url or "",
                "menu_url": build_miniapp_url(bot_context.bot_username) or "",
            },
            "user": {
                "id": int(user_payload["id"]),
                "username": user_payload.get("username") or "",
                "first_name": user_payload.get("first_name") or "",
                "last_name": user_payload.get("last_name") or "",
            },
            "profile": self._serialize_profile(profile, bot_context),
            "orders": self._serialize_orders(orders),
            "orders_count": count_user_orders(int(profile["user_id"])),
            "transactions": self._serialize_transactions(orders, topups),
            "rules_text": MINIAPP_RULES_TEXT,
        }
        return web.json_response(payload)

    async def handle_catalog(self, request: web.Request) -> web.Response:
        bot_context, _, _ = await self._resolve_request_context(request)
        try:
            categories = await get_market_categories()
        except MarketProviderError as error:
            raise web.HTTPBadGateway(
                text=json.dumps({"error": "Не удалось загрузить товары. Попробуйте ещё раз позже."}),
                content_type="application/json",
            ) from error

        sections: list[dict[str, Any]] = []
        flat_categories: list[dict[str, Any]] = []
        mail_separator_added = False
        for category in categories:
            if not mail_separator_added and is_market_email_category(category):
                sections.append(
                    {
                        "id": "mail-separator",
                        "name": "Почты ↓",
                        "items": [],
                        "decorative": True,
                    }
                )
                mail_separator_added = True
            children = category.get("children") or []
            if children:
                section_items = []
                for child in children:
                    item = {
                        "id": int(child["id"]),
                        "name": child.get("name") or "Категория",
                        "parent_name": category.get("name") or "",
                        "kind": "market_category",
                        "description": "",
                        "redirect_url": "",
                    }
                    section_items.append(item)
                    flat_categories.append(item)
                if section_items:
                    sections.append(
                        {
                            "id": int(category["id"]),
                            "name": category.get("name") or "Категория",
                            "items": section_items,
                        }
                    )
                continue

            item = {
                "id": int(category["id"]),
                "name": category.get("name") or "Категория",
                "parent_name": "",
                "kind": "market_category",
                "description": "",
                "redirect_url": "",
            }
            flat_categories.append(item)
            sections.append(
                {
                    "id": int(category["id"]),
                    "name": category.get("name") or "Категория",
                    "items": [item],
                }
            )

        return web.json_response(
            {
                "sections": sections,
                "flat": flat_categories,
            }
        )

    async def handle_assortment(self, request: web.Request) -> web.Response:
        try:
            categories = await get_market_categories()
            leaves: list[tuple[dict, str]] = []
            stack: list[tuple[dict, str]] = [(category, "") for category in reversed(categories)]
            while stack:
                category, parent_name = stack.pop()
                name_value = category.get("name") or "Категория"
                if isinstance(name_value, dict):
                    category_name = str(name_value.get("ru") or name_value.get("en") or next(iter(name_value.values()), "Категория"))
                else:
                    category_name = str(name_value)
                children = category.get("children") or []
                if children:
                    for child in reversed(children):
                        stack.append((child, category_name))
                    continue
                leaves.append((category, parent_name or category_name))

            # Loading every category one after another made this public page take
            # minutes and nginx eventually returned an empty/error page. Fetch a
            # small number concurrently while keeping pressure on the API bounded.
            semaphore = asyncio.Semaphore(8)

            async def load_category(category: dict, category_name: str) -> list[tuple[str, str, float, int]]:
                category_rows: list[tuple[str, str, float, int]] = []
                async with semaphore:
                    page = 1
                    while page <= 100:
                        payload = await get_market_products(int(category["id"]), page=page, per_page=100)
                        items = payload.get("items") or []
                        for product in items:
                            title = str(product.get("title") or "Товар")
                            price = convert_rub_to_usdt(product.get("price", 0), MARKET_MARKUP_PERCENT)
                            category_rows.append(
                                (category_name, title, price, int(product.get("quantity") or 0))
                            )
                        meta = payload.get("meta") or {}
                        if page >= int(meta.get("last_page") or 1):
                            break
                        page += 1
                return category_rows

            results = await asyncio.gather(
                *(load_category(category, category_name) for category, category_name in leaves),
                return_exceptions=True,
            )
            rows: list[tuple[str, str, float, int]] = []
            for result in results:
                if not isinstance(result, Exception):
                    rows.extend(result)
            groups: dict[str, list[tuple[str, float, int]]] = {}
            for category_name, title, price, quantity in rows:
                groups.setdefault(category_name, []).append((title, price, quantity))
            blocks = []
            for category_name, items in groups.items():
                item_html = "".join(
                    f'<li><span>{html.escape(title)}</span><b>{price:.2f} $</b><small>В наличии: {quantity}</small></li>'
                    for title, price, quantity in items
                )
                blocks.append(f'<section><h2>{html.escape(category_name)}</h2><ul>{item_html}</ul></section>')
            content = "".join(blocks) or '<div class="empty">Товаров в наличии пока нет.</div>'
        except Exception:
            content = '<div class="empty">Не удалось загрузить ассортимент. Обновите страницу позже.</div>'
        page_html = f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Ассортимент SOUS MARKET</title><style>
body{{margin:0;background:#0d1520;color:#f5f7fa;font-family:Inter,Arial,sans-serif}}main{{max-width:920px;margin:auto;padding:28px 16px 60px}}h1{{font-size:30px;margin:0 0 8px}}.lead{{color:#9eacbc;margin:0 0 28px}}section{{background:#152232;border:1px solid #26384c;border-radius:18px;padding:18px;margin:14px 0}}h2{{font-size:19px;margin:0 0 12px}}ul{{list-style:none;margin:0;padding:0}}li{{display:grid;grid-template-columns:1fr auto;gap:6px 16px;padding:13px 0;border-top:1px solid #26384c}}li:first-child{{border-top:0}}li span{{line-height:1.35}}li b{{color:#6ee7a8;white-space:nowrap}}li small{{color:#8494a7;grid-column:1/3}}.empty{{padding:28px;background:#152232;border-radius:18px;color:#9eacbc}}@media(max-width:520px){{h1{{font-size:25px}}section{{padding:15px}}}}
</style></head><body><main><h1>Ассортимент всех товаров</h1><p class="lead">Актуальный список товаров SOUS MARKET</p>{content}</main></body></html>'''
        return web.Response(text=page_html, content_type="text/html", charset="utf-8")

    async def handle_products(self, request: web.Request) -> web.Response:
        bot_context, _, profile = await self._resolve_request_context(request)
        category_id = int(request.query.get("category_id", "0") or 0)
        page = max(int(request.query.get("page", "1") or 1), 1)
        if category_id <= 0:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "category_id is required"}),
                content_type="application/json",
            )

        try:
            products_payload = await get_market_products(
                category_id,
                page=page,
                per_page=MARKET_PRODUCTS_PAGE_SIZE,
            )
        except MarketProviderError as error:
            raise web.HTTPBadGateway(
                text=json.dumps({"error": "Не удалось загрузить товары. Попробуйте позже."}),
                content_type="application/json",
            ) from error

        favorite_ids = list_favorite_product_ids(int(profile["user_id"]))
        is_public_api = request.path.startswith("/api/v1/") or request.path.startswith("/market/api/")
        loyalty_discount = 0.0 if is_public_api else get_loyalty_discount_percent(int(profile["user_id"]))
        items = []
        for product in products_payload.get("items", []):
            attributes = [row.get("value") for row in product.get("attributes", []) if row.get("value")]
            item_payload = {
                    "id": int(product["id"]),
                    "title": product.get("title") or "Товар",
                    "description": clean_html_text(product.get("description") or ""),
                    "quantity": int(product.get("quantity") or 0),
                    "sales_count": int(product.get("sales_count") or 0),
                    "price_usdt": round(
                        convert_rub_to_usdt(product.get("price", 0), bot_context.margin_percentage)
                        * (1 - loyalty_discount / 100), 2
                    ),
                    "discount_percent": loyalty_discount,
                    "attributes": attributes[:8],
                    "favorite": int(product["id"]) in favorite_ids,
                }
            if not is_public_api:
                item_payload["price_rub"] = round(float(product.get("price") or 0.0), 2)
            items.append(item_payload)

        meta = products_payload.get("meta", {}) or {}
        return web.json_response(
            {
                "items": items,
                "page": int(meta.get("current_page") or page),
                "last_page": int(meta.get("last_page") or 1),
                "total": int(meta.get("total") or len(items)),
            }
        )

    async def handle_product_favorite(self, request: web.Request) -> web.Response:
        _, _, profile = await self._resolve_request_context(request)
        product_id = int(request.match_info.get("product_id", "0") or 0)
        if product_id <= 0:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "Некорректный товар"}),
                content_type="application/json",
            )
        try:
            payload = await request.json()
        except (json.JSONDecodeError, TypeError):
            payload = {}
        favorite = bool(payload.get("favorite", True))
        saved = set_product_favorite(int(profile["user_id"]), product_id, favorite)
        return web.json_response({"product_id": product_id, "favorite": saved})

    async def handle_favorite_products(self, request: web.Request) -> web.Response:
        bot_context, _, profile = await self._resolve_request_context(request)
        favorite_ids = sorted(list_favorite_product_ids(int(profile["user_id"])), reverse=True)
        loyalty_discount = 0.0 if (request.path.startswith("/api/v1/") or request.path.startswith("/market/api/")) else get_loyalty_discount_percent(int(profile["user_id"]))
        products = await asyncio.gather(
            *(get_market_product(product_id) for product_id in favorite_ids[:100]),
            return_exceptions=True,
        )
        items = []
        for product in products:
            if not isinstance(product, dict):
                continue
            attributes = [row.get("value") for row in product.get("attributes", []) if row.get("value")]
            items.append({
                "id": int(product["id"]),
                "title": product.get("title") or "Товар",
                "description": clean_html_text(product.get("description") or ""),
                "quantity": int(product.get("quantity") or 0),
                "sales_count": int(product.get("sales_count") or 0),
                "price_usdt": round(convert_rub_to_usdt(product.get("price", 0), bot_context.margin_percentage)
                                    * (1 - loyalty_discount / 100), 2),
                "discount_percent": loyalty_discount,
                "attributes": attributes[:8],
                "favorite": True,
            })
        return web.json_response({"items": items, "total": len(items), "page": 1, "last_page": 1})

    async def handle_orders(self, request: web.Request) -> web.Response:
        _, _, profile = await self._resolve_request_context(request)
        page = max(int(request.query.get("page", "1") or 1), 1)
        per_page = max(min(int(request.query.get("per_page", "10") or 10), 30), 1)
        offset = (page - 1) * per_page
        orders = list_user_orders(int(profile["user_id"]), limit=per_page, offset=offset)
        return web.json_response(
            {
                "items": self._serialize_orders(orders),
                "page": page,
                "per_page": per_page,
                "total": count_user_orders(int(profile["user_id"])),
            }
        )

    async def handle_transactions(self, request: web.Request) -> web.Response:
        _, _, profile = await self._resolve_request_context(request)
        orders = list_user_orders(int(profile["user_id"]), limit=20, offset=0)
        topups = list_user_topups(int(profile["user_id"]), limit=20, offset=0)
        return web.json_response({"items": self._serialize_transactions(orders, topups)})

    async def handle_order_detail(self, request: web.Request) -> web.Response:
        _, _, profile = await self._resolve_request_context(request)
        order_id = int(request.match_info["order_id"])
        order = get_order(order_id)
        if order is None or int(order.get("user_id") or 0) != int(profile["user_id"]):
            raise web.HTTPNotFound(
                text=json.dumps({"error": "Order not found"}),
                content_type="application/json",
            )
        return web.json_response({"order": self._serialize_order_detail(order)})

    async def handle_order_refresh(self, request: web.Request) -> web.Response:
        _, _, profile = await self._resolve_request_context(request)
        order_id = int(request.match_info["order_id"])
        order = get_order(order_id)
        if order is None or int(order.get("user_id") or 0) != int(profile["user_id"]):
            raise web.HTTPNotFound(
                text=json.dumps({"error": "Order not found"}),
                content_type="application/json",
            )
        if str(order.get("proxy_kind") or "") != "account":
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "Only marketplace orders can be refreshed in mini app"}),
                content_type="application/json",
            )
        if str(order.get("status") or "") in {"paid", "delivery_pending"}:
            self._schedule_market_order_fulfillment(order_id)
        refreshed_order = get_order(order_id)
        if refreshed_order is None:
            raise web.HTTPNotFound(
                text=json.dumps({"error": "Order not found"}),
                content_type="application/json",
            )
        updated_profile = get_user_profile(int(profile["user_id"])) or profile
        return web.json_response(
            {
                "order": self._serialize_order_detail(refreshed_order),
                "profile": self._serialize_profile(updated_profile, await self.resolve_bot_context(self.get_request_bot_slug(request))),
            }
        )

    async def handle_balance_checkout(self, request: web.Request) -> web.Response:
        bot_context, _, profile = await self._resolve_request_context(request)
        try:
            payload = await request.json()
        except json.JSONDecodeError as error:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "Invalid JSON payload"}),
                content_type="application/json",
            ) from error

        raw_items = payload.get("items")
        if not isinstance(raw_items, list) or not raw_items:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "Cart is empty"}),
                content_type="application/json",
            )

        categories = await get_market_categories()
        user_id = int(profile["user_id"])
        prepared_items: list[dict[str, Any]] = []
        estimated_total = 0.0

        for raw_item in raw_items[:20]:
            if not isinstance(raw_item, dict):
                continue
            product_id = int(raw_item.get("product_id") or 0)
            category_id = int(raw_item.get("category_id") or 0)
            quantity = int(raw_item.get("quantity") or 0)
            if product_id <= 0 or category_id <= 0 or quantity <= 0:
                continue

            product = await get_market_product(product_id)
            available_quantity = int(product.get("quantity") or 0)
            if quantity > available_quantity:
                raise web.HTTPBadRequest(
                    text=json.dumps(
                        {
                            "error": f"Недостаточно остатка для товара: {product.get('title') or 'Товар'}",
                        }
                    ),
                    content_type="application/json",
                )

            unit_price = convert_rub_to_usdt(product.get("price", 0), bot_context.margin_percentage)
            estimated_total += round(unit_price * quantity, 2)
            prepared_items.append(
                {
                    "product": product,
                    "quantity": quantity,
                    "category_name": self._resolve_category_name(categories, category_id, str(raw_item.get("category_name") or "")),
                }
            )

        if not prepared_items:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "Cart is empty"}),
                content_type="application/json",
            )

        loyalty_discount = 0.0 if (request.path.startswith("/api/v1/") or request.path.startswith("/market/api/")) else get_loyalty_discount_percent(user_id)
        estimated_total = round(estimated_total * (1 - loyalty_discount / 100), 2)
        current_balance = round(float(profile.get("balance") or 0.0), 2)
        if current_balance < round(estimated_total, 2):
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "Недостаточно баланса"}),
                content_type="application/json",
            )

        order_ids: list[int] = []
        results: list[dict[str, Any]] = []
        for item in prepared_items:
            order_id = self._create_market_order_from_product(
                user_id=user_id,
                bot_context=bot_context,
                category_name=item["category_name"],
                product=item["product"],
                quantity=item["quantity"],
            )
            order_ids.append(order_id)

        for order_id in order_ids:
            charged_order, error_code = charge_user_balance_for_order(order_id)
            if error_code or charged_order is None:
                raise web.HTTPBadRequest(
                    text=json.dumps({"error": "Не удалось списать баланс для оформления заказа"}),
                    content_type="application/json",
                )
            mark_order_delivery_pending(order_id)
            try:
                # Return DJEKXA's order_number in the checkout/API response;
                # the potentially longer delivery polling remains background.
                await self._create_market_supplier_reference(order_id)
            except MarketProviderError as error:
                current_order = get_order(order_id)
                if not (current_order or {}).get("supplier_order_uuid") and is_market_order_creation_rejected(error):
                    credit_order_to_user_balance(order_id)
            self._schedule_market_order_fulfillment(order_id)
            scheduled_order = get_order(order_id)
            if scheduled_order is not None:
                results.append(self._serialize_order_detail(scheduled_order))

        updated_profile = get_user_profile(user_id) or profile
        self._schedule_miniapp_user_event_log(
            request=request,
            bot_context=bot_context,
            user_payload={"id": user_id},
            profile=updated_profile,
            event_label="Оплата корзины",
            app_scope="store",
            extra_lines=[
                f"🧾 Заказов: {len(results)}",
                f"💰 Списано: {round(sum(float(order.get('total_price') or 0.0) for order in results), 2):.2f} $",
                f"🆔 Order IDs: {', '.join(str(order.get('id')) for order in results) or '-'}",
            ],
        )
        return web.json_response(
            {
                "orders": results,
                "profile": self._serialize_profile(updated_profile, bot_context),
                "balance_charged": round(sum(float(order.get("total_price") or 0.0) for order in results), 2),
            }
        )

    async def handle_create_topup(self, request: web.Request) -> web.Response:
        bot_context, user_payload, profile = await self._resolve_request_context(request)
        try:
            payload = await request.json()
        except json.JSONDecodeError as error:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "Invalid JSON payload"}),
                content_type="application/json",
            ) from error

        amount = round(float(payload.get("amount") or 0.0), 2)
        provider = str(payload.get("provider") or "xrocket").strip().lower()
        if amount <= 0:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "Amount must be greater than 0"}),
                content_type="application/json",
            )
        if amount > 100000:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "Amount is too large"}),
                content_type="application/json",
            )
        if provider not in {"xrocket", "lolz", "heleket", "crystalpay"}:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "Unsupported payment provider"}),
                content_type="application/json",
            )

        user_id = int(profile["user_id"])
        topup_id = create_topup(
            user_id,
            amount,
            int(bot_context.partner_bot["id"]) if bot_context.partner_bot else None,
        )
        success_url = f"https://t.me/{bot_context.bot_username}" if bot_context.bot_username else "https://t.me"
        description = f"Balance topup #{topup_id} | user {user_id}"

        try:
            if provider == "heleket":
                merchant_order_id = f"topup-{topup_id}-{user_id}-{int(time.time())}"
                invoice = await create_heleket_invoice(
                    order_id=merchant_order_id,
                    amount_usd=amount,
                    description=description,
                    success_url=success_url,
                    additional_data=str(topup_id),
                )
                payment_asset = invoice["currency"] or "USD"
                provider_label = "Heleket"
            elif provider == "crystalpay":
                invoice = await create_crystalpay_invoice(
                    amount_usd=amount,
                    description=f"SOUS MARKET balance top-up #{topup_id}",
                    extra=f"topup:{topup_id}:user:{user_id}",
                    redirect_url=success_url,
                )
                payment_asset = invoice["currency"] or "USD"
                provider_label = "CryptoBot"
            elif provider == "lolz":
                payment_id = f"topup-{topup_id}-{user_id}-{int(time.time())}"
                invoice = await create_lolz_invoice(
                    amount_usd=amount,
                    payment_id=payment_id,
                    comment=description,
                    success_url=success_url,
                    additional_data=str(topup_id),
                )
                payment_asset = invoice["currency"]
                provider_label = "LOLZ"
            else:
                invoice = await create_xrocket_invoice(
                    topup_id,
                    amount,
                    description,
                    payload_data={"topup_id": topup_id, "user_id": user_id},
                )
                payment_asset = invoice["currency"]
                provider_label = "XROCKET"
        except (XRocketError, LolzError, HeleketError, CrystalPayError) as error:
            raise web.HTTPBadGateway(
                text=json.dumps({"error": str(error)}),
                content_type="application/json",
            ) from error

        update_topup_invoice(
            topup_id=topup_id,
            invoice_id=invoice["invoice_id"],
            pay_url=invoice["pay_url"],
            payment_asset=payment_asset,
            payment_provider=provider,
        )
        topup = get_topup(topup_id)
        self._schedule_topup_payment_poll(topup_id)
        if bot_context.partner_bot is None:
            self._schedule_miniapp_user_event_log(
                request=request,
                bot_context=bot_context,
                user_payload=user_payload,
                profile=profile,
                event_label="Создание пополнения",
                app_scope="store",
                extra_lines=[
                    f"🆔 Topup ID: {topup_id}",
                    f"💰 Сумма: {amount:.2f} $",
                    f"💱 Провайдер: {provider_label}",
                    f"📌 Статус: {(topup or {}).get('status') or 'waiting_payment'}",
                ],
            )
        return web.json_response(
            {
                "topup_id": int(topup_id),
                "amount": round(float(topup.get("amount") or amount), 2) if topup else amount,
                "provider": provider,
                "provider_label": provider_label,
                "pay_url": invoice["pay_url"],
                "invoice_id": invoice["invoice_id"],
                "invoice_currency": invoice.get("currency"),
                "invoice_amount": invoice.get("amount"),
                "status": (topup or {}).get("status") or "waiting_payment",
                "user_id": int(user_payload["id"]),
            }
        )

    async def handle_activate_promocode(self, request: web.Request) -> web.Response:
        return await self._handle_promocode_activation(request, "store")

    async def handle_public_api_health(self, _: web.Request) -> web.Response:
        return web.json_response({"ok": True, "version": "v1"})

    async def handle_public_api_profile(self, request: web.Request) -> web.Response:
        _, user_payload, profile = await self._resolve_request_context(request)
        return web.json_response({
            "user_id": int(user_payload.get("owner_id") or user_payload["id"]),
            "balance": round(float(profile.get("balance") or 0.0), 6),
            "currency": "USDT",
        })

    @staticmethod
    def _serialize_public_api_proxy_purchase(purchase: dict) -> dict[str, Any]:
        service = str(purchase.get("partner_service") or "residential")
        quantity = (
            int(purchase.get("partner_quantity") or 0)
            if service != "residential"
            else float(purchase.get("gb") or 0.0)
        )
        payload = {
            "order_id": int(purchase["id"]),
            "request_id": str(purchase.get("api_request_key") or ""),
            "service": service,
            "quantity": quantity,
            "unit": PUBLIC_API_PROXY_SERVICE_UNITS.get(service, "unit"),
            "amount": round(float(purchase.get("price_usd") or 0.0), 4),
            "status": str(purchase.get("status") or "draft"),
            "created_at": int(purchase.get("created_at") or 0),
            "updated_at": int(purchase.get("updated_at") or 0),
        }
        if purchase.get("delivery_text"):
            payload["data"] = str(purchase["delivery_text"])
        return payload

    async def handle_public_api_proxy_services(self, request: web.Request) -> web.Response:
        await self._resolve_request_context(request)
        return web.json_response({"items": get_public_api_proxy_services()})

    async def _refresh_public_api_partner_proxy_purchase(self, purchase: dict) -> dict:
        if str(purchase.get("status") or "") not in {"processing", "delivery_pending"}:
            return purchase
        provider_order_id = int(purchase.get("provider_order_id") or 0)
        if provider_order_id <= 0:
            return purchase
        try:
            provider_order = await check_partner_proxy_order(provider_order_id)
        except PartnerProxyApiError:
            return purchase
        delivery = str(provider_order.get("data") or "").strip()
        if delivery:
            set_partner_proxy_delivery(int(purchase["id"]), provider_order_id, delivery)
            return get_maskify_proxy_purchase(int(purchase["id"])) or purchase
        return purchase

    async def handle_public_api_proxy_service_order(self, request: web.Request) -> web.Response:
        _, _, profile = await self._resolve_request_context(request)
        try:
            body = await request.json()
        except json.JSONDecodeError as error:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "Invalid JSON payload"}),
                content_type="application/json",
            ) from error
        service = str(body.get("service") or "").strip().lower()
        request_id = str(body.get("request_id") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", request_id):
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "request_id must contain 8-80 letters, digits, _ or -"}),
                content_type="application/json",
            )
        try:
            quantity = float(body.get("quantity") or 0) if service == "residential" else int(body.get("quantity") or 0)
        except (TypeError, ValueError):
            quantity = 0
        if service == "residential":
            quantity = round(float(quantity), 2)
            valid_quantity = 1 <= quantity <= 10000
            unit_price = PUBLIC_API_RESIDENTIAL_PRICE_PER_GB
        else:
            valid_quantity = service in PUBLIC_API_PROXY_SERVICE_TARIFFS and 1 <= int(quantity) <= 10000
            unit_price = 0.0
        if not valid_quantity:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "Unsupported service or quantity"}),
                content_type="application/json",
            )

        user_id = int(profile["user_id"])
        lock_key = (user_id, request_id)
        lock = self._api_proxy_locks.setdefault(lock_key, asyncio.Lock())
        async with lock:
            existing = get_maskify_proxy_purchase_by_request(user_id, request_id)
            if existing is not None:
                existing = await self._refresh_public_api_partner_proxy_purchase(existing)
                updated_profile = get_user_profile(user_id) or profile
                return web.json_response({
                    "order": self._serialize_public_api_proxy_purchase(existing),
                    "balance": round(float(updated_profile.get("balance") or 0.0), 4),
                    "idempotent_replay": True,
                })

            amount = (
                round(max(float(unit_price) * float(quantity), 0.01) + 1e-9, 4)
                if service == "residential"
                else get_public_api_proxy_service_total(service, int(quantity))
            )
            if service == "residential":
                try:
                    available_gb = await get_maskify_sellable_gb()
                except MaskifyError as error:
                    raise web.HTTPBadGateway(
                        text=json.dumps({"error": str(error)}), content_type="application/json"
                    ) from error
                if float(quantity) > available_gb + 1e-9:
                    raise web.HTTPConflict(
                        text=json.dumps({"error": "Requested traffic is unavailable"}),
                        content_type="application/json",
                    )
            else:
                try:
                    account = await get_partner_proxy_account_info()
                    supplier_price = float((account.get("prices") or {})[service])
                    supplier_balance = float(account.get("balance_usd") or 0.0)
                except (PartnerProxyApiError, KeyError, TypeError, ValueError) as error:
                    raise web.HTTPBadGateway(
                        text=json.dumps({"error": "Proxy provider is unavailable"}),
                        content_type="application/json",
                    ) from error
                if supplier_balance + 1e-9 < supplier_price * int(quantity):
                    raise web.HTTPConflict(
                        text=json.dumps({"error": "Requested proxy quantity is unavailable"}),
                        content_type="application/json",
                    )

            updated_profile, balance_error = adjust_user_balance(user_id, -amount, precision=4)
            if balance_error or updated_profile is None:
                raise web.HTTPPaymentRequired(
                    text=json.dumps({"error": "Insufficient API balance"}),
                    content_type="application/json",
                )
            purchase_id = (
                create_maskify_proxy_purchase(user_id, float(quantity), amount, request_id)
                if service == "residential"
                else create_partner_proxy_purchase(user_id, service, int(quantity), amount, request_id)
            )
            finish_maskify_proxy_purchase(purchase_id, "processing")
            residential_traffic_allocated = False
            try:
                if service == "residential":
                    await add_maskify_personal_user_traffic(user_id, float(quantity))
                    residential_traffic_allocated = True
                    raw_settings = body.get("settings") if isinstance(body.get("settings"), dict) else {}
                    proxy_count = max(1, min(int(raw_settings.get("proxy_count") or 1), 10000))
                    settings = {
                        "type": "sticky" if raw_settings.get("rotation") == "sticky" else "rotating",
                        "sessionttl": max(0, min(int(raw_settings.get("session_ttl") or 0), 86400)),
                        "country": str(raw_settings.get("country") or "").strip().upper(),
                        # Maskify residential gateways support HTTP CONNECT;
                        # HTTPS is the destination protocol, not the proxy
                        # protocol, and SOCKS5 is not available here.
                        "protocol": "http",
                        "format": str(raw_settings.get("format") or "login:password@hostname:port"),
                        "quantity": proxy_count,
                    }
                    delivery = await generate_maskify_proxies(user_id, settings)
                    set_maskify_residential_delivery(purchase_id, delivery)
                    credit_api_residential_pool(user_id, float(quantity), purchase_id)
                else:
                    created = await create_partner_proxy_order(
                        service,
                        int(quantity),
                        f"api-{purchase_id}-{uuid.uuid4().hex[:12]}",
                    )
                    provider_order_id = int(created.get("id") or 0)
                    if provider_order_id <= 0:
                        raise PartnerProxyApiError("Provider order id is missing")
                    delivery = str(created.get("data") or "").strip()
                    if delivery:
                        set_partner_proxy_delivery(purchase_id, provider_order_id, delivery)
                    else:
                        mark_partner_proxy_delivery_pending(purchase_id, provider_order_id)
            except (MaskifyError, PartnerProxyApiError, ValueError) as error:
                # Do not issue a monetary refund after the residential traffic
                # was allocated upstream. Retrying the same request id returns
                # the purchase record instead of making a duplicate allocation.
                if not residential_traffic_allocated:
                    adjust_user_balance(user_id, amount, precision=4)
                    finish_maskify_proxy_purchase(purchase_id, "credited")
                raise web.HTTPBadGateway(
                    text=json.dumps({"error": str(error), "order_id": purchase_id}),
                    content_type="application/json",
                ) from error

            purchase = get_maskify_proxy_purchase(purchase_id) or {}
            current_profile = get_user_profile(user_id) or updated_profile
            asyncio.create_task(
                dispatch_api_webhook(
                    user_id, "proxy.order.completed",
                    {"order": self._serialize_public_api_proxy_purchase(purchase)},
                )
            )
            return web.json_response({
                "order": self._serialize_public_api_proxy_purchase(purchase),
                "balance": round(float(current_profile.get("balance") or 0.0), 4),
                "idempotent_replay": False,
            })

    async def handle_public_api_proxy_service_order_detail(self, request: web.Request) -> web.Response:
        _, _, profile = await self._resolve_request_context(request)
        purchase = get_maskify_proxy_purchase(int(request.match_info["order_id"]))
        if purchase is None or int(purchase.get("telegram_user_id") or 0) != int(profile["user_id"]):
            raise web.HTTPNotFound(
                text=json.dumps({"error": "Order not found"}), content_type="application/json"
            )
        purchase = await self._refresh_public_api_partner_proxy_purchase(purchase)
        return web.json_response({"order": self._serialize_public_api_proxy_purchase(purchase)})

    @staticmethod
    def _residential_proxy_settings(body: dict[str, Any]) -> dict[str, Any]:
        raw = body.get("settings") if isinstance(body.get("settings"), dict) else body
        return {
            "type": "sticky" if raw.get("rotation") == "sticky" else "rotating",
            "sessionttl": max(0, min(int(raw.get("session_ttl") or 0), 86400)),
            "country": str(raw.get("country") or "").strip().upper(),
            "protocol": "http",
            "format": str(raw.get("format") or "login:password@hostname:port"),
            "quantity": max(1, min(int(raw.get("proxy_count") or 1), 10000)),
        }

    async def handle_public_api_residential_traffic(self, request: web.Request) -> web.Response:
        _, _, profile = await self._resolve_request_context(request)
        try:
            traffic = await get_api_residential_traffic(int(profile["user_id"]))
        except MaskifyError as error:
            raise web.HTTPBadGateway(
                text=json.dumps({"error": str(error)}), content_type="application/json"
            ) from error
        return web.json_response(traffic)

    async def handle_public_api_residential_clients_create(self, request: web.Request) -> web.Response:
        _, _, profile = await self._resolve_request_context(request)
        try:
            body = await request.json()
            client = create_api_residential_client(
                int(profile["user_id"]), str(body.get("client_id") or ""), str(body.get("label") or ""),
                rotate_agent_key=bool(body.get("rotate_agent_key", False)),
            )
            live = await get_api_residential_client_live(int(profile["user_id"]), str(client["client_id"]))
        except (json.JSONDecodeError, MaskifyError, TypeError, ValueError) as error:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": str(error)}), content_type="application/json"
            ) from error
        # A subagent key is returned only at creation/rotation. It is never
        # written to logs or returned by any read endpoint.
        response = {"client": live}
        if client.get("agent_key"):
            response["agent_key"] = client["agent_key"]
        return web.json_response(response, status=201)

    @staticmethod
    def _extract_api_key(request: web.Request) -> str:
        authorization = str(request.headers.get("Authorization") or "").strip()
        if authorization.lower().startswith("bearer "):
            return authorization[7:].strip()
        return str(request.headers.get("X-Subagent-Key") or request.headers.get("X-API-Key") or "").strip()

    async def _resolve_subagent_context(self, request: web.Request) -> dict[str, Any]:
        client = get_api_residential_client_by_key(self._extract_api_key(request))
        if client is None:
            raise web.HTTPUnauthorized(
                text=json.dumps({"error": "Invalid subagent key"}), content_type="application/json"
            )
        return client

    async def handle_public_api_subagent_traffic(self, request: web.Request) -> web.Response:
        client = await self._resolve_subagent_context(request)
        try:
            live = await get_api_residential_client_live(
                int(client["api_user_id"]), str(client["client_id"])
            )
        except MaskifyError as error:
            raise web.HTTPBadGateway(
                text=json.dumps({"error": str(error)}), content_type="application/json"
            ) from error
        return web.json_response({"agent": live})

    async def handle_public_api_subagent_proxies(self, request: web.Request) -> web.Response:
        client = await self._resolve_subagent_context(request)
        try:
            body = await request.json()
            data = await generate_api_residential_client_proxies(
                int(client["api_user_id"]), str(client["client_id"]), self._residential_proxy_settings(body)
            )
            live = await get_api_residential_client_live(
                int(client["api_user_id"]), str(client["client_id"])
            )
        except json.JSONDecodeError as error:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "Invalid JSON payload"}), content_type="application/json"
            ) from error
        except (MaskifyError, TypeError, ValueError) as error:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": str(error)}), content_type="application/json"
            ) from error
        return web.json_response({"agent": live, "data": data})

    async def handle_public_api_residential_client(self, request: web.Request) -> web.Response:
        _, _, profile = await self._resolve_request_context(request)
        try:
            client = await get_api_residential_client_live(
                int(profile["user_id"]), request.match_info["client_id"]
            )
        except MaskifyError as error:
            raise web.HTTPNotFound(
                text=json.dumps({"error": str(error)}), content_type="application/json"
            ) from error
        return web.json_response({"client": client})

    async def handle_public_api_residential_transfer(self, request: web.Request) -> web.Response:
        _, _, profile = await self._resolve_request_context(request)
        try:
            body = await request.json()
            request_id = str(body.get("request_id") or "").strip()
            if not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", request_id):
                raise MaskifyError("request_id must contain 8-80 letters, digits, _ or -")
            api_user_id = int(profile["user_id"])
            lock = self._api_proxy_locks.setdefault((api_user_id, "__residential_traffic__"), asyncio.Lock())
            async with lock:
                result = await transfer_api_residential_traffic(
                    api_user_id,
                    request.match_info["client_id"],
                    str(body.get("action") or "").strip().lower(),
                    float(body.get("gb") or 0),
                    request_id,
                )
        except json.JSONDecodeError as error:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "Invalid JSON payload"}), content_type="application/json"
            ) from error
        except (MaskifyError, TypeError, ValueError) as error:
            message = str(error)
            if "временно недоступен" in message.lower():
                raise web.HTTPServiceUnavailable(
                    text=json.dumps({"error": message}),
                    content_type="application/json",
                    headers={"Retry-After": "5"},
                ) from error
            status = web.HTTPConflict if any(
                marker in message.lower() for marker in ("insufficient", "cannot reclaim", "already used")
            ) else web.HTTPBadRequest
            raise status(text=json.dumps({"error": message}), content_type="application/json") from error
        asyncio.create_task(
            dispatch_api_webhook(int(profile["user_id"]), "residential.traffic.updated", result)
        )
        return web.json_response(result)

    async def handle_public_api_residential_proxies(self, request: web.Request) -> web.Response:
        _, _, profile = await self._resolve_request_context(request)
        try:
            body = await request.json()
            data = await generate_api_residential_client_proxies(
                int(profile["user_id"]), request.match_info["client_id"], self._residential_proxy_settings(body)
            )
            client = await get_api_residential_client_live(
                int(profile["user_id"]), request.match_info["client_id"]
            )
        except json.JSONDecodeError as error:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "Invalid JSON payload"}), content_type="application/json"
            ) from error
        except (MaskifyError, TypeError, ValueError) as error:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": str(error)}), content_type="application/json"
            ) from error
        return web.json_response({"client": client, "data": data})

    async def handle_public_api_webhook_get(self, request: web.Request) -> web.Response:
        _, _, profile = await self._resolve_request_context(request)
        webhook = get_api_webhook(int(profile["user_id"]))
        return web.json_response({"webhook": webhook})

    async def handle_public_api_webhook_put(self, request: web.Request) -> web.Response:
        _, _, profile = await self._resolve_request_context(request)
        try:
            body = await request.json()
            url = await validate_api_webhook_url(str(body.get("url") or ""))
            supplied_secret = str(body.get("secret") or "").strip()
            secret = supplied_secret or secrets.token_urlsafe(32)
            if len(secret) < 16 or len(secret) > 200:
                raise MaskifyError("Webhook secret must contain 16-200 characters")
            save_api_webhook(int(profile["user_id"]), url, secret, bool(body.get("enabled", True)))
        except (json.JSONDecodeError, MaskifyError) as error:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": str(error)}), content_type="application/json"
            ) from error
        webhook = get_api_webhook(int(profile["user_id"])) or {}
        webhook["secret_configured"] = True
        return web.json_response({"webhook": webhook})

    async def handle_public_api_webhook_delete(self, request: web.Request) -> web.Response:
        _, _, profile = await self._resolve_request_context(request)
        return web.json_response({"deleted": delete_api_webhook(int(profile["user_id"]))})

    async def handle_public_api_webhook_test(self, request: web.Request) -> web.Response:
        _, _, profile = await self._resolve_request_context(request)
        delivered = await dispatch_api_webhook(
            int(profile["user_id"]), "webhook.test", {"message": "SOUS API webhook test"}
        )
        return web.json_response({"delivered": delivered})

    async def handle_public_api_openapi(self, request: web.Request) -> web.Response:
        public_scheme = request.headers.get("X-Forwarded-Proto", request.scheme).split(",", 1)[0].strip()
        origin = f"{public_scheme}://{request.host}"
        schema = {
            "openapi": "3.1.0",
            "info": {"title": "SOUS API", "version": "1.1.0"},
            "servers": [{"url": f"{origin}/api/v1"}],
            "components": {
                "securitySchemes": {
                    "BearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "SOUS API key"}
                },
                "schemas": {
                    "TrafficTransfer": {
                        "type": "object", "required": ["action", "gb", "request_id"],
                        "properties": {
                            "action": {"type": "string", "enum": ["allocate", "reclaim"]},
                            "gb": {"type": "number", "minimum": 0.01},
                            "request_id": {"type": "string", "minLength": 8, "maxLength": 80},
                        },
                    },
                    "ProxySettings": {
                        "type": "object",
                        "properties": {
                            "proxy_count": {"type": "integer", "minimum": 1, "maximum": 10000},
                            "rotation": {"type": "string", "enum": ["rotating", "sticky"]},
                            "session_ttl": {"type": "integer", "minimum": 0, "maximum": 86400},
                            "country": {"type": "string", "examples": ["UA"]},
                            "protocol": {"type": "string", "enum": ["http", "socks5"]},
                            "format": {"type": "string"},
                        },
                    },
                },
            },
            "security": [{"BearerAuth": []}],
            "paths": {
                "/health": {"get": {"security": [], "summary": "Health check", "responses": {"200": {"description": "OK"}}}},
                "/profile": {"get": {"summary": "API balance", "responses": {"200": {"description": "Profile"}}}},
                "/proxy/services": {"get": {"summary": "Proxy services and prices", "responses": {"200": {"description": "Catalog"}}}},
                "/proxy/services/orders": {"post": {"summary": "Buy proxy service or residential GB", "responses": {"200": {"description": "Order"}}}},
                "/proxy/services/orders/{order_id}": {
                    "get": {"summary": "Proxy order", "parameters": [{"in": "path", "name": "order_id", "required": True, "schema": {"type": "integer"}}], "responses": {"200": {"description": "Order"}}}
                },
                "/proxy/residential/traffic": {"get": {"summary": "Pool and client traffic usage", "responses": {"200": {"description": "Traffic"}}}},
                "/proxy/residential/clients": {
                    "post": {"summary": "Create isolated residential subagent and return its one-time key", "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object", "required": ["client_id"], "properties": {"client_id": {"type": "string"}, "label": {"type": "string"}, "rotate_agent_key": {"type": "boolean"}}}}}}, "responses": {"201": {"description": "Subagent and one-time agent_key"}}}
                },
                "/proxy/residential/clients/{client_id}": {
                    "get": {"summary": "Client allocation and live usage", "parameters": [{"in": "path", "name": "client_id", "required": True, "schema": {"type": "string"}}], "responses": {"200": {"description": "Client"}}}
                },
                "/proxy/residential/clients/{client_id}/traffic": {
                    "post": {"summary": "Allocate or reclaim GB", "parameters": [{"in": "path", "name": "client_id", "required": True, "schema": {"type": "string"}}], "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/TrafficTransfer"}}}}, "responses": {"200": {"description": "Transfer"}}}
                },
                "/proxy/residential/clients/{client_id}/proxies": {
                    "post": {"summary": "Generate proxies bound to the client's traffic limit", "parameters": [{"in": "path", "name": "client_id", "required": True, "schema": {"type": "string"}}], "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/ProxySettings"}}}}, "responses": {"200": {"description": "Proxy list"}}}
                },
                "/proxy/residential/subagent/traffic": {"get": {"summary": "Subagent live traffic (agent key only)", "responses": {"200": {"description": "Traffic"}}}},
                "/proxy/residential/subagent/proxies": {"post": {"summary": "Generate sessions using an agent key", "responses": {"200": {"description": "Proxy list"}}}},
                "/webhooks": {
                    "get": {"summary": "Get webhook", "responses": {"200": {"description": "Webhook"}}},
                    "put": {"summary": "Create or replace webhook", "responses": {"200": {"description": "Webhook"}}},
                    "delete": {"summary": "Delete webhook", "responses": {"200": {"description": "Deleted"}}},
                },
                "/webhooks/test": {"post": {"summary": "Send test event", "responses": {"200": {"description": "Delivery result"}}}},
            },
        }
        return web.json_response(schema)

    async def handle_public_api_swagger_ui(self, _: web.Request) -> web.Response:
        page = '''<!doctype html><html><head><meta charset="utf-8"><title>SOUS API OpenAPI</title><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css"></head><body><div id="swagger-ui"></div><script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script><script>SwaggerUIBundle({url:"/api/v1/openapi.json",dom_id:"#swagger-ui",persistAuthorization:true,tryItOutEnabled:true})</script></body></html>'''
        return web.Response(text=page, content_type="text/html")

    async def handle_public_api_sdk_python(self, _: web.Request) -> web.Response:
        source = '''import requests\n\nclass SousAPI:\n    def __init__(self, api_key, base_url="https://31-77-145-160.sslip.io/api/v1"):\n        self.base_url = base_url.rstrip("/")\n        self.session = requests.Session()\n        self.session.headers.update({"Authorization": f"Bearer {api_key}"})\n\n    def _call(self, method, path, **kwargs):\n        response = self.session.request(method, self.base_url + path, timeout=30, **kwargs)\n        response.raise_for_status()\n        return response.json()\n\n    def traffic(self):\n        return self._call("GET", "/proxy/residential/traffic")\n\n    def create_client(self, client_id, label=""):\n        return self._call("POST", "/proxy/residential/clients", json={"client_id": client_id, "label": label})\n\n    def transfer(self, client_id, action, gb, request_id):\n        return self._call("POST", f"/proxy/residential/clients/{client_id}/traffic", json={"action": action, "gb": gb, "request_id": request_id})\n\n    def proxies(self, client_id, **settings):\n        return self._call("POST", f"/proxy/residential/clients/{client_id}/proxies", json=settings)\n\n    def set_webhook(self, url, secret=None):\n        body = {"url": url}\n        if secret: body["secret"] = secret\n        return self._call("PUT", "/webhooks", json=body)\n'''
        source = source.replace("https://31-77-145-160.sslip.io", "https://sousmarketfranchize.shop")
        return web.Response(text=source, content_type="text/x-python", headers={"Content-Disposition": "attachment; filename=sous_api.py"})

    async def handle_public_api_sdk_javascript(self, _: web.Request) -> web.Response:
        source = '''export class SousAPI {\n  constructor(apiKey, baseURL = "https://31-77-145-160.sslip.io/api/v1") { this.baseURL = baseURL.replace(/\\/$/, ""); this.headers = {Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json"}; }\n  async call(method, path, body) { const r = await fetch(this.baseURL + path, {method, headers: this.headers, body: body ? JSON.stringify(body) : undefined}); const data = await r.json(); if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`); return data; }\n  traffic() { return this.call("GET", "/proxy/residential/traffic"); }\n  createClient(client_id, label = "") { return this.call("POST", "/proxy/residential/clients", {client_id, label}); }\n  transfer(client_id, action, gb, request_id) { return this.call("POST", `/proxy/residential/clients/${client_id}/traffic`, {action, gb, request_id}); }\n  proxies(client_id, settings = {}) { return this.call("POST", `/proxy/residential/clients/${client_id}/proxies`, settings); }\n  setWebhook(url, secret) { return this.call("PUT", "/webhooks", secret ? {url, secret} : {url}); }\n}\n'''
        source = source.replace("https://31-77-145-160.sslip.io", "https://sousmarketfranchize.shop")
        return web.Response(text=source, content_type="text/javascript", headers={"Content-Disposition": "attachment; filename=sous-api.js"})

    async def handle_proxy_activate_promocode(self, request: web.Request) -> web.Response:
        return await self._handle_promocode_activation(request, "proxy")

    async def handle_legacy_root(self, request: web.Request) -> web.Response:
        redirect_url = build_miniapp_url(self.get_request_bot_slug(request))
        if not redirect_url:
            raise web.HTTPServiceUnavailable(
                text=json.dumps({"error": "Mini app URL is not configured"}),
                content_type="application/json",
            )
        raise web.HTTPFound(location=redirect_url)

    async def handle_proxy_legacy_root(self, request: web.Request) -> web.Response:
        redirect_url = build_proxy_url(self.get_request_bot_slug(request))
        if not redirect_url:
            raise web.HTTPServiceUnavailable(
                text=json.dumps({"error": "Proxy app URL is not configured"}),
                content_type="application/json",
            )
        raise web.HTTPFound(location=redirect_url)

    async def handle_email_legacy_root(self, request: web.Request) -> web.Response:
        redirect_url = build_email_url(self.get_request_bot_slug(request))
        if not redirect_url:
            raise web.HTTPServiceUnavailable(
                text=json.dumps({"error": "Email mini app URL is not configured"}),
                content_type="application/json",
            )
        raise web.HTTPFound(location=redirect_url)

    async def handle_sms_legacy_root(self, request: web.Request) -> web.Response:
        redirect_url = build_sms_url(self.get_request_bot_slug(request))
        if not redirect_url:
            raise web.HTTPServiceUnavailable(
                text=json.dumps({"error": "SMS mini app URL is not configured"}),
                content_type="application/json",
            )
        raise web.HTTPFound(location=redirect_url)

    def build_app(self) -> web.Application:
        app = web.Application(
            middlewares=[self.security_headers_middleware, self.safe_error_middleware],
            client_max_size=64 * 1024,
        )
        app.router.add_get("/market", self.handle_market_site_index)
        app.router.add_get("/market/", self.handle_market_site_index)
        app.router.add_get("/market/api/session", self.handle_market_site_session)
        app.router.add_post("/market/api/register", self.handle_market_site_register)
        app.router.add_post("/market/api/login", self.handle_market_site_login)
        app.router.add_post("/market/api/logout", self.handle_market_site_logout)
        app.router.add_get("/market/api/categories", self.handle_market_site_categories)
        app.router.add_get("/market/api/products", self.handle_market_site_products)
        app.router.add_post("/market/api/topup", self.handle_market_site_topup)
        app.router.add_get("/api/v1/health", self.handle_public_api_health)
        app.router.add_get("/api/v1/openapi.json", self.handle_public_api_openapi)
        app.router.add_get("/api/v1/docs", self.handle_public_api_swagger_ui)
        app.router.add_get("/api/v1/sdk/python", self.handle_public_api_sdk_python)
        app.router.add_get("/api/v1/sdk/javascript", self.handle_public_api_sdk_javascript)
        app.router.add_get("/assortment/", self.handle_assortment)
        app.router.add_get("/api/v1/profile", self.handle_public_api_profile)
        app.router.add_get("/api/v1/proxy/services", self.handle_public_api_proxy_services)
        app.router.add_post("/api/v1/proxy/services/orders", self.handle_public_api_proxy_service_order)
        app.router.add_get(
            r"/api/v1/proxy/services/orders/{order_id:\d+}",
            self.handle_public_api_proxy_service_order_detail,
        )
        app.router.add_get("/api/v1/proxy/residential/traffic", self.handle_public_api_residential_traffic)
        app.router.add_post("/api/v1/proxy/residential/clients", self.handle_public_api_residential_clients_create)
        app.router.add_get(r"/api/v1/proxy/residential/clients/{client_id:[A-Za-z0-9_-]{3,64}}", self.handle_public_api_residential_client)
        app.router.add_post(r"/api/v1/proxy/residential/clients/{client_id:[A-Za-z0-9_-]{3,64}}/traffic", self.handle_public_api_residential_transfer)
        app.router.add_post(r"/api/v1/proxy/residential/clients/{client_id:[A-Za-z0-9_-]{3,64}}/proxies", self.handle_public_api_residential_proxies)
        app.router.add_get("/api/v1/proxy/residential/subagent/traffic", self.handle_public_api_subagent_traffic)
        app.router.add_post("/api/v1/proxy/residential/subagent/proxies", self.handle_public_api_subagent_proxies)
        app.router.add_get("/api/v1/webhooks", self.handle_public_api_webhook_get)
        app.router.add_put("/api/v1/webhooks", self.handle_public_api_webhook_put)
        app.router.add_delete("/api/v1/webhooks", self.handle_public_api_webhook_delete)
        app.router.add_post("/api/v1/webhooks/test", self.handle_public_api_webhook_test)
        app.router.add_get("/api/v1/market/categories", self.handle_catalog)
        app.router.add_get("/api/v1/market/products", self.handle_products)
        app.router.add_get("/api/v1/market/favorites", self.handle_favorite_products)
        app.router.add_get("/api/v1/market/orders", self.handle_orders)
        app.router.add_post("/api/v1/market/orders", self.handle_balance_checkout)
        app.router.add_get(r"/api/v1/market/orders/{order_id:\d+}", self.handle_order_detail)
        app.router.add_post(r"/api/v1/market/orders/{order_id:\d+}/refresh", self.handle_order_refresh)
        app.router.add_get("/api/v1/sms/catalog", self.handle_sms_bootstrap)
        app.router.add_get("/api/v1/sms/prices", self.handle_sms_prices)
        app.router.add_post("/api/v1/sms/orders", self.handle_sms_order)
        app.router.add_get("/api/v1/sms/activations", self.handle_sms_activations)
        app.router.add_get(r"/api/v1/sms/activations/{activation_id:\d+}", self.handle_sms_activation)
        app.router.add_post(r"/api/v1/sms/activations/{activation_id:\d+}/check", self.handle_sms_check)
        app.router.add_post(r"/api/v1/sms/activations/{activation_id:\d+}/new-code", self.handle_sms_new_code)
        app.router.add_post(r"/api/v1/sms/activations/{activation_id:\d+}/cancel", self.handle_sms_cancel)
        app.router.add_get("/api/v1/email/domains", self.handle_email_domains)
        app.router.add_post("/api/v1/email/orders", self.handle_email_order)
        app.router.add_get("/api/v1/email/activations", self.handle_email_activations)
        app.router.add_get(r"/api/v1/email/activations/{activation_id:\d+}", self.handle_email_activation)
        app.router.add_post(r"/api/v1/email/activations/{activation_id:\d+}/check", self.handle_email_check)
        app.router.add_post(r"/api/v1/email/activations/{activation_id:\d+}/reorder", self.handle_email_reorder)
        app.router.add_get("/api/v1/proxy/catalog", self.handle_proxy_catalog)
        app.router.add_get("/api/v1/proxy/orders", self.handle_proxy_orders)
        app.router.add_post("/api/v1/proxy/orders", self.handle_proxy_balance_checkout)
        app.router.add_get(r"/api/v1/proxy/orders/{order_id:\d+}", self.handle_proxy_order_detail)
        app.router.add_post(r"/api/v1/proxy/orders/{order_id:\d+}/refresh", self.handle_proxy_order_refresh)
        app.router.add_get("/miniapp/", self.handle_legacy_root)
        app.router.add_get("/miniapp/{bot_slug:[A-Za-z0-9_]+}/", self.handle_index)
        app.router.add_get("/partner/{bot_slug:[A-Za-z0-9_]+}/", self.handle_partner_index)
        app.router.add_get("/partner/{bot_slug:[A-Za-z0-9_]+}/styles.css", self.handle_partner_styles)
        app.router.add_get("/partner/{bot_slug:[A-Za-z0-9_]+}/app.js", self.handle_partner_app_script)
        app.router.add_get("/partner/{bot_slug:[A-Za-z0-9_]+}/reference/bots/{asset_name:ref-[0-9]{2}\\.png}", self.handle_partner_reference_bot_asset)
        app.router.add_get("/partner/{bot_slug:[A-Za-z0-9_]+}/api/dashboard", self.handle_partner_dashboard)
        app.router.add_post("/partner/{bot_slug:[A-Za-z0-9_]+}/api/create-bot", self.handle_partner_create_bot)
        app.router.add_post("/partner/{bot_slug:[A-Za-z0-9_]+}/api/settings", self.handle_partner_settings)
        app.router.add_post("/partner/{bot_slug:[A-Za-z0-9_]+}/api/config", self.handle_partner_config)
        app.router.add_post("/partner/{bot_slug:[A-Za-z0-9_]+}/api/action", self.handle_partner_action)
        app.router.add_post("/partner/{bot_slug:[A-Za-z0-9_]+}/api/broadcast", self.handle_partner_broadcast)
        app.router.add_post("/partner/{bot_slug:[A-Za-z0-9_]+}/api/promo", self.handle_partner_promo)
        app.router.add_post("/partner/{bot_slug:[A-Za-z0-9_]+}/api/payout", self.handle_partner_payout)
        app.router.add_get("/miniapp/{bot_slug:[A-Za-z0-9_]+}/avatar", self.handle_bot_avatar)
        app.router.add_get("/miniapp/{bot_slug:[A-Za-z0-9_]+}/styles.css", self.handle_styles)
        app.router.add_get("/miniapp/{bot_slug:[A-Za-z0-9_]+}/app.js", self.handle_app_script)
        app.router.add_get("/miniapp/{bot_slug:[A-Za-z0-9_]+}/api/bootstrap", self.handle_bootstrap)
        app.router.add_get("/miniapp/{bot_slug:[A-Za-z0-9_]+}/api/catalog", self.handle_catalog)
        app.router.add_get("/miniapp/{bot_slug:[A-Za-z0-9_]+}/api/products", self.handle_products)
        app.router.add_get("/miniapp/{bot_slug:[A-Za-z0-9_]+}/api/favorites", self.handle_favorite_products)
        app.router.add_post(
            "/miniapp/{bot_slug:[A-Za-z0-9_]+}/api/products/{product_id:\\d+}/favorite",
            self.handle_product_favorite,
        )
        app.router.add_get("/miniapp/{bot_slug:[A-Za-z0-9_]+}/api/orders", self.handle_orders)
        app.router.add_get("/miniapp/{bot_slug:[A-Za-z0-9_]+}/api/orders/{order_id:\\d+}", self.handle_order_detail)
        app.router.add_post("/miniapp/{bot_slug:[A-Za-z0-9_]+}/api/orders/{order_id:\\d+}/refresh", self.handle_order_refresh)
        app.router.add_get("/miniapp/{bot_slug:[A-Za-z0-9_]+}/api/transactions", self.handle_transactions)
        app.router.add_post("/miniapp/{bot_slug:[A-Za-z0-9_]+}/api/topup", self.handle_create_topup)
        app.router.add_post("/miniapp/{bot_slug:[A-Za-z0-9_]+}/api/promocode/activate", self.handle_activate_promocode)
        app.router.add_post("/miniapp/{bot_slug:[A-Za-z0-9_]+}/api/checkout/balance", self.handle_balance_checkout)
        app.router.add_get("/proxy/", self.handle_proxy_legacy_root)
        app.router.add_get("/proxy/{bot_slug:[A-Za-z0-9_]+}/", self.handle_proxy_index)
        app.router.add_get("/proxy/{bot_slug:[A-Za-z0-9_]+}/styles.css", self.handle_proxy_styles)
        app.router.add_get("/proxy/{bot_slug:[A-Za-z0-9_]+}/app.js", self.handle_proxy_app_script)
        app.router.add_get("/proxy/{bot_slug:[A-Za-z0-9_]+}/api/bootstrap", self.handle_proxy_bootstrap)
        app.router.add_get("/proxy/{bot_slug:[A-Za-z0-9_]+}/api/catalog", self.handle_proxy_catalog)
        app.router.add_get("/proxy/{bot_slug:[A-Za-z0-9_]+}/api/orders", self.handle_proxy_orders)
        app.router.add_get("/proxy/{bot_slug:[A-Za-z0-9_]+}/api/manager", self.handle_proxy_manager)
        app.router.add_get("/proxy/{bot_slug:[A-Za-z0-9_]+}/api/orders/{order_id:\\d+}", self.handle_proxy_order_detail)
        app.router.add_post("/proxy/{bot_slug:[A-Za-z0-9_]+}/api/orders/{order_id:\\d+}/refresh", self.handle_proxy_order_refresh)
        app.router.add_post("/proxy/{bot_slug:[A-Za-z0-9_]+}/api/topup", self.handle_create_topup)
        app.router.add_post("/proxy/{bot_slug:[A-Za-z0-9_]+}/api/promocode/activate", self.handle_proxy_activate_promocode)
        app.router.add_post("/proxy/{bot_slug:[A-Za-z0-9_]+}/api/checkout/balance", self.handle_proxy_balance_checkout)
        app.router.add_get("/email/", self.handle_email_legacy_root)
        app.router.add_get("/email/{bot_slug:[A-Za-z0-9_]+}/", self.handle_email_index)
        app.router.add_get("/email/{bot_slug:[A-Za-z0-9_]+}/styles.css", self.handle_email_styles)
        app.router.add_get("/email/{bot_slug:[A-Za-z0-9_]+}/app.js", self.handle_email_app_script)
        app.router.add_get("/email/{bot_slug:[A-Za-z0-9_]+}/api/bootstrap", self.handle_email_bootstrap)
        app.router.add_get("/email/{bot_slug:[A-Za-z0-9_]+}/api/domains", self.handle_email_domains)
        app.router.add_post("/email/{bot_slug:[A-Za-z0-9_]+}/api/order", self.handle_email_order)
        app.router.add_get("/email/{bot_slug:[A-Za-z0-9_]+}/api/activations", self.handle_email_activations)
        app.router.add_get(r"/email/{bot_slug:[A-Za-z0-9_]+}/api/activations/{activation_id:\d+}", self.handle_email_activation)
        app.router.add_post(r"/email/{bot_slug:[A-Za-z0-9_]+}/api/activations/{activation_id:\d+}/check", self.handle_email_check)
        app.router.add_post(r"/email/{bot_slug:[A-Za-z0-9_]+}/api/activations/{activation_id:\d+}/cancel", self.handle_email_cancel)
        app.router.add_post(r"/email/{bot_slug:[A-Za-z0-9_]+}/api/activations/{activation_id:\d+}/reorder", self.handle_email_reorder)
        app.router.add_post("/email/{bot_slug:[A-Za-z0-9_]+}/api/topup", self.handle_create_topup)
        app.router.add_get("/sms/", self.handle_sms_legacy_root)
        app.router.add_get("/sms/{bot_slug:[A-Za-z0-9_]+}/", self.handle_sms_index)
        app.router.add_get("/sms/{bot_slug:[A-Za-z0-9_]+}/styles.css", self.handle_sms_styles)
        app.router.add_get("/sms/{bot_slug:[A-Za-z0-9_]+}/app.js", self.handle_sms_app_script)
        app.router.add_get("/sms/{bot_slug:[A-Za-z0-9_]+}/api/bootstrap", self.handle_sms_bootstrap)
        app.router.add_get("/sms/{bot_slug:[A-Za-z0-9_]+}/api/prices", self.handle_sms_prices)
        app.router.add_post("/sms/{bot_slug:[A-Za-z0-9_]+}/api/order", self.handle_sms_order)
        app.router.add_get("/sms/{bot_slug:[A-Za-z0-9_]+}/api/activations", self.handle_sms_activations)
        app.router.add_get(r"/sms/{bot_slug:[A-Za-z0-9_]+}/api/activations/{activation_id:\d+}", self.handle_sms_activation)
        app.router.add_post(r"/sms/{bot_slug:[A-Za-z0-9_]+}/api/activations/{activation_id:\d+}/check", self.handle_sms_check)
        app.router.add_post(r"/sms/{bot_slug:[A-Za-z0-9_]+}/api/activations/{activation_id:\d+}/new-code", self.handle_sms_new_code)
        app.router.add_post(r"/sms/{bot_slug:[A-Za-z0-9_]+}/api/activations/{activation_id:\d+}/cancel", self.handle_sms_cancel)
        app.router.add_post("/sms/{bot_slug:[A-Za-z0-9_]+}/api/topup", self.handle_create_topup)
        app.router.add_get("/crm/", self.handle_crm_index)
        app.router.add_get("/crm/styles.css", self.handle_crm_styles)
        app.router.add_get("/crm/app.js", self.handle_crm_app_script)
        app.router.add_get("/crm/api/bootstrap", self.handle_crm_bootstrap)
        return app


async def configure_bot_menu_button(bot: Bot, bot_username: str | None, brand_name: str | None = None) -> None:
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())


async def ensure_miniapp_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        commands=[BotCommand(command=command_name, description=description) for command_name, description in MINIAPP_ENTRY_COMMANDS]
    )
