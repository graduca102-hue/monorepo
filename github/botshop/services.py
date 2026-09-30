import asyncio
import base64
from urllib.parse import urlsplit
import copy
import hashlib
import ipaddress
import json
import os
import re
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiohttp
from dotenv import load_dotenv
from yarl import URL


load_dotenv()

PROXY_PROVIDER_BASE_URL = "https://" + "proxy" + "soxy.com"
PROXY_PROVIDER_AUTH_TOKEN = os.getenv(
    "PROXY_PROVIDER_AUTH_TOKEN",
    os.getenv("PROXYSOXY_AUTH_TOKEN", ""),
).strip()
PROXY_PROVIDER_ORDER_DURATION_DAYS = int(
    os.getenv(
        "PROXY_PROVIDER_ORDER_DURATION_DAYS",
        os.getenv("PROXYSOXY_ORDER_DURATION_DAYS", "14"),
    )
    or 14
)
PROXY_PROVIDER_CREATE_ATTEMPTS = int(os.getenv("PROXY_PROVIDER_CREATE_ATTEMPTS", "3") or 3)
PROXY_PROVIDER_CREATE_RETRY_DELAY_SECONDS = float(os.getenv("PROXY_PROVIDER_CREATE_RETRY_DELAY_SECONDS", "0.8") or 0.8)
PROXY_PROVIDER_DETAILS_POLL_ATTEMPTS = int(os.getenv("PROXY_PROVIDER_DETAILS_POLL_ATTEMPTS", "5") or 5)
PROXY_PROVIDER_DETAILS_POLL_INTERVAL_SECONDS = max(
    0.05,
    float(os.getenv("PROXY_PROVIDER_DETAILS_POLL_INTERVAL_SECONDS", "0.15") or 0.15),
)
PROXY_PROVIDER_VALIDATE_BEFORE_DELIVERY = (
    os.getenv("PROXY_PROVIDER_VALIDATE_BEFORE_DELIVERY", "0").strip() == "1"
)
PROXY_PROVIDER_VALIDATION_TIMEOUT_SECONDS = max(
    0.25,
    float(os.getenv("PROXY_PROVIDER_VALIDATION_TIMEOUT_SECONDS", "2") or 2),
)
PROXY_PROVIDER_BALANCE_ATTEMPTS = int(os.getenv("PROXY_PROVIDER_BALANCE_ATTEMPTS", "3") or 3)
PROXY_PROVIDER_BALANCE_RETRY_DELAY_SECONDS = float(os.getenv("PROXY_PROVIDER_BALANCE_RETRY_DELAY_SECONDS", "0.6") or 0.6)
PROXY_PROVIDER_CATEGORIES_CACHE_PATH = Path(__file__).resolve().with_name("proxy_provider_categories_cache.json")
PARTNER_PROXY_API_BASE_URL = os.getenv("PARTNER_PROXY_API_BASE_URL", "http://193.161.204.52:8000").strip().rstrip("/")
PARTNER_API_TOKEN = os.getenv("PARTNER_API_TOKEN", "").strip()

XROCKET_API_BASE_URL = os.getenv("XROCKET_API_BASE_URL", "https://pay.xrocket.exchange").strip()
XROCKET_API_TOKEN = os.getenv("XROCKET_API_TOKEN", "").strip()
XROCKET_SKIP_SSL_VERIFY = os.getenv("XROCKET_SKIP_SSL_VERIFY", "0").strip() == "1"
XROCKET_PAYMENT_ASSET = os.getenv("XROCKET_PAYMENT_ASSET", "USDT").strip()
HELEKET_API_BASE_URL = os.getenv("HELEKET_API_BASE_URL", "https://api.heleket.com").strip().rstrip("/")
HELEKET_PAYMENT_API_KEY = os.getenv("HELEKET_PAYMENT_API_KEY", os.getenv("HELEKET_API_KEY", "")).strip()
HELEKET_MERCHANT_UUID = os.getenv("HELEKET_MERCHANT_UUID", os.getenv("HELEKET_MERCHANT_ID", "")).strip()
LOLZ_API_BASE_URL = os.getenv("LOLZ_API_BASE_URL", "https://prod-api.lzt.market").strip().rstrip("/")
LOLZ_API_KEY = os.getenv("LOLZ_API_KEY", "").strip()
LOLZ_MERCHANT_ID = int(os.getenv("LOLZ_MERCHANT_ID", "0") or 0)
LOLZ_PAYMENT_CURRENCY = os.getenv("LOLZ_PAYMENT_CURRENCY", "USD").strip().upper()
LOLZ_MERCHANT_CREATE_URL = "https://lzt.market/account/merchants"
CRYSTALPAY_API_BASE_URL = os.getenv("CRYSTALPAY_API_BASE_URL", "https://api.crystalpay.io/v3").strip().rstrip("/")
CRYSTALPAY_AUTH_LOGIN = os.getenv("CRYSTALPAY_AUTH_LOGIN", "").strip()
CRYSTALPAY_AUTH_SECRET = os.getenv("CRYSTALPAY_AUTH_SECRET", "").strip()
COINGECKO_API_BASE_URL = os.getenv("COINGECKO_API_BASE_URL", "https://api.coingecko.com/api/v3").strip().rstrip("/")
XROCKET_CURRENCIES_CACHE_TTL_SECONDS = int(os.getenv("XROCKET_CURRENCIES_CACHE_TTL_SECONDS", "300"))
XROCKET_RATES_CACHE_TTL_SECONDS = int(os.getenv("XROCKET_RATES_CACHE_TTL_SECONDS", "120"))

MARKET_API_BASE_URL = os.getenv(
    "MARKET_API_BASE_URL",
    os.getenv("DJEKXA_BASE_URL", "https://djekxa.com"),
).strip().rstrip("/")
MARKET_API_TOKEN = os.getenv("MARKET_API_TOKEN", os.getenv("DJEKXA_API_TOKEN", "")).strip()
MARKET_RUB_PER_USDT = float(os.getenv("MARKET_RUB_PER_USDT", os.getenv("DJEKXA_RUB_PER_USDT", "100")) or 100)
MARKET_USER_AGENT = os.getenv(
    "MARKET_USER_AGENT",
    os.getenv(
        "DJEKXA_USER_AGENT",
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
        ),
    ),
).strip()
INSTAGRAM_VALIDATE_BEFORE_DELIVERY = (
    os.getenv("INSTAGRAM_VALIDATE_BEFORE_DELIVERY", "1").strip() == "1"
)
INSTAGRAM_VALIDATION_TIMEOUT_SECONDS = max(
    2.0,
    float(os.getenv("INSTAGRAM_VALIDATION_TIMEOUT_SECONDS", "10") or 10),
)
INSTAGRAM_VALIDATION_COOLDOWN_SECONDS = max(
    15.0,
    float(os.getenv("INSTAGRAM_VALIDATION_COOLDOWN_SECONDS", "60") or 60),
)
INSTAGRAM_WEB_APP_ID = os.getenv("INSTAGRAM_WEB_APP_ID", "936619743392459").strip()
MARKET_CACHE_TTL_SECONDS = int(os.getenv("MARKET_CACHE_TTL_SECONDS", os.getenv("DJEKXA_CACHE_TTL_SECONDS", "600")))
MARKET_RUB_RATE_CACHE_TTL_SECONDS = int(
    os.getenv("MARKET_RUB_RATE_CACHE_TTL_SECONDS", os.getenv("DJEKXA_RUB_RATE_CACHE_TTL_SECONDS", "3600"))
)

COUNTRY_ORDER = {
    "Россия": 0,
    "Великобритания": 1,
    "Германия": 2,
    "США": 3,
    "Нидерланды": 4,
    "Казахстан": 5,
}

COUNTRY_BUTTON_LABELS = {
    "Россия": "РОССИЯ | IPV4 ПРОКСИ",
    "Великобритания": "АНГЛИЯ | IPV4 ПРОКСИ",
    "Германия": "ГЕРМАНИЯ | IPV4 ПРОКСИ",
    "США": "США | IPV4 ПРОКСИ",
    "Нидерланды": "НИДЕРЛАНДЫ | IPV4 ПРОКСИ",
    "Казахстан": "КАЗАХСТАН | IPV4 ПРОКСИ",
}

QUALITY_LABELS = {
    "BASIC": "BASIC",
    "PRIVATE": "PRIVATE",
    "DEDICATED": "DEDICATED",
}

XROCKET_PAID_STATUSES = {"paid", "completed", "success"}
HELEKET_PAID_STATUSES = {"paid", "paid_over"}
MARKET_READY_STATUSES = {"completed", "partially-completed"}
_MARKET_COOKIE_CACHE: dict[str, object] = {"cookies": None, "expires_at": 0.0}
_MARKET_RESPONSE_CACHE: dict[tuple, dict[str, object]] = {}
_MARKET_RUB_RATE_CACHE: dict[str, float | None] = {"value": None, "expires_at": 0.0}
_MARKET_COOKIE_LOCK = asyncio.Lock()
_MARKET_RESPONSE_CACHE_LOCK = asyncio.Lock()
_MARKET_REQUEST_SEMAPHORE = asyncio.Semaphore(4)
_MARKET_REQUEST_COOLDOWN = 0.15
_MARKET_LAST_REQUEST_AT = 0.0
_MARKET_RATE_LIMIT_UNTIL = 0.0
_MARKET_INFLIGHT: dict[tuple, asyncio.Task] = {}
_MARKET_INFLIGHT_LOCK = asyncio.Lock()
_MARKET_RUB_RATE_LOCK = asyncio.Lock()
_MARKET_CATEGORY_CACHE: dict[str, object] = {"value": None, "expires_at": 0.0}
_MARKET_CATEGORY_CACHE_LOCK = asyncio.Lock()
_XROCKET_CURRENCIES_CACHE: dict[str, object] = {"value": None, "expires_at": 0.0}
_XROCKET_CURRENCIES_LOCK = asyncio.Lock()
_XROCKET_RATE_CACHE: dict[str, dict[str, float | str]] = {}
_XROCKET_RATE_LOCK = asyncio.Lock()

