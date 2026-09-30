"""StrikeProxy reseller API client and proxy-line builder.

A second proxy provider next to DataImpulse. The model is close but not the
same: instead of sub-users, StrikeProxy sells *services* — one purchase creates
a service with its own proxy login/password and a GB allowance, and further GB
is pushed onto the same service. Every customer therefore gets one service per
plan type (residential / mobile / datacenter), created on their first top-up.

Unlike DataImpulse the provider publishes a real price list per account
(``GET /api/reseller/catalog`` → ``reseller_price``), so the buying price never
has to be guessed or hand-entered — the shop reads it and marks it up.

Proxy lines come from the provider itself (``POST /api/reseller/proxy/generate``)
because the geo/session grammar lives in the username and is built server-side.
The returned host is then swapped for our own relay so the buyer never sees who
the upstream is.

Reference: https://strikeproxy.net/strike/docs
"""
from __future__ import annotations

import json
import os
from typing import Any, Iterable

import aiohttp

from .clients import ApiError


API_BASE = os.getenv("SP_API_BASE", "https://strikeproxy.net/api").rstrip("/")

# strikeproxy.net sits behind Cloudflare, which answers a bare API client with a
# JS challenge page instead of JSON. A normal browser User-Agent is enough to
# pass it — without this header every call fails with unparseable HTML.
USER_AGENT = os.getenv(
    "SP_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
)

GATEWAY_HOST = os.getenv("SP_GATEWAY_HOST", "beta.strikeproxy.net").strip()
GATEWAY_PORT = int(os.getenv("SP_GATEWAY_PORT", "8080"))
RELAY_ENDPOINT = os.getenv("SP_RELAY_ENDPOINT", "31.77.145.160:18082").strip()

# Rotating mode: the provider's own "rotating" session type returns N identical
# lines, which the owner rejected — so rotation is a sticky session with the
# shortest TTL the API accepts, giving distinct lines that each cycle fast.
ROTATING_MINUTES = max(1, int(os.getenv("SP_ROTATING_MINUTES", "1")))
SESSION_MINUTES_CAP = 120
GENERATE_CAP = 10000

# GB plans only. The duration-based "unlimited" plans need a different purchase
# flow (hours, not gigabytes) and are not sold in the bot.
PLAN_TYPES: tuple[str, ...] = ("residential", "mobile", "datacenter")

PLAN_LABELS: dict[str, str] = {
    "residential": "🏠 Резидентские",
    "mobile": "📱 Мобильные",
    "datacenter": "🖥 Датацентр",
}

# Section codes are what the shop stores in settings / service_overrides. They
# are prefixed so StrikeProxy pools never collide with the DataImpulse ones.
SECTION_PREFIX = "sp_"


def section_codes() -> tuple[str, ...]:
    return tuple(f"{SECTION_PREFIX}{plan}" for plan in PLAN_TYPES)


def section_code(plan: str) -> str:
    return f"{SECTION_PREFIX}{plan}"


def plan_of(code: str) -> str:
    """``sp_residential`` → ``residential``; anything else → ``""``."""
    if code.startswith(SECTION_PREFIX):
        plan = code[len(SECTION_PREFIX) :]
        if plan in PLAN_TYPES:
            return plan
    return ""


def plan_label(plan: str) -> str:
    return PLAN_LABELS.get(plan, plan)


def cost_setting_key(plan: str) -> str:
    """Rate the provider charges when a brand new service is ordered."""
    return f"sp_cost_{plan}"


def topup_cost_setting_key(plan: str) -> str:
    """Rate the provider charges to add GB to an existing service.

    It is *not* the catalog rate: on 2026-09-24 a mobile top-up billed
    1.40 $/GB while ``/reseller/catalog`` and a fresh order both said 0.80.
    Selling against the catalog number alone would give away the margin, so the
    two rates are tracked apart and the shop prices off the higher one.
    """
    return f"sp_cost_{plan}_topup"


