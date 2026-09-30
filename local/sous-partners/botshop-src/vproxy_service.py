import asyncio
import json
import os
import sqlite3
import time
import secrets
import base64
import hashlib
import hmac
import ipaddress
import socket
from contextlib import contextmanager
from urllib.parse import unquote, urlsplit
from typing import Any

import aiohttp
from dotenv import load_dotenv


load_dotenv()

VPROXY_API_BASE = os.getenv("VPROXY_API_BASE", "https://vproxy.cc/public-api").rstrip("/")
VPROXY_USERNAME = os.getenv("VPROXY_USERNAME", "").strip()
VPROXY_PASSWORD = os.getenv("VPROXY_PASSWORD", "").strip()
VPROXY_JWT = os.getenv("VPROXY_JWT", "").strip()
VPROXY_API_KEY = os.getenv("VPROXY_API_KEY", "").strip()
VPROXY_RESELLER_API_BASE = os.getenv("VPROXY_RESELLER_API_BASE", "https://vproxy.cc/reseller-api").rstrip("/")
MASKIFY_USER_API_BASE = os.getenv("MASKIFY_USER_API_BASE", "https://maskify.su/api/user/v1").rstrip("/")
MASKIFY_USER_API_KEY = os.getenv("MASKIFY_USER_API_KEY", "").strip()
MASKIFY_RESELLER_API_BASE = os.getenv("MASKIFY_RESELLER_API_BASE", "https://maskify.su/api/reseller/v2").rstrip("/")
MASKIFY_RESELLER_API_KEY = os.getenv("MASKIFY_RESELLER_API_KEY", "").strip()
MASKIFY_SUBUSER_USERNAME = os.getenv("MASKIFY_SUBUSER_USERNAME", "").strip()
MASKIFY_SUBUSER_PASSWORD = os.getenv("MASKIFY_SUBUSER_PASSWORD", "").strip()
MASKIFY_PROXY_HOST = os.getenv("MASKIFY_PROXY_HOST", "resi.maskify.su").strip()
MASKIFY_PROXY_PORT = int(os.getenv("MASKIFY_PROXY_PORT", "80") or 80)
MASKIFY_VALIDATE_BEFORE_DELIVERY = os.getenv("MASKIFY_VALIDATE_BEFORE_DELIVERY", "0").strip() == "1"

VPROXY_POOLS = {
    "datacenter": "Датацентр",
    "residential": "Резидентские",
    "residential_premium": "Резидентские премиум",
    "mobile": "Мобильные",
}

_TOKEN_CACHE: dict[str, Any] = {"value": "", "expires_at": 0.0}
_TOKEN_LOCK = asyncio.Lock()
VPROXY_DB_PATH = os.path.join(os.path.dirname(__file__), "vproxy_users.db")


@contextmanager
def _vproxy_conn():
    """Open the vproxy SQLite DB, commit/rollback like ``with connection``, and
    always close the handle.

    ``with sqlite3.connect(path) as db:`` only commits or rolls back on exit —
    it never closes the connection, so every call leaked a file descriptor to
    ``vproxy_users.db`` until GC. On the long-lived botshop process those piled
    up (hundreds of open handles) and eventually hit ``LimitNOFILE`` with
    "Too many open files", making the whole :8081 API unresponsive.
    """
    conn = sqlite3.connect(VPROXY_DB_PATH)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


class VProxyError(RuntimeError):
    pass


async def get_maskify_available_gb() -> float:
    """Return the traffic currently available for allocation by the supplier."""
    if MASKIFY_RESELLER_API_KEY:
        account = await get_maskify_reseller_account()
        return max(float(account["gb_available"] or 0.0), 0.0)
    if not MASKIFY_USER_API_KEY:
        raise VProxyError("Сервис временно недоступен. Попробуйте позже.")
    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                f"{MASKIFY_USER_API_BASE}/info",
                headers={
                    "X-User-API-Key": MASKIFY_USER_API_KEY,
                    "Accept": "application/json",
                },
            ) as response:
                payload = await response.json(content_type=None)
                if response.status != 200 or not isinstance(payload, dict) or not payload.get("success"):
                    raise VProxyError("Сервис временно недоступен. Попробуйте позже.")
                try:
                    return max(float(payload.get("gb_available") or 0.0), 0.0)
                except (TypeError, ValueError) as error:
                    raise VProxyError("Сервис временно недоступен. Попробуйте позже.") from error
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError) as error:
        raise VProxyError("Сервис временно недоступен. Попробуйте позже.") from error


async def _maskify_reseller_request(method: str, path: str, *, json_body: dict | None = None) -> dict:
    if not MASKIFY_RESELLER_API_KEY:
        raise VProxyError("Сервис временно недоступен. Попробуйте позже.")
    timeout = aiohttp.ClientTimeout(total=20)
    # Maskify occasionally returns HTTP 400 with its temporary-maintenance
    # message.  Treat that as a transient upstream failure, not as a bad API
    # request from our customer.  A short retry also prevents an otherwise
    # successful allocation from being needlessly rejected.
    for attempt in range(3):
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(
                    method,
                    f"{MASKIFY_RESELLER_API_BASE}{path}",
                    headers={
                        "X-Reseller-API-Key": MASKIFY_RESELLER_API_KEY,
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                    json=json_body,
                ) as response:
                    payload = await response.json(content_type=None)
                    if response.status < 400 and isinstance(payload, dict):
                        return payload
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError):
            pass
        if attempt < 2:
            await asyncio.sleep(0.5 * (attempt + 1))
    raise VProxyError("Сервис временно недоступен. Попробуйте позже.")


async def get_maskify_reseller_account() -> dict[str, float]:
    payload = await _maskify_reseller_request("GET", "/account")
    try:
        return {
            "balance_usd": max(float(payload.get("balance_usd") or 0.0), 0.0),
            "gb_available": max(float(payload.get("gb_available") or 0.0), 0.0),
            "cost_per_gb": max(float(payload.get("cost_per_gb") or 0.0), 0.0),
            "total_allocated_gb": max(float(payload.get("total_allocated_gb") or 0.0), 0.0),
        }
    except (TypeError, ValueError) as error:
        raise VProxyError("Сервис временно недоступен. Попробуйте позже.") from error


async def add_maskify_subuser_traffic(gb: float) -> float:
    if not MASKIFY_SUBUSER_USERNAME:
        raise VProxyError("Сервис временно недоступен. Попробуйте позже.")
    amount = round(float(gb), 2)
    if amount <= 0:
        raise VProxyError("Некорректное количество трафика.")
    payload = await _maskify_reseller_request(
        "PATCH",
        f"/subusers/{MASKIFY_SUBUSER_USERNAME}",
        json_body={"add_gb": amount},
    )
    try:
        allocated = float(payload.get("allocated_gb") or 0.0)
        used = float(payload.get("gb_used") or 0.0)
        return max(allocated - used, 0.0)
    except (TypeError, ValueError) as error:
        raise VProxyError("Сервис временно недоступен. Попробуйте позже.") from error


async def get_maskify_subuser_remaining_gb() -> float:
    if not MASKIFY_SUBUSER_USERNAME:
        raise VProxyError("Сервис временно недоступен. Попробуйте позже.")
    payload = await _maskify_reseller_request("GET", f"/subusers/{MASKIFY_SUBUSER_USERNAME}")
    try:
        if payload.get("gb_remaining") is not None:
            return max(float(payload["gb_remaining"]), 0.0)
        return max(float(payload.get("allocated_gb") or 0.0) - float(payload.get("gb_used") or 0.0), 0.0)
    except (TypeError, ValueError) as error:
        raise VProxyError("Сервис временно недоступен. Попробуйте позже.") from error


def _get_maskify_personal_subuser(telegram_user_id: int) -> dict[str, Any] | None:
    init_vproxy_db()
    with _vproxy_conn() as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT * FROM maskify_personal_subusers WHERE telegram_user_id=?",
            (int(telegram_user_id),),
        ).fetchone()
    return dict(row) if row else None


def _save_maskify_personal_subuser(telegram_user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "").strip()
    email = str(payload.get("email") or f"proxy.{int(telegram_user_id)}@sous-market.com").strip()
    if not username or not password:
        raise VProxyError("Сервис временно недоступен. Попробуйте позже.")
    now = int(time.time())
    init_vproxy_db()
    with _vproxy_conn() as db:
        db.execute(
            """INSERT INTO maskify_personal_subusers(
                   telegram_user_id,username,password,email,created_at,updated_at
               ) VALUES(?,?,?,?,?,?)
               ON CONFLICT(telegram_user_id) DO UPDATE SET
                   username=excluded.username,password=excluded.password,
                   email=excluded.email,updated_at=excluded.updated_at""",
            (int(telegram_user_id), username, password, email, now, now),
        )
    return {"username": username, "password": password, "email": email}


