"""Proxyma.io reseller integration: static residential and mobile proxies.

Two product kinds are resold through the single Proxyma reseller API key:

* ``isp``    — ``/api/isp/*``                         static residential (ISP) IPv4
* ``mobile`` — ``/api/reseller/residential-mobile/*`` rotating mobile residential,
  sold through one fixed custom tariff (``PROXYMA_MOBILE_TARIFF_ID``) at our own
  GB package sizes rather than the supplier's named bundle catalog.

Purchases are written to the same ``maskify_proxy_purchases`` table the rest of
the proxy storefront already uses, with ``partner_service`` set to
``proxyma_<kind>``.  That keeps the existing balance debit, invoice, refund and
mini-app history plumbing working unchanged; the Proxyma-only fields live in the
extra ``proxyma_*`` columns created by :func:`init_proxyma_db`.

Everything here returns *supplier* prices in USD.  The retail markup is applied
by the callers (``main.py`` for the shop, ``miniapp.py`` for the public API).
"""

import asyncio
import json
import logging
import os
import re
import sqlite3
import time
from typing import Any

import aiohttp
from dotenv import load_dotenv

from vproxy_service import _vproxy_conn, init_vproxy_db


load_dotenv()

logger = logging.getLogger("proxyma")

PROXYMA_API_BASE = os.getenv("PROXYMA_API_BASE", "https://api.proxyma.io").rstrip("/")
PROXYMA_API_KEY = os.getenv("PROXYMA_API_KEY", "").strip()
PROXYMA_TIMEOUT_SECONDS = float(os.getenv("PROXYMA_TIMEOUT_SECONDS", "30") or 30)
PROXYMA_CATALOG_CACHE_TTL_SECONDS = float(os.getenv("PROXYMA_CATALOG_CACHE_TTL_SECONDS", "300") or 300)
# Shop-side markup fallback. The live value is the admin-editable
# ``proxyma_markup_percent`` knob; this is only used when it was never set.
PROXYMA_MARKUP_PERCENT_DEFAULT = float(os.getenv("PROXYMA_MARKUP_PERCENT", "100") or 100)
# Mobile proxy lists are created with this output format (1-4 on Proxyma's side).
PROXYMA_MOBILE_LIST_FORMAT = int(os.getenv("PROXYMA_MOBILE_LIST_FORMAT", "1") or 1)
# One custom Proxyma tariff sells all mobile traffic; its per-GB cost was quoted
# to us directly by Proxyma support and cannot be read back from any catalog
# endpoint, so it is configured here rather than fetched.
PROXYMA_MOBILE_TARIFF_ID = os.getenv("PROXYMA_MOBILE_TARIFF_ID", "01M2WE7RF2V83G42VG1S4FDVF6").strip()
PROXYMA_MOBILE_COST_PER_GB = float(os.getenv("PROXYMA_MOBILE_COST_PER_GB", "2") or 2)
PROXYMA_MOBILE_PACKAGES_GB = tuple(
    float(chunk) for chunk in os.getenv("PROXYMA_MOBILE_PACKAGES_GB", "1,5,10,25,50,100").split(",") if chunk.strip()
)
# The ISP catalog's own ``price`` field is not this account's real cost — see
# the note in ``_normalize_tariff``. $1.50/IP flat was confirmed with two live
# test purchases (ISP-1 and ISP-3) on 2026-09-24.
PROXYMA_ISP_COST_PER_IP = float(os.getenv("PROXYMA_ISP_COST_PER_IP", "1.5") or 1.5)

PROXYMA_KINDS = ("isp", "mobile")
PROXYMA_KIND_TITLES = {
    "isp": "Статика ISP",
    "mobile": "Мобильные",
}
PROXYMA_KIND_UNITS = {
    "isp": "IP",
    "mobile": "GB",
}
PROXYMA_SERVICE_PREFIX = "proxyma_"
PROXYMA_MOBILE_PRESETS = (
    ("world-mix", "🌍 Весь мир"),
    ("europe", "🇪🇺 Европа"),
    ("north-america", "🌎 Северная Америка"),
    ("south-america", "🌎 Южная Америка"),
    ("asia", "🌏 Азия"),
    ("africa", "🌍 Африка"),
    ("oceania", "🏝 Океания"),
)
PROXYMA_MOBILE_PRESET_KEYS = {key for key, _ in PROXYMA_MOBILE_PRESETS}