XROCKET_MEME_PAYMENT_ASSETS = {
    asset.strip().upper()
    for asset in os.getenv(
        "XROCKET_MEME_PAYMENT_ASSETS",
        "DOGS,CATS,TRUMP,MELANIA,WOOF,1MBABYDOGE,PUNK,FISH,VIRUS1,CATI,ALENKA,KINGY,PUMP",
    ).split(",")
    if asset.strip()
}
XROCKET_PAYMENT_RATE_IDS = {
    "USDT": "tether",
    "USDC": "usd-coin",
    "TONCOIN": "the-open-network",
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "BNB": "binancecoin",
    "TRX": "tron",
    "SOL": "solana",
    "XAUT": "tether-gold",
}


class ProxyProviderError(Exception):
    pass


class PartnerProxyApiError(Exception):
    pass


PROXY_GENERIC_ERROR_CODE = "ошибка hakiro435"


async def _partner_proxy_api_request(method: str, path: str, *, json_body: dict | None = None) -> dict:
    if not PARTNER_API_TOKEN:
        raise PartnerProxyApiError("API прокси не настроен")
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            async with session.request(
                method,
                f"{PARTNER_PROXY_API_BASE_URL}{path}",
                headers={"token": PARTNER_API_TOKEN, "Accept": "application/json"},
                json=json_body,
            ) as response:
                data = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
            raise PartnerProxyApiError("Поставщик прокси временно недоступен") from error
    if response.status >= 400:
        message = data.get("detail") or data.get("message") or data.get("error") if isinstance(data, dict) else None
        raise PartnerProxyApiError(str(message or "Поставщик прокси вернул ошибку"))
    if not isinstance(data, dict):
        raise PartnerProxyApiError("Некорректный ответ поставщика прокси")
    return data


async def get_partner_proxy_account_info() -> dict:
    return await _partner_proxy_api_request("GET", "/account_info/")


async def create_partner_proxy_order(service: str, balance_amount: int, nonce: str) -> dict:
    return await _partner_proxy_api_request(
        "POST",
        "/create_order/",
        json_body={
            "service": str(service),
            "product_type": "cdkey",
            "balance_amount": int(balance_amount),
            "nonce": str(nonce),
        },
    )


async def check_partner_proxy_order(provider_order_id: int) -> dict:
    return await _partner_proxy_api_request("POST", "/check_order/", json_body={"id": int(provider_order_id)})


class XRocketError(Exception):
    pass


class HeleketError(Exception):
    pass


class CrystalPayError(Exception):
    pass


class MarketProviderError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


# Public-facing catalog message.  Never expose upstream response text here:
# it may contain provider names, implementation details, or rate-limit tokens.
MARKET_PUBLIC_ERROR = "Не удалось загрузить товары. Попробуйте обновить страницу позже."


def market_public_error(_: Exception | None = None) -> str:
    """Return a stable user-facing message without upstream/internal details."""
    return MARKET_PUBLIC_ERROR


def normalize_market_provider_error_message(message: str, *, status_code: int | None = None) -> str:
    lowered = str(message or "").strip().lower()
    if status_code == 429 or "too many attempts" in lowered or "too many requests" in lowered:
        return MARKET_PUBLIC_ERROR
    if "temporarily unavailable" in lowered or "service unavailable" in lowered:
        return MARKET_PUBLIC_ERROR
    return MARKET_PUBLIC_ERROR


def is_market_order_creation_rejected(error: Exception) -> bool:
    """True only when the supplier explicitly rejected a not-yet-created order."""
    status_code = getattr(error, "status_code", None)
    if status_code is not None and 400 <= int(status_code) < 500 and int(status_code) not in {408, 425, 429}:
        return True
    message = str(error or "").strip().lower()
    definitive_markers = (
        "кол-во не должно превышать 0",
        "количество не должно превышать 0",
        "недостаточно товара",
        "недостаточный остаток",
        "товар закончился",
        "товар недоступен",
        "product is out of stock",
        "insufficient stock",
    )
    return any(marker in message for marker in definitive_markers)


class LolzError(Exception):
    pass


def _is_market_cacheable_request(method: str, path: str, json_body: dict | None) -> bool:
    """Cache catalogue reads only; order state and balances are live data."""
    if method.upper() != "GET" or json_body is not None:
        return False
    normalized_path = "/" + str(path or "").strip().lstrip("/")
    return normalized_path == "/api/v2/categories" or normalized_path.startswith("/api/v2/products")


def _extract_proxy_provider_error_message(data: object) -> str:
    if isinstance(data, dict):
        message = str(data.get("message") or "").strip()
        if message:
            return message
    return PROXY_GENERIC_ERROR_CODE


def normalize_proxy_provider_error_message(error_text: str) -> str:
    lowered = str(error_text or "").strip().lower()
    if "ip not in whitelist" in lowered:
        return "Сервис временно недоступен. Попробуйте позже."
    if "unauthorized" in lowered or "forbidden" in lowered or "invalid token" in lowered:
        return "Сервис временно недоступен. Попробуйте позже."
    if "insufficient" in lowered and "balance" in lowered:
        return "Сервис временно недоступен. Попробуйте позже."
    if "balance" in lowered and "not enough" in lowered:
        return "Сервис временно недоступен. Попробуйте позже."
    return PROXY_GENERIC_ERROR_CODE


async def _get_proxy_provider_bearer_token(session: aiohttp.ClientSession) -> str:
    if not PROXY_PROVIDER_AUTH_TOKEN:
        raise ProxyProviderError(PROXY_GENERIC_ERROR_CODE)

    async with session.post(
        f"{PROXY_PROVIDER_BASE_URL}/api/api-auth",
        json={"authToken": PROXY_PROVIDER_AUTH_TOKEN},
    ) as response:
        data = await response.json(content_type=None)
        if response.status != 200 or not data.get("token"):
            raise ProxyProviderError(_extract_proxy_provider_error_message(data))
        return data["token"]


async def _proxy_provider_request(method: str, path: str, *, params: dict | None = None, json_body: dict | None = None):
    async with aiohttp.ClientSession() as session:
        bearer_token = await _get_proxy_provider_bearer_token(session)
        headers = {"Authorization": f"Bearer {bearer_token}"}
        async with session.request(
            method,
            f"{PROXY_PROVIDER_BASE_URL}{path}",
            headers=headers,
            params=params,
            json=json_body,
        ) as response:
            try:
                data = await response.json(content_type=None)
            except Exception:
                await response.text()
                raise ProxyProviderError(PROXY_GENERIC_ERROR_CODE)

            if response.status >= 500:
                raise ProxyProviderError(PROXY_GENERIC_ERROR_CODE)
            if response.status != 200:
                raise ProxyProviderError(_extract_proxy_provider_error_message(data))
            return data