async def ensure_maskify_personal_subuser(telegram_user_id: int, initial_gb: float | None = None) -> dict[str, Any]:
    existing = _get_maskify_personal_subuser(telegram_user_id)
    if existing:
        return existing
    amount = round(float(initial_gb if initial_gb is not None else get_maskify_user_traffic(telegram_user_id)), 2)
    if amount < 1:
        raise VProxyError("Сначала приобретите минимум 1 GB трафика.")
    payload = await _maskify_reseller_request(
        "POST",
        "/subusers",
        json_body={"email": f"proxy.{int(telegram_user_id)}@sous-market.com", "gb": amount},
    )
    return _save_maskify_personal_subuser(telegram_user_id, payload)


async def add_maskify_personal_user_traffic(telegram_user_id: int, gb: float) -> float:
    amount = round(float(gb), 2)
    if amount <= 0:
        raise VProxyError("Некорректное количество трафика.")
    existing = _get_maskify_personal_subuser(telegram_user_id)
    if existing is None:
        legacy_balance = max(0.0, float(get_maskify_user_traffic(telegram_user_id)))
        migrated_amount = round(legacy_balance + amount, 2)
        subuser = await ensure_maskify_personal_subuser(telegram_user_id, migrated_amount)
        remaining = migrated_amount
    else:
        subuser = existing
        payload = await _maskify_reseller_request(
            "PATCH", f"/subusers/{subuser['username']}", json_body={"add_gb": amount}
        )
        remaining = max(
            float(payload.get("gb_remaining"))
            if payload.get("gb_remaining") is not None
            else float(payload.get("allocated_gb") or 0.0) - float(payload.get("gb_used") or 0.0),
            0.0,
        )
    add_maskify_user_traffic(telegram_user_id, amount)
    return remaining


async def adjust_maskify_personal_user_traffic(telegram_user_id: int, gb_delta: float) -> float:
    """Adjust a personal proxy allocation; negative values safely remove GB."""
    delta = round(float(gb_delta), 2)
    if delta == 0:
        raise VProxyError("Количество трафика не должно быть равно нулю.")
    if delta > 0:
        return await add_maskify_personal_user_traffic(telegram_user_id, delta)

    amount = abs(delta)
    subuser = _get_maskify_personal_subuser(telegram_user_id)
    if subuser is None:
        raise VProxyError("У пользователя нет активного proxy-аккаунта.")
    purchased = get_maskify_user_traffic(telegram_user_id)
    info = await _get_maskify_subuser_info(str(subuser["username"]))
    remaining = float(info.get("gb_remaining") or 0.0)
    if amount > remaining + 1e-9 or amount > purchased + 1e-9:
        raise VProxyError(f"Недостаточно трафика для списания. Доступно: {min(remaining, purchased):g} GB.")

    payload = await _maskify_reseller_request(
        "PATCH", f"/subusers/{subuser['username']}", json_body={"remove_gb": amount}
    )
    try:
        with _vproxy_conn() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                """UPDATE maskify_user_traffic
                   SET purchased_gb=ROUND(purchased_gb-?,2),updated_at=?
                   WHERE telegram_user_id=? AND purchased_gb>=?""",
                (amount, int(time.time()), int(telegram_user_id), amount),
            ).rowcount
            if changed != 1:
                raise VProxyError("Не удалось обновить локальный баланс трафика.")
    except Exception:
        # Restore upstream allocation if the local ledger could not be updated.
        await _maskify_reseller_request(
            "PATCH", f"/subusers/{subuser['username']}", json_body={"add_gb": amount}
        )
        raise
    return max(
        float(payload.get("gb_remaining"))
        if payload.get("gb_remaining") is not None
        else float(payload.get("allocated_gb") or 0.0) - float(payload.get("gb_used") or 0.0),
        0.0,
    )


async def transfer_maskify_personal_user_traffic(source_user_id: int, target_user_id: int, gb: float) -> tuple[float, float]:
    """Move GB between personal users, restoring the source if crediting fails."""
    amount = round(float(gb), 2)
    if amount <= 0 or int(source_user_id) == int(target_user_id):
        raise VProxyError("Некорректные параметры переноса трафика.")
    source_remaining = await adjust_maskify_personal_user_traffic(source_user_id, -amount)
    try:
        target_remaining = await adjust_maskify_personal_user_traffic(target_user_id, amount)
    except Exception:
        await adjust_maskify_personal_user_traffic(source_user_id, amount)
        raise
    return source_remaining, target_remaining


async def get_maskify_user_remaining_gb(telegram_user_id: int) -> float:
    subuser = _get_maskify_personal_subuser(telegram_user_id)
    if subuser is None:
        return get_maskify_user_traffic(telegram_user_id)
    payload = await _maskify_reseller_request("GET", f"/subusers/{subuser['username']}")
    if payload.get("gb_remaining") is not None:
        return max(float(payload["gb_remaining"]), 0.0)
    return max(float(payload.get("allocated_gb") or 0.0) - float(payload.get("gb_used") or 0.0), 0.0)


def _normalize_api_client_id(client_id: str) -> str:
    value = str(client_id or "").strip()
    if not 3 <= len(value) <= 64 or not all(char.isalnum() or char in "_-" for char in value):
        raise VProxyError("client_id must contain 3-64 letters, digits, _ or -")
    return value


async def _get_maskify_subuser_info(username: str) -> dict[str, Any]:
    payload = await _maskify_reseller_request("GET", f"/subusers/{username}")
    try:
        allocated = max(float(payload.get("allocated_gb") or 0.0), 0.0)
        used = max(float(payload.get("gb_used") or 0.0), 0.0)
        remaining = max(
            float(payload["gb_remaining"])
            if payload.get("gb_remaining") is not None
            else allocated - used,
            0.0,
        )
    except (TypeError, ValueError) as error:
        raise VProxyError("Сервис временно недоступен. Попробуйте позже.") from error
    return {
        **payload,
        "allocated_gb": allocated,
        "gb_used": used,
        "gb_remaining": remaining,
    }


async def _adjust_maskify_subuser_traffic(username: str, action: str, gb: float) -> dict[str, Any]:
    amount = round(float(gb), 2)
    if action not in {"add_gb", "remove_gb"} or amount <= 0:
        raise VProxyError("Некорректное количество трафика.")
    payload = await _maskify_reseller_request(
        "PATCH", f"/subusers/{username}", json_body={action: amount}
    )
    return await _get_maskify_subuser_info(str(payload.get("username") or username))


def create_api_residential_client(api_user_id: int, client_id: str, label: str = "") -> dict[str, Any]:
    init_vproxy_db()
    normalized = _normalize_api_client_id(client_id)
    clean_label = str(label or normalized).strip()[:120] or normalized
    timestamp = int(time.time())
    with _vproxy_conn() as db:
        db.row_factory = sqlite3.Row
        db.execute(
            """INSERT INTO api_residential_clients(api_user_id,client_id,label,created_at,updated_at)
               VALUES(?,?,?,?,?) ON CONFLICT(api_user_id,client_id) DO UPDATE SET
               label=excluded.label,updated_at=excluded.updated_at""",
            (int(api_user_id), normalized, clean_label, timestamp, timestamp),
        )
        row = db.execute(
            "SELECT * FROM api_residential_clients WHERE api_user_id=? AND client_id=?",
            (int(api_user_id), normalized),
        ).fetchone()
    return dict(row)


def get_api_residential_client(api_user_id: int, client_id: str) -> dict[str, Any] | None:
    init_vproxy_db()
    normalized = _normalize_api_client_id(client_id)
    with _vproxy_conn() as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT * FROM api_residential_clients WHERE api_user_id=? AND client_id=?",
            (int(api_user_id), normalized),
        ).fetchone()
    return dict(row) if row else None


def list_api_residential_clients(api_user_id: int) -> list[dict[str, Any]]:
    init_vproxy_db()
    with _vproxy_conn() as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT * FROM api_residential_clients WHERE api_user_id=? ORDER BY created_at,client_id",
            (int(api_user_id),),
        ).fetchall()
    return [dict(row) for row in rows]


def credit_api_residential_pool(api_user_id: int, gb: float, purchase_id: int | None = None) -> float:
    """Credit traffic already reserved on the API account's upstream subuser."""
    init_vproxy_db()
    amount = round(float(gb), 2)
    if amount <= 0:
        raise VProxyError("Некорректное количество трафика.")
    timestamp = int(time.time())
    with _vproxy_conn() as db:
        db.execute("BEGIN IMMEDIATE")
        if purchase_id is not None:
            inserted = db.execute(
                "INSERT OR IGNORE INTO api_residential_pool_credits(api_user_id,purchase_id,gb,created_at) VALUES(?,?,?,?)",
                (int(api_user_id), int(purchase_id), amount, timestamp),
            ).rowcount
            if not inserted:
                row = db.execute(
                    "SELECT available_gb FROM api_residential_pools WHERE api_user_id=?",
                    (int(api_user_id),),
                ).fetchone()
                return max(float(row[0] or 0.0), 0.0) if row else 0.0
        db.execute(
            """INSERT INTO api_residential_pools(api_user_id,purchased_gb,available_gb,updated_at)
               VALUES(?,?,?,?) ON CONFLICT(api_user_id) DO UPDATE SET
               purchased_gb=ROUND(api_residential_pools.purchased_gb+excluded.purchased_gb,2),
               available_gb=ROUND(api_residential_pools.available_gb+excluded.available_gb,2),
               updated_at=excluded.updated_at""",
            (int(api_user_id), amount, amount, timestamp),
        )
        row = db.execute(
            "SELECT available_gb FROM api_residential_pools WHERE api_user_id=?", (int(api_user_id),)
        ).fetchone()
    return max(float(row[0] or 0.0), 0.0)