_catalog_cache: dict[str, tuple[float, Any]] = {}
_catalog_locks: dict[str, asyncio.Lock] = {}


class ProxymaError(RuntimeError):
    """A Proxyma request failed in a way the customer should be told about."""


def proxyma_service_name(kind: str) -> str:
    return f"{PROXYMA_SERVICE_PREFIX}{kind}"


def is_proxyma_service(service: str | None) -> bool:
    return str(service or "").startswith(PROXYMA_SERVICE_PREFIX)


def proxyma_kind_from_service(service: str | None) -> str:
    kind = str(service or "")[len(PROXYMA_SERVICE_PREFIX):]
    return kind if kind in PROXYMA_KINDS else ""


# ---------------------------------------------------------------------------
# Low-level API access
# ---------------------------------------------------------------------------


async def _proxyma_request(
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
    params: dict | None = None,
) -> Any:
    if not PROXYMA_API_KEY:
        raise ProxymaError("Прокси Proxyma временно недоступны. Попробуйте позже.")
    timeout = aiohttp.ClientTimeout(total=PROXYMA_TIMEOUT_SECONDS)
    last_error = ""
    for attempt in range(3):
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(
                    method,
                    f"{PROXYMA_API_BASE}{path}",
                    headers={
                        "api-key": PROXYMA_API_KEY,
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                    json=json_body,
                    params=params,
                ) as response:
                    # Keep the body whole for parsing: an ISP plan answer runs to
                    # tens of kilobytes and truncating it turns valid JSON into
                    # an empty dict. Only the log line gets clipped.
                    body = await response.text()
                    excerpt = body[:1000]
                    try:
                        payload = json.loads(body) if body else {}
                    except ValueError:
                        payload = {}
                    if response.status < 400:
                        return payload
                    message = _extract_error_message(payload) or excerpt
                    # 4xx is a decision, not a hiccup: retrying never changes it.
                    if response.status < 500:
                        logger.warning("proxyma %s %s rejected: %s %s", method, path, response.status, excerpt)
                        raise ProxymaError(f"Поставщик отклонил запрос: {message}")
                    last_error = f"{response.status} {excerpt}"
        except ProxymaError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError) as error:
            last_error = repr(error)
        if attempt < 2:
            await asyncio.sleep(0.5 * (attempt + 1))
    logger.warning("proxyma %s %s failed after retries: %s", method, path, last_error)
    raise ProxymaError("Прокси Proxyma временно недоступны. Попробуйте позже.")


