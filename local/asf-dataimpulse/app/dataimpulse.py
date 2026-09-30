"""DataImpulse reseller API client and proxy-line builder.

The shop resells DataImpulse traffic: every customer gets their own DataImpulse
sub-user per pool type (residential / residential_premium / mobile / datacenter),
traffic is added to that sub-user when the customer pays, and the proxy lines are
built locally from the sub-user's own login/password — the provider has no
"give me N proxies" endpoint, the gateway does the work.

Reference: https://documenter.getpostman.com/view/39259587/2sAYJ4hLAo
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from typing import Any, Iterable

import aiohttp

from .clients import ApiError


API_BASE = os.getenv("DI_API_BASE", "https://api.dataimpulse.com/reseller").rstrip("/")

# Public gateway. Every delivered line is repointed at our own relay below so the
# provider host never reaches the buyer.
GATEWAY_HOST = os.getenv("DI_GATEWAY_HOST", "gw.dataimpulse.com").strip()
GATEWAY_PORT = int(os.getenv("DI_GATEWAY_PORT", "823"))
RELAY_ENDPOINT = os.getenv("DI_RELAY_ENDPOINT", "31.77.145.160:18081").strip()

# A rotating line still carries its own session id (otherwise every row would be
# the same text — the owner rejected identical lists), so it rotates on this TTL
# instead of per request. Keep it short.
ROTATING_TTL_SECONDS = int(os.getenv("DI_ROTATING_TTL", "60"))
SESSION_TTL_CAP = 7200

POOL_TYPES: tuple[str, ...] = ("residential", "residential_premium", "mobile", "datacenter")

POOL_LABELS: dict[str, str] = {
    "residential": "🏠 Резидентские",
    "residential_premium": "💎 Резидентские Premium",
    "mobile": "📱 Мобильные",
    "datacenter": "🖥 Датацентр",
}

# No default price per GB on purpose. The buying price is read from the
# provider's top-up log (see DataImpulseClient.observed_rate) or entered by the
# admin; until one of those has happened a pool is shown as "цена уточняется"
# and cannot be bought, because a guessed cost means selling at a guessed price.


def pool_label(code: str) -> str:
    return POOL_LABELS.get(code, code)


def cost_setting_key(pool: str) -> str:
    return f"di_cost_{pool}"


class DataImpulseClient:
    """Thin async wrapper over the reseller API.

    Auth is a 24 h JWT obtained from ``/user/token/get`` with the dashboard
    login (an e-mail) and password; the token is cached in memory and refreshed
    on expiry or on a 401.
    """

    TOKEN_TTL = 20 * 3600

    def __init__(self, session: aiohttp.ClientSession, credentials_getter) -> None:
        self.session = session
        self.credentials_getter = credentials_getter
        self._token = ""
        self._token_at = 0.0
        self._token_key = ""
        self._lock = asyncio.Lock()

    async def _credentials(self) -> tuple[str, str]:
        login, password = await self.credentials_getter()
        login = (login or "").strip()
        password = (password or "").strip()
        if not login or not password:
            raise ApiError("Прокси-сервис не настроен")
        return login, password

    async def _fetch_token(self, login: str, password: str) -> str:
        form = aiohttp.FormData()
        form.add_field("login", login)
        form.add_field("password", password)
        try:
            async with self.session.post(
                f"{API_BASE}/user/token/get", data=form, headers={"Accept": "application/json"}
            ) as response:
                data = await self._decode(response)
                if response.status >= 400:
                    raise ApiError(
                        self._message(data, "Авторизация прокси-сервиса не прошла"),
                        status=response.status,
                    )
        except ApiError:
            raise
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise ApiError("Сервис временно недоступен") from exc
        token = str(data.get("token") or "")
        if not token:
            raise ApiError("Прокси-сервис не выдал токен")
        return token

    async def token(self, *, force: bool = False) -> str:
        login, password = await self._credentials()
        key = f"{login}:{hash(password)}"
        async with self._lock:
            fresh = (
                self._token
                and self._token_key == key
                and time.time() - self._token_at < self.TOKEN_TTL
            )
            if fresh and not force:
                return self._token
            self._token = await self._fetch_token(login, password)
            self._token_at = time.time()
            self._token_key = key
            return self._token

    @staticmethod
    async def _decode(response: aiohttp.ClientResponse) -> dict[str, Any]:
        raw = await response.text()
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = {"message": raw[:300] or "Пустой ответ сервиса"}
        return data if isinstance(data, dict) else {"items": data}

    @staticmethod
    def _message(data: dict[str, Any], default: str) -> str:
        for key in ("message", "error", "detail"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return value
        errors = data.get("errors")
        if isinstance(errors, dict) and errors:
            first = next(iter(errors.values()))
            if isinstance(first, list) and first:
                return str(first[0])
        return default

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        timeout: float | None = None,
        _retry: bool = True,
    ) -> dict[str, Any]:
        token = await self.token()
        extra: dict[str, Any] = {}
        if timeout:
            extra["timeout"] = aiohttp.ClientTimeout(total=timeout)
        try:
            async with self.session.request(
                method,
                f"{API_BASE}{path}",
                params=params,
                json=payload,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                **extra,
            ) as response:
                data = await self._decode(response)
                if response.status == 401 and _retry:
                    await self.token(force=True)
                    return await self.request(
                        method,
                        path,
                        params=params,
                        payload=payload,
                        timeout=timeout,
                        _retry=False,
                    )
                if response.status >= 400:
                    raise ApiError(
                        self._message(data, "Ошибка прокси-сервиса"), status=response.status
                    )
                return data
        except ApiError:
            raise
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise ApiError(
                "Сервис временно недоступен", uncertain=method.upper() != "GET"
            ) from exc

    # --- reseller account -------------------------------------------------

    async def balance(self) -> dict[str, Any]:
        return await self.request("GET", "/user/balance")

    # --- sub-users --------------------------------------------------------

    async def subuser_create(
        self,
        *,
        label: str,
        pool_type: str,
        threads: int = 100,
        pool_parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "label": label,
            "pool_type": pool_type,
            "threads": max(1, min(int(threads), 2000)),
            "sticky_range": {"start": 10000, "end": 20000},
        }
        if pool_parameters:
            payload["default_pool_parameters"] = pool_parameters
        return await self.request("POST", "/sub-user/create", payload=payload)

    async def subuser_get(self, subuser_id: int) -> dict[str, Any]:
        return await self.request("GET", "/sub-user/get", params={"subuser_id": int(subuser_id)})

    async def subuser_list(self, limit: int = 1000, offset: int = 0) -> list[dict[str, Any]]:
        data = await self.request(
            "GET", "/sub-user/list", params={"limit": int(limit), "offset": int(offset)}
        )
        rows = data.get("subusers")
        return rows if isinstance(rows, list) else []

    async def subuser_reset_password(self, subuser_id: int) -> dict[str, Any]:
        return await self.request(
            "POST", "/sub-user/reset-password", payload={"subuser_id": int(subuser_id)}
        )

    async def subuser_balance(self, subuser_id: int) -> dict[str, Any]:
        return await self.request(
            "GET", "/sub-user/balance/get", params={"subuser_id": int(subuser_id)}
        )

    async def subuser_balance_add(self, subuser_id: int, traffic_gb: int) -> dict[str, Any]:
        return await self.request(
            "POST",
            "/sub-user/balance/add",
            payload={"subuser_id": int(subuser_id), "traffic": int(traffic_gb)},
        )

    async def subuser_addition_history(self, subuser_id: int) -> list[dict[str, Any]]:
        data = await self.request(
            "GET", "/sub-user/balance/addition-history", params={"subuser_id": int(subuser_id)}
        )
        rows = data.get("history")
        return rows if isinstance(rows, list) else []

    async def observed_rate(self, subuser_id: int) -> float:
        """The provider's real price per GB for this sub-user's pool.

        There is no price endpoint, but every top-up is logged with what it
        added (``traffic_added``, GB) and what it cost off the reseller balance
        (``balance_charged``) — their ratio is the rate we actually pay.
        """
        for row in await self.subuser_addition_history(subuser_id):
            try:
                added = float(row.get("traffic_added") or 0)
                charged = float(row.get("balance_charged") or 0)
            except (TypeError, ValueError):
                continue
            if added > 0 and charged > 0:
                return round(charged / added, 4)
        return 0.0

    async def subuser_set_pool_parameters(
        self, subuser_id: int, parameters: dict[str, Any]
    ) -> dict[str, Any]:
        return await self.request(
            "POST",
            "/sub-user/set-default-pool-parameters",
            payload={"subuser_id": int(subuser_id), "default_pool_parameters": parameters},
        )

    # --- catalog ----------------------------------------------------------

    async def countries(self, pool_type: str) -> list[dict[str, Any]]:
        data = await self.request(
            "POST",
            "/common/locations/countries",
            payload={"pool_type": pool_type, "order_by": "count,desc"},
        )
        rows = data.get("countries") or data.get("items") or data.get("data")
        out: list[dict[str, Any]] = []
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict):
                    code = str(row.get("code") or row.get("country_code") or "").upper()
                    if code:
                        out.append({"code": code, "name": str(row.get("name") or code)})
                elif isinstance(row, str) and row:
                    out.append({"code": row.upper(), "name": row.upper()})
        return out

    async def pool_stats(self, pool_type: str) -> dict[str, Any]:
        return await self.request("GET", "/common/pool_stats", params={"pool_type": pool_type})


# --- proxy line building ---------------------------------------------------

PROXY_FORMATS: dict[str, tuple[str, str]] = {
    "hpu": ("host:port:login:password", "ip:port:login:pass"),
    "uph": ("login:password@host:port", "login:pass@ip:port"),
    "hpa": ("host:port@login:password", "ip:port@login:pass"),
    "url": ("http://login:password@host:port", "http://login:pass@ip:port"),
}


def format_label(key: str) -> str:
    return PROXY_FORMATS.get(key, PROXY_FORMATS["hpu"])[1]


def gateway_endpoint() -> tuple[str, str]:
    """Where the customer is told to connect: our relay when configured, else
    the provider gateway."""
    if RELAY_ENDPOINT and ":" in RELAY_ENDPOINT:
        host, port = RELAY_ENDPOINT.rsplit(":", 1)
        if host.strip() and port.strip().isdigit():
            return host.strip(), port.strip()
    return GATEWAY_HOST, str(GATEWAY_PORT)


def build_login(base_login: str, *, country: str = "", session_id: str = "", ttl: int = 0) -> str:
    """DataImpulse targeting lives in the username after a double underscore:
    ``login__cr.us;sessid.ab12;sessttl.600``."""
    parts: list[str] = []
    code = (country or "").strip().lower()
    if code:
        parts.append(f"cr.{code}")
    if session_id:
        parts.append(f"sessid.{session_id}")
        if ttl > 0:
            parts.append(f"sessttl.{min(int(ttl), SESSION_TTL_CAP)}")
    if not parts:
        return base_login
    return f"{base_login}__{';'.join(parts)}"


def render_line(login: str, password: str, host: str, port: str, fmt: str) -> str:
    if fmt == "uph":
        return f"{login}:{password}@{host}:{port}"
    if fmt == "hpa":
        return f"{host}:{port}@{login}:{password}"
    if fmt == "url":
        return f"http://{login}:{password}@{host}:{port}"
    return f"{host}:{port}:{login}:{password}"


def build_lines(
    base_login: str,
    password: str,
    *,
    count: int,
    country: str = "",
    rotation: str = "rotating",
    session_ttl: int = 0,
    fmt: str = "hpu",
) -> list[str]:
    """Build ``count`` distinct proxy lines.

    Every row carries its own ``sessid`` so the list is never N identical lines.
    In rotating mode the session gets a short TTL so each row still cycles its
    exit IP; in sticky mode the customer's chosen TTL is used.
    """
    host, port = gateway_endpoint()
    ttl = ROTATING_TTL_SECONDS if rotation != "sticky" else max(int(session_ttl or 0), 60)
    lines: list[str] = []
    for _ in range(max(1, int(count))):
        login = build_login(base_login, country=country, session_id=secrets.token_hex(4), ttl=ttl)
        lines.append(render_line(login, password, host, port, fmt))
    return lines


def pool_parameters(country: str, *, rotation: str, session_ttl: int) -> dict[str, Any]:
    """Sub-user level defaults mirrored from the customer's menu choices."""
    params: dict[str, Any] = {
        "countries": [country.lower()] if country else [],
        "cities": [],
        "states": [],
        "zipcodes": [],
        "asns": [],
        "exclude_countries": [],
        "exclude_asn": [],
        "anonymous_filter": False,
    }
    minutes = (
        max(1, min(int(session_ttl or 0) // 60, 120))
        if rotation == "sticky"
        else max(1, ROTATING_TTL_SECONDS // 60)
    )
    params["rotation_interval"] = minutes
    return params


def filter_countries(rows: Iterable[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    needle = (query or "").strip().lower()
    if not needle:
        return list(rows)
    return [
        row
        for row in rows
        if needle in str(row.get("name", "")).lower() or needle == str(row.get("code", "")).lower()
    ]