def reconcile_api_residential_pool(api_user_id: int) -> dict[str, float]:
    """Idempotently expose all completed API residential purchases in the split pool."""
    init_vproxy_db()
    with _vproxy_conn() as db:
        rows = db.execute(
            """SELECT id,gb FROM maskify_proxy_purchases
               WHERE telegram_user_id=? AND COALESCE(partner_service,'residential')='residential'
               AND status='completed' ORDER BY id""",
            (int(api_user_id),),
        ).fetchall()
    for purchase_id, gb in rows:
        credit_api_residential_pool(api_user_id, float(gb or 0.0), int(purchase_id))
    return get_api_residential_pool(api_user_id)


def _adjust_api_residential_pool(api_user_id: int, delta_gb: float) -> float:
    init_vproxy_db()
    delta = round(float(delta_gb), 2)
    with _vproxy_conn() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT available_gb FROM api_residential_pools WHERE api_user_id=?", (int(api_user_id),)
        ).fetchone()
        current = max(float(row[0] or 0.0), 0.0) if row else 0.0
        updated = round(current + delta, 2)
        if updated < -1e-9:
            raise VProxyError("Insufficient residential traffic pool")
        db.execute(
            """INSERT INTO api_residential_pools(api_user_id,purchased_gb,available_gb,updated_at)
               VALUES(?,0,?,?) ON CONFLICT(api_user_id) DO UPDATE SET
               available_gb=excluded.available_gb,updated_at=excluded.updated_at""",
            (int(api_user_id), max(updated, 0.0), int(time.time())),
        )
    return max(updated, 0.0)


def get_api_residential_pool(api_user_id: int) -> dict[str, float]:
    init_vproxy_db()
    with _vproxy_conn() as db:
        row = db.execute(
            "SELECT purchased_gb,available_gb FROM api_residential_pools WHERE api_user_id=?",
            (int(api_user_id),),
        ).fetchone()
    return {
        "purchased_gb": max(float(row[0] or 0.0), 0.0) if row else 0.0,
        "available_gb": max(float(row[1] or 0.0), 0.0) if row else 0.0,
    }


def get_api_residential_transfer(api_user_id: int, request_id: str) -> dict[str, Any] | None:
    init_vproxy_db()
    with _vproxy_conn() as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT * FROM api_residential_transfers WHERE api_user_id=? AND request_id=?",
            (int(api_user_id), str(request_id)),
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["result"] = json.loads(result.pop("result_json") or "{}")
    return result


def _save_api_residential_transfer(
    api_user_id: int, client_id: str, request_id: str, action: str, gb: float, result: dict[str, Any]
) -> None:
    init_vproxy_db()
    with _vproxy_conn() as db:
        db.execute(
            """INSERT INTO api_residential_transfers(
                   api_user_id,client_id,request_id,action,gb,result_json,created_at
               ) VALUES(?,?,?,?,?,?,?)""",
            (
                int(api_user_id), str(client_id), str(request_id), str(action), float(gb),
                json.dumps(result, ensure_ascii=False), int(time.time()),
            ),
        )


async def get_api_residential_client_live(api_user_id: int, client_id: str) -> dict[str, Any]:
    client = get_api_residential_client(api_user_id, client_id)
    if client is None:
        raise VProxyError("Residential client not found")
    result = {
        "client_id": str(client["client_id"]),
        "label": str(client.get("label") or ""),
        "status": "pending" if not client.get("upstream_username") else "active",
        "allocated_gb": 0.0,
        "used_gb": 0.0,
        "remaining_gb": 0.0,
        "created_at": int(client.get("created_at") or 0),
        "updated_at": int(client.get("updated_at") or 0),
    }
    if client.get("upstream_username"):
        info = await _get_maskify_subuser_info(str(client["upstream_username"]))
        result.update(
            allocated_gb=round(float(info["allocated_gb"]), 6),
            used_gb=round(float(info["gb_used"]), 6),
            remaining_gb=round(float(info["gb_remaining"]), 6),
        )
    return result


async def get_api_residential_traffic(api_user_id: int) -> dict[str, Any]:
    pool = reconcile_api_residential_pool(api_user_id)
    # Do not silently turn an upstream outage into a zero balance.  Callers
    # must receive a retryable service error, otherwise an allocate request is
    # incorrectly rejected as an insufficient local pool.
    master_remaining = await get_maskify_user_remaining_gb(api_user_id)
    clients = []
    for client in list_api_residential_clients(api_user_id):
        try:
            clients.append(await get_api_residential_client_live(api_user_id, str(client["client_id"])))
        except VProxyError:
            clients.append({
                "client_id": str(client["client_id"]), "label": str(client.get("label") or ""),
                "status": "upstream_unavailable",
            })
    effective_available = min(float(pool["available_gb"]), max(float(master_remaining), 0.0))
    return {
        "pool": {
            **pool,
            "upstream_remaining_gb": round(max(float(master_remaining), 0.0), 6),
            "allocatable_gb": round(max(effective_available, 0.0), 6),
        },
        "clients": clients,
    }


async def transfer_api_residential_traffic(
    api_user_id: int, client_id: str, action: str, gb: float, request_id: str
) -> dict[str, Any]:
    normalized = _normalize_api_client_id(client_id)
    amount = round(float(gb), 2)
    if action not in {"allocate", "reclaim"} or amount < 0.01:
        raise VProxyError("Invalid traffic transfer")
    existing_transfer = get_api_residential_transfer(api_user_id, request_id)
    if existing_transfer:
        if (
            str(existing_transfer["client_id"]) != normalized
            or str(existing_transfer["action"]) != action
            or abs(float(existing_transfer["gb"]) - amount) > 1e-9
        ):
            raise VProxyError("request_id is already used for another operation")
        return {**existing_transfer["result"], "idempotent_replay": True}

    client = get_api_residential_client(api_user_id, normalized)
    if client is None:
        raise VProxyError("Residential client not found")
    master = await ensure_maskify_personal_subuser(api_user_id)
    master_username = str(master["username"])

    if action == "allocate":
        traffic = await get_api_residential_traffic(api_user_id)
        if amount > float(traffic["pool"]["allocatable_gb"]) + 1e-9:
            raise VProxyError("Insufficient residential traffic pool")
        # First release the reserved traffic from the API owner's master
        # subuser, then reserve the same amount on the isolated client.
        await _adjust_maskify_subuser_traffic(master_username, "remove_gb", amount)
        try:
            if client.get("upstream_username"):
                await _adjust_maskify_subuser_traffic(str(client["upstream_username"]), "add_gb", amount)
            else:
                payload = await _maskify_reseller_request(
                    "POST",
                    "/subusers",
                    json_body={
                        "email": f"api.{int(api_user_id)}.{hashlib.sha256(normalized.encode()).hexdigest()[:16]}@sous-market.com",
                        "gb": amount,
                    },
                )
                username = str(payload.get("username") or "").strip()
                password = str(payload.get("password") or "").strip()
                if not username or not password:
                    raise VProxyError("Proxy provider did not return client credentials")
                with _vproxy_conn() as db:
                    db.execute(
                        """UPDATE api_residential_clients SET upstream_username=?,upstream_password=?,
                           updated_at=? WHERE api_user_id=? AND client_id=?""",
                        (username, password, int(time.time()), int(api_user_id), normalized),
                    )
        except Exception:
            await _adjust_maskify_subuser_traffic(master_username, "add_gb", amount)
            raise
        _adjust_api_residential_pool(api_user_id, -amount)
    else:
        if not client.get("upstream_username"):
            raise VProxyError("Client has no allocated traffic")
        info = await _get_maskify_subuser_info(str(client["upstream_username"]))
        if amount > float(info["gb_remaining"]) + 1e-9:
            raise VProxyError("Cannot reclaim used or unavailable traffic")
        await _adjust_maskify_subuser_traffic(str(client["upstream_username"]), "remove_gb", amount)
        try:
            await _adjust_maskify_subuser_traffic(master_username, "add_gb", amount)
        except Exception:
            await _adjust_maskify_subuser_traffic(str(client["upstream_username"]), "add_gb", amount)
            raise
        _adjust_api_residential_pool(api_user_id, amount)

    live_client = await get_api_residential_client_live(api_user_id, normalized)
    traffic = await get_api_residential_traffic(api_user_id)
    result = {
        "action": action,
        "gb": amount,
        "client": live_client,
        "pool": traffic["pool"],
        "idempotent_replay": False,
    }
    _save_api_residential_transfer(api_user_id, normalized, request_id, action, amount, result)
    return result