def _extract_error_message(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    result = payload.get("result")
    if isinstance(result, dict) and result.get("message"):
        return str(result["message"])
    for key in ("message", "error", "detail"):
        if payload.get(key):
            return str(payload[key])
    return ""


def _unwrap(payload: Any) -> Any:
    """Return the useful body of a Proxyma response.

    The API is inconsistent: some endpoints answer ``{"result": {"status": 200,
    "data": ...}}``, some ``{"success": true, "data": ...}`` and some a bare
    array.  Callers only ever want the ``data`` part.
    """
    if isinstance(payload, dict):
        result = payload.get("result")
        if isinstance(result, dict) and "data" in result:
            return result["data"]
        if "data" in payload:
            return payload["data"]
    return payload


_PRICE_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


def parse_proxyma_price(raw: Any) -> float:
    """``"42.75 $"`` -> ``42.75``. Unparseable prices become 0.0."""
    if isinstance(raw, (int, float)):
        return max(float(raw), 0.0)
    match = _PRICE_RE.search(str(raw or ""))
    if not match:
        return 0.0
    try:
        return max(float(match.group(0).replace(",", ".")), 0.0)
    except ValueError:
        return 0.0


async def _cached(key: str, loader) -> Any:
    now = time.monotonic()
    cached = _catalog_cache.get(key)
    if cached is not None and now - cached[0] < PROXYMA_CATALOG_CACHE_TTL_SECONDS:
        return cached[1]
    lock = _catalog_locks.setdefault(key, asyncio.Lock())
    async with lock:
        cached = _catalog_cache.get(key)
        if cached is not None and time.monotonic() - cached[0] < PROXYMA_CATALOG_CACHE_TTL_SECONDS:
            return cached[1]
        value = await loader()
        _catalog_cache[key] = (time.monotonic(), value)
        return value


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


async def get_proxyma_balance() -> float:
    """Reseller balance in USD. Zero means no purchase will go through."""
    payload = await _proxyma_request("GET", "/api/reseller/get/balance")
    result = payload.get("result") if isinstance(payload, dict) else None
    raw = result.get("message") if isinstance(result, dict) else None
    return parse_proxyma_price(raw)


def _normalize_tariff(kind: str, row: Any) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    try:
        tariff_id = int(row.get("tariff_id") or row.get("id") or 0)
    except (TypeError, ValueError):
        return None
    if tariff_id <= 0:
        return None
    quantity = int(row.get("ports") or 0)
    if quantity <= 0:
        return None
    # The catalog's ``price`` field is not what actually gets deducted from
    # the reseller balance on this account — confirmed live on 2026-09-24:
    # ISP-1 (catalog $5) billed $1.50, ISP-3 (catalog $13.5) billed $4.50,
    # both exactly quantity * $1.50. Price accordingly rather than trusting
    # the catalog figure.
    supplier_price = round(quantity * PROXYMA_ISP_COST_PER_IP, 2)
    return {
        "kind": kind,
        "tariff_id": tariff_id,
        "name": str(row.get("name") or f"Tariff {tariff_id}").strip(),
        "supplier_price": supplier_price,
        "period_days": int(row.get("period") or 30),
        "unit": PROXYMA_KIND_UNITS[kind],
        "plan_id": int(row.get("plan_id") or 0),
        "quantity": quantity,
    }


def _mobile_packages() -> list[dict[str, Any]]:
    """Our own GB packages for the one custom mobile tariff, cheapest first.

    Proxyma has no catalog entry for a custom tariff, so unlike ``isp`` these
    are not fetched — the per-GB cost was quoted to us directly and the
    package sizes are ours to pick.
    """
    return [
        {
            "kind": "mobile",
            "tariff_id": int(round(gb * 100)),
            "name": f"Mobile - {gb:g}",
            "supplier_price": round(gb * PROXYMA_MOBILE_COST_PER_GB, 2),
            "period_days": 30,
            "unit": PROXYMA_KIND_UNITS["mobile"],
            "quantity": gb,
        }
        for gb in PROXYMA_MOBILE_PACKAGES_GB
    ]


_TARIFF_PATHS = {
    "isp": "/api/isp/buy/packages/list",
}


async def get_proxyma_tariffs(kind: str) -> list[dict[str, Any]]:
    """Available tariffs/packages for one product kind, cheapest first."""
    if kind not in PROXYMA_KINDS:
        raise ProxymaError("Неизвестный тип прокси.")
    if kind == "mobile":
        return _mobile_packages()

    async def load() -> list[dict[str, Any]]:
        payload = await _proxyma_request("GET", _TARIFF_PATHS[kind])
        rows = _unwrap(payload)
        if not isinstance(rows, list):
            return []
        tariffs = [item for item in (_normalize_tariff(kind, row) for row in rows) if item]
        tariffs.sort(key=lambda item: (item["supplier_price"], item["tariff_id"]))
        return tariffs

    return await _cached(f"tariffs:{kind}", load)


async def get_proxyma_tariff(kind: str, tariff_id: int) -> dict[str, Any]:
    for tariff in await get_proxyma_tariffs(kind):
        if int(tariff["tariff_id"]) == int(tariff_id):
            return tariff
    raise ProxymaError("Тариф не найден или больше не продаётся.")


async def get_proxyma_isp_plan(plan_id: int) -> dict[str, Any]:
    """Purposes (use cases) and their per-country stock for one ISP plan."""

    async def load() -> dict[str, Any]:
        payload = await _proxyma_request("GET", f"/api/isp/plans/{int(plan_id)}")
        if not isinstance(payload, dict) or not payload.get("isSuccess"):
            raise ProxymaError("Не удалось получить список локаций ISP.")
        purposes = []
        for row in payload.get("purposes") or []:
            if not isinstance(row, dict):
                continue
            countries = row.get("countries") if isinstance(row.get("countries"), dict) else {}
            available = {
                str(name): int(count or 0)
                for name, count in countries.items()
                if int(count or 0) > 0
            }
            if not available:
                continue
            purposes.append({
                "purpose_id": int(row.get("id") or 0),
                "name": str(row.get("display_name") or "").strip(),
                "countries": available,
            })
        purposes.sort(key=lambda item: item["name"].lower())
        return {
            "plan_id": int(plan_id),
            "title": str(payload.get("title") or ""),
            "ip_count": int(payload.get("ip_count") or 0),
            "purposes": purposes,
        }

    return await _cached(f"isp_plan:{int(plan_id)}", load)


async def get_proxyma_isp_purpose(plan_id: int, purpose_id: int) -> dict[str, Any]:
    plan = await get_proxyma_isp_plan(plan_id)
    for purpose in plan["purposes"]:
        if int(purpose["purpose_id"]) == int(purpose_id):
            return purpose
    raise ProxymaError("Назначение прокси больше недоступно.")


def sorted_purpose_countries(purpose: dict[str, Any]) -> list[tuple[str, int]]:
    """Countries of an ISP purpose in a stable order, so a callback can address
    one by index instead of by (possibly long) name."""
    countries = purpose.get("countries") or {}
    return sorted(countries.items(), key=lambda item: (item[0] != "Any", item[0]))


# ---------------------------------------------------------------------------
# Purchasing
# ---------------------------------------------------------------------------


def _require_purchase_success(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ProxymaError("Поставщик вернул неожиданный ответ.")
    if payload.get("success") is False:
        raise ProxymaError(_extract_error_message(payload) or "Поставщик отклонил покупку.")
    return payload


async def buy_proxyma_isp(tariff_id: int, purpose_id: int, country: str) -> int:
    payload = _require_purchase_success(
        await _proxyma_request(
            "POST",
            "/api/isp/buy",
            json_body={
                "tariff_id": int(tariff_id),
                "purpose_id": str(purpose_id),
                "country": str(country),
            },
        )
    )
    order_id = int(payload.get("order_id") or 0)
    if order_id <= 0:
        raise ProxymaError("Поставщик не вернул номер заказа.")
    return order_id


async def buy_proxyma_mobile(traffic_gb: float) -> str:
    if traffic_gb <= 0:
        raise ProxymaError("Некорректный объём трафика для мобильного пакета.")
    body: dict[str, Any] = {"tariff_id": PROXYMA_MOBILE_TARIFF_ID, "traffic": float(traffic_gb)}
    payload = await _proxyma_request("POST", "/api/reseller/residential-mobile/buy/package", json_body=body)
    data = _unwrap(payload)
    package_key = ""
    if isinstance(data, dict):
        package_key = str(data.get("package_key") or "").strip()
    if not package_key:
        raise ProxymaError(_extract_error_message(payload) or "Поставщик не вернул ключ пакета.")
    return package_key


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


def _format_endpoint(host: str, login: str, password: str) -> str:
    host = str(host or "").strip()
    if not host:
        return ""
    login = str(login or "").strip()
    password = str(password or "").strip()
    if login and password:
        return f"{host}:{login}:{password}"
    return host


def _isp_lines(package: dict[str, Any]) -> list[str]:
    login = package.get("login") or ""
    password = package.get("password") or ""
    lines = []
    for item in package.get("ip_list") or []:
        if isinstance(item, dict):
            line = _format_endpoint(item.get("ip"), item.get("login") or login, item.get("password") or password)
        else:
            line = _format_endpoint(item, login, password)
        if line:
            lines.append(line)
    return lines


def _packages_list(payload: Any) -> list[dict[str, Any]]:
    data = _unwrap(payload)
    if isinstance(data, dict):
        data = [data]
    return [row for row in (data or []) if isinstance(row, dict)]


def _match_package(packages: list[dict[str, Any]], order_id: int) -> dict[str, Any] | None:
    # ``/api/isp/buy`` returns its ``order_id`` matching each row's ``id`` in
    # ``/api/isp/packages`` — that list also carries an unrelated internal
    # ``order_id`` field of its own, which must not be used for the match
    # (confirmed live on 2026-09-24: matching on ``order_id`` never found the
    # row, so delivery silently stayed empty).
    for row in packages:
        try:
            if int(row.get("id") or 0) == int(order_id):
                return row
        except (TypeError, ValueError):
            continue
    return None


async def fetch_proxyma_isp_delivery(order_id: int) -> tuple[str, dict[str, Any]]:
    package = _match_package(_packages_list(await _proxyma_request("GET", "/api/isp/packages")), order_id)
    if package is None:
        return "", {}
    lines = _isp_lines(package)
    if not lines:
        return "", package
    header = [
        f"# Proxyma ISP · заказ {order_id}",
        f"# Страна: {package.get('coutnry') or package.get('country') or '-'}",
        f"# Действует до: {package.get('expires_at') or '-'}",
    ]
    return "\n".join(header + lines), package


def _mobile_proxy_lines(payload: Any) -> list[str]:
    """Pull proxy strings out of whatever shape ``/reseller/get/proxy`` returned."""
    data = _unwrap(payload)
    if isinstance(data, str):
        return [line.strip() for line in data.splitlines() if line.strip()]
    if isinstance(data, dict):
        for key in ("proxy", "proxies", "list", "lines", "content"):
            if key in data:
                return _mobile_proxy_lines(data[key])
        return []
    if isinstance(data, list):
        lines = []
        for item in data:
            if isinstance(item, str) and item.strip():
                lines.append(item.strip())
            elif isinstance(item, dict):
                line = _format_endpoint(
                    item.get("proxy") or item.get("ip") or item.get("host"),
                    item.get("login") or item.get("username"),
                    item.get("password"),
                )
                if line:
                    lines.append(line)
        return lines
    return []


async def create_proxyma_mobile_list(
    package_key: str,
    *,
    list_name: str,
    list_login: str,
    location_preset: str = "world-mix",
    country_code: str = "",
    rotation_period: int = 0,
) -> None:
    body: dict[str, Any] = {
        "package_key": str(package_key),
        "list_name": str(list_name),
        "list_login": str(list_login),
        "rotation_period": max(int(rotation_period), 0),
        "format": PROXYMA_MOBILE_LIST_FORMAT,
    }
    if country_code:
        body["location_preset"] = "custom"
        body["country_code"] = str(country_code).upper()
    else:
        body["location_preset"] = location_preset if location_preset in PROXYMA_MOBILE_PRESET_KEYS else "world-mix"
    await _proxyma_request(
        "POST",
        "/api/reseller/create/proxy",
        json_body=body,
        params={"package_key": str(package_key)},
    )


async def fetch_proxyma_mobile_delivery(
    package_key: str,
    *,
    list_name: str,
    list_id: str = "",
) -> tuple[str, dict[str, Any]]:
    if not list_id:
        lists_payload = await _proxyma_request(
            "GET", "/api/reseller/get/lists", params={"package_key": str(package_key)}
        )
        for row in _packages_list(lists_payload):
            if str(row.get("list_name") or row.get("name") or "") == list_name:
                list_id = str(row.get("list_id") or row.get("id") or "")
                break
    payload = await _proxyma_request(
        "GET",
        "/api/reseller/get/proxy",
        params={"package_key": str(package_key), "list_name": str(list_name), "list_id": str(list_id or "")},
    )
    lines = _mobile_proxy_lines(payload)
    if not lines:
        return "", {"list_id": list_id}
    header = [
        f"# Proxyma Mobile · пакет {package_key}",
        f"# Список: {list_name}",
    ]
    return "\n".join(header + lines), {"list_id": list_id}


async def get_proxyma_mobile_usage(package_key: str) -> dict[str, Any]:
    payload = await _proxyma_request("GET", f"/api/reseller/info/package/{package_key}")
    data = _unwrap(payload)
    return data if isinstance(data, dict) else {}


async def provision_proxyma_order(purchase: dict[str, Any]) -> str:
    """Place the upstream order for a paid purchase and return the delivery text.

    An empty string means "ordered, not ready yet" — the caller should leave the
    purchase in ``delivery_pending`` and let :func:`refresh_proxyma_delivery`
    pick it up later.
    """
    kind = proxyma_kind_from_service(purchase.get("partner_service"))
    if kind not in PROXYMA_KINDS:
        raise ProxymaError("Неизвестный тип прокси.")
    options = load_proxyma_options(purchase)
    purchase_id = int(purchase["id"])

    if kind == "mobile":
        package_key = str(purchase.get("proxyma_package_key") or "")
        if not package_key:
            package_key = await buy_proxyma_mobile(float(options.get("traffic_gb") or 0))
            set_proxyma_reference(purchase_id, package_key=package_key)
        list_name = str(options.get("list_name") or f"sous{purchase_id}")
        if not options.get("list_created"):
            await create_proxyma_mobile_list(
                package_key,
                list_name=list_name,
                list_login=str(options.get("list_login") or f"sous{purchase_id}"),
                location_preset=str(options.get("location_preset") or "world-mix"),
                country_code=str(options.get("country_code") or ""),
                rotation_period=int(options.get("rotation_period") or 0),
            )
            options["list_created"] = True
            options["list_name"] = list_name
            update_proxyma_options(purchase_id, options)
        delivery, meta = await fetch_proxyma_mobile_delivery(
            package_key, list_name=list_name, list_id=str(options.get("list_id") or "")
        )
        if meta.get("list_id") and not options.get("list_id"):
            options["list_id"] = meta["list_id"]
            update_proxyma_options(purchase_id, options)
        return delivery

    order_id = int(purchase.get("provider_order_id") or 0)
    if order_id <= 0:
        order_id = await buy_proxyma_isp(
            int(options.get("tariff_id") or 0),
            int(options.get("purpose_id") or 0),
            str(options.get("country") or "Any"),
        )
        set_proxyma_reference(purchase_id, provider_order_id=order_id)
    delivery, _ = await fetch_proxyma_isp_delivery(order_id)
    return delivery


async def refresh_proxyma_delivery(purchase: dict[str, Any]) -> str:
    """Re-check an already-placed order. Never places a new upstream order."""
    kind = proxyma_kind_from_service(purchase.get("partner_service"))
    if kind == "mobile":
        package_key = str(purchase.get("proxyma_package_key") or "")
        if not package_key:
            return ""
        options = load_proxyma_options(purchase)
        delivery, _ = await fetch_proxyma_mobile_delivery(
            package_key,
            list_name=str(options.get("list_name") or ""),
            list_id=str(options.get("list_id") or ""),
        )
        return delivery
    order_id = int(purchase.get("provider_order_id") or 0)
    if order_id <= 0 or kind != "isp":
        return ""
    delivery, _ = await fetch_proxyma_isp_delivery(order_id)
    return delivery


# ---------------------------------------------------------------------------
# Persistence (extra columns on maskify_proxy_purchases)
# ---------------------------------------------------------------------------


_db_ready = False


def init_proxyma_db() -> None:
    global _db_ready
    if _db_ready:
        return
    init_vproxy_db()
    with _vproxy_conn() as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(maskify_proxy_purchases)")}
        for column, definition in (
            ("proxyma_options", "TEXT"),
            ("proxyma_package_key", "TEXT"),
            ("proxyma_supplier_price", "REAL"),
        ):
            if column not in columns:
                db.execute(f"ALTER TABLE maskify_proxy_purchases ADD COLUMN {column} {definition}")
    _db_ready = True


def create_proxyma_purchase(
    telegram_user_id: int,
    kind: str,
    quantity: float,
    price_usd: float,
    supplier_price_usd: float,
    options: dict[str, Any],
    api_request_key: str | None = None,
) -> int:
    init_proxyma_db()
    now = int(time.time())
    with _vproxy_conn() as db:
        cursor = db.execute(
            """INSERT INTO maskify_proxy_purchases(
                telegram_user_id,gb,price_usd,status,partner_service,partner_quantity,
                proxyma_options,proxyma_supplier_price,api_request_key,created_at,updated_at
            ) VALUES(?,?,?,'draft',?,?,?,?,?,?,?)""",
            (
                int(telegram_user_id),
                float(quantity) if kind == "mobile" else 0.0,
                float(price_usd),
                proxyma_service_name(kind),
                int(quantity) if kind != "mobile" else 0,
                json.dumps(options, ensure_ascii=False),
                float(supplier_price_usd),
                api_request_key,
                now,
                now,
            ),
        )
        return int(cursor.lastrowid)


def load_proxyma_options(purchase: dict[str, Any]) -> dict[str, Any]:
    try:
        options = json.loads(str(purchase.get("proxyma_options") or "{}"))
    except ValueError:
        return {}
    return options if isinstance(options, dict) else {}


def update_proxyma_options(purchase_id: int, options: dict[str, Any]) -> None:
    init_proxyma_db()
    with _vproxy_conn() as db:
        db.execute(
            "UPDATE maskify_proxy_purchases SET proxyma_options=?,updated_at=? WHERE id=?",
            (json.dumps(options, ensure_ascii=False), int(time.time()), int(purchase_id)),
        )


def set_proxyma_reference(
    purchase_id: int,
    *,
    provider_order_id: int | None = None,
    package_key: str | None = None,
) -> None:
    init_proxyma_db()
    with _vproxy_conn() as db:
        if provider_order_id is not None:
            db.execute(
                "UPDATE maskify_proxy_purchases SET provider_order_id=?,updated_at=? WHERE id=?",
                (int(provider_order_id), int(time.time()), int(purchase_id)),
            )
        if package_key is not None:
            db.execute(
                "UPDATE maskify_proxy_purchases SET proxyma_package_key=?,updated_at=? WHERE id=?",
                (str(package_key), int(time.time()), int(purchase_id)),
            )


def claim_proxyma_draft_purchase(purchase_id: int, telegram_user_id: int) -> bool:
    """Atomically move a draft order to ``processing``.

    ``claim_maskify_proxy_purchase`` only claims rows that already went through
    an invoice (``waiting_payment``); paying from the internal balance starts
    from ``draft``, and this is what stops a double tap on "Списать с баланса"
    from ordering — and charging — twice.
    """
    init_proxyma_db()
    with _vproxy_conn() as db:
        cursor = db.execute(
            """UPDATE maskify_proxy_purchases SET status='processing',updated_at=?
               WHERE id=? AND telegram_user_id=? AND status='draft'
                 AND partner_service LIKE 'proxyma_%'""",
            (int(time.time()), int(purchase_id), int(telegram_user_id)),
        )
        return cursor.rowcount == 1


def release_proxyma_draft_purchase(purchase_id: int) -> None:
    """Undo :func:`claim_proxyma_draft_purchase` when the payment did not happen."""
    init_proxyma_db()
    with _vproxy_conn() as db:
        db.execute(
            "UPDATE maskify_proxy_purchases SET status='draft',updated_at=? WHERE id=? AND status='processing'",
            (int(time.time()), int(purchase_id)),
        )


def mark_proxyma_delivery_pending(purchase_id: int, partner_bot_id: int | None = None) -> None:
    init_proxyma_db()
    with _vproxy_conn() as db:
        db.execute(
            """UPDATE maskify_proxy_purchases
               SET status='delivery_pending',partner_bot_id=?,updated_at=? WHERE id=?""",
            (
                int(partner_bot_id) if partner_bot_id else None,
                int(time.time()),
                int(purchase_id),
            ),
        )


def set_proxyma_delivery(purchase_id: int, delivery_text: str) -> None:
    init_proxyma_db()
    with _vproxy_conn() as db:
        db.execute(
            """UPDATE maskify_proxy_purchases
               SET delivery_text=?,status='completed',updated_at=? WHERE id=?""",
            (str(delivery_text), int(time.time()), int(purchase_id)),
        )


def get_proxyma_purchase(purchase_id: int) -> dict[str, Any] | None:
    init_proxyma_db()
    with _vproxy_conn() as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT * FROM maskify_proxy_purchases WHERE id=?", (int(purchase_id),)
        ).fetchone()
    return dict(row) if row else None


def get_proxyma_purchase_by_request(telegram_user_id: int, request_key: str) -> dict[str, Any] | None:
    init_proxyma_db()
    with _vproxy_conn() as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            """SELECT * FROM maskify_proxy_purchases
               WHERE telegram_user_id=? AND api_request_key=?
                 AND partner_service LIKE 'proxyma_%'""",
            (int(telegram_user_id), str(request_key)),
        ).fetchone()
    return dict(row) if row else None


def list_user_proxyma_purchases(telegram_user_id: int, limit: int = 30) -> list[dict[str, Any]]:
    init_proxyma_db()
    with _vproxy_conn() as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            """SELECT * FROM maskify_proxy_purchases
               WHERE telegram_user_id=? AND partner_service LIKE 'proxyma_%'
                 AND status IN ('completed','delivery_pending','processing')
               ORDER BY id DESC LIMIT ?""",
            (int(telegram_user_id), int(limit)),
        ).fetchall()
    return [dict(row) for row in rows]


def list_pending_proxyma_purchases(limit: int = 50) -> list[dict[str, Any]]:
    """Paid Proxyma orders the supplier has not finished provisioning yet.

    Public-API orders are included: unlike the cdkey partner services, a Proxyma
    package has to be polled by us before the client can see it at all.
    """
    init_proxyma_db()
    with _vproxy_conn() as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            """SELECT * FROM maskify_proxy_purchases
               WHERE status='delivery_pending' AND partner_service LIKE 'proxyma_%'
               ORDER BY id ASC LIMIT ?""",
            (int(limit),),
        ).fetchall()
    return [dict(row) for row in rows]
