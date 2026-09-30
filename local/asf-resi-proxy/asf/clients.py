from __future__ import annotations

import base64
import hashlib
import hmac
import json
from decimal import Decimal, ROUND_CEILING
from typing import Any

import aiohttp


class ApiError(RuntimeError):
    def __init__(self, message: str, *, status: int = 0, uncertain: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.uncertain = uncertain


class SousClient:
    def __init__(self, session: aiohttp.ClientSession, base_url: str, api_key_getter) -> None:
        self.session = session
        self.base_url = base_url.rstrip("/")
        self.api_key_getter = api_key_getter

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        key = await self.api_key_getter()
        if not key:
            raise ApiError("Сервис временно недоступен")
        try:
            async with self.session.request(
                method,
                f"{self.base_url}{path}",
                params=params,
                json=payload,
                headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            ) as response:
                raw = await response.text()
                try:
                    data = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    data = {"message": raw[:500] or "Пустой ответ API"}
                if response.status >= 400:
                    message = data.get("message") or data.get("detail") or str(data.get("errors") or "Ошибка API")
                    raise ApiError(str(message), status=response.status)
                if not isinstance(data, dict):
                    raise ApiError("Сервис вернул неожиданный ответ")
                return data
        except ApiError:
            raise
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise ApiError("Сервис временно недоступен", uncertain=method.upper() != "GET") from exc

    async def profile(self) -> dict[str, Any]:
        return await self.request("GET", "/profile")

    async def market_categories(self) -> dict[str, Any]:
        return await self.request("GET", "/market/categories")

    async def market_products(self, category_id: int, page: int = 1) -> dict[str, Any]:
        return await self.request(
            "GET", "/market/products", params={"category_id": category_id, "page": page}
        )

    async def market_order(self, category_id: int, product_id: int, quantity: int) -> dict[str, Any]:
        return await self.request(
            "POST",
            "/market/orders",
            payload={
                "items": [
                    {"product_id": product_id, "category_id": category_id, "quantity": quantity}
                ]
            },
        )

    async def market_order_status(self, external_id: str) -> dict[str, Any]:
        return await self.request("GET", f"/market/orders/{external_id}")

    async def proxy_catalog(self) -> dict[str, Any]:
        return await self.request("GET", "/proxy/catalog")

    async def proxy_order(self, country_id: int, protocol_id: int, quantity: int) -> dict[str, Any]:
        return await self.request(
            "POST",
            "/proxy/orders",
            payload={
                "items": [
                    {"category_id": country_id, "item_id": protocol_id, "quantity": quantity}
                ]
            },
        )

    async def proxy_services(self) -> dict[str, Any]:
        return await self.request("GET", "/proxy/services")

    async def residential_traffic(self) -> dict[str, Any]:
        return await self.request("GET", "/proxy/residential/traffic")

    async def proxy_service_order(
        self, service: str, quantity: float, request_id: str, settings: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "service": service,
            "quantity": quantity,
            "request_id": request_id,
        }
        if settings:
            payload["settings"] = settings
        return await self.request("POST", "/proxy/services/orders", payload=payload)

    async def proxy_service_order_status(self, external_id: str) -> dict[str, Any]:
        return await self.request("GET", f"/proxy/services/orders/{external_id}")

    # --- residential proxy client management (public API v1) ---

    async def residential_client_get(self, client_id: str) -> dict[str, Any]:
        return await self.request("GET", f"/proxy/residential/clients/{client_id}")

    async def residential_client_create(self, client_id: str, label: str = "") -> dict[str, Any]:
        return await self.request(
            "POST",
            "/proxy/residential/clients",
            payload={"client_id": client_id, "label": label or client_id},
        )

    async def residential_transfer(
        self, client_id: str, action: str, gb: float, request_id: str
    ) -> dict[str, Any]:
        return await self.request(
            "POST",
            f"/proxy/residential/clients/{client_id}/traffic",
            payload={"action": action, "gb": gb, "request_id": request_id},
        )

    async def residential_proxies(self, client_id: str, settings: dict[str, Any]) -> dict[str, Any]:
        return await self.request(
            "POST", f"/proxy/residential/clients/{client_id}/proxies", payload=settings
        )

    async def residential_rotate_password(self, client_id: str, request_id: str) -> dict[str, Any]:
        return await self.request(
            "POST",
            f"/proxy/residential/clients/{client_id}/rotate-password",
            payload={"request_id": request_id},
        )


class HeleketClient:
    BASE_URL = "https://api.heleket.com/v1"

    def __init__(self, session: aiohttp.ClientSession, credentials_getter) -> None:
        self.session = session
        self.credentials_getter = credentials_getter

    @staticmethod
    def encode_body(payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("/", "\\/")

    @staticmethod
    def signature(body: str, api_key: str) -> str:
        encoded = base64.b64encode(body.encode()).decode()
        return hashlib.md5(f"{encoded}{api_key}".encode()).hexdigest()

    async def request(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        merchant, api_key = await self.credentials_getter()
        if not merchant or not api_key:
            raise ApiError("Heleket merchant ID или API key не настроены")
        body = self.encode_body(payload)
        headers = {
            "merchant": merchant,
            "sign": self.signature(body, api_key),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            async with self.session.post(
                f"{self.BASE_URL}{path}", data=body.encode(), headers=headers
            ) as response:
                raw = await response.text()
                try:
                    data = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    data = {"message": raw[:500] or "Пустой ответ Heleket"}
                if response.status >= 400 or data.get("state") not in (None, 0):
                    message = data.get("message") or str(data.get("errors") or "Ошибка Heleket")
                    raise ApiError(str(message), status=response.status)
                result = data.get("result", data)
                if not isinstance(result, dict):
                    return {"items": result}
                return result
        except ApiError:
            raise
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise ApiError("Heleket временно недоступен", uncertain=True) from exc

    async def services(self) -> dict[str, Any]:
        return await self.request("/payment/services", {})

    async def create_invoice(
        self,
        *,
        amount_rub: Decimal,
        order_id: str,
        user_id: int,
        callback_url: str = "",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "amount": f"{amount_rub:.2f}",
            "currency": "USDT",
            "order_id": order_id,
            "lifetime": 3600,
            "additional_data": f"user_{user_id}",
            "theme": "dark",
        }
        if callback_url:
            payload["url_callback"] = callback_url
        return await self.request("/payment", payload)

    async def payment_info(self, order_id: str) -> dict[str, Any]:
        return await self.request("/payment/info", {"order_id": order_id})

    @classmethod
    def verify_webhook(cls, payload: dict[str, Any], api_key: str) -> bool:
        received = str(payload.get("sign", ""))
        if not received or not api_key:
            return False
        unsigned = dict(payload)
        unsigned.pop("sign", None)
        normal = json.dumps(unsigned, ensure_ascii=False, separators=(",", ":"))
        candidates = {normal, normal.replace("/", "\\/")}
        return any(hmac.compare_digest(cls.signature(body, api_key), received) for body in candidates)


def sale_price_kopecks(cost_usdt: float | str, rate: float, markup_percent: float) -> int:
    cost = Decimal(str(cost_usdt))
    value = cost * Decimal(str(rate)) * (Decimal("1") + Decimal(str(markup_percent)) / Decimal("100"))
    return int((value * 100).quantize(Decimal("1"), rounding=ROUND_CEILING))


def tariff_total(service: dict[str, Any], quantity: float) -> float:
    tariffs = service.get("tariffs") or []
    for tariff in tariffs:
        if float(tariff.get("quantity", -1)) == float(quantity):
            return float(tariff.get("total_price", 0))
    return float(service.get("price", 0)) * float(quantity)


def external_order_id(data: dict[str, Any]) -> str:
    for key in ("order_id", "id", "uuid"):
        value = data.get(key)
        if value is not None:
            return str(value)
    result = data.get("result")
    if isinstance(result, dict):
        return external_order_id(result)
    for key in ("order", "orders", "items"):
        value = data.get(key)
        if isinstance(value, dict):
            order_id = external_order_id(value)
            if order_id:
                return order_id
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    order_id = external_order_id(item)
                    if order_id:
                        return order_id
    return ""


def external_order_number(data: Any) -> str:
    """Human-facing order number returned by the SOUS API.

    Market orders carry an ``order_number`` / ``supplier_order_number`` such as
    ``"545580-5979"`` — that is the number the customer sees on the SOUS side and
    the one support asks for.  Proxy and proxy-service orders only expose a
    numeric ``order_id``; for those we fall back to :func:`external_order_id` so
    the caller always gets *some* real provider identifier.
    """
    if isinstance(data, dict):
        for key in ("order_number", "supplier_order_number"):
            value = data.get(key)
            if value not in (None, ""):
                return str(value)
        nested = data.get("result")
        if isinstance(nested, dict):
            found = external_order_number(nested)
            if found:
                return found
        for key in ("order", "orders", "items"):
            value = data.get(key)
            if isinstance(value, dict):
                found = external_order_number(value)
                if found:
                    return found
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        found = external_order_number(item)
                        if found:
                            return found
    return external_order_id(data)


def extract_delivery(data: Any) -> str:
    if not data:
        return ""
    if isinstance(data, str):
        return data
    if isinstance(data, list):
        parts = [extract_delivery(item) for item in data]
        return "\n".join(part for part in parts if part)
    if not isinstance(data, dict):
        return str(data)
    preferred = (
        "delivery_text",
        "data",
        "delivery",
        "credentials",
        "accounts",
        "proxies",
        "codes",
        "items",
        "orders",
        "order",
        "result",
        "proxy_list",
        "content",
        "account_data",
    )
    for key in preferred:
        if key in data and data[key] not in (None, "", [], {}):
            value = extract_delivery(data[key])
            if value:
                return value
    delivery_keys = {"login", "password", "email", "proxy", "host", "port", "code", "token"}
    if delivery_keys.intersection(data):
        return json.dumps(data, ensure_ascii=False)
    return ""