async def generate_api_residential_client_proxies(
    api_user_id: int, client_id: str, settings: dict[str, Any]
) -> str:
    client = get_api_residential_client(api_user_id, client_id)
    if not client or not client.get("upstream_username") or not client.get("upstream_password"):
        raise VProxyError("Client has no allocated traffic")
    return await _generate_maskify_proxies_with_credentials(
        str(client["upstream_username"]), str(client["upstream_password"]), settings
    )


async def rotate_api_residential_client_password(
    api_user_id: int, client_id: str, request_id: str
) -> dict[str, Any]:
    """Issue the residential client a brand-new upstream login and password.

    There is no provider call that resets a subuser's password in place, so a
    rotation reuses the tested traffic-transfer paths: reclaim every remaining
    GB back into the API pool, drop the spent upstream subuser, then re-allocate
    the same amount, which provisions a fresh subuser (new username + password).
    The API pool balance is unchanged; only the credentials change, so every
    proxy line handed out before the rotation stops authenticating and the
    customer must pull a new list.
    """
    normalized = _normalize_api_client_id(client_id)
    base_request_id = str(request_id or "").strip()
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")
    if not (8 <= len(base_request_id) <= 78 and set(base_request_id) <= allowed):
        raise VProxyError("request_id must contain 8-78 letters, digits, _ or -")
    client = get_api_residential_client(api_user_id, normalized)
    if client is None:
        raise VProxyError("Residential client not found")
    if not client.get("upstream_username"):
        raise VProxyError("Client has no allocated traffic")
    info = await _get_maskify_subuser_info(str(client["upstream_username"]))
    gb = round(float(info["gb_remaining"]), 2)
    if gb < 0.01:
        raise VProxyError("No reusable traffic left to rotate")
    await transfer_api_residential_traffic(
        api_user_id, normalized, "reclaim", gb, f"{base_request_id}r"
    )
    with _vproxy_conn() as db:
        db.execute(
            "UPDATE api_residential_clients SET upstream_username=NULL,upstream_password=NULL,"
            "updated_at=? WHERE api_user_id=? AND client_id=?",
            (int(time.time()), int(api_user_id), normalized),
        )
    # The reclaim above credited the pool; the traffic is not lost even if the
    # re-allocation needs a couple of tries while the upstream balance settles.
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            await transfer_api_residential_traffic(
                api_user_id, normalized, "allocate", gb, f"{base_request_id}a"
            )
            last_error = None
            break
        except VProxyError as error:
            last_error = error
            await asyncio.sleep(0.7 * (attempt + 1))
    if last_error is not None:
        raise VProxyError(
            "Пароль сброшен, но новый трафик не выдан — трафик сохранён в пуле, "
            "повторите операцию через минуту."
        )
    return await get_api_residential_client_live(api_user_id, normalized)


def get_api_webhook(api_user_id: int, include_secret: bool = False) -> dict[str, Any] | None:
    init_vproxy_db()
    with _vproxy_conn() as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM api_webhooks WHERE api_user_id=?", (int(api_user_id),)).fetchone()
    if not row:
        return None
    result = dict(row)
    if not include_secret:
        result.pop("secret", None)
    result["enabled"] = bool(result.get("enabled"))
    return result


async def validate_api_webhook_url(url: str) -> str:
    parsed = urlsplit(str(url or "").strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise VProxyError("Webhook URL must be a public HTTPS URL")
    if parsed.port not in {None, 443}:
        raise VProxyError("Webhook URL must use HTTPS port 443")
    try:
        addresses = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM),
        )
    except OSError as error:
        raise VProxyError("Webhook hostname cannot be resolved") from error
    if not addresses:
        raise VProxyError("Webhook hostname cannot be resolved")
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise VProxyError("Webhook URL must resolve only to public IP addresses")
    return parsed.geturl()


def save_api_webhook(api_user_id: int, url: str, secret: str, enabled: bool = True) -> dict[str, Any]:
    init_vproxy_db()
    timestamp = int(time.time())
    with _vproxy_conn() as db:
        db.execute(
            """INSERT INTO api_webhooks(api_user_id,url,secret,enabled,created_at,updated_at)
               VALUES(?,?,?,?,?,?) ON CONFLICT(api_user_id) DO UPDATE SET
               url=excluded.url,secret=excluded.secret,enabled=excluded.enabled,updated_at=excluded.updated_at""",
            (int(api_user_id), str(url), str(secret), int(bool(enabled)), timestamp, timestamp),
        )
    return get_api_webhook(api_user_id, include_secret=True) or {}


def delete_api_webhook(api_user_id: int) -> bool:
    init_vproxy_db()
    with _vproxy_conn() as db:
        return db.execute("DELETE FROM api_webhooks WHERE api_user_id=?", (int(api_user_id),)).rowcount > 0