class StrikeProxyClient:
    """Thin async wrapper over the reseller API.

    Auth is a single long-lived ``sp_live_…`` token held in the shop's encrypted
    settings, sent as a Bearer header.
    """

    def __init__(self, session: aiohttp.ClientSession, key_getter) -> None:
        self.session = session
        self.key_getter = key_getter

    async def _key(self) -> str:
        key = (await self.key_getter() or "").strip()
        if not key:
            raise ApiError("Прокси-сервис не настроен")
        return key

    @staticmethod
    async def _decode(response: aiohttp.ClientResponse) -> dict[str, Any]:
        raw = await response.text()
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = {"error": "Сервис ответил некорректно"}
        return data if isinstance(data, dict) else {"items": data}

    # Provider error codes are internal; buyers get a readable Russian line and
    # never learn which upstream is behind the shop.
    _ERRORS: dict[str, str] = {
        "unauthorized": "Прокси-сервис не авторизован",
        "forbidden": "Операция недоступна",
        "invalid_body": "Некорректный запрос к прокси-сервису",
        "invalid_json": "Некорректный запрос к прокси-сервису",
        "unknown_plan": "Такой тип прокси недоступен",
        "insufficient_funds": "Не хватает средств на счёте сервиса",
        "rate_limited": "Слишком много запросов, попробуйте через минуту",
        "not_found": "Прокси-аккаунт не найден",
        "plan_type_unsupported": "Операция недоступна для этого типа прокси",
        "upstream_error": "Сервис временно недоступен",
        "balance_unavailable": "Сервис временно недоступен",
    }

    @classmethod
    def _message(cls, data: dict[str, Any], default: str) -> str:
        for key in ("error", "message", "detail", "msg"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return cls._ERRORS.get(value, value)
        return default

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        key = await self._key()
        headers = {
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        extra: dict[str, Any] = {}
        if timeout:
            extra["timeout"] = aiohttp.ClientTimeout(total=timeout)
        try:
            async with self.session.request(
                method,
                f"{API_BASE}{path}",
                params=params,
                json=payload,
                headers=headers,
                **extra,
            ) as response:
                data = await self._decode(response)
                if response.status >= 400:
                    write = method.upper() != "GET"
                    if response.status >= 500:
                        # The provider sits behind Cloudflare, whose 5xx pages
                        # name the stack and say nothing about whether the
                        # origin processed the request. Never show that text to
                        # a buyer, and never assume a failed write was a no-op:
                        # the caller has to verify against the provider first.
                        raise ApiError(
                            "Сервис временно недоступен, попробуйте позже",
                            status=response.status,
                            uncertain=write,
                        )
                    raise ApiError(
                        self._message(data, "Ошибка прокси-сервиса"),
                        status=response.status,
                        # A 4xx is a clean refusal: nothing was bought.
                        uncertain=False,
                    )
                return data
        except ApiError:
            raise
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise ApiError(
                "Сервис временно недоступен", uncertain=method.upper() != "GET"
            ) from exc

    # --- reseller account -------------------------------------------------

    async def balance(self) -> float:
        data = await self.request("GET", "/balance")
        try:
            return float(data.get("balance") or 0)
        except (TypeError, ValueError):
            return 0.0

    async def reseller_me(self) -> dict[str, Any]:
        return await self.request("GET", "/reseller/me")

    async def catalog(self) -> list[dict[str, Any]]:
        data = await self.request("GET", "/reseller/catalog")
        rows = data.get("catalog")
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    async def plan_costs(self) -> dict[str, float]:
        """Real buying price per GB for every GB plan, straight from the
        account's own catalog — no guessing, no hand-entered numbers."""
        costs: dict[str, float] = {}
        for row in await self.catalog():
            plan = str(row.get("plan_key") or "")
            if plan not in PLAN_TYPES or str(row.get("pricing_type") or "") != "pergb":
                continue
            try:
                price = float(row.get("reseller_price") or row.get("retail_price") or 0)
            except (TypeError, ValueError):
                continue
            if 0 < price < 1000:
                costs[plan] = round(price, 4)
        return costs

    # --- services ---------------------------------------------------------

    async def services(self) -> list[dict[str, Any]]:
        data = await self.request("GET", "/reseller/services", timeout=45)
        rows = data.get("services")
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    async def find_service(
        self, *, service_id: int = 0, proxy_username: str = ""
    ) -> dict[str, Any] | None:
        for row in await self.services():
            if service_id and str(row.get("id") or "") == str(service_id):
                return row
            if proxy_username and str(row.get("proxy_username") or "") == proxy_username:
                return row
        return None

    async def order(self, plan: str, quantity: float) -> dict[str, Any]:
        """Buy a new service off the reseller balance; credentials come back
        immediately (no crypto redirect)."""
        return await self.request(
            "POST",
            "/reseller/order",
            payload={"plan": plan, "quantity": quantity},
            timeout=90,
        )

    async def add_bandwidth(self, proxy_username: str, add_gb: float) -> dict[str, Any]:
        return await self.request(
            "POST",
            "/proxy/add-bandwidth",
            payload={"proxy_username": proxy_username, "add_gb": add_gb},
            timeout=90,
        )

    async def update_password(self, proxy_username: str, new_password: str) -> dict[str, Any]:
        return await self.request(
            "POST",
            "/proxy/update-password",
            payload={"proxy_username": proxy_username, "new_password": new_password},
        )

    async def locations(self, plan: str) -> list[dict[str, Any]]:
        data = await self.request(
            "GET", "/reseller/proxy/locations", params={"plan": plan}, timeout=45
        )
        rows = data.get("countries")
        out: list[dict[str, Any]] = []
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                code = str(row.get("iso2") or row.get("code") or "").upper()
                if code:
                    out.append({"code": code, "name": str(row.get("name") or code)})
        return out

    async def generate(
        self,
        service_id: int,
        *,
        count: int,
        country: str = "",
        rotation: str = "rotating",
        session_ttl: int = 0,
    ) -> list[str]:
        """Ask the provider for ``count`` proxy lines in a canonical format.

        ``session_ttl`` is in seconds, matching the customer menu; the API wants
        minutes. Rotation is sticky-with-a-short-TTL rather than the provider's
        own "rotating" type, because that type returns N identical lines.

        A single generate call with ``count=N`` and ``session_type=sticky`` also
        returns N rows sharing one session id — one user:pass credential
        repeated. To hand out distinct lines the shop calls the endpoint once
        per row, so each row becomes its own session; duplicates are dropped in
        case the provider ever returns a repeat.
        """
        minutes = (
            max(1, min(int(session_ttl or 0) // 60, SESSION_MINUTES_CAP))
            if rotation == "sticky"
            else ROTATING_MINUTES
        )
        total = max(1, min(int(count), GENERATE_CAP))
        code = (country or "").strip()
        collected: list[str] = []
        seen: set[str] = set()
        for _ in range(total):
            payload: dict[str, Any] = {
                "service_id": int(service_id),
                "count": 1,
                "protocol": "http",
                "format": "user:pass@host:port",
                "session_type": "sticky",
                "session_minutes": minutes,
            }
            if code:
                payload["country"] = code.upper()
            data = await self.request(
                "POST", "/reseller/proxy/generate", payload=payload, timeout=120
            )
            rows = data.get("proxies") if isinstance(data, dict) else None
            if not isinstance(rows, list):
                continue
            for row in rows:
                line = str(row).strip()
                if not line or line in seen:
                    continue
                seen.add(line)
                collected.append(line)
                break
        return collected


# --- proxy line building ---------------------------------------------------

PROXY_FORMATS: dict[str, tuple[str, str]] = {
    "hpu": ("host:port:login:password", "ip:port:login:pass"),
    "uph": ("login:password@host:port", "login:pass@ip:port"),
    "hpa": ("host:port@login:password", "ip:port@login:pass"),
    "url": ("http://login:password@host:port", "http://login:pass@ip:port"),
}


def format_label(key: str) -> str:
    return PROXY_FORMATS.get(key, PROXY_FORMATS["hpu"])[1]


def relay_endpoint() -> tuple[str, str]:
    """What the customer is told to connect to: our own relay when configured,
    otherwise the provider gateway."""
    if RELAY_ENDPOINT and ":" in RELAY_ENDPOINT:
        host, port = RELAY_ENDPOINT.rsplit(":", 1)
        if host.strip() and port.strip().isdigit():
            return host.strip(), port.strip()
    return GATEWAY_HOST, str(GATEWAY_PORT)


def render_line(login: str, password: str, host: str, port: str, fmt: str) -> str:
    if fmt == "uph":
        return f"{login}:{password}@{host}:{port}"
    if fmt == "hpa":
        return f"{host}:{port}@{login}:{password}"
    if fmt == "url":
        return f"http://{login}:{password}@{host}:{port}"
    return f"{host}:{port}:{login}:{password}"


def rewrite_lines(raw_lines: Iterable[str], fmt: str) -> list[str]:
    """Repoint provider lines at our relay and re-render in the chosen format.

    Input rows are ``login:password@host:port`` — the login carries the whole
    geo/session grammar, so it is kept verbatim and only the endpoint changes.
    """
    host, port = relay_endpoint()
    out: list[str] = []
    for raw in raw_lines:
        line = str(raw).strip()
        if not line:
            continue
        if "://" in line:
            line = line.split("://", 1)[1]
        if "@" not in line:
            # Unexpected shape — pass it through rather than dropping a line the
            # customer already paid for.
            out.append(line)
            continue
        credentials, _endpoint = line.rsplit("@", 1)
        login, _, password = credentials.partition(":")
        if not login:
            out.append(line)
            continue
        out.append(render_line(login, password, host, port, fmt))
    return out


def filter_countries(rows: Iterable[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    needle = (query or "").strip().lower()
    if not needle:
        return list(rows)
    return [
        row
        for row in rows
        if needle in str(row.get("name", "")).lower() or needle == str(row.get("code", "")).lower()
    ]
