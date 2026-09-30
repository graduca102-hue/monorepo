import asyncio
import json
import os
import socket
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import aiohttp
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
DB_PATH = BASE_DIR / "data1.db"
API_BASE = os.getenv("GREEDY_SMS_API_BASE", "https://api.greedy-sms.com").rstrip("/")
API_KEY = os.getenv("GREEDY_SMS_API_KEY", "").strip()
MARKUP_PERCENT = float(os.getenv("GREEDY_SMS_MARKUP_PERCENT", "50") or 50)
RUB_PER_USDT = float(os.getenv("GREEDY_SMS_RUB_PER_USDT", os.getenv("MARKET_RUB_PER_USDT", "100")) or 100)
_COUNTRY_ISO_CACHE: dict[str, str] = {}
_COUNTRY_ISO_LOAD_TASK: asyncio.Task | None = None
_COUNTRY_ISO_LOCK = asyncio.Lock()


class GreedySmsError(RuntimeError):
    """An upstream error whose raw details must not be shown to customers."""

    _PUBLIC_MESSAGES = {
        "configuration_error": "SMS-сервис временно недоступен. Попробуйте позже.",
        "network_error": "SMS-сервис временно недоступен. Попробуйте позже.",
        "500": "Поставщик временно не принимает заказы. Попробуйте позже.",
        "401": "SMS-сервис временно недоступен. Попробуйте позже.",
        "403": "SMS-сервис временно недоступен. Попробуйте позже.",
        "404": "Предложение больше недоступно. Обновите список тарифов.",
        "offer_not_found": "Предложение больше недоступно. Обновите список тарифов.",
        "409": "Предложение больше недоступно. Обновите список тарифов.",
        "422": "Выбранный тариф больше недоступен. Обновите список.",
        "429": "Слишком много запросов. Попробуйте немного позже.",
    }

    def __init__(self, message: str, code: str = "provider_error", status_code: int | None = None):
        self.provider_message = str(message or "provider_error")
        self.code = str(code or "provider_error").strip().lower()
        self.status_code = status_code
        super().__init__(self.provider_message)

    @property
    def public_message(self) -> str:
        """Stable, user-safe message; ``str(error)`` remains useful in server logs."""
        if self.code in self._PUBLIC_MESSAGES:
            if self.code != "500":
                return self._PUBLIC_MESSAGES[self.code]
        normalized = self.provider_message.strip().upper()
        if "OFFER_NOT_FOUND" in normalized or "NO_NUMBERS" in normalized:
            return "Предложение больше недоступно. Обновите список тарифов."
        if (
            "NO_BALANCE" in normalized
            or "NOT ENOUGH BALANCE" in normalized
            or "INSUFFICIENT BALANCE" in normalized
        ):
            return "Поставщик временно не принимает заказы. Попробуйте позже."
        if self.code == "500":
            return self._PUBLIC_MESSAGES[self.code]
        return "SMS-сервис временно недоступен. Попробуйте позже."

    @property
    def offer_unavailable(self) -> bool:
        """True when the selected catalog row vanished at the supplier."""
        normalized = f"{self.code} {self.provider_message}".strip().upper()
        return any(
            marker in normalized
            for marker in ("OFFER_NOT_FOUND", "OFFER NOT FOUND", "NO_NUMBERS")
        ) or self.status_code in {404, 409}


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        result = super().__exit__(exc_type, exc_value, traceback)
        self.close()
        return result


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH, timeout=30, factory=ClosingConnection)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=30000")
    return db


