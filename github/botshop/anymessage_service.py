from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urlsplit

import aiohttp
from dotenv import load_dotenv


load_dotenv()

ANYMESSAGE_API_BASE_URL = os.getenv("ANYMESSAGE_API_BASE_URL", "https://api.anymessage.shop").strip().rstrip("/")
ANYMESSAGE_API_TOKEN = os.getenv("ANYMESSAGE_API_TOKEN", "").strip()
ANYMESSAGE_REQUEST_TIMEOUT_SECONDS = float(os.getenv("ANYMESSAGE_REQUEST_TIMEOUT_SECONDS", "25") or 25)


class AnyMessageAPIError(Exception):
    def __init__(self, code: str, message: str | None = None):
        self.code = str(code or "provider_error").strip().lower()
        super().__init__(message or self.code)


ERROR_MESSAGES = {
    "token": "Сервис временно недоступен.",
    "site": "Проверьте адрес сайта.",
    "domain": "Этот почтовый домен недоступен.",
    "no emails": "Для выбранного домена закончились адреса.",
    "no balance": "Поставщик временно не принимает заказы.",
    "wait message": "Письмо ещё не пришло.",
    "activation canceled": "Активация уже отменена.",
    "activation already canceled": "Активация уже отменена.",
    "no activation": "Активация не найдена.",
    "email banned": "Этот адрес больше нельзя использовать повторно.",
}


def get_anymessage_error_message(code: str) -> str:
    normalized = str(code or "provider_error").strip().lower()
    return ERROR_MESSAGES.get(normalized, "Поставщик временно недоступен. Попробуйте ещё раз.")


def normalize_target_site(raw_value: str) -> str:
    value = str(raw_value or "").strip().lower()
    if not value:
        raise ValueError("Введите адрес сайта.")
    compact_value = re.sub(r"[^a-zа-яё0-9]", "", value)
    site_aliases = {
        "instagram": "instagram.com",
        "instgram": "instagram.com",
        "insta": "instagram.com",
        "инстаграм": "instagram.com",
        "инста": "instagram.com",
        "threads": "threads.net",
        "telegram": "telegram.org",
        "телеграм": "telegram.org",
        "facebook": "facebook.com",
        "фейсбук": "facebook.com",
        "tiktok": "tiktok.com",
        "тикток": "tiktok.com",
        "google": "google.com",
        "гугл": "google.com",
        "gmail": "gmail.com",
        "гмейл": "gmail.com",
        "vk": "vk.com",
        "вк": "vk.com",
        "вконтакте": "vk.com",
    }
    if compact_value in site_aliases:
        value = site_aliases[compact_value]
    if "://" not in value:
        value = f"https://{value}"
    parsed = urlsplit(value)
    hostname = str(parsed.hostname or "").strip().rstrip(".")
    if not hostname or len(hostname) > 253:
        raise ValueError("Проверьте адрес сайта.")
    try:
        hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise ValueError("Проверьте адрес сайта.") from error
    if not re.fullmatch(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", hostname):
        raise ValueError("Введите домен сайта, например instagram.com.")
    return hostname


async def _request(path: str, **params: Any) -> dict[str, Any]:
    if not ANYMESSAGE_API_TOKEN:
        raise AnyMessageAPIError("token")
    timeout = aiohttp.ClientTimeout(total=ANYMESSAGE_REQUEST_TIMEOUT_SECONDS)
    request_params = {"token": ANYMESSAGE_API_TOKEN, **params}
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{ANYMESSAGE_API_BASE_URL}{path}", params=request_params) as response:
                payload = await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as error:
        raise AnyMessageAPIError("network", "Поставщик не ответил вовремя.") from error
    if not isinstance(payload, dict):
        raise AnyMessageAPIError("invalid_response")
    return payload


async def get_provider_balance() -> float:
    payload = await _request("/user/balance")
    if payload.get("status") != "success":
        raise AnyMessageAPIError(str(payload.get("value") or "provider_error"))
    return float(payload.get("balance") or 0.0)


async def get_email_domains(site: str) -> list[dict[str, Any]]:
    normalized_site = normalize_target_site(site)
    payload = await _request("/email/quantity", site=normalized_site)
    if payload.get("status") != "success":
        raise AnyMessageAPIError(str(payload.get("value") or "provider_error"))
    raw_domains = payload.get("data") or {}
    if not isinstance(raw_domains, dict):
        raise AnyMessageAPIError("invalid_response")
    result: list[dict[str, Any]] = []
    for domain, details in raw_domains.items():
        if not isinstance(details, dict):
            continue
        normalized_domain = str(domain or "").strip().lower()
        if not normalized_domain or not re.fullmatch(r"[a-z0-9.-]{3,253}", normalized_domain):
            continue
        available_count = max(int(details.get("count") or 0), 0)
        if available_count <= 0:
            continue
        result.append(
            {
                "domain": normalized_domain,
                "count": available_count,
                "supplier_price": max(float(details.get("price") or 0.0), 0.0),
            }
        )
    return [dict(row) for row in result]


async def order_email(site: str, domain: str) -> dict[str, str]:
    normalized_site = normalize_target_site(site)
    normalized_domain = str(domain or "").strip().lower()
    payload = await _request("/email/order", site=normalized_site, domain=normalized_domain)
    if payload.get("status") != "success":
        raise AnyMessageAPIError(str(payload.get("value") or "provider_error"))
    activation_id = str(payload.get("id") or "").strip()
    email = str(payload.get("email") or "").strip()
    if not activation_id or not email or "@" not in email:
        raise AnyMessageAPIError("invalid_response")
    return {"activation_id": activation_id, "email": email}


async def reorder_email(activation_id: str) -> dict[str, str]:
    normalized_id = str(activation_id or "").strip()
    if not normalized_id:
        raise AnyMessageAPIError("no activation")
    payload = await _request("/email/reorder", id=normalized_id)
    if payload.get("status") != "success":
        raise AnyMessageAPIError(str(payload.get("value") or "provider_error"))
    reordered_id = str(payload.get("id") or "").strip()
    email = str(payload.get("email") or "").strip()
    if not reordered_id or not email or "@" not in email:
        raise AnyMessageAPIError("invalid_response")
    return {"activation_id": reordered_id, "email": email}


async def get_email_message(activation_id: str) -> dict[str, Any]:
    payload = await _request("/email/getmessage", id=str(activation_id or "").strip())
    if payload.get("status") == "success":
        return {
            "status": "received",
            "value": str(payload.get("value") or "").strip(),
            "message": str(payload.get("message") or ""),
        }
    code = str(payload.get("value") or "provider_error").strip().lower()
    if code == "wait message":
        return {"status": "waiting", "value": "", "message": ""}
    if code == "activation canceled":
        return {"status": "canceled", "value": "", "message": ""}
    raise AnyMessageAPIError(code)


async def cancel_email(activation_id: str) -> None:
    payload = await _request("/email/cancel", id=str(activation_id or "").strip())
    if payload.get("status") != "success":
        raise AnyMessageAPIError(str(payload.get("value") or "provider_error"))