async def dispatch_api_webhook(api_user_id: int, event_type: str, data: dict[str, Any]) -> bool:
    webhook = get_api_webhook(api_user_id, include_secret=True)
    if not webhook or not webhook.get("enabled"):
        return False
    try:
        await validate_api_webhook_url(str(webhook["url"]))
    except VProxyError:
        return False
    event_id = f"evt_{secrets.token_hex(16)}"
    payload = {
        "id": event_id,
        "type": str(event_type),
        "created_at": int(time.time()),
        "data": data,
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    signature = hmac.new(str(webhook["secret"]).encode(), raw, hashlib.sha256).hexdigest()
    delivered = False
    status_code: int | None = None
    error_text = ""
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.post(
                str(webhook["url"]),
                data=raw,
                headers={
                    "Content-Type": "application/json",
                    "X-Sous-Event": str(event_type),
                    "X-Sous-Event-Id": event_id,
                    "X-Sous-Signature-256": f"sha256={signature}",
                },
                allow_redirects=False,
            ) as response:
                status_code = int(response.status)
                delivered = 200 <= response.status < 300
                if not delivered:
                    error_text = (await response.text())[:500]
    except (aiohttp.ClientError, asyncio.TimeoutError) as error:
        error_text = str(error)[:500]
    timestamp = int(time.time())
    with _vproxy_conn() as db:
        db.execute(
            """INSERT INTO api_webhook_deliveries(
                   api_user_id,event_id,event_type,status_code,delivered,error,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                int(api_user_id), event_id, str(event_type), status_code, int(delivered),
                error_text, timestamp, timestamp,
            ),
        )
    return delivered


async def _check_proxy_line(line: str, protocol: str, timeout: float = 8.0) -> bool:
    """Check proxy auth and CONNECT before exposing a proxy to a user."""
    value = str(line or '').strip()
    if not value:
        return False
    if '://' not in value:
        value = f"{protocol}://{value}"
    parsed = urlsplit(value)
    host = parsed.hostname
    port = parsed.port
    username = parsed.username or ''
    password = parsed.password or ''
    if not host or not port or not username or not password:
        return False
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        try:
            if protocol == 'socks5':
                writer.write(b'\x05\x01\x02')
                await writer.drain()
                greeting = await asyncio.wait_for(reader.readexactly(2), timeout=timeout)
                if greeting != b'\x05\x02':
                    return False
                user_bytes = username.encode()
                pass_bytes = password.encode()
                if len(user_bytes) > 255 or len(pass_bytes) > 255:
                    return False
                writer.write(b'\x01' + bytes([len(user_bytes)]) + user_bytes + bytes([len(pass_bytes)]) + pass_bytes)
                await writer.drain()
                auth = await asyncio.wait_for(reader.readexactly(2), timeout=timeout)
                if auth != b'\x01\x00':
                    return False
                target = b'www.instagram.com'
                writer.write(b'\x05\x01\x00\x03' + bytes([len(target)]) + target + (443).to_bytes(2, 'big'))
                await writer.drain()
                response = await asyncio.wait_for(reader.readexactly(2), timeout=timeout)
                return response == b'\x05\x00'
            auth = base64.b64encode(f'{username}:{password}'.encode()).decode()
            request = (
                'CONNECT www.instagram.com:443 HTTP/1.1\r\n'
                'Host: www.instagram.com:443\r\n'
                f'Proxy-Authorization: Basic {auth}\r\n\r\n'
            ).encode()
            writer.write(request)
            await writer.drain()
            response = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), timeout=timeout)
            return response.startswith(b'HTTP/') and b' 200 ' in response.split(b'\r\n', 1)[0]
        finally:
            writer.close()
            await writer.wait_closed()
    except (OSError, asyncio.TimeoutError, ValueError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        return False


async def _filter_working_proxy_lines(lines: list[str], protocol: str, required: int) -> list[str]:
    sem = asyncio.Semaphore(20)
    async def check(line: str) -> tuple[str, bool]:
        async with sem:
            return line, await _check_proxy_line(line, protocol)
    results = await asyncio.gather(*(check(line) for line in lines))
    return [line for line, ok in results if ok][:required]


async def generate_maskify_proxies(telegram_user_id: int, settings: dict[str, Any]) -> str:
    subuser = await ensure_maskify_personal_subuser(telegram_user_id)
    maskify_username = str(subuser.get("username") or "")
    maskify_password = str(subuser.get("password") or "")
    return await _generate_maskify_proxies_with_credentials(maskify_username, maskify_password, settings)


async def _generate_maskify_proxies_with_credentials(
    maskify_username: str, maskify_password: str, settings: dict[str, Any]
) -> str:
    if not maskify_username or not maskify_password or not MASKIFY_PROXY_HOST:
        raise VProxyError("Сервис временно недоступен. Попробуйте позже.")
    quantity = max(1, min(int(settings.get("quantity") or 1), 10000))
    ttl = max(0, int(settings.get("sessionttl") or 0))
    country = str(settings.get("country") or "").strip().upper()
    # Residential Maskify endpoints speak HTTP CONNECT.  The target URL may
    # be HTTPS, but the proxy URL itself must remain http://; SOCKS5 here is
    # not supported by the upstream gateway.
    protocol = "http"
    output_format = str(settings.get("format") or "login:password@hostname:port")
    # Always validate the canonical URL form. Display formats such as
    # host:port:user:password are not URLs and urlsplit would otherwise treat
    # the host as a scheme, marking every healthy proxy as unavailable.
    canonical_lines: list[str] = []
    exported_by_canonical: dict[str, str] = {}
    # Each exported line must carry its own session selector.  Without it,
    # every generated row has identical credentials and routes to one gateway
    # session (exactly the duplicate output reported by users).
    #
    # A zero TTL is the provider's "rotate per request" mode; it still needs a
    # unique session id per exported proxy so the rows are independently usable.
    attempts = min(max(quantity * 3, quantity), 30000)
    use_sticky_sessions = settings.get("type") == "sticky"
    session_ttl_suffix = f"-ttl-{ttl}" if use_sticky_sessions else ""
    for index in range(attempts):
        login = maskify_username
        if country:
            # The supplier accepts ISO-3166 alpha-2 country codes here:
            # UA is Ukraine; UK is the United Kingdom.
            login += f"-cc-{country}"
        session_id = f"{secrets.token_hex(8)}{index + 1:x}"[:24]
        login += f"-s-{session_id}{session_ttl_suffix}"
        canonical = f"{protocol}://{login}:{maskify_password}@{MASKIFY_PROXY_HOST}:{MASKIFY_PROXY_PORT}"
        if output_format == "hostname:port:login:password":
            line = f"{MASKIFY_PROXY_HOST}:{MASKIFY_PROXY_PORT}:{login}:{maskify_password}"
        elif output_format == "hostname:port@login:password":
            line = f"{MASKIFY_PROXY_HOST}:{MASKIFY_PROXY_PORT}@{login}:{maskify_password}"
        elif output_format == "protocol://login:password@hostname:port":
            line = canonical
        else:
            line = f"{login}:{maskify_password}@{MASKIFY_PROXY_HOST}:{MASKIFY_PROXY_PORT}"
        canonical_lines.append(canonical)
        exported_by_canonical[canonical] = line
        if len(canonical_lines) >= attempts:
            break
    # Maskify has already issued valid credentials. A mandatory CONNECT probe
    # made delivery fail whenever the gateway was warming up or the probe target
    # was temporarily unavailable. Keep strict validation opt-in for diagnostics
    # and return the generated credentials immediately in the normal flow.
    selected = canonical_lines[:quantity]
    if MASKIFY_VALIDATE_BEFORE_DELIVERY:
        selected = await _filter_working_proxy_lines(canonical_lines, protocol, quantity)
        if len(selected) < quantity:
            raise VProxyError(
                f"Проверка прокси не пройдена: доступно {len(selected)} из {quantity}. Попробуйте ещё раз."
            )
    return "\n".join(exported_by_canonical[line] for line in selected)


def init_vproxy_db() -> None:
    with _vproxy_conn() as db:
        db.execute(
            """CREATE TABLE IF NOT EXISTS vproxy_subusers (
                telegram_user_id INTEGER NOT NULL,
                pool TEXT NOT NULL,
                subuser_id INTEGER NOT NULL,
                settings_json TEXT NOT NULL DEFAULT '{}',
                created_at INTEGER NOT NULL,
                PRIMARY KEY (telegram_user_id, pool)
            )"""
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS vproxy_pending_topups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_user_id INTEGER NOT NULL,
                pool TEXT NOT NULL,
                gb REAL NOT NULL,
                price_usd REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )"""
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS maskify_proxy_purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_user_id INTEGER NOT NULL,
                gb REAL NOT NULL,
                price_usd REAL NOT NULL,
                invoice_id TEXT,
                client_invoice_id TEXT,
                payment_provider TEXT,
                pay_url TEXT,
                status TEXT NOT NULL DEFAULT 'draft',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )"""
        )
        purchase_columns = {row[1] for row in db.execute("PRAGMA table_info(maskify_proxy_purchases)")}
        if "client_invoice_id" not in purchase_columns:
            db.execute("ALTER TABLE maskify_proxy_purchases ADD COLUMN client_invoice_id TEXT")
        if "payment_provider" not in purchase_columns:
            db.execute("ALTER TABLE maskify_proxy_purchases ADD COLUMN payment_provider TEXT")
        for column, definition in (
            ("partner_service", "TEXT"),
            ("partner_quantity", "INTEGER"),
            ("provider_order_id", "INTEGER"),
            ("delivery_text", "TEXT"),
            ("api_request_key", "TEXT"),
            ("partner_bot_id", "INTEGER"),
            ("stuck_alert_sent", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if column not in purchase_columns:
                db.execute(f"ALTER TABLE maskify_proxy_purchases ADD COLUMN {column} {definition}")
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_maskify_proxy_api_request "
            "ON maskify_proxy_purchases(telegram_user_id,api_request_key) "
            "WHERE api_request_key IS NOT NULL"
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS maskify_user_traffic (
                telegram_user_id INTEGER PRIMARY KEY,
                purchased_gb REAL NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL
            )"""
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS maskify_personal_subusers (
                telegram_user_id INTEGER PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password TEXT NOT NULL,
                email TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )"""
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS api_residential_pools (
                api_user_id INTEGER PRIMARY KEY,
                purchased_gb REAL NOT NULL DEFAULT 0,
                available_gb REAL NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL
            )"""
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS api_residential_pool_credits (
                api_user_id INTEGER NOT NULL,
                purchase_id INTEGER NOT NULL,
                gb REAL NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (api_user_id,purchase_id)
            )"""
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS api_residential_clients (
                api_user_id INTEGER NOT NULL,
                client_id TEXT NOT NULL,
                label TEXT NOT NULL DEFAULT '',
                upstream_username TEXT,
                upstream_password TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (api_user_id,client_id),
                UNIQUE (upstream_username)
            )"""
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS api_residential_transfers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                api_user_id INTEGER NOT NULL,
                client_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                action TEXT NOT NULL,
                gb REAL NOT NULL,
                result_json TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                UNIQUE (api_user_id,request_id)
            )"""
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS api_webhooks (
                api_user_id INTEGER PRIMARY KEY,
                url TEXT NOT NULL,
                secret TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )"""
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS api_webhook_deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                api_user_id INTEGER NOT NULL,
                event_id TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL,
                status_code INTEGER,
                delivered INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )"""
        )


def get_maskify_user_traffic(telegram_user_id: int) -> float:
    init_vproxy_db()
    with _vproxy_conn() as db:
        row = db.execute(
            "SELECT purchased_gb FROM maskify_user_traffic WHERE telegram_user_id=?",
            (int(telegram_user_id),),
        ).fetchone()
    return max(float(row[0] or 0.0), 0.0) if row else 0.0


def add_maskify_user_traffic(telegram_user_id: int, gb: float) -> float:
    init_vproxy_db()
    amount = round(float(gb), 2)
    if amount <= 0:
        raise VProxyError("Некорректное количество трафика.")
    with _vproxy_conn() as db:
        db.execute(
            """INSERT INTO maskify_user_traffic(telegram_user_id,purchased_gb,updated_at)
               VALUES(?,?,?)
               ON CONFLICT(telegram_user_id) DO UPDATE SET
                   purchased_gb=ROUND(maskify_user_traffic.purchased_gb + excluded.purchased_gb, 2),
                   updated_at=excluded.updated_at""",
            (int(telegram_user_id), amount, int(time.time())),
        )
        row = db.execute(
            "SELECT purchased_gb FROM maskify_user_traffic WHERE telegram_user_id=?",
            (int(telegram_user_id),),
        ).fetchone()
    return max(float(row[0] or 0.0), 0.0)


def get_total_maskify_user_traffic() -> float:
    init_vproxy_db()
    with _vproxy_conn() as db:
        row = db.execute("SELECT COALESCE(SUM(purchased_gb),0) FROM maskify_user_traffic").fetchone()
    return max(float(row[0] or 0.0), 0.0)


async def get_maskify_sellable_gb() -> float:
    """Traffic that the reseller can still allocate to personal subusers."""
    return await get_maskify_available_gb()


def create_maskify_proxy_purchase(
    telegram_user_id: int,
    gb: float,
    price_usd: float,
    api_request_key: str | None = None,
) -> int:
    init_vproxy_db()
    now = int(time.time())
    with _vproxy_conn() as db:
        cursor = db.execute(
            """INSERT INTO maskify_proxy_purchases(
                telegram_user_id,gb,price_usd,status,api_request_key,created_at,updated_at
            ) VALUES(?,?,?,'draft',?,?,?)""",
            (int(telegram_user_id), float(gb), float(price_usd), api_request_key, now, now),
        )
        return int(cursor.lastrowid)


def create_partner_proxy_purchase(
    telegram_user_id: int,
    service: str,
    quantity: int,
    price_usd: float,
    api_request_key: str | None = None,
) -> int:
    init_vproxy_db()
    now = int(time.time())
    with _vproxy_conn() as db:
        cursor = db.execute(
            """INSERT INTO maskify_proxy_purchases(
                telegram_user_id,gb,price_usd,status,partner_service,partner_quantity,
                api_request_key,created_at,updated_at
            ) VALUES(?,?,?,'draft',?,?,?,?,?)""",
            (
                int(telegram_user_id), 0.0, float(price_usd), str(service), int(quantity),
                api_request_key, now, now,
            ),
        )
        return int(cursor.lastrowid)


def set_partner_proxy_delivery(purchase_id: int, provider_order_id: int, delivery_text: str) -> None:
    init_vproxy_db()
    with _vproxy_conn() as db:
        db.execute(
            """UPDATE maskify_proxy_purchases
               SET provider_order_id=?,delivery_text=?,status='completed',updated_at=? WHERE id=?""",
            (int(provider_order_id), str(delivery_text), int(time.time()), int(purchase_id)),
        )


def set_maskify_residential_delivery(purchase_id: int, delivery_text: str) -> None:
    init_vproxy_db()
    with _vproxy_conn() as db:
        db.execute(
            """UPDATE maskify_proxy_purchases
               SET delivery_text=?,status='completed',updated_at=? WHERE id=?""",
            (str(delivery_text), int(time.time()), int(purchase_id)),
        )


def mark_partner_proxy_delivery_pending(
    purchase_id: int, provider_order_id: int, partner_bot_id: int | None = None
) -> None:
    init_vproxy_db()
    with _vproxy_conn() as db:
        db.execute(
            """UPDATE maskify_proxy_purchases
               SET provider_order_id=?,status='delivery_pending',partner_bot_id=?,updated_at=? WHERE id=?""",
            (
                int(provider_order_id),
                (int(partner_bot_id) if partner_bot_id else None),
                int(time.time()),
                int(purchase_id),
            ),
        )


def list_pending_partner_proxy_purchases(limit: int = 50) -> list[dict[str, Any]]:
    """Partner-proxy (cdkey) purchases paid but not yet delivered by the provider.
    Excludes public-API orders (the API client polls for status itself)."""
    init_vproxy_db()
    with _vproxy_conn() as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            """SELECT * FROM maskify_proxy_purchases
               WHERE status='delivery_pending'
                 AND partner_service IS NOT NULL
                 AND provider_order_id IS NOT NULL AND provider_order_id > 0
                 AND api_request_key IS NULL
               ORDER BY id ASC LIMIT ?""",
            (int(limit),),
        ).fetchall()
    return [dict(row) for row in rows]


def mark_partner_proxy_stuck_alerted(purchase_id: int) -> None:
    init_vproxy_db()
    with _vproxy_conn() as db:
        db.execute(
            "UPDATE maskify_proxy_purchases SET stuck_alert_sent=1 WHERE id=?",
            (int(purchase_id),),
        )


def list_user_partner_proxy_purchases(telegram_user_id: int, limit: int = 30) -> list[dict[str, Any]]:
    """Delivered / awaiting-delivery proxy (cdkey) purchases for a user — used to
    show them in the shop mini-app operation history."""
    init_vproxy_db()
    with _vproxy_conn() as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            """SELECT * FROM maskify_proxy_purchases
               WHERE telegram_user_id=? AND partner_service IS NOT NULL
                 AND status IN ('completed','delivery_pending')
               ORDER BY id DESC LIMIT ?""",
            (int(telegram_user_id), int(limit)),
        ).fetchall()
    return [dict(row) for row in rows]


def set_maskify_proxy_purchase_invoice(
    purchase_id: int,
    invoice_id: str,
    pay_url: str,
    payment_provider: str,
    client_invoice_id: str | None = None,
) -> None:
    init_vproxy_db()
    with _vproxy_conn() as db:
        db.execute(
            """UPDATE maskify_proxy_purchases
               SET invoice_id=?,client_invoice_id=?,payment_provider=?,pay_url=?,status='waiting_payment',updated_at=?
               WHERE id=? AND status='draft'""",
            (
                str(invoice_id),
                str(client_invoice_id or invoice_id),
                str(payment_provider),
                str(pay_url),
                int(time.time()),
                int(purchase_id),
            ),
        )


def get_maskify_proxy_purchase(purchase_id: int) -> dict[str, Any] | None:
    init_vproxy_db()
    with _vproxy_conn() as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM maskify_proxy_purchases WHERE id=?", (int(purchase_id),)).fetchone()
    return dict(row) if row else None


def get_maskify_proxy_purchase_by_request(telegram_user_id: int, request_key: str) -> dict[str, Any] | None:
    init_vproxy_db()
    with _vproxy_conn() as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT * FROM maskify_proxy_purchases WHERE telegram_user_id=? AND api_request_key=?",
            (int(telegram_user_id), str(request_key)),
        ).fetchone()
    return dict(row) if row else None


def claim_maskify_proxy_purchase(purchase_id: int, telegram_user_id: int) -> bool:
    init_vproxy_db()
    with _vproxy_conn() as db:
        cursor = db.execute(
            """UPDATE maskify_proxy_purchases SET status='processing',updated_at=?
               WHERE id=? AND telegram_user_id=? AND status='waiting_payment'""",
            (int(time.time()), int(purchase_id), int(telegram_user_id)),
        )
        return cursor.rowcount == 1


def finish_maskify_proxy_purchase(purchase_id: int, status: str) -> None:
    init_vproxy_db()
    with _vproxy_conn() as db:
        db.execute(
            "UPDATE maskify_proxy_purchases SET status=?,updated_at=? WHERE id=?",
            (str(status), int(time.time()), int(purchase_id)),
        )


def default_proxy_settings() -> dict[str, Any]:
    return {
        "country": "",
        "country_name": "Любая",
        "type": "rotating",
        "sessionttl": 1800,
        "protocol": "http",
        "format": "login:password@hostname:port",
        "quantity": 5,
    }


def get_proxy_settings(telegram_user_id: int, pool: str) -> dict[str, Any]:
    init_vproxy_db()
    with _vproxy_conn() as db:
        row = db.execute(
            "SELECT settings_json FROM vproxy_subusers WHERE telegram_user_id=? AND pool=?",
            (telegram_user_id, pool),
        ).fetchone()
    settings = default_proxy_settings()
    if row:
        try:
            stored = json.loads(row[0] or "{}")
            if isinstance(stored, dict):
                settings.update(stored)
        except (json.JSONDecodeError, TypeError):
            pass
    # A session ID makes a residential proxy sticky. It must never be added
    # for the rotating mode: doing so turns "per request" rotation into a
    # fixed IP, while some gateways fall back to a one-minute interval.
    settings["type"] = settings.get("type") if settings.get("type") in {"rotating", "sticky"} else "rotating"
    try:
        settings["sessionttl"] = max(0, min(int(settings.get("sessionttl", 1800)), 86400))
    except (TypeError, ValueError):
        settings["sessionttl"] = 1800
    if settings["type"] == "rotating":
        settings["sessionttl"] = 0
    elif settings["sessionttl"] <= 0:
        settings["sessionttl"] = 1800
    settings["protocol"] = settings.get("protocol") if settings.get("protocol") in {"http", "socks5"} else "http"
    # Maskify residential gateways expose HTTP CONNECT only.  Treating them as
    # SOCKS5 produces provider-side 400/405 errors, so never persist or export
    # an unsupported protocol for this pool.
    if pool == "residential":
        settings["protocol"] = "http"
    settings["format"] = settings.get("format") if settings.get("format") in {
        "login:password@hostname:port", "hostname:port:login:password",
        "hostname:port@login:password", "protocol://login:password@hostname:port",
    } else "login:password@hostname:port"
    if pool == "residential" and settings["format"] == "login:password@hostname:port":
        settings["format"] = "protocol://login:password@hostname:port"
    try:
        settings["quantity"] = max(1, min(int(settings.get("quantity", 5)), 10000))
    except (TypeError, ValueError):
        settings["quantity"] = 5
    settings["country"] = str(settings.get("country") or "").strip().upper()
    # ISO uses UA for Ukraine. Old UI/provider payloads could store UK, which
    # means the United Kingdom and therefore selected the wrong geo.
    if settings["country"] == "UK" and "укр" in str(settings.get("country_name") or "").lower():
        settings["country"] = "UA"
    settings["country_name"] = str(settings.get("country_name") or ("Любая" if not settings["country"] else settings["country"]))
    return settings


def save_proxy_settings(telegram_user_id: int, pool: str, settings: dict[str, Any]) -> None:
    init_vproxy_db()
    payload = json.dumps(settings, ensure_ascii=False)
    with _vproxy_conn() as db:
        row = db.execute(
            "SELECT subuser_id FROM vproxy_subusers WHERE telegram_user_id=? AND pool=?",
            (telegram_user_id, pool),
        ).fetchone()
        if row:
            db.execute(
                "UPDATE vproxy_subusers SET settings_json=? WHERE telegram_user_id=? AND pool=?",
                (payload, telegram_user_id, pool),
            )
        else:
            db.execute(
                "INSERT INTO vproxy_subusers(telegram_user_id,pool,subuser_id,settings_json,created_at) VALUES(?,?,0,?,?)",
                (telegram_user_id, pool, payload, int(time.time())),
            )


async def _reseller_request(
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
    params: dict | None = None,
    accept: str = "application/json",
) -> Any:
    if not VPROXY_API_KEY:
        raise VProxyError("Сервис временно недоступен. Попробуйте позже.")
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.request(
            method,
            f"{VPROXY_RESELLER_API_BASE}{path}",
            headers={"apikey": VPROXY_API_KEY, "Accept": accept},
            json=json_body,
            params=params,
        ) as response:
            raw = await response.text()
            if response.status >= 400:
                try:
                    data = json.loads(raw)
                    message = data.get("message") or raw
                except (json.JSONDecodeError, AttributeError):
                    message = raw
                error_code = str(data.get("code") or "") if isinstance(data, dict) else ""
                if response.status == 402 or error_code == "PT402" or "insufficient" in str(message).lower():
                    raise VProxyError("Сервис временно недоступен. Попробуйте позже.")
                raise VProxyError("Сервис временно недоступен. Попробуйте позже.")
            if accept == "text/plain":
                return raw
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return raw


def _extract_subuser_id(payload: Any) -> int:
    if isinstance(payload, (int, float)):
        return int(payload)
    if isinstance(payload, str) and payload.strip().isdigit():
        return int(payload.strip())
    if isinstance(payload, dict):
        for key in ("subuser_id", "id", "user_id", "data"):
            value = payload.get(key)
            if isinstance(value, dict):
                try:
                    return _extract_subuser_id(value)
                except VProxyError:
                    continue
            if str(value or "").isdigit():
                return int(value)
    raise VProxyError("Сервис временно недоступен. Попробуйте позже.")


async def ensure_vproxy_subuser(telegram_user_id: int, pool: str) -> int:
    init_vproxy_db()
    with _vproxy_conn() as db:
        row = db.execute(
            "SELECT subuser_id FROM vproxy_subusers WHERE telegram_user_id=? AND pool=?",
            (telegram_user_id, pool),
        ).fetchone()
    if row and int(row[0] or 0) > 0:
        return int(row[0])
    settings = get_proxy_settings(telegram_user_id, pool)
    payload = await _reseller_request(
        "POST",
        "/subuser/create",
        json_body={
            "pool_type": pool,
            "sticky_range": None,
            "threads": max(1, int(settings.get("quantity") or 5)),
            "allowed_ips": [],
            "default_pool_parameters": None,
        },
    )
    subuser_id = _extract_subuser_id(payload)
    with _vproxy_conn() as db:
        db.execute(
            """INSERT INTO vproxy_subusers(telegram_user_id,pool,subuser_id,settings_json,created_at)
               VALUES(?,?,?,?,?) ON CONFLICT(telegram_user_id,pool) DO UPDATE SET subuser_id=excluded.subuser_id""",
            (telegram_user_id, pool, subuser_id, json.dumps(settings, ensure_ascii=False), int(time.time())),
        )
    return subuser_id


async def add_subuser_traffic(telegram_user_id: int, pool: str, gb: float) -> float:
    subuser_id = await ensure_vproxy_subuser(telegram_user_id, pool)
    await _reseller_request(
        "POST", "/subuser/balance/add", json_body={"subuser_id": subuser_id, "gb": float(gb)}
    )
    return await get_subuser_balance(telegram_user_id, pool)


def queue_traffic_topup(telegram_user_id: int, pool: str, gb: float, price_usd: float) -> int:
    init_vproxy_db()
    now = int(time.time())
    with _vproxy_conn() as db:
        cursor = db.execute(
            """INSERT INTO vproxy_pending_topups(
                telegram_user_id,pool,gb,price_usd,status,attempts,created_at,updated_at
            ) VALUES(?,?,?,?, 'pending',0,?,?)""",
            (telegram_user_id, pool, float(gb), float(price_usd), now, now),
        )
        return int(cursor.lastrowid)


def get_pending_traffic(telegram_user_id: int, pool: str) -> float:
    init_vproxy_db()
    with _vproxy_conn() as db:
        row = db.execute(
            """SELECT COALESCE(SUM(gb),0) FROM vproxy_pending_topups
               WHERE telegram_user_id=? AND pool=? AND status IN ('pending','processing')""",
            (telegram_user_id, pool),
        ).fetchone()
    return max(float(row[0] or 0.0), 0.0)


def list_pending_traffic_topups(limit: int = 50) -> list[dict[str, Any]]:
    init_vproxy_db()
    with _vproxy_conn() as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT * FROM vproxy_pending_topups WHERE status='pending' ORDER BY id LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


async def process_pending_traffic_topup(row: dict[str, Any]) -> bool:
    init_vproxy_db()
    row_id = int(row["id"])
    now = int(time.time())
    with _vproxy_conn() as db:
        claimed = db.execute(
            """UPDATE vproxy_pending_topups SET status='processing',attempts=attempts+1,updated_at=?
               WHERE id=? AND status='pending'""",
            (now, row_id),
        )
        if claimed.rowcount != 1:
            return False
    try:
        if MASKIFY_RESELLER_API_KEY and MASKIFY_SUBUSER_USERNAME and str(row["pool"]) == "residential":
            await add_maskify_subuser_traffic(float(row["gb"]))
        else:
            await add_subuser_traffic(int(row["telegram_user_id"]), str(row["pool"]), float(row["gb"]))
    except Exception:
        with _vproxy_conn() as db:
            db.execute(
                "UPDATE vproxy_pending_topups SET status='pending',updated_at=? WHERE id=?",
                (int(time.time()), row_id),
            )
        return False
    with _vproxy_conn() as db:
        db.execute(
            "UPDATE vproxy_pending_topups SET status='completed',updated_at=? WHERE id=?",
            (int(time.time()), row_id),
        )
    return True


async def get_subuser_balance(telegram_user_id: int, pool: str) -> float:
    subuser_id = await ensure_vproxy_subuser(telegram_user_id, pool)
    payload = await _reseller_request("GET", "/subuser/balance/get", params={"subuser_id": subuser_id})
    value = normalize_balance(payload)
    if value is None:
        raise VProxyError("Сервис временно недоступен. Попробуйте позже.")
    return value


async def get_existing_subuser_balance(telegram_user_id: int, pool: str) -> float:
    init_vproxy_db()
    with _vproxy_conn() as db:
        row = db.execute(
            "SELECT subuser_id FROM vproxy_subusers WHERE telegram_user_id=? AND pool=?",
            (telegram_user_id, pool),
        ).fetchone()
    if not row or int(row[0] or 0) <= 0:
        return 0.0
    payload = await _reseller_request("GET", "/subuser/balance/get", params={"subuser_id": int(row[0])})
    value = normalize_balance(payload)
    return max(float(value or 0.0), 0.0)


# Used when the provider endpoint is temporarily unavailable.  Country codes are
# ISO-3166-1 alpha-2 and are accepted by the residential login suffix.
_FALLBACK_COUNTRIES = [
    ("RU", "Россия"), ("US", "США"), ("GB", "Великобритания"),
    ("DE", "Германия"), ("NL", "Нидерланды"), ("FR", "Франция"),
    ("IT", "Италия"), ("ES", "Испания"), ("PL", "Польша"),
    ("KZ", "Казахстан"), ("UA", "Украина"), ("TR", "Турция"),
    ("CA", "Канада"), ("AU", "Австралия"), ("JP", "Япония"),
]


def _fallback_countries() -> list[dict[str, str]]:
    return [{"code": code, "name": name} for code, name in _FALLBACK_COUNTRIES]


async def get_vproxy_countries(pool: str) -> list[dict[str, str]]:
    """Return countries in one normalized shape, even if provider changes its response."""
    try:
        payload = await _reseller_request("GET", "/common/location", params={"pool": pool})
    except Exception:
        return _fallback_countries()

    rows: Any = payload
    if isinstance(payload, dict):
        for key in ("data", "countries", "locations", "items", "result"):
            if key in payload:
                rows = payload[key]
                break
    result: list[dict[str, str]] = []
    if isinstance(rows, dict):
        # Some API versions return {"RU": "Russia", "US": "United States"}.
        rows = [{"code": code, "name": name} for code, name in rows.items()]
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, str):
                code = row.strip().upper()
                name = code
            elif isinstance(row, dict):
                code = str(row.get("code") or row.get("country_code") or row.get("countryCode") or row.get("iso") or row.get("id") or "").strip().upper()
                name = str(row.get("name") or row.get("country_name") or row.get("countryName") or row.get("name_ru") or row.get("title") or code).strip()
            else:
                continue
            if code and len(code) <= 3:
                result.append({"code": code, "name": name or code})
    # Deduplicate and keep a usable menu if API returned an empty object.
    unique = {row["code"]: row for row in result}
    return list(unique.values()) or _fallback_countries()


async def generate_vproxy(telegram_user_id: int, pool: str) -> str:
    subuser_id = await ensure_vproxy_subuser(telegram_user_id, pool)
    settings = get_proxy_settings(telegram_user_id, pool)
    requested_format = str(settings.get("format") or "login:password@hostname:port")
    provider_format = (
        "login:password@hostname:port"
        if requested_format == "protocol://login:password@hostname:port"
        else requested_format
    )
    params: dict[str, Any] = {
        "pool": pool,
        "subuser_id": subuser_id,
        "type": settings["type"],
        "protocol": settings["protocol"],
        "format": provider_format,
        "quantity": int(settings["quantity"]),
        "sessionttl": int(settings["sessionttl"]),
        "anonymous": "false",
        "cities": "{}",
        "connection_host": "auto",
        "excluded_asns": "{}",
        "included_asns": "{}",
        "states": "{}",
        "zipcodes": "{}",
    }
    if settings.get("country"):
        params["countries"] = "{" + str(settings["country"]).lower() + "}"
    raw = await _reseller_request("GET", "/get-proxy", params=params, accept="text/plain")
    if not str(raw).strip():
        raise VProxyError("Прокси временно недоступны. Попробуйте позже.")
    # The reseller API does not consistently honour its `format` argument.
    # Normalize whatever it returned before exposing the requested format.
    return format_proxy_export(str(raw).strip(), requested_format, str(settings["protocol"]))


def _parse_proxy_line(line: str) -> tuple[str, int, str, str] | None:
    """Parse all proxy forms accepted by the UI/provider."""
    value = str(line or "").strip()
    if not value:
        return None

    if "://" in value or "@" in value:
        candidate = value if "://" in value else f"http://{value}"
        try:
            parsed = urlsplit(candidate)
            host = parsed.hostname or ""
            port = parsed.port
            login = unquote(parsed.username or "")
            password = unquote(parsed.password or "")
            if host and port and login:
                return host, int(port), login, password
        except (ValueError, TypeError):
            pass

    # host:port:login:password -- password may itself contain colons.
    parts = value.split(":", 3)
    if len(parts) == 4 and parts[1].isdigit():
        host, port, login, password = parts
        if host and login and 0 < int(port) <= 65535:
            return host, int(port), login, password
    return None


def format_proxy_export(raw: str, output_format: str, protocol: str) -> str:
    """Convert the provider's plain proxy list without exposing provider details."""
    formatted: list[str] = []
    for original in raw.splitlines():
        line = original.strip()
        if not line:
            continue
        parsed = _parse_proxy_line(line)
        if parsed is None:
            formatted.append(line)
            continue
        host, port, login, password = parsed
        if output_format == "hostname:port:login:password":
            formatted.append(f"{host}:{port}:{login}:{password}")
        elif output_format == "hostname:port@login:password":
            formatted.append(f"{host}:{port}@{login}:{password}")
        elif output_format == "protocol://login:password@hostname:port":
            formatted.append(f"{protocol}://{login}:{password}@{host}:{port}")
        else:
            formatted.append(f"{login}:{password}@{host}:{port}")
    return "\n".join(formatted)