def _load_proxy_provider_categories_cache() -> list[dict]:
    try:
        payload = json.loads(PROXY_PROVIDER_CATEGORIES_CACHE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []

    categories = payload.get("categories")
    if not isinstance(categories, list):
        return []
    return categories


def _save_proxy_provider_categories_cache(categories: list[dict]) -> None:
    try:
        PROXY_PROVIDER_CATEGORIES_CACHE_PATH.write_text(
            json.dumps(
                {
                    "saved_at": int(time.time()),
                    "categories": categories,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


def _sort_categories(categories: list[dict]) -> list[dict]:
    return sorted(
        categories,
        key=lambda item: (
            COUNTRY_ORDER.get(item.get("name", {}).get("ru", ""), 99),
            item.get("price", 0),
        ),
    )


async def get_mobile_proxy_categories() -> list[dict]:
    return []


async def get_static_proxy_categories_snapshot() -> tuple[list[dict], bool]:
    last_error: ProxyProviderError | None = None
    for attempt in range(3):
        try:
            categories_data = await _proxy_provider_request("GET", "/api/categories/all", params={"is_rotate": "true"})
            categories = categories_data.get("categories", [])
            allowed_qualities = {"BASIC", "PRIVATE", "DEDICATED"}
            filtered = [
                category
                for category in categories
                if category.get("available") and str(category.get("quality") or "").upper() in allowed_qualities
            ]
            filtered = _sort_categories(filtered)
            _save_proxy_provider_categories_cache(filtered)
            return filtered, True
        except ProxyProviderError as error:
            last_error = error
            if attempt < 2:
                await asyncio.sleep(0.6)

    cached_categories = _load_proxy_provider_categories_cache()
    return _sort_categories(cached_categories), False


async def get_static_proxy_categories() -> list[dict]:
    categories, _ = await get_static_proxy_categories_snapshot()
    if not categories:
        raise ProxyProviderError(PROXY_GENERIC_ERROR_CODE)
    return categories


async def get_proxy_category_by_id(category_id: int, proxy_kind: str) -> dict | None:
    categories = await get_static_proxy_categories() if proxy_kind == "static" else await get_mobile_proxy_categories()
    for category in categories:
        if category.get("id") == category_id:
            return category
    return None


async def get_proxy_provider_balance() -> float:
    data = await _proxy_provider_request("GET", "/api/me")
    return float(data.get("user", {}).get("balance", 0) or 0)


async def check_proxy_provider_purchase_availability(required_balance_rub: float | None = None) -> tuple[bool, str]:
    last_error_message = ""
    for attempt in range(PROXY_PROVIDER_BALANCE_ATTEMPTS):
        try:
            balance = await get_proxy_provider_balance()
            break
        except ProxyProviderError as error:
            last_error_message = normalize_proxy_provider_error_message(str(error))
            if attempt + 1 < PROXY_PROVIDER_BALANCE_ATTEMPTS:
                await asyncio.sleep(PROXY_PROVIDER_BALANCE_RETRY_DELAY_SECONDS)
    else:
        if last_error_message and last_error_message != PROXY_GENERIC_ERROR_CODE:
            return False, last_error_message
        # Do not block catalog/purchase on flaky balance endpoint if the provider
        # itself may still accept order creation.
        return True, ""

    if required_balance_rub is not None and balance + 1e-9 < float(required_balance_rub or 0.0):
        return False, "Сервис временно недоступен. Попробуйте позже."

    return True, ""


async def create_proxy_provider_order(category_id: int, item_id: int, count: int) -> dict:
    payload = {
        "paymentSystem": "balance",
        "count": count,
        "categoryId": category_id,
        "itemId": item_id,
        "duration": PROXY_PROVIDER_ORDER_DURATION_DAYS,
    }
    return await _proxy_provider_request("POST", "/api/order/create", json_body=payload)


async def get_proxy_provider_order(order_id: int) -> dict:
    data = await _proxy_provider_request("GET", f"/api/order/{order_id}/get")
    return data.get("order", {})


def ensure_proxy_endpoint_scheme(endpoint: str, protocol: str) -> str:
    endpoint = str(endpoint or "").strip()
    if not endpoint or "://" in endpoint:
        return endpoint
    if str(protocol or "").strip().upper().startswith("SOCKS"):
        return f"socks5://{endpoint}"
    return endpoint


def format_proxy_endpoint(detail: dict, protocol: str) -> str:
    host = detail.get("host", "")
    login = detail.get("login", "")
    password = detail.get("password", "")
    is_http = str(protocol or "").strip().upper() == "HTTP"
    port = detail.get("portHttp") if is_http else detail.get("portSocks")
    return ensure_proxy_endpoint_scheme(f"{login}:{password}@{host}:{port}", protocol)



async def check_proxy_endpoint(endpoint: str, protocol: str, timeout: float = 8.0) -> bool:
    value = str(endpoint or '').strip()
    if '://' not in value:
        value = f"{protocol.lower()}://{value}"
    parsed = urlsplit(value)
    if not parsed.hostname or not parsed.port or not parsed.username or parsed.password is None:
        return False
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(parsed.hostname, parsed.port), timeout=timeout)
        try:
            username = parsed.username.encode(); password = parsed.password.encode()
            if protocol.upper().startswith('SOCKS'):
                writer.write(b'\x05\x01\x02'); await writer.drain()
                if await asyncio.wait_for(reader.readexactly(2), timeout) != b'\x05\x02': return False
                writer.write(b'\x01' + bytes([len(username)]) + username + bytes([len(password)]) + password); await writer.drain()
                if await asyncio.wait_for(reader.readexactly(2), timeout) != b'\x01\x00': return False
                target=b'www.instagram.com'; writer.write(b'\x05\x01\x00\x03'+bytes([len(target)])+target+(443).to_bytes(2,'big')); await writer.drain()
                return (await asyncio.wait_for(reader.readexactly(2), timeout)) == b'\x05\x00'
            auth=base64.b64encode(f'{parsed.username}:{parsed.password}'.encode()).decode()
            writer.write((f'CONNECT www.instagram.com:443 HTTP/1.1\r\nHost: www.instagram.com:443\r\nProxy-Authorization: Basic {auth}\r\n\r\n').encode()); await writer.drain()
            head=await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), timeout)
            return head.startswith(b'HTTP/') and b' 200 ' in head.split(b'\r\n',1)[0]
        finally:
            writer.close(); await writer.wait_closed()
    except (OSError, asyncio.TimeoutError, ValueError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        return False


async def filter_working_proxy_details(details: list[dict], protocol: str) -> list[dict]:
    """Optionally validate supplier endpoints without delaying normal delivery.

    The supplier has already created and returned these credentials. Doing a
    second network handshake for every endpoint added up to eight seconds to
    the customer-facing request and could also discard a newly-created proxy
    while it was still warming up. Strict validation remains opt-in for
    diagnostics, but the default path returns the provider payload at once.
    """
    normalized_details = [detail for detail in details if isinstance(detail, dict)]
    if not PROXY_PROVIDER_VALIDATE_BEFORE_DELIVERY or not normalized_details:
        return normalized_details
    checks = await asyncio.gather(
        *(
            check_proxy_endpoint(
                format_proxy_endpoint(detail, protocol),
                protocol,
                timeout=PROXY_PROVIDER_VALIDATION_TIMEOUT_SECONDS,
            )
            for detail in normalized_details
        )
    )
    working = [detail for detail, ok in zip(normalized_details, checks) if ok]
    # Never issue a partial order: all purchased endpoints must authenticate
    # and be able to open an Instagram HTTPS tunnel.
    return working if len(working) == len(normalized_details) else []

def format_proxy_delivery(order: dict, protocol: str) -> str:
    details = order.get("details", [])
    if not details:
        return "Прокси выданы, но список деталей пуст."

    return "\n".join(format_proxy_endpoint(detail, protocol) for detail in details)


def get_country_button_label(category: dict) -> str:
    country_name = category.get("name", {}).get("ru", "Страна")
    return COUNTRY_BUTTON_LABELS.get(country_name, f"{country_name.upper()} | IPV4 ПРОКСИ")


def get_quality_label(quality_code: str) -> str:
    return QUALITY_LABELS.get(quality_code, quality_code or "UNKNOWN")


def _market_headers() -> dict:
    if not MARKET_API_TOKEN:
        raise MarketProviderError("Сервис временно недоступен")
    return {
        "Authorization": f"Bearer {MARKET_API_TOKEN}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": MARKET_USER_AGENT,
    }


def _get_market_jhash(code: int) -> int:
    x = 123456789
    k = 0
    for i in range(1677696):
        x = ((x + code) ^ (x + (x % 3) + (x % 17) + code) ^ i) % 16776960
        if x % 117 == 0:
            k = (k + 1) % 1111
    return k


def _encode_market_user_agent(user_agent: str) -> str:
    return quote(user_agent, safe="").replace("!", "%21").replace("'", "%27")


def _filter_active_categories(categories: list[dict]) -> list[dict]:
    filtered: list[dict] = []
    for category in categories:
        if not category.get("active", True):
            continue
        children = [
            child
            for child in category.get("children", [])
            if child.get("active", True)
        ]
        filtered.append({**category, "children": children})
    return filtered


def _is_market_challenge_response(response: aiohttp.ClientResponse, text: str) -> bool:
    content_type = response.headers.get("content-type", "")
    return "text/html" in content_type and "__js_p_" in text and "get_jhash" in text


def _build_market_cache_key(method: str, path: str, params: dict | None = None) -> tuple:
    normalized_params = tuple(sorted((params or {}).items()))
    return method.upper(), path, normalized_params


async def _get_market_cached_response(cache_key: tuple):
    if MARKET_CACHE_TTL_SECONDS <= 0:
        return None

    async with _MARKET_RESPONSE_CACHE_LOCK:
        cached_entry = _MARKET_RESPONSE_CACHE.get(cache_key)
        if not cached_entry:
            return None
        # Keep expired entries as a short-lived stale fallback when the API is
        # rate limited.  They are replaced on the next successful response.
        return copy.deepcopy(cached_entry["value"]) if time.monotonic() < float(cached_entry["expires_at"]) else None


async def _get_market_stale_response(cache_key: tuple):
    async with _MARKET_RESPONSE_CACHE_LOCK:
        entry = _MARKET_RESPONSE_CACHE.get(cache_key)
        return copy.deepcopy(entry["value"]) if entry else None


async def _set_market_cached_response(cache_key: tuple, value):
    if MARKET_CACHE_TTL_SECONDS <= 0:
        return

    async with _MARKET_RESPONSE_CACHE_LOCK:
        _MARKET_RESPONSE_CACHE[cache_key] = {
            "value": copy.deepcopy(value),
            "expires_at": time.monotonic() + MARKET_CACHE_TTL_SECONDS,
        }


async def _clear_market_catalog_cache():
    async with _MARKET_RESPONSE_CACHE_LOCK:
        _MARKET_RESPONSE_CACHE.clear()


async def _refresh_market_cookies(force: bool = False) -> dict[str, str]:
    async with _MARKET_COOKIE_LOCK:
        now = time.monotonic()
        cached_cookies = _MARKET_COOKIE_CACHE.get("cookies")
        expires_at = float(_MARKET_COOKIE_CACHE.get("expires_at") or 0.0)
        if not force and cached_cookies is not None and now < expires_at:
            return dict(cached_cookies)

        async with aiohttp.ClientSession(headers=_market_headers()) as session:
            challenge_url = f"{MARKET_API_BASE_URL}/api/v2/categories"
            async with session.get(challenge_url) as response:
                first_response_text = await response.text()
                requires_challenge = _is_market_challenge_response(response, first_response_text)

            if not requires_challenge:
                # Some supplier edges return JSON immediately and do not require
                # anti-bot cookies at all. In that case we cache an empty cookie set.
                _MARKET_COOKIE_CACHE["cookies"] = {}
                _MARKET_COOKIE_CACHE["expires_at"] = time.monotonic() + max(MARKET_CACHE_TTL_SECONDS, 15)
                return {}

            cookies = session.cookie_jar.filter_cookies(URL(MARKET_API_BASE_URL))
            js_p_cookie = cookies.get("__js_p_")
            if js_p_cookie is None or not js_p_cookie.value:
                raise MarketProviderError("Поставщик не вернул anti-bot cookie")

            try:
                code_text, age_text, *_ = js_p_cookie.value.split(",")
                code = int(code_text)
                age_seconds = int(age_text)
            except (TypeError, ValueError):
                raise MarketProviderError("Поставщик вернул некорректные anti-bot cookie")

            prepared_cookies = {
                "__js_p_": js_p_cookie.value,
                "__jhash_": str(_get_market_jhash(code)),
                "__jua_": _encode_market_user_agent(MARKET_USER_AGENT),
            }
            session.cookie_jar.update_cookies(
                {
                    "__jhash_": prepared_cookies["__jhash_"],
                    "__jua_": prepared_cookies["__jua_"],
                },
                response_url=URL(MARKET_API_BASE_URL),
            )

            await asyncio.sleep(1.1)

            async with session.get(challenge_url) as verify_response:
                verify_text = await verify_response.text()
                if _is_market_challenge_response(verify_response, verify_text):
                    raise MarketProviderError("Сервис временн�� недоступен")

            _MARKET_COOKIE_CACHE["cookies"] = prepared_cookies
            _MARKET_COOKIE_CACHE["expires_at"] = time.monotonic() + max(age_seconds - 60, 60)
            return dict(prepared_cookies)


async def _build_market_session(force_refresh: bool = False) -> aiohttp.ClientSession:
    cookies = await _refresh_market_cookies(force=force_refresh)
    session = aiohttp.ClientSession(headers=_market_headers())
    session.cookie_jar.update_cookies(cookies, response_url=URL(MARKET_API_BASE_URL))
    return session


async def _market_request_impl(method: str, path: str, *, params: dict | None = None, json_body: dict | None = None):
    global _MARKET_LAST_REQUEST_AT, _MARKET_RATE_LIMIT_UNTIL
    use_cache = _is_market_cacheable_request(method, path, json_body)
    cache_key = _build_market_cache_key(method, path, params) if use_cache else None
    if cache_key is not None:
        cached_value = await _get_market_cached_response(cache_key)
        if cached_value is not None:
            return cached_value

    last_error: Exception | None = None
    # Do not hammer an upstream which is already rate limiting us. A single
    # retry is enough; callers receive cached/stale data whenever available.
    for attempt in range(2):
        # Bound concurrent upstream calls.  Several mini-app clients can open
        # the catalog at once; unbounded retries otherwise trigger rate limits.
        async with _MARKET_REQUEST_SEMAPHORE:
            # Refresh cookies only after an anti-bot challenge, never merely
            # because a request was retried (429 responses must reuse the cache).
            now = time.monotonic()
            if now < _MARKET_RATE_LIMIT_UNTIL:
                stale = await _get_market_stale_response(cache_key) if cache_key is not None else None
                if stale is not None:
                    return stale
                await asyncio.sleep(_MARKET_RATE_LIMIT_UNTIL - now)
            wait_for = _MARKET_LAST_REQUEST_AT + _MARKET_REQUEST_COOLDOWN - time.monotonic()
            if wait_for > 0:
                await asyncio.sleep(wait_for)
            _MARKET_LAST_REQUEST_AT = time.monotonic()
            async with await _build_market_session(force_refresh=attempt > 0 and isinstance(last_error, MarketProviderError) and "challenge" in str(last_error).lower()) as session:
                async with session.request(
                    method,
                    f"{MARKET_API_BASE_URL}{path}",
                    params=params,
                    json=json_body,
                ) as response:
                    text = await response.text()
                    if response.status == 429:
                        retry_after = min(10.0, max(1.0, float(response.headers.get("Retry-After", "3") or 3)))
                        _MARKET_RATE_LIMIT_UNTIL = time.monotonic() + retry_after
                        last_error = MarketProviderError(MARKET_PUBLIC_ERROR, status_code=429)
                        if cache_key is not None:
                            stale = await _get_market_stale_response(cache_key)
                            if stale is not None:
                                return stale
                        if attempt < 1:
                            await asyncio.sleep(retry_after)
                        continue
                    if _is_market_challenge_response(response, text):
                        last_error = MarketProviderError("challenge")
                        if attempt < 1:
                            await asyncio.sleep(0.8 * (attempt + 1))
                        continue

                    try:
                        data = json.loads(text)
                    except json.JSONDecodeError:
                        raise MarketProviderError(MARKET_PUBLIC_ERROR)

                    if response.status >= 500:
                        last_error = MarketProviderError(MARKET_PUBLIC_ERROR, status_code=response.status)
                        if attempt < 1:
                            await asyncio.sleep(0.8 * (attempt + 1))
                            continue
                        break
                    if response.status >= 400 or data.get("success") is False:
                        raise MarketProviderError(MARKET_PUBLIC_ERROR, status_code=response.status)
                    if cache_key is not None:
                        await _set_market_cached_response(cache_key, data)
                    return data

    if cache_key is not None:
        stale = await _get_market_stale_response(cache_key)
        if stale is not None:
            return stale
    raise MarketProviderError(MARKET_PUBLIC_ERROR, status_code=getattr(last_error, "status_code", None))


async def _market_request(method: str, path: str, *, params: dict | None = None, json_body: dict | None = None):
    """Deduplicate concurrent catalog reads so one user action makes one upstream call."""
    if not _is_market_cacheable_request(method, path, json_body):
        return await _market_request_impl(method, path, params=params, json_body=json_body)
    key = _build_market_cache_key(method, path, params)
    cached = await _get_market_cached_response(key)
    if cached is not None:
        return cached
    async with _MARKET_INFLIGHT_LOCK:
        task = _MARKET_INFLIGHT.get(key)
        if task is None or task.done():
            task = asyncio.create_task(_market_request_impl(method, path, params=params, json_body=json_body))
            _MARKET_INFLIGHT[key] = task
    try:
        return await task
    finally:
        if task.done():
            async with _MARKET_INFLIGHT_LOCK:
                if _MARKET_INFLIGHT.get(key) is task:
                    _MARKET_INFLIGHT.pop(key, None)


async def get_market_balance_rub() -> float:
    """Read the reseller balance without involving catalogue caching."""
    data = await _market_request("GET", "/api/v2/user/balance")
    try:
        return max(float((data.get("data") or {}).get("balance") or 0.0), 0.0)
    except (AttributeError, TypeError, ValueError) as error:
        raise MarketProviderError(MARKET_PUBLIC_ERROR) from error


async def get_market_categories() -> list[dict]:
    # The exchange-rate endpoint is auxiliary.  A transient rate limit there
    # must never make an otherwise valid catalog disappear.
    try:
        await refresh_market_rub_per_usdt()
    except Exception:
        pass
    try:
        data = await _market_request("GET", "/api/v2/categories")
        categories = _filter_financial_account_categories(_filter_active_categories(data.get("data", [])))
        async with _MARKET_CATEGORY_CACHE_LOCK:
            _MARKET_CATEGORY_CACHE["value"] = copy.deepcopy(categories)
            _MARKET_CATEGORY_CACHE["expires_at"] = time.monotonic() + max(MARKET_CACHE_TTL_SECONDS, 900)
        return categories
    except Exception:
        # Serve the last known-good catalog during a short upstream outage or
        # rate limit.  No upstream exception text is exposed to callers.
        async with _MARKET_CATEGORY_CACHE_LOCK:
            cached = _MARKET_CATEGORY_CACHE.get("value")
            if isinstance(cached, list):
                return copy.deepcopy(cached)
        return []


def _market_search_text(value: Any) -> str:
    if isinstance(value, dict):
        value = " ".join(str(item or "") for item in value.values())
    # Normalize full-width characters too (for example ＠gmail.com), otherwise
    # custom-domain offers can bypass the catalogue filter by typography alone.
    return unicodedata.normalize("NFKC", str(value or "")).casefold().replace("ё", "е")


MARKET_VERIFICATION_SERVICE_MARKERS = (
    "делаю верификац", "сделаю верификац", "пройду верификац", "пройти верификац",
    "услуга верификац", "помощь с верификац", "верификация для", "прохождение верификац",
    "делаю kyc", "пройду kyc", "прохождение kyc", "kyc verification service",
)
MARKET_FINANCIAL_ACCOUNT_MARKERS = (
    "банковск", "банк аккаунт", "банк. аккаунт", "bank account", "banking account",
    "интернет-банк", "онлайн-банк", "online bank", "mobile bank", "личный кабинет банка",
    "аккаунт кошельк", "аккаунт криптокошельк", "wallet account", "crypto wallet account",
    "электронный кошелек", "электронного кошелька", "платежный кошелек",
    "аккаунт платежной системы", "payment wallet", "готовый кошелек", "готовый wallet",
    "логин от банка", "доступ к банку", "доступ к кошельку", "сид фраз", "seed phrase",
    "paypal", "pay pal", "yoomoney", "юmoney", "юмани", "ю мани", "capitalist",
    "payeer", "perfect money", "advcash", "wise account", "revolut account",
    "skrill", "neteller", "cash app", "venmo", "zelle",
)
MARKET_BLOCKED_CATEGORY_MARKERS = (
    "платежные системы", "платежная система", "payment systems", "payment system",
    "накрут", "раскручен", "сервис по раскрутк", "фолловер",
)

# These catalogue branches are either fulfilled manually by the upstream
# supplier or were explicitly removed from the storefront.  Keeping the list
# by stable provider id avoids accidental matches in otherwise valid product
# descriptions.
MARKET_HIDDEN_CATEGORY_IDS = {
    # The whole miscellaneous branch was removed from the storefront.
    68,
    # Facebook
    117, 159, 161, 163,
    # VK
    11, 12, 78, 95, 164,
    # Instagram, including promoted/follower-service branches
    17, 82, 126, 137, 140,
    # TikTok, including promoted-account branches
    27, 100, 119,
    # Telegram ("Реальные" is hidden only here)
    29, 32, 33, 138,
    # Twitter follower/promotion branches
    37, 132,
    # Google
    127, 141,
    # OK.ru
    79, 99,
    # Mail providers removed from the storefront
    64, 67, 84,
    # Keep only VPN inside the combined upstream VPN/Proxy/VPS category.
    77, 139,
}

# Mail providers must be the final group in every catalogue presentation.
MARKET_EMAIL_CATEGORY_IDS = (39, 50, 54, 58, 62)
MARKET_BLOCKED_PRODUCT_NAME_MARKERS = ("заблокирован", "заблокировк")
MARKET_BLOCKED_PROMOTION_MARKERS = (
    "накрут", "сервис по раскрутк", "услуга раскрутк", "boosting service",
)
MARKET_GMAIL_CATEGORY_ID = 40
MARKET_CUSTOM_GMAIL_MARKERS = (
    "non gmail", "non-gmail", "non @gmail", "non-@gmail",
    "не gmail", "не @gmail", "не-@gmail", "не почта@gmail",
    "домен не gmail", "домен не @gmail", "домен - не @gmail",
    "домен почты не gmail", "домен почты - не @gmail",
    "домен, не относящийся к gmail", "домен, не gmail",
    "domains not @gmail", "domain not @gmail", "not @gmail.com",
    "gmail.edu", "google .edu", "google edu", "gmail education",
    "корпоративная почта", "корпоративные почты", "корпоративный домен",
    "специальном корпоративном домене", "custom domain",
)


def is_market_email_category(category: dict) -> bool:
    try:
        if int(category.get("id") or 0) in MARKET_EMAIL_CATEGORY_IDS:
            return True
    except (TypeError, ValueError):
        pass
    name = _market_search_text(category.get("name"))
    return any(marker in name for marker in ("gmail", "mail.ru", "яндекс", "rambler", "почты"))


def is_market_category_available(category_id: Any) -> bool:
    try:
        return int(category_id or 0) not in MARKET_HIDDEN_CATEGORY_IDS
    except (TypeError, ValueError):
        return False


def _sort_market_email_categories_last(categories: list[dict]) -> list[dict]:
    email_order = {39: 0, 58: 1, 54: 2, 50: 3, 62: 4}

    def sort_key(category: dict) -> tuple[int, int]:
        if not is_market_email_category(category):
            return (0, 0)
        try:
            category_id = int(category.get("id") or 0)
        except (TypeError, ValueError):
            category_id = 0
        return (1, email_order.get(category_id, 99))

    return sorted(categories, key=sort_key)


def _sort_market_autoreg_children_first(categories: list[dict]) -> list[dict]:
    """Place autoregistration subcategories first while preserving other order."""
    for category in categories:
        children = category.get("children") or []
        category["children"] = sorted(
            children,
            key=lambda child: 0 if "авторег" in _market_search_text(child.get("name")) else 1,
        )
    return categories


def is_market_financial_account_allowed(item: dict) -> bool:
    text = " ".join(
        _market_search_text(item.get(key))
        for key in ("title", "name", "description", "short_description", "category_name")
    )
    # Only the verification *service* is permitted. A ready-made "verified
    # account" is still an account and must not pass this exception.
    if any(marker in text for marker in MARKET_VERIFICATION_SERVICE_MARKERS):
        return True
    if any(marker in text for marker in MARKET_BLOCKED_PRODUCT_NAME_MARKERS):
        return False
    return not any(marker in text for marker in MARKET_FINANCIAL_ACCOUNT_MARKERS)


def is_market_gmail_product_allowed(item: dict) -> bool:
    """Keep only real @gmail.com mailboxes in the Gmail autoreg category."""
    try:
        if int(item.get("category_id") or 0) != MARKET_GMAIL_CATEGORY_ID:
            return True
    except (TypeError, ValueError):
        return False

    text = " ".join(
        _market_search_text(item.get(key))
        for key in ("title", "name", "description", "short_description")
    )
    if any(marker in text for marker in MARKET_CUSTOM_GMAIL_MARKERS):
        return False

    # An explicitly named email-like domain other than gmail.com is custom.
    domains = re.findall(r"@([a-z0-9][a-z0-9.-]*\.[a-z]{2,})", text)
    return all(domain.rstrip(".") == "gmail.com" for domain in domains)


def is_market_product_allowed(item: dict) -> bool:
    text = " ".join(
        _market_search_text(item.get(key))
        for key in ("title", "name", "description", "short_description", "category_name")
    )
    return (
        not any(marker in text for marker in MARKET_BLOCKED_PROMOTION_MARKERS)
        and is_market_financial_account_allowed(item)
        and is_market_gmail_product_allowed(item)
    )


def _filter_financial_account_categories(categories: list[dict]) -> list[dict]:
    result: list[dict] = []
    for original in categories:
        category = copy.deepcopy(original)
        try:
            category_id = int(category.get("id") or 0)
        except (TypeError, ValueError):
            category_id = 0
        if category_id in MARKET_HIDDEN_CATEGORY_IDS:
            continue
        category_text = " ".join(
            _market_search_text(category.get(key))
            for key in ("title", "name", "description", "short_description")
        )
        # Remove the whole category together with all nested subcategories.
        if any(marker in category_text for marker in MARKET_BLOCKED_CATEGORY_MARKERS):
            continue
        children = _filter_financial_account_categories(category.get("children") or [])
        category["children"] = children
        if is_market_financial_account_allowed(category):
            result.append(category)
        elif children:
            # Keep a neutral parent only when it contains permitted children.
            result.append(category)
    return _sort_market_email_categories_last(_sort_market_autoreg_children_first(result))


async def get_market_category_by_id(category_id: int) -> dict | None:
    categories = await get_market_categories()
    for category in categories:
        if category.get("id") == category_id:
            return category
        for child in category.get("children", []):
            if child.get("id") == category_id:
                return child
    return None


async def get_market_products(category_id: int, page: int = 1, per_page: int = 6) -> dict:
    if not is_market_category_available(category_id):
        return {"items": [], "meta": {"current_page": max(page, 1), "last_page": 1, "total": 0}, "links": {}}
    try:
        await refresh_market_rub_per_usdt()
        if int(category_id) == MARKET_GMAIL_CATEGORY_ID:
            # The provider's cheapest Gmail pages are dominated by custom
            # domains. Collect and filter upstream pages first so customers do
            # not see several misleading empty pages before @gmail.com items.
            allowed_items: list[dict] = []
            upstream_page = 1
            while upstream_page <= 20:
                batch = await _market_request(
                    "GET",
                    "/api/v2/products",
                    params={
                        "page": upstream_page,
                        "per_page": 100,
                        "category_id": category_id,
                        "only_in_stock": 1,
                        "order_by": "price",
                        "order_direction": "asc",
                    },
                )
                allowed_items.extend(
                    item for item in batch.get("data", [])
                    if isinstance(item, dict) and is_market_product_allowed(item)
                )
                meta = batch.get("meta", {}) or {}
                if upstream_page >= int(meta.get("last_page") or 1):
                    break
                upstream_page += 1

            current_page = max(int(page), 1)
            page_size = max(int(per_page), 1)
            total = len(allowed_items)
            last_page = max((total + page_size - 1) // page_size, 1)
            start = (current_page - 1) * page_size
            return {
                "items": allowed_items[start:start + page_size],
                "meta": {"current_page": current_page, "last_page": last_page, "total": total},
                "links": {},
            }
        data = await _market_request(
            "GET",
            "/api/v2/products",
            params={
                "page": max(page, 1),
                "per_page": per_page,
                "category_id": category_id,
                "only_in_stock": 1,
                "order_by": "price",
                "order_direction": "asc",
            },
        )
    except Exception:
        # Keep the API contract stable during transient limits.  The frontend
        # renders an empty page and can retry, without leaking upstream text.
        return {"items": [], "meta": {"current_page": max(page, 1), "last_page": 1, "total": 0}, "links": {}}
    return {
        "items": [item for item in data.get("data", []) if isinstance(item, dict) and is_market_product_allowed(item)],
        "meta": data.get("meta", {}) or {},
        "links": data.get("links", {}) or {},
    }


async def get_market_product(product_id: int) -> dict:
    await refresh_market_rub_per_usdt()
    data = await _market_request("GET", f"/api/v2/products/{product_id}")
    product = data.get("data")
    if not isinstance(product, dict):
        raise MarketProviderError("Поставщик не вернул данные товара")
    if not is_market_category_available(product.get("category_id")):
        raise MarketProviderError("Этот товар недоступен")
    if not is_market_product_allowed(product):
        raise MarketProviderError("Этот товар недоступен")
    return product


async def create_market_order(product_id: int, quantity: int, *, product_prevalidated: bool = False) -> dict:
    # Re-check server-side immediately before purchase so a hidden product
    # cannot be ordered through a forged callback or an old Telegram message.
    # Checkout paths that have just fetched this exact product may explicitly
    # skip the duplicate round-trip; the supplier still validates stock while
    # creating the order.
    if not product_prevalidated:
        await get_market_product(int(product_id))
    data = await _market_request(
        "POST",
        "/api/v2/orders",
        json_body={
            "items": [{"product_id": product_id, "quantity": quantity}],
        },
    )
    await _clear_market_catalog_cache()
    order = data.get("data")
    if not isinstance(order, dict):
        raise MarketProviderError("Поставщик не вернул данные заказа")
    return order


async def get_market_order(order_uuid: str) -> dict:
    data = await _market_request("GET", f"/api/v2/orders/{order_uuid}")
    order = data.get("data")
    if not isinstance(order, dict):
        raise MarketProviderError("Поставщик не вернул данные заказа")
    return order


def get_market_order_file_url(order_data: dict) -> str | None:
    for item in order_data.get("items", []):
        link = item.get("link_to_file")
        if link:
            return str(link)
    return None


def is_market_order_ready(order_data: dict) -> bool:
    return str(order_data.get("status", "")).lower() in MARKET_READY_STATUSES and bool(
        get_market_order_file_url(order_data)
    )


def _is_private_or_local_host(host: str | None) -> bool:
    normalized = str(host or "").strip().lower().strip(".")
    if not normalized:
        return True
    normalized = normalized.strip("[]")
    if normalized in {"localhost", "localhost.localdomain"} or normalized.endswith(".local"):
        return True
    try:
        ip_address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return any(
        (
            ip_address.is_private,
            ip_address.is_loopback,
            ip_address.is_link_local,
            ip_address.is_multicast,
            ip_address.is_reserved,
            ip_address.is_unspecified,
        )
    )


def _validate_remote_download_url(raw_url: str) -> str:
    try:
        parsed_url = URL(str(raw_url or "").strip())
    except ValueError as error:
        raise MarketProviderError("Поставщик вернул некорректную ссылку на файл") from error

    # Delivered files must never be fetched over clear-text HTTP. Besides
    # leaking order data, an HTTP redirect could be used to tamper with the
    # downloaded payload in transit.
    if parsed_url.scheme != "https" or not parsed_url.host:
        raise MarketProviderError("Поставщик вернул небезопасную ссылку на файл")
    if parsed_url.user or parsed_url.password:
        raise MarketProviderError("Поставщик вернул небезопасную ссылку на файл")
    if _is_private_or_local_host(parsed_url.host):
        raise MarketProviderError("Поставщик вернул ссылку на недопустимый адрес")

    return str(parsed_url)


async def download_text_file(url: str) -> str:
    timeout = aiohttp.ClientTimeout(total=30, connect=8, sock_read=20)
    async with aiohttp.ClientSession(headers={"User-Agent": MARKET_USER_AGENT}, timeout=timeout) as session:
        current_url = _validate_remote_download_url(url)
        for _ in range(4):
            async with session.get(current_url, allow_redirects=False) as response:
                if response.status in {301, 302, 303, 307, 308}:
                    redirect_target = response.headers.get("Location")
                    if not redirect_target:
                        raise MarketProviderError("Поставщик не вернул корректный редирект на файл")
                    current_url = _validate_remote_download_url(str(response.url.join(URL(redirect_target))))
                    continue

                raw = await response.content.read(2 * 1024 * 1024 + 1)
                if len(raw) > 2 * 1024 * 1024:
                    raise MarketProviderError("Файл поставщика слишком большой")
                text = raw.decode(response.charset or "utf-8", errors="replace")
                if response.status >= 400:
                    raise MarketProviderError(text or "Не удалось скачать файл с товаром")
                return text

    raise MarketProviderError("Слишком много редиректов при скачивании файла")


def is_instagram_market_order(order: dict) -> bool:
    label = " ".join(
        str(order.get(key) or "")
        for key in ("category_name", "product_title", "protocol")
    ).casefold()
    return bool(re.search(r"(?:instagram|instagr|инстаграм|инста)", label))


def split_instagram_delivery_records(delivery_text: str) -> list[str]:
    return [line.strip() for line in str(delivery_text or "").splitlines() if line.strip()]


def _extract_instagram_username(record: str) -> str | None:
    credentials = str(record or "").split("||", 1)[0].strip(" |")
    candidate = re.split(r"[:|;\s]", credentials, maxsplit=1)[0].strip().lstrip("@")
    if (
        not candidate
        or "@" in candidate
        or len(candidate) > 30
        or not re.fullmatch(r"[A-Za-z0-9._]+", candidate)
        or not re.search(r"[A-Za-z_]", candidate)
    ):
        return None
    return candidate


def _extract_instagram_cookie_header(record: str) -> str | None:
    value = str(record or "").strip()
    if "sessionid=" not in value.casefold():
        return None
    for segment in value.split("||"):
        if "sessionid=" in segment.casefold():
            return segment.strip(" |")
    session_offset = value.casefold().find("sessionid=")
    return value[session_offset:].strip(" |") if session_offset >= 0 else None


_INSTAGRAM_VALIDATION_SEMAPHORE = asyncio.Semaphore(1)
_INSTAGRAM_VALIDATION_BLOCKED_UNTIL = 0.0


def _classify_instagram_validation_response(
    status_code: int,
    payload: Any,
    *,
    cookie_header: bool,
) -> tuple[str, str]:
    """Classify Instagram responses without treating validator failures as bad stock."""
    if status_code in {429, 500, 502, 503, 504}:
        return "retry", f"Instagram temporary response {status_code}"
    if not isinstance(payload, dict):
        if not cookie_header and status_code == 404:
            return "invalid", "Instagram profile is unavailable"
        return "retry", "Instagram returned an empty or unexpected validation response"

    message = str(payload.get("message") or payload.get("error_type") or "").casefold()
    if cookie_header:
        # A cookie can be bound to the browser User-Agent used by the supplier.
        # The delivery format does not include that value, so this response says
        # nothing about whether the session itself is alive.
        if "useragent mismatch" in message or "user-agent mismatch" in message:
            return "retry", "Instagram requires the supplier session User-Agent"
        if status_code in {400, 401} or "login_required" in message:
            return "invalid", "Instagram session is not authorized"
        if status_code == 403:
            return "retry", "Instagram temporarily rejected validation"
        user = payload.get("user")
        if status_code == 200 and isinstance(user, dict) and user.get("username"):
            return "valid", "Instagram session is active"
        return "retry", "Instagram session response could not be confirmed"

    data = payload.get("data")
    user = data.get("user") if isinstance(data, dict) else None
    if status_code == 404 or (status_code == 200 and user is None):
        return "invalid", "Instagram profile is unavailable"
    if status_code in {401, 403}:
        return "retry", "Instagram temporarily rejected profile validation"
    if status_code == 200 and isinstance(user, dict):
        return "valid", "Instagram profile is available"
    return "retry", "Instagram profile response could not be confirmed"


async def check_instagram_delivery_record(record: str) -> tuple[str, str]:
    """Return valid, invalid, or retry without exposing account contents."""
    global _INSTAGRAM_VALIDATION_BLOCKED_UNTIL
    username = _extract_instagram_username(record)
    cookie_header = _extract_instagram_cookie_header(record)
    timeout = aiohttp.ClientTimeout(total=INSTAGRAM_VALIDATION_TIMEOUT_SECONDS)
    headers = {
        "User-Agent": MARKET_USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "X-IG-App-ID": INSTAGRAM_WEB_APP_ID,
    }
    if cookie_header:
        headers["Cookie"] = cookie_header
        csrf_match = re.search(r"(?:^|;\s*)csrftoken=([^;|]+)", cookie_header, flags=re.IGNORECASE)
        if csrf_match:
            headers["X-CSRFToken"] = csrf_match.group(1)
        url = "https://www.instagram.com/api/v1/accounts/current_user/?edit=true"
    elif username:
        url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={quote(username)}"
    else:
        return "invalid", "delivery record has no verifiable Instagram username or session"

    try:
        async with _INSTAGRAM_VALIDATION_SEMAPHORE:
            if time.monotonic() < _INSTAGRAM_VALIDATION_BLOCKED_UNTIL:
                return "retry", "Instagram validation is cooling down after rate limiting"
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(url, allow_redirects=False) as response:
                    try:
                        payload = await response.json(content_type=None)
                    except (ValueError, TypeError, json.JSONDecodeError):
                        payload = None
                    if response.status == 429:
                        _INSTAGRAM_VALIDATION_BLOCKED_UNTIL = (
                            time.monotonic() + INSTAGRAM_VALIDATION_COOLDOWN_SECONDS
                        )
                    return _classify_instagram_validation_response(
                        response.status,
                        payload,
                        cookie_header=bool(cookie_header),
                    )
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return "retry", "Instagram validation request failed"


async def validate_instagram_market_delivery(order: dict, delivery_text: str) -> tuple[str, str]:
    if not INSTAGRAM_VALIDATE_BEFORE_DELIVERY or not is_instagram_market_order(order):
        return "valid", "validation not required"
    records = split_instagram_delivery_records(delivery_text)
    expected = max(int(order.get("quantity") or 0), 1)
    if len(records) < expected:
        return "invalid", "supplier returned fewer Instagram accounts than purchased"

    semaphore = asyncio.Semaphore(5)

    async def check(record: str) -> tuple[str, str]:
        async with semaphore:
            return await check_instagram_delivery_record(record)

    results = await asyncio.gather(*(check(record) for record in records[:expected]))
    for status, reason in results:
        if status == "invalid":
            return status, reason
    for status, reason in results:
        if status == "retry":
            return status, reason
    return "valid", "all Instagram accounts passed validation"


def _xrocket_headers() -> dict:
    if not XROCKET_API_TOKEN:
        raise XRocketError("Платёж временно недоступен")
    return {
        "Rocket-Pay-Key": XROCKET_API_TOKEN,
        "Accept": "application/json",
    }


def _normalize_invoice_url(data: dict) -> str | None:
    for key in ("link", "payUrl", "invoiceUrl", "miniAppPayUrl", "botPayUrl", "url"):
        value = data.get(key)
        if value:
            return value
    return None


def _extract_xrocket_payload(data: dict) -> dict:
    payload = data.get("data")
    return payload if isinstance(payload, dict) else data


def _extract_xrocket_error(data: dict) -> str:
    errors = data.get("errors")
    if isinstance(errors, list) and errors:
        details = []
        for item in errors[:3]:
            if not isinstance(item, dict):
                continue
            property_name = item.get("property")
            error_text = item.get("error") or item.get("message")
            if property_name and error_text:
                details.append(f"{property_name}: {error_text}")
            elif error_text:
                details.append(str(error_text))
        if details:
            return "; ".join(details)
    return (
        data.get("message")
        or data.get("detail")
        or data.get("error")
        or "XROCKET вернул ошибку"
    )


def _heleket_signature(payload_text: str) -> str:
    encoded_payload = base64.b64encode(payload_text.encode("utf-8")).decode("utf-8")
    return hashlib.md5(f"{encoded_payload}{HELEKET_PAYMENT_API_KEY}".encode("utf-8")).hexdigest()


def _heleket_headers(payload_text: str) -> dict:
    if not HELEKET_PAYMENT_API_KEY:
        raise HeleketError("Платёж временно недоступен")
    if not HELEKET_MERCHANT_UUID:
        raise HeleketError("Не задан HELEKET_MERCHANT_UUID в .env")
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "merchant": HELEKET_MERCHANT_UUID,
        "sign": _heleket_signature(payload_text),
    }


def _extract_heleket_error(data: dict) -> str:
    errors = data.get("errors")
    if isinstance(errors, dict) and errors:
        details: list[str] = []
        for key, value in list(errors.items())[:3]:
            if isinstance(value, list):
                joined_value = ", ".join(str(item) for item in value[:3])
            else:
                joined_value = str(value)
            details.append(f"{key}: {joined_value}")
        if details:
            return "; ".join(details)
    if isinstance(errors, list) and errors:
        return "; ".join(str(item) for item in errors[:3])
    if isinstance(errors, str) and errors:
        return errors
    return data.get("message") or data.get("error") or "Heleket вернул ошибку"


def _lolz_headers() -> dict:
    if not LOLZ_API_KEY:
        raise LolzError("Платёж временно недоступен")
    return {
        "Authorization": f"Bearer {LOLZ_API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _extract_lolz_error(data: dict) -> str:
    errors = data.get("errors")
    if isinstance(errors, list) and errors:
        return "; ".join(str(item) for item in errors[:3])
    if isinstance(errors, str) and errors:
        return errors
    return data.get("message") or data.get("error") or "LOLZ вернул ошибку"


async def _xrocket_request(method: str, path: str, *, params: dict | None = None, json_body: dict | None = None):
    ssl_context = False if XROCKET_SKIP_SSL_VERIFY else None
    async with aiohttp.ClientSession() as session:
        async with session.request(
            method,
            f"{XROCKET_API_BASE_URL}{path}",
            headers=_xrocket_headers(),
            params=params,
            json=json_body,
            ssl=ssl_context,
        ) as response:
            try:
                data = await response.json(content_type=None)
            except Exception:
                text = await response.text()
                raise XRocketError(text or "XROCKET вернул некорректный ответ")

            if response.status >= 400 or data.get("success") is False:
                raise XRocketError(_extract_xrocket_error(data))
            return data


async def _heleket_request(method: str, path: str, *, json_body: dict | None = None):
    payload_text = json.dumps(json_body or {}, ensure_ascii=False, separators=(",", ":"))
    async with aiohttp.ClientSession() as session:
        async with session.request(
            method,
            f"{HELEKET_API_BASE_URL}{path}",
            headers=_heleket_headers(payload_text),
            data=payload_text,
        ) as response:
            try:
                data = await response.json(content_type=None)
            except Exception:
                text = await response.text()
                raise HeleketError(text or "Heleket вернул некорректный ответ")

            if response.status >= 400 or int(data.get("state") or 0) != 0:
                raise HeleketError(_extract_heleket_error(data))
            return data


async def _crystalpay_request(path: str, payload: dict) -> dict:
    if not CRYSTALPAY_AUTH_LOGIN or not CRYSTALPAY_AUTH_SECRET:
        raise CrystalPayError("Платёж временно недоступен")
    body = {"auth_login": CRYSTALPAY_AUTH_LOGIN, "auth_secret": CRYSTALPAY_AUTH_SECRET, **payload}
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            f"{CRYSTALPAY_API_BASE_URL}{path}",
            json=body,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        ) as response:
            try:
                data = await response.json(content_type=None)
            except Exception:
                await response.read()
                raise CrystalPayError("Не удалось получить ответ платёжной системы")
            if response.status >= 400 or bool(data.get("error")):
                errors = data.get("errors")
                if isinstance(errors, list):
                    reason = "; ".join(str(item) for item in errors[:3])
                else:
                    reason = str(errors or data.get("message") or "Не удалось выполнить запрос")
                raise CrystalPayError(reason)
            return data


async def _lolz_request(method: str, path: str, *, params: dict | None = None, json_body: dict | None = None):
    async with aiohttp.ClientSession() as session:
        async with session.request(
            method,
            f"{LOLZ_API_BASE_URL}{path}",
            headers=_lolz_headers(),
            params=params,
            json=json_body,
        ) as response:
            try:
                data = await response.json(content_type=None)
            except Exception:
                text = await response.text()
                raise LolzError(text or "LOLZ вернул некорректный ответ")

            if response.status >= 400 or data.get("errors"):
                raise LolzError(_extract_lolz_error(data))
            return data


async def _coingecko_request(path: str, *, params: dict | None = None) -> dict:
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{COINGECKO_API_BASE_URL}{path}",
            params=params,
            headers={"Accept": "application/json"},
        ) as response:
            try:
                data = await response.json(content_type=None)
            except Exception:
                text = await response.text()
                raise XRocketError(text or "CoinGecko вернул некорректный ответ")

            if response.status >= 400:
                raise XRocketError("Не удалось получить курс валюты для XROCKET")
            return data


def get_cached_market_rub_per_usdt() -> float:
    cached_value = _MARKET_RUB_RATE_CACHE.get("value")
    if isinstance(cached_value, (int, float)) and float(cached_value) > 0:
        return float(cached_value)
    return MARKET_RUB_PER_USDT if MARKET_RUB_PER_USDT > 0 else 100.0


async def refresh_market_rub_per_usdt(force: bool = False) -> float:
    now = time.monotonic()
    cached_value = _MARKET_RUB_RATE_CACHE.get("value")
    expires_at = float(_MARKET_RUB_RATE_CACHE.get("expires_at") or 0.0)
    if (
        not force
        and isinstance(cached_value, (int, float))
        and float(cached_value) > 0
        and now < expires_at
    ):
        return float(cached_value)

    async with _MARKET_RUB_RATE_LOCK:
        now = time.monotonic()
        cached_value = _MARKET_RUB_RATE_CACHE.get("value")
        expires_at = float(_MARKET_RUB_RATE_CACHE.get("expires_at") or 0.0)
        if (
            not force
            and isinstance(cached_value, (int, float))
            and float(cached_value) > 0
            and now < expires_at
        ):
            return float(cached_value)

        # The exchange-rate endpoint is auxiliary to the catalog.  A transient
        # rate limit must never make the catalog unavailable or leak upstream
        # diagnostics to users; keep using the last known/default rate.
        try:
            data = await _coingecko_request(
                "/simple/price",
                params={"ids": "tether", "vs_currencies": "rub"},
            )
        except Exception:
            return get_cached_market_rub_per_usdt()
        rate = float(((data.get("tether") or {}).get("rub")) or 0.0)
        if rate <= 0:
            fallback_rate = get_cached_market_rub_per_usdt()
            if fallback_rate > 0:
                return fallback_rate
            return get_cached_market_rub_per_usdt()

        _MARKET_RUB_RATE_CACHE["value"] = rate
        _MARKET_RUB_RATE_CACHE["expires_at"] = now + max(MARKET_RUB_RATE_CACHE_TTL_SECONDS, 60)
        return rate


async def get_xrocket_available_payment_currencies() -> list[dict]:
    now = time.monotonic()
    cached_value = _XROCKET_CURRENCIES_CACHE.get("value")
    expires_at = float(_XROCKET_CURRENCIES_CACHE.get("expires_at") or 0.0)
    if cached_value is not None and now < expires_at:
        return copy.deepcopy(cached_value)

    async with _XROCKET_CURRENCIES_LOCK:
        now = time.monotonic()
        cached_value = _XROCKET_CURRENCIES_CACHE.get("value")
        expires_at = float(_XROCKET_CURRENCIES_CACHE.get("expires_at") or 0.0)
        if cached_value is not None and now < expires_at:
            return copy.deepcopy(cached_value)

        data = await _xrocket_request("GET", "/currencies/available")
        payload = _extract_xrocket_payload(data)
        raw_results = payload.get("results", []) if isinstance(payload, dict) else []
        currencies: list[dict] = []
        for row in raw_results:
            if not isinstance(row, dict):
                continue
            currency = str(row.get("currency") or "").strip().upper()
            if not currency or currency in XROCKET_MEME_PAYMENT_ASSETS:
                continue
            coingecko_id = XROCKET_PAYMENT_RATE_IDS.get(currency)
            if not coingecko_id:
                continue
            currencies.append(
                {
                    "currency": currency,
                    "name": str(row.get("name") or currency).strip(),
                    "min_invoice": float(row.get("minInvoice") or 0.0),
                    "coingecko_id": coingecko_id,
                }
            )

        currencies.sort(key=lambda item: (item["currency"] != "USDT", item["currency"]))
        _XROCKET_CURRENCIES_CACHE["value"] = copy.deepcopy(currencies)
        _XROCKET_CURRENCIES_CACHE["expires_at"] = now + max(XROCKET_CURRENCIES_CACHE_TTL_SECONDS, 30)
        return currencies


async def get_xrocket_currency_usd_rate(currency: str) -> float:
    normalized_currency = (currency or "").strip().upper()
    if not normalized_currency:
        raise XRocketError("Не указана валюта XROCKET")

    coingecko_id = XROCKET_PAYMENT_RATE_IDS.get(normalized_currency)
    if not coingecko_id:
        raise XRocketError(f"Для валюты {normalized_currency} не настроен курс")

    now = time.monotonic()
    async with _XROCKET_RATE_LOCK:
        cached_entry = _XROCKET_RATE_CACHE.get(normalized_currency)
        if cached_entry and now < float(cached_entry.get("expires_at") or 0.0):
            return float(cached_entry["rate"])

    data = await _coingecko_request(
        "/simple/price",
        params={"ids": coingecko_id, "vs_currencies": "usd"},
    )
    rate = float(((data.get(coingecko_id) or {}).get("usd")) or 0.0)
    if rate <= 0:
        raise XRocketError(f"Не удалось получить курс для {normalized_currency}")

    async with _XROCKET_RATE_LOCK:
        _XROCKET_RATE_CACHE[normalized_currency] = {
            "rate": rate,
            "expires_at": now + max(XROCKET_RATES_CACHE_TTL_SECONDS, 30),
        }
    return rate


async def convert_usd_amount_to_xrocket_currency(amount_usd: float, currency: str) -> float:
    normalized_currency = (currency or "").strip().upper()
    if normalized_currency in {"USDT", "USDC"}:
        return round(float(amount_usd or 0.0), 6)

    usd_rate = await get_xrocket_currency_usd_rate(normalized_currency)
    return round(float(amount_usd or 0.0) / usd_rate, 8)


async def create_xrocket_invoice(
    entity_id: int,
    amount_usd: float,
    description: str,
    currency: str | None = None,
    payload_data: dict | None = None,
) -> dict:
    if payload_data is None:
        payload_data = {"order_id": entity_id}
    invoice_currency = (currency or XROCKET_PAYMENT_ASSET).strip().upper()
    invoice_amount = await convert_usd_amount_to_xrocket_currency(amount_usd, invoice_currency)
    payload = {
        "amount": invoice_amount,
        "numPayments": 1,
        "currency": invoice_currency,
        "description": description,
        "payload": json.dumps(payload_data),
        "commentsEnabled": False,
        "expiredIn": 3600,
    }
    data = await _xrocket_request("POST", "/tg-invoices", json_body=payload)
    invoice_data = _extract_xrocket_payload(data)
    invoice_id = str(invoice_data.get("id") or "")
    pay_url = _normalize_invoice_url(invoice_data)
    if not invoice_id:
        raise XRocketError("XROCKET не вернул id счёта")
    if not pay_url:
        raise XRocketError("XROCKET не вернул ссылку на оплату")
    return {
        "client_invoice_id": invoice_id,
        "invoice_id": invoice_id,
        "pay_url": pay_url,
        "currency": invoice_currency,
        "amount": invoice_amount,
        "raw": data,
    }


async def create_heleket_invoice(
    order_id: str,
    amount_usd: float,
    description: str,
    success_url: str | None = None,
    additional_data: str | None = None,
) -> dict:
    payload = {
        "amount": f"{float(amount_usd or 0.0):.2f}",
        "currency": "USD",
        "order_id": order_id,
    }
    if description:
        payload["description"] = description
    if success_url:
        payload["url_success"] = success_url
        payload["url_return"] = success_url
    if additional_data:
        payload["additional_data"] = additional_data

    data = await _heleket_request("POST", "/v1/payment", json_body=payload)
    invoice_data = data.get("result") if isinstance(data, dict) else None
    if not isinstance(invoice_data, dict):
        raise HeleketError("Heleket не вернул данные счёта")

    invoice_uuid = str(invoice_data.get("uuid") or "")
    pay_url = str(invoice_data.get("url") or "")
    payer_currency = str(invoice_data.get("payer_currency") or "").strip().upper()
    payer_amount = float(invoice_data.get("payer_amount") or 0.0)
    if not invoice_uuid:
        raise HeleketError("Heleket не вернул uuid счёта")
    if not pay_url:
        raise HeleketError("Heleket не вернул ссылку на оплату")

    return {
        "client_invoice_id": order_id,
        "invoice_id": invoice_uuid,
        "pay_url": pay_url,
        "currency": payer_currency or None,
        "amount": payer_amount if payer_currency and payer_amount > 0 else None,
        "raw": data,
    }


async def create_crystalpay_invoice(
    amount_usd: float,
    description: str,
    extra: str,
    redirect_url: str | None = None,
) -> dict:
    payload = {
        "amount": round(float(amount_usd), 2),
        "type": "purchase",
        "lifetime": 30,
        "currency": "USD",
        "subtract_from": "amount",
        "description": str(description or "SOUS MARKET payment")[:255],
        "extra": str(extra or "")[:255],
    }
    if redirect_url:
        payload["redirect_url"] = redirect_url
    data = await _crystalpay_request("/invoice/create/", payload)
    invoice_id = str(data.get("id") or "")
    pay_url = str(data.get("url") or "")
    if not invoice_id or not pay_url:
        raise CrystalPayError("Не удалось создать счёт")
    return {
        "client_invoice_id": invoice_id,
        "invoice_id": invoice_id,
        "pay_url": pay_url,
        "currency": str(data.get("currency") or "USD").upper(),
        "amount": float(data.get("amount") or amount_usd),
        "raw": data,
    }


async def get_crystalpay_invoice(invoice_id: str) -> dict:
    if not str(invoice_id or "").strip():
        raise CrystalPayError("Счёт не найден")
    return await _crystalpay_request("/invoice/info/", {"id": str(invoice_id)})


def is_crystalpay_invoice_paid(invoice_data: dict) -> bool:
    return str(invoice_data.get("state") or "").lower() in {"payed", "paid"}


async def get_lolz_payment_currencies() -> dict:
    data = await _lolz_request("GET", "/currency")
    return data.get("currencyList", {}) if isinstance(data, dict) else {}


async def create_lolz_invoice(
    amount_usd: float,
    payment_id: str,
    comment: str,
    success_url: str,
    additional_data: str | None = None,
) -> dict:
    """Create a LOLZ invoice in the configured currency.

    Internal prices are USDT/USD. If LOLZ is configured for another fiat
    currency (for example RUB), convert the amount with LOLZ's own live rate
    before creating the invoice. Passing a dollar amount as RUB was the cause
    of underpaid invoices such as $0.30 becoming 0.30 RUB.
    """
    if LOLZ_MERCHANT_ID <= 0:
        raise LolzError(
            "Не задан LOLZ_MERCHANT_ID в .env.\n"
            f"Сначала создайте merchant: {LOLZ_MERCHANT_CREATE_URL}\n"
            "Потом укажите его ID в переменной LOLZ_MERCHANT_ID и перезапустите бота."
        )

    normalized_comment = str(comment or "").strip()
    if len(normalized_comment) > 100:
        normalized_comment = normalized_comment[:97].rstrip() + "..."

    currency = LOLZ_PAYMENT_CURRENCY
    amount = round(float(amount_usd or 0.0), 2)
    if amount <= 0:
        raise LolzError("Некорректная сумма счёта LOLZ")
    if currency not in {"USD", "USDT", "USDC", "DAI"}:
        currencies = await get_lolz_payment_currencies()
        usd_rate = float((currencies.get("USD") or {}).get("rate") or 0.0)
        target_rate = float((currencies.get(currency) or {}).get("rate") or 0.0)
        if usd_rate <= 0 or target_rate <= 0:
            raise LolzError(f"LOLZ не вернул курс для {currency}")
        amount = round(amount * usd_rate / target_rate, 2)
        if amount <= 0:
            raise LolzError("Некорректная сумма после конвертации LOLZ")

    payload = {
        "currency": currency,
        "amount": amount,
        "payment_id": payment_id,
        "comment": normalized_comment,
        "merchant_id": LOLZ_MERCHANT_ID,
        "url_success": success_url,
        "lifetime": 3600,
    }
    if additional_data:
        payload["additional_data"] = additional_data

    data = await _lolz_request("POST", "/invoice", json_body=payload)
    invoice_data = data.get("invoice") if isinstance(data, dict) else None
    if not isinstance(invoice_data, dict):
        raise LolzError("LOLZ не вернул данные счёта")

    invoice_id = str(invoice_data.get("invoice_id") or "")
    pay_url = str(invoice_data.get("url") or "")
    if not invoice_id:
        raise LolzError("LOLZ не вернул invoice_id")
    if not pay_url:
        raise LolzError("LOLZ не вернул ссылку на оплату")

    return {
        "client_invoice_id": str(invoice_data.get("payment_id") or payment_id),
        "invoice_id": invoice_id,
        "pay_url": pay_url,
        "currency": currency,
        "amount": amount,
        "status": str(invoice_data.get("status") or ""),
        "raw": data,
    }


async def get_lolz_invoice(invoice_id: str | None = None, payment_id: str | None = None) -> dict:
    normalized_invoice_id = str(invoice_id or "").strip()
    normalized_payment_id = str(payment_id or "").strip()
    if not normalized_invoice_id and not normalized_payment_id:
        raise LolzError("Не указан invoice_id или payment_id LOLZ")
    last_error: Exception | None = None
    queries: list[dict[str, str]] = []
    if normalized_invoice_id:
        queries.append({"invoice_id": normalized_invoice_id})
    if normalized_payment_id:
        queries.append({"payment_id": normalized_payment_id})

    for params in queries:
        try:
            data = await _lolz_request("GET", "/invoice", params=params)
        except LolzError as error:
            last_error = error
            continue
        invoice_data = data.get("invoice") if isinstance(data, dict) else None
        if isinstance(invoice_data, dict):
            return invoice_data

    raise last_error or LolzError("LOLZ не вернул данные счёта")


async def get_lolz_paid_invoice_fallback(
    *,
    order_id: int,
    amount_usd: float,
    expected_invoice_id: str | None = None,
    expected_payment_id: str | None = None,
) -> dict | None:
    """Find a paid replacement invoice created for the same local order.

    Users can click the LOLZ payment method more than once. Each click used to
    overwrite the invoice stored in the order, even when an earlier invoice had
    already been paid. The invoice API then truthfully reports the newest one as
    unpaid. Payment history is authoritative for completed incoming payments,
    and includes both our local order number and the original payment id.
    """
    target_amount_usd = round(float(amount_usd or 0.0), 2)
    if int(order_id) <= 0 or target_amount_usd <= 0:
        return None
    target_amount = target_amount_usd
    if LOLZ_PAYMENT_CURRENCY not in {"USD", "USDT", "USDC", "DAI"}:
        currencies = await get_lolz_payment_currencies()
        usd_rate = float((currencies.get("USD") or {}).get("rate") or 0.0)
        target_rate = float((currencies.get(LOLZ_PAYMENT_CURRENCY) or {}).get("rate") or 0.0)
        if usd_rate <= 0 or target_rate <= 0:
            return None
        target_amount = round(target_amount_usd * usd_rate / target_rate, 2)
    data = await _lolz_request(
        "GET",
        "/user/payments",
        params={
            "page": 1,
            "pmin": max(target_amount - 0.01, 0.01),
            "pmax": target_amount + 0.01,
        },
    )
    payments = data.get("payments") if isinstance(data, dict) else None
    rows = payments.values() if isinstance(payments, dict) else payments if isinstance(payments, list) else []
    order_marker = f"order-{int(order_id)}-"
    comment_marker = f"order #{int(order_id)}"
    expected_invoice = str(expected_invoice_id or "").strip()
    expected_payment = str(expected_payment_id or "").strip()
    for payment in rows:
        if not isinstance(payment, dict):
            continue
        details = payment.get("data") if isinstance(payment.get("data"), dict) else {}
        candidate_invoice = str(details.get("invoice_id") or "").strip()
        candidate_payment = str(details.get("payment_id") or "").strip()
        comment = _market_search_text(details.get("commentPlain") or details.get("comment"))
        incoming = round(float(payment.get("incoming_sum") or payment.get("sum") or 0.0), 2)
        succeeded = (
            int(payment.get("is_finished") or 0) == 1
            and str(payment.get("payment_status") or "").lower() in {"success_in", "success", "completed"}
            and incoming >= target_amount - 0.01
        )
        matches_order = (
            order_marker in candidate_payment
            or comment_marker in comment
            or (expected_invoice and candidate_invoice == expected_invoice)
            or (expected_payment and candidate_payment == expected_payment)
        )
        if succeeded and matches_order:
            return {
                "status": "paid",
                "invoice_id": candidate_invoice,
                "payment_id": candidate_payment,
                "paid_date": int(payment.get("operation_date") or 0),
                "payment_history": payment,
            }
    return None


def is_lolz_invoice_paid(invoice_data: dict) -> bool:
    return str(invoice_data.get("status") or "").lower() in {"paid", "completed", "success"}


async def get_heleket_payment(invoice_id: str | None = None, order_id: str | None = None) -> dict:
    payload: dict[str, str] = {}
    if invoice_id:
        payload["uuid"] = str(invoice_id)
    if order_id:
        payload["order_id"] = str(order_id)
    if not payload:
        raise HeleketError("Не указан uuid или order_id Heleket")

    data = await _heleket_request("POST", "/v1/payment/info", json_body=payload)
    payment_data = data.get("result") if isinstance(data, dict) else None
    if not isinstance(payment_data, dict):
        raise HeleketError("Heleket не вернул данные платежа")
    return payment_data


def is_heleket_invoice_paid(invoice_data: dict) -> bool:
    status_value = invoice_data.get("status") or invoice_data.get("payment_status") or ""
    return str(status_value).lower() in HELEKET_PAID_STATUSES


async def get_xrocket_app_info() -> dict:
    data = await _xrocket_request("GET", "/app/info")
    return _extract_xrocket_payload(data)


async def create_xrocket_transfer(tg_user_id: int, amount: float, currency: str, description: str | None = None) -> dict:
    transfer_id = f"pt{uuid.uuid4().hex[:18]}"
    payload = {
        "tgUserId": int(tg_user_id),
        "currency": currency,
        "amount": round(float(amount), 2),
        "transferId": transfer_id,
    }
    if description:
        payload["description"] = description

    data = await _xrocket_request("POST", "/app/transfer", json_body=payload)
    transfer_data = _extract_xrocket_payload(data)
    return {
        "transfer_id": transfer_id,
        "raw": transfer_data,
    }


async def get_xrocket_invoice(invoice_id: str) -> dict:
    data = await _xrocket_request("GET", f"/tg-invoices/{invoice_id}")
    return _extract_xrocket_payload(data)


def is_xrocket_invoice_paid(invoice_data: dict) -> bool:
    status_value = (
        invoice_data.get("status")
        or invoice_data.get("invoiceStatus")
        or invoice_data.get("state")
        or ""
    )
    return str(status_value).lower() in XROCKET_PAID_STATUSES