def init_sms_db() -> None:
    with _connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS sms_activations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_key TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                partner_bot_id INTEGER,
                service_id TEXT NOT NULL,
                service_name TEXT NOT NULL,
                country_id INTEGER NOT NULL,
                country_name TEXT NOT NULL,
                provider_id INTEGER,
                provider_activation_id INTEGER,
                phone TEXT,
                supplier_price_rub REAL NOT NULL,
                sale_price_usd REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'ordering',
                sms_code TEXT,
                provider_status TEXT,
                charged INTEGER NOT NULL DEFAULT 1,
                refunded INTEGER NOT NULL DEFAULT 0,
                partner_profit_accrued REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(user_id, request_key),
                UNIQUE(provider_activation_id)
            );
            CREATE INDEX IF NOT EXISTS idx_sms_activations_user
                ON sms_activations(user_id, id DESC);
            """
        )
        columns = {str(row[1]) for row in db.execute("PRAGMA table_info(sms_activations)").fetchall()}
        if "partner_profit_accrued" not in columns:
            db.execute("ALTER TABLE sms_activations ADD COLUMN partner_profit_accrued REAL NOT NULL DEFAULT 0")


def sale_price_usd(supplier_price_rub: float, markup_percent: float | None = None) -> float:
    if RUB_PER_USDT <= 0:
        raise GreedySmsError("Некорректный курс RUB/USDT", "configuration_error")
    effective_markup = MARKUP_PERCENT if markup_percent is None else max(float(markup_percent), 0.0)
    return round(float(supplier_price_rub) / RUB_PER_USDT * (1 + effective_markup / 100), 4)


async def api_request(path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if not API_KEY:
        raise GreedySmsError("API SMS не настроен", "configuration_error")
    headers = {
        "x-api-key": API_KEY,
        "Accept": "application/json",
        "Accept-Encoding": "gzip, deflate",
        "Content-Type": "application/json",
    }
    timeout = aiohttp.ClientTimeout(total=25)
    method = "GET" if payload is None else "POST"
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            # The VPS has a broken IPv6 route while the provider advertises AAAA.
            # Restrict this upstream connection to IPv4 so DNS preference cannot
            # turn a healthy provider into a network_error.
            connector = aiohttp.TCPConnector(family=socket.AF_INET)
            async with aiohttp.ClientSession(timeout=timeout, headers=headers, connector=connector) as session:
                async with session.request(method, API_BASE + path, json=payload) as response:
                    raw = await response.text()
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        data = {"message": raw.strip() or f"HTTP {response.status}"}
                    if response.status >= 400:
                        message = str(data.get("message") or data.get("error") or data.get("details") or f"HTTP {response.status}")
                        provider_code = data.get("providerCode") or data.get("code") or "provider_error"
                        error = GreedySmsError(message, str(provider_code), response.status)
                        if response.status < 500 or attempt == 2:
                            raise error
                        last_error = error
                    else:
                        return data
        except GreedySmsError as error:
            if error.status_code is None or error.status_code < 500 or attempt == 2:
                raise
            last_error = error
        except (aiohttp.ClientError, TimeoutError) as error:
            last_error = error
        if attempt < 2:
            await asyncio.sleep(0.35 * (attempt + 1))
    raise GreedySmsError("SMS-сервис временно недоступен. Попробуйте позже.", "network_error") from last_error


async def get_services(page: int = 1, page_size: int = 100) -> dict:
    return await api_request("/activations/getServices", {"language": "rus", "page": page, "pageSize": page_size})


async def get_provider_balance_usd() -> float:
    """Return the supplier balance reported by Greedy SMS in USDT."""
    data = await api_request("/users/getMe")
    try:
        return max(float(data.get("balance") or 0.0), 0.0)
    except (AttributeError, TypeError, ValueError) as error:
        raise GreedySmsError("Некорректный ответ баланса SMS", "provider_error") from error


async def _load_country_iso_cache() -> None:
    """Warm flag metadata in the background; it must never delay SMS startup."""
    if _COUNTRY_ISO_CACHE:
        return
    try:
        timeout = aiohttp.ClientTimeout(total=6)
        async with aiohttp.ClientSession(timeout=timeout, headers={"Accept-Encoding": "gzip, deflate"}) as session:
            async with session.get("https://flagcdn.com/en/codes.json") as response:
                rows = await response.json()
        if isinstance(rows, dict):
            _COUNTRY_ISO_CACHE.update({
                "".join(ch for ch in str(name).lower() if ch.isalnum()): str(iso).lower()
                for iso, name in rows.items() if name and iso
            })
    except (aiohttp.ClientError, TimeoutError, ValueError, TypeError):
        pass


async def _schedule_country_iso_cache_load() -> None:
    global _COUNTRY_ISO_LOAD_TASK
    async with _COUNTRY_ISO_LOCK:
        if _COUNTRY_ISO_CACHE:
            return
        if _COUNTRY_ISO_LOAD_TASK is None or _COUNTRY_ISO_LOAD_TASK.done():
            _COUNTRY_ISO_LOAD_TASK = asyncio.create_task(_load_country_iso_cache())


async def get_countries(page: int = 1, page_size: int = 200) -> dict:
    payload = await api_request("/activations/getCountries", {"page": page, "pageSize": page_size})
    # Flag CDN is cosmetic. Do not block numbers/service data on a third party.
    await _schedule_country_iso_cache_load()
    aliases = {"england": "gb", "usa": "us", "southkorea": "kr", "northkorea": "kp", "ivorycoast": "ci", "congorepublic": "cg", "democraticrepublicofthecongo": "cd", "congo": "cg", "laos": "la", "moldova": "md", "syria": "sy", "bolivia": "bo", "venezuela": "ve", "iran": "ir", "tanzania": "tz", "brunei": "bn", "macau": "mo", "macao": "mo", "palestine": "ps", "czech": "cz", "papua": "pg", "uae": "ae", "salvador": "sv", "swaziland": "sz", "bosnia": "ba", "reunion": "re", "saotomeandprincipe": "st"}
    for row in payload.get("countries", []):
        eng = str((row.get("title") or {}).get("eng") or "")
        key = "".join(ch for ch in eng.lower() if ch.isalnum())
        row["iso"] = _COUNTRY_ISO_CACHE.get(key) or aliases.get(key) or ""
    return payload


async def get_prices(service: str, country: int | None = None) -> dict:
    body: dict[str, Any] = {"service": service, "page": 1, "pageSize": 200}
    if country is not None:
        body["country"] = country
    try:
        return await api_request("/activations/getPrices", body)
    except GreedySmsError as error:
        # No matching offer is an empty catalog result, not an infrastructure
        # failure. This also keeps the provider's internal business_logic token
        # out of the customer-facing API.
        if error.code == "offer_not_found" or "OFFER_NOT_FOUND" in error.provider_message.upper():
            return {"countries": []}
        raise


async def get_number(service: str, country: int, provider_id: int, max_price: float) -> dict:
    result = await api_request(
        "/activations/getNumber",
        {"service": service, "country": country, "providerId": provider_id, "maxPrice": round(max_price, 2)},
    )
    if not all(key in result for key in ("activationId", "phone", "price")):
        raise GreedySmsError(str(result.get("message") or result.get("error") or "Поставщик не выдал номер"))
    return result


async def get_provider_status(activation_id: int) -> dict:
    return await api_request("/activations/getStatus", {"activationId": activation_id})


async def set_provider_status(activation_id: int, status: str) -> dict:
    return await api_request("/activations/setStatus", {"activationId": activation_id, "status": status})


def reserve_activation(
    *, user_id: int, request_key: str, partner_bot_id: int | None, service_id: str,
    service_name: str, country_id: int, country_name: str, provider_id: int,
    supplier_price_rub: float, sale_price: float,
) -> tuple[dict | None, str | None, bool]:
    db = _connect()
    try:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT * FROM sms_activations WHERE user_id=? AND request_key=?", (user_id, request_key)).fetchone()
        if existing:
            db.commit()
            return dict(existing), None, False
        charged = db.execute("UPDATE users SET balance=balance-? WHERE user_id=? AND balance>=?", (sale_price, user_id, sale_price))
        if charged.rowcount != 1:
            db.rollback()
            return None, "insufficient_balance", False
        timestamp = _now()
        cursor = db.execute(
            """INSERT INTO sms_activations(
                request_key,user_id,partner_bot_id,service_id,service_name,country_id,country_name,
                provider_id,supplier_price_rub,sale_price_usd,status,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?, 'ordering',?,?)""",
            (request_key, user_id, partner_bot_id, service_id, service_name, country_id, country_name,
             provider_id, supplier_price_rub, sale_price, timestamp, timestamp),
        )
        db.commit()
        return get_activation(int(cursor.lastrowid), user_id), None, True
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def finalize_activation(local_id: int, provider_data: dict) -> dict:
    with _connect() as db:
        row = db.execute("SELECT * FROM sms_activations WHERE id=?", (local_id,)).fetchone()
        actual_supplier_rub = float(provider_data["price"])
        partner_profit = 0.0
        if row and row["partner_bot_id"] and float(row["partner_profit_accrued"] or 0) <= 0:
            base_sale_price = sale_price_usd(actual_supplier_rub, MARKUP_PERCENT)
            partner_profit = round(max(float(row["sale_price_usd"] or 0) - base_sale_price, 0.0), 6)
            if partner_profit > 0:
                db.execute(
                    "UPDATE partners_bots SET partner_earnings=partner_earnings+?,updated_at=? WHERE id=?",
                    (partner_profit, _now(), int(row["partner_bot_id"])),
                )
        db.execute(
            "UPDATE sms_activations SET provider_activation_id=?,phone=?,supplier_price_rub=?,partner_profit_accrued=?,status='waiting',provider_status='waiting',updated_at=? WHERE id=?",
            (int(provider_data["activationId"]), str(provider_data["phone"]), actual_supplier_rub, partner_profit, _now(), local_id),
        )
    return get_activation(local_id) or {}


def get_activation(local_id: int, user_id: int | None = None) -> dict | None:
    with _connect() as db:
        if user_id is None:
            row = db.execute("SELECT * FROM sms_activations WHERE id=?", (local_id,)).fetchone()
        else:
            row = db.execute("SELECT * FROM sms_activations WHERE id=? AND user_id=?", (local_id, user_id)).fetchone()
    return dict(row) if row else None


def list_activations(user_id: int, limit: int = 30) -> list[dict]:
    with _connect() as db:
        rows = db.execute("SELECT * FROM sms_activations WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit)).fetchall()
    return [dict(row) for row in rows]


def update_activation_status(local_id: int, provider_status: str) -> dict:
    raw = str(provider_status or "")
    normalized = raw.upper()
    code = ""
    status = "waiting"
    if normalized.startswith("STATUS_OK"):
        status = "code_received"
        code = raw.split(":", 1)[1] if ":" in raw else raw.removeprefix("STATUS_OK").strip()
    elif normalized in {"FINISHED", "STATUS_FINISH", "STATUS_FINISHED"}:
        status = "finished"
    elif normalized in {"CANCELED", "CANCELLED", "STATUS_CANCEL", "STATUS_CANCELED"}:
        status = "canceled"
    with _connect() as db:
        db.execute("UPDATE sms_activations SET status=?,sms_code=COALESCE(NULLIF(?,''),sms_code),provider_status=?,updated_at=? WHERE id=?",
                   (status, code, raw, _now(), local_id))
    return get_activation(local_id) or {}


def refund_activation(local_id: int, status: str = "canceled") -> tuple[dict | None, bool]:
    db = _connect()
    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM sms_activations WHERE id=?", (local_id,)).fetchone()
        if not row:
            db.rollback()
            return None, False
        if int(row["charged"] or 0) == 1 and int(row["refunded"] or 0) == 0:
            db.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (row["sale_price_usd"], row["user_id"]))
            if row["partner_bot_id"] and float(row["partner_profit_accrued"] or 0) > 0:
                db.execute(
                    "UPDATE partners_bots SET partner_earnings=MAX(partner_earnings-?,0),updated_at=? WHERE id=?",
                    (float(row["partner_profit_accrued"]), _now(), int(row["partner_bot_id"])),
                )
            refunded = True
        else:
            refunded = False
        db.execute("UPDATE sms_activations SET status=?,refunded=1,updated_at=? WHERE id=?", (status, _now(), local_id))
        db.commit()
        return get_activation(local_id), refunded
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def mark_finished(local_id: int) -> dict:
    with _connect() as db:
        db.execute("UPDATE sms_activations SET status='finished',provider_status='finished',updated_at=? WHERE id=?", (_now(), local_id))
    return get_activation(local_id) or {}