async def reset_vproxy_password(telegram_user_id: int, pool: str) -> str:
    subuser_id = await ensure_vproxy_subuser(telegram_user_id, pool)
    payload = await _reseller_request(
        "POST", "/subuser/reset-password", json_body={"subuser_id": subuser_id}
    )
    if isinstance(payload, dict):
        password = payload.get("password") or payload.get("new_password")
        if password:
            return str(password)
    return "Пароль обновлён"


def _extract_token(payload: Any, raw: str = "") -> str:
    if isinstance(payload, str):
        return payload.strip().strip('"')
    if isinstance(payload, dict):
        for key in ("token", "access_token", "jwt", "data"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                nested = _extract_token(value)
                if nested:
                    return nested
    return raw.strip().strip('"') if raw and not raw.lstrip().startswith("{") else ""


async def _request_token(session: aiohttp.ClientSession, force: bool = False) -> str:
    if VPROXY_JWT:
        return VPROXY_JWT
    now = time.monotonic()
    if not force and _TOKEN_CACHE["value"] and float(_TOKEN_CACHE["expires_at"]) > now:
        return str(_TOKEN_CACHE["value"])
    if not VPROXY_USERNAME or not VPROXY_PASSWORD:
        raise VProxyError("Сервис временно недоступен. Попробуйте позже.")
    async with _TOKEN_LOCK:
        now = time.monotonic()
        if not force and _TOKEN_CACHE["value"] and float(_TOKEN_CACHE["expires_at"]) > now:
            return str(_TOKEN_CACHE["value"])
        async with session.post(
            f"{VPROXY_API_BASE}/token",
            json={"username": VPROXY_USERNAME, "password": VPROXY_PASSWORD},
            headers={"Accept": "text/plain"},
        ) as response:
            raw = await response.text()
            try:
                payload = await response.json(content_type=None)
            except Exception:
                payload = raw
            token = _extract_token(payload, raw)
            if response.status >= 400 or not token:
                raise VProxyError("Сервис временно недоступен. Попробуйте позже.")
            _TOKEN_CACHE.update(value=token, expires_at=time.monotonic() + 45 * 60)
            return token


async def _request(method: str, path: str, *, json_body: dict | None = None, params: dict | None = None) -> Any:
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        token = await _request_token(session)
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        if VPROXY_API_KEY:
            headers["X-API-Key"] = VPROXY_API_KEY
        async with session.request(
            method,
            f"{VPROXY_API_BASE}{path}",
            headers=headers,
            json=json_body,
            params=params,
        ) as response:
            raw = await response.text()
            try:
                payload = await response.json(content_type=None)
            except Exception:
                payload = raw
            if response.status >= 400:
                raise VProxyError("Сервис временно недоступен. Попробуйте позже.")
            return payload


def normalize_balance(payload: Any) -> float | None:
    if isinstance(payload, (int, float)):
        return float(payload)
    if isinstance(payload, str):
        try:
            return float(payload.strip())
        except ValueError:
            return None
    if isinstance(payload, dict):
        for key in ("balance", "traffic", "available", "amount", "gb", "data"):
            if key in payload:
                value = normalize_balance(payload[key])
                if value is not None:
                    return value
    return None


async def get_pool_balance(pool: str) -> float:
    if pool not in VPROXY_POOLS:
        raise ValueError("Unknown VProxy pool")
    if VPROXY_API_KEY:
        balances = await get_all_pool_balances()
        value = balances.get(pool)
        if value is None:
            raise VProxyError("Сервис временно недоступен. Попробуйте позже.")
        return value
    value = normalize_balance(await _request("POST", "/balance", json_body={"pool": pool}))
    if value is None:
        raise VProxyError("Сервис временно недоступен. Попробуйте позже.")
    return max(value, 0.0)


async def get_all_pool_balances() -> dict[str, float | None]:
    # A reseller token is not a Public API JWT. It exposes the reseller's
    # shared traffic balance, which is converted differently for every pool.
    if VPROXY_API_KEY:
        timeout = aiohttp.ClientTimeout(total=20)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    f"{VPROXY_RESELLER_API_BASE}/balance/get_full",
                    headers={"apikey": VPROXY_API_KEY, "Accept": "application/json"},
                ) as response:
                    payload = await response.json(content_type=None)
                    if response.status >= 400:
                        raise VProxyError("Сервис временно недоступен. Попробуйте позже.")
            reseller_gb = normalize_balance(payload)
            if reseller_gb is None:
                raise VProxyError("Сервис временно недоступен. Попробуйте позже.")
            coefficients = {
                "datacenter": 0.5,
                "residential": 1.0,
                "residential_premium": 4.0,
                "mobile": 2.0,
            }
            return {pool: reseller_gb / factor for pool, factor in coefficients.items()}
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError, VProxyError):
            return {pool: None for pool in VPROXY_POOLS}

    results = await asyncio.gather(
        *(get_pool_balance(pool) for pool in VPROXY_POOLS),
        return_exceptions=True,
    )
    return {
        pool: (None if isinstance(result, Exception) else float(result))
        for pool, result in zip(VPROXY_POOLS, results)
    }


