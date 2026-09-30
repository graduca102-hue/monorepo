from __future__ import annotations

import html
import hashlib
import json
import logging
import os
import re
import signal
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import requests


BASE_DIR = Path(__file__).resolve().parent
MOSCOW = ZoneInfo("Europe/Moscow")


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


load_dotenv(BASE_DIR / ".env")


@dataclass(frozen=True)
class Sponsor:
    label: str
    url: str
    chat_id: str | None = None


@dataclass(frozen=True)
class OrderService:
    provider_service_id: int
    platform: str
    title: str
    unit_name: str
    unit_price: int
    minimum: int
    intro: str
    link_prompt: str


@dataclass(frozen=True)
class CoinPackage:
    title: str
    stars_amount: int
    coins_amount: int
    description: str



def parse_sponsors(env_name: str, count: int) -> list[Sponsor]:
    """Parse Label|URL|@chat entries separated by semicolons."""
    configured = os.getenv(env_name, "").strip()
    sponsors: list[Sponsor] = []
    if configured:
        for index, entry in enumerate(configured.split(";"), start=1):
            parts = [part.strip() for part in entry.split("|")]
            if len(parts) >= 2 and parts[1].startswith(("https://", "http://")):
                sponsors.append(
                    Sponsor(
                        label=parts[0] or f"Спонсор: {index}",
                        url=parts[1],
                        chat_id=parts[2] if len(parts) >= 3 and parts[2] else None,
                    )
                )
    if sponsors:
        return sponsors
    # Без данных заказчика кнопки остаются кликабельными, а строгая проверка
    # включится автоматически после добавления chat_id в .env.
    return [
        Sponsor(f"Спонсор: {index}", "https://t.me/telegram")
        for index in range(1, count + 1)
    ]


def parse_int_set(raw: str) -> set[int]:
    values: set[int] = set()
    for part in raw.split(","):
        item = part.strip()
        if item.isdigit():
            values.add(int(item))
    return values


FREE_SPONSORS = parse_sponsors("FREE_SPONSORS", 6)
EARN_SPONSORS = parse_sponsors("EARN_SPONSORS", 8)
TASK_URL = os.getenv("TASK_URL", "https://t.me/telegram").strip()
REFERRAL_REWARD = int(os.getenv("REFERRAL_REWARD", "4500"))
EARN_REWARD = int(os.getenv("EARN_REWARD", "500"))
TASK_REWARD = int(os.getenv("TASK_REWARD", "250"))
FLYER_API_KEY = os.getenv("FLYER_API_KEY", "").strip()
FLYER_TASK_REWARD = int(os.getenv("FLYER_TASK_REWARD", "250"))
FLYER_INCOMPLETE_STATUSES = {"incomplete", "abort"}
FLYER_REWARDABLE_STATUSES = {"waiting", "complete"}
LINKNI_SELL_CODE = os.getenv("LINKNI_SELL_CODE", "2i2r5").strip()
LINKNI_APP_URL = os.getenv(
    "LINKNI_APP_URL",
    "https://telegram.me/linknibot/app?startapp=x_2i2r5",
).strip()
LINKNI_API_URL = os.getenv(
    "LINKNI_API_URL",
    "https://go.linkni.me/api/subscriptions",
).strip()
LINKNI_REWARD = int(os.getenv("LINKNI_REWARD", "250"))
LINKNI_WEBHOOK_HOST = os.getenv("LINKNI_WEBHOOK_HOST", "0.0.0.0").strip()
LINKNI_WEBHOOK_PORT = int(os.getenv("LINKNI_WEBHOOK_PORT", "8081"))
LINKNI_WEBHOOK_PATH = os.getenv("LINKNI_WEBHOOK_PATH", "/linkni/webhook").strip() or "/linkni/webhook"
FLYER_WEBHOOK_HOST = os.getenv("FLYER_WEBHOOK_HOST", "0.0.0.0").strip()
FLYER_WEBHOOK_PORT = int(os.getenv("FLYER_WEBHOOK_PORT", "8082"))
FLYER_WEBHOOK_PATH = os.getenv("FLYER_WEBHOOK_PATH", "/flyer/webhook").strip() or "/flyer/webhook"
SOCPROOF_API_KEY = os.getenv("SOCPROOF_API_KEY", "").strip()
SOCPROOF_API_URL = os.getenv("SOCPROOF_API_URL", "https://soc-proof.su/api/v2").strip()
BOTOHUB_AUTH_TOKEN = os.getenv("BOTOHUB_AUTH_TOKEN", "").strip()
BOTOHUB_API_URL = os.getenv("BOTOHUB_API_URL", "https://botohub.me/get-tasks").strip()
EARN_TASK_PRICE = int(os.getenv("EARN_TASK_PRICE", "400"))
ADMIN_IDS = parse_int_set(os.getenv("ADMIN_IDS", "").strip())
ADMIN_BROADCAST_BATCH_DELAY = float(os.getenv("ADMIN_BROADCAST_BATCH_DELAY", "0.05"))
EARN_BATCH_SIZE = int(os.getenv("EARN_BATCH_SIZE", "6"))
MANDATORY_TASK_LIMIT = int(os.getenv("MANDATORY_TASK_LIMIT", "4"))
MANDATORY_REWARD = int(os.getenv("MANDATORY_REWARD", "1000"))
MANDATORY_CACHE_HOURS = int(os.getenv("MANDATORY_CACHE_HOURS", "2"))


ORDER_SERVICES = {
    "followers_basic": OrderService(
        provider_service_id=458,
        platform="telegram",
        title="Telegram-подписчики",
        unit_name="подписчика",
        unit_price=30,
        minimum=10,
        intro=(
            "⚡ Внимание!! Качество подписчиков очень низкое. "
            "Возможны массовые отписки! Претензии не принимаются!"
        ),
        link_prompt="Введите ссылку на Telegram-канал:",
    ),
    "followers_premium": OrderService(
        provider_service_id=1504,
        platform="telegram",
        title="Telegram-подписчики премиум",
        unit_name="подписчика",
        unit_price=400,
        minimum=10,
        intro="💎 Премиум качество. Гарантия 30 суток.",
        link_prompt="Введите ссылку на Telegram-канал:",
    ),
    "reactions": OrderService(
        provider_service_id=884,
        platform="telegram",
        title="Реакции",
        unit_name="реакции",
        unit_price=15,
        minimum=10,
        intro="🔥 Положительные реакции на публикацию.",
        link_prompt="Введите ссылку на публикацию:",
    ),
    "views": OrderService(
        provider_service_id=1565,
        platform="telegram",
        title="Просмотры",
        unit_name="просмотра",
        unit_price=2,
        minimum=50,
        intro="👁 Быстрые просмотры публикации.",
        link_prompt="Введите ссылку на публикацию:",
    ),
    "tiktok_views": OrderService(
        provider_service_id=639,
        platform="tiktok",
        title="TikTok-просмотры",
        unit_name="единицы",
        unit_price=10,
        minimum=100,
        intro=(
            "▶️ Просмотры на TikTok-видео.\n\n"
            "Внимание!! Гарантии на данную услугу нет! Претензии не принимаются"
        ),
        link_prompt="Введите ссылку на TikTok-видео:",
    ),
    "tiktok_likes": OrderService(
        provider_service_id=539,
        platform="tiktok",
        title="TikTok-лайки",
        unit_name="единицы",
        unit_price=100,
        minimum=50,
        intro=(
            "❤️ Лайки на TikTok-видео.\n\n"
            "Внимание!! Гарантии на данную услугу нет! Претензии не принимаются"
        ),
        link_prompt="Введите ссылку на TikTok-видео:",
    ),
}

COIN_PACKAGES = {
    "starter": CoinPackage(
        title="Стартовый пакет",
        stars_amount=50,
        coins_amount=50000,
        description="Подходит для первых заказов.",
    ),
    "boost": CoinPackage(
        title="Пакет Boost",
        stars_amount=100,
        coins_amount=100000,
        description="Увеличенный пакет с выгодным курсом.",
    ),
    "max": CoinPackage(
        title="Пакет Max",
        stars_amount=250,
        coins_amount=250000,
        description="Максимальный пакет для больших заказов.",
    ),
}
CUSTOM_STARS_MIN = 1
CUSTOM_STARS_MAX = 10000


class TelegramError(RuntimeError):
    pass


class TelegramAPI:
    def __init__(self, token: str) -> None:
        self.base_url = f"https://api.telegram.org/bot{token}/"

    def call(self, method: str, **payload: Any) -> Any:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            self.base_url + method,
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=45) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                result = json.loads(exc.read().decode("utf-8"))
                description = result.get("description", f"HTTP {exc.code}")
            except (ValueError, UnicodeDecodeError):
                description = f"HTTP {exc.code}"
            raise TelegramError(description) from None
        except (URLError, TimeoutError) as exc:
            raise TelegramError(f"Сетевая ошибка: {exc.reason if isinstance(exc, URLError) else exc}") from None

        if not result.get("ok"):
            raise TelegramError(result.get("description", "Telegram API вернул ошибку"))
        return result.get("result")

    def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
        *,
        disable_preview: bool = True,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": disable_preview,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self.call("sendMessage", **payload)

    def answer_callback(self, callback_id: str, text: str = "", *, alert: bool = False) -> None:
        self.call("answerCallbackQuery", callback_query_id=callback_id, text=text, show_alert=alert)

    def send_invoice(
        self,
        chat_id: int,
        title: str,
        description: str,
        payload: str,
        currency: str,
        prices: list[dict[str, Any]],
        **extra: Any,
    ) -> dict[str, Any]:
        invoice_payload: dict[str, Any] = {
            "chat_id": chat_id,
            "title": title,
            "description": description,
            "payload": payload,
            "currency": currency,
            "prices": prices,
        }
        invoice_payload.update(extra)
        return self.call("sendInvoice", **invoice_payload)

    def answer_pre_checkout_query(
        self,
        pre_checkout_query_id: str,
        ok: bool,
        error_message: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {"pre_checkout_query_id": pre_checkout_query_id, "ok": ok}
        if error_message:
            payload["error_message"] = error_message
        self.call("answerPreCheckoutQuery", **payload)


class FlyerError(RuntimeError):
    pass


class FlyerAPI:
    """Синхронный клиент двух разрешённых методов Flyer."""

    def __init__(self, key: str) -> None:
        self.key = key
        self.base_url = "https://api.flyerhubs.com/"

    def call(self, method: str, **params: Any) -> Any:
        try:
            response = requests.post(
                self.base_url + method,
                json={"key": self.key, **params},
                headers={"User-Agent": "flyerapi/1.0"},
                timeout=10,
            )
        except requests.RequestException as exc:
            raise FlyerError(f"Сетевая ошибка Flyer: {exc.__class__.__name__}") from None
        try:
            payload = response.json()
        except requests.JSONDecodeError:
            raise FlyerError(f"Flyer вернул некорректный ответ (HTTP {response.status_code})") from None

        if response.status_code >= 400:
            raise FlyerError(str(payload.get("error", f"HTTP {response.status_code}")))

        if payload.get("error"):
            raise FlyerError(str(payload["error"]))
        return payload.get("result")

    def get_tasks(
        self,
        user_id: int,
        language_code: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"user_id": user_id, "limit": min(max(limit, 1), 10)}
        if language_code:
            params["language_code"] = language_code
        result = self.call("get_tasks", **params)
        if result is None:
            return []
        if not isinstance(result, list):
            raise FlyerError("Flyer вернул неожиданный формат списка заданий")
        return [task for task in result if isinstance(task, dict) and task.get("signature")]

    def check_task(self, signature: str) -> str | None:
        result = self.call("check_task", signature=signature)
        return result if isinstance(result, str) else None

    def get_tasks_max(
        self,
        chat_id: int,
        user_id: int,
        user_locale: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"chat_id": chat_id, "user_id": user_id, "limit": min(max(limit, 1), 10)}
        if user_locale:
            params["user_locale"] = user_locale
        result = self.call("max/get_tasks", **params)
        if result is None:
            return []
        if not isinstance(result, list):
            raise FlyerError("Flyer MAX вернул неожиданный формат")
        return [task for task in result if isinstance(task, dict) and task.get("signature")]

    def check_task_max(self, signature: str) -> str | None:
        result = self.call("max/check_task", signature=signature)
        return result if isinstance(result, str) else None


class LinkniError(RuntimeError):
    pass


class BotoHubError(RuntimeError):
    pass


class BotoHubAPI:
    def __init__(self, api_url: str, token: str) -> None:
        self.api_url = api_url
        self.token = token

    def get_tasks(
        self,
        chat_id: int,
        *,
        segment: str | None = None,
        is_task: bool = False,
        skip: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"chat_id": chat_id}
        if segment:
            payload["age"] = segment
        if is_task:
            payload["is_task"] = True
            payload["skip"] = skip
        try:
            response = requests.post(
                self.api_url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Auth": self.token,
                    "User-Agent": "GoodKrutBot/1.0",
                },
                timeout=15,
            )
        except requests.RequestException as exc:
            raise BotoHubError(f"Сетевая ошибка BotoHub: {exc.__class__.__name__}") from None
        try:
            data = response.json()
        except requests.JSONDecodeError:
            raise BotoHubError(f"BotoHub вернул некорректный ответ (HTTP {response.status_code})") from None
        if response.status_code >= 400:
            if isinstance(data, dict) and data.get("error"):
                raise BotoHubError(str(data["error"]))
            raise BotoHubError(f"BotoHub API вернул HTTP {response.status_code}")
        if not isinstance(data, dict):
            raise BotoHubError("BotoHub вернул неожиданный формат")
        tasks = data.get("tasks")
        if tasks is not None and not isinstance(tasks, list):
            raise BotoHubError("BotoHub вернул неожиданный список заданий")
        return data


class SocProofError(RuntimeError):
    pass


class SocProofAPI:
    def __init__(self, api_url: str, key: str) -> None:
        self.api_url = api_url
        self.key = key

    def call(self, **params: Any) -> Any:
        try:
            response = requests.post(
                self.api_url,
                data={"key": self.key, **params},
                headers={"User-Agent": "GoodKrutBot/1.0"},
                timeout=15,
            )
        except requests.RequestException as exc:
            raise SocProofError(f"Сетевая ошибка Soc-proof: {exc.__class__.__name__}") from None
        try:
            payload = response.json()
        except requests.JSONDecodeError:
            raise SocProofError(f"Soc-proof вернул некорректный ответ (HTTP {response.status_code})") from None
        if response.status_code >= 400:
            if isinstance(payload, dict) and payload.get("error"):
                raise SocProofError(str(payload["error"]))
            raise SocProofError(f"Soc-proof API вернул HTTP {response.status_code}")
        if isinstance(payload, dict) and payload.get("error"):
            raise SocProofError(str(payload["error"]))
        return payload

    def add_order(self, service_id: int, link: str, quantity: int) -> int:
        payload = self.call(
            action="add",
            service=service_id,
            link=link,
            quantity=quantity,
        )
        if not isinstance(payload, dict) or "order" not in payload:
            raise SocProofError("Soc-proof не вернул номер заказа")
        try:
            return int(payload["order"])
        except (TypeError, ValueError):
            raise SocProofError("Soc-proof вернул некорректный номер заказа") from None


class LinkniAPI:
    def __init__(self, api_url: str, sell_code: str, app_url: str) -> None:
        self.api_url = api_url
        self.sell_code = sell_code
        self.app_url = app_url.rstrip("_")

    @staticmethod
    def sub_code(user_id: int, suffix: str | None = None) -> str:
        base = f"tg{user_id}"
        return f"{base}_{suffix}" if suffix else base

    def task_url(self, user_id: int, suffix: str | None = None) -> str:
        return f"{self.app_url}_{self.sub_code(user_id, suffix)}"

    @staticmethod
    def select_status(
        records: list[dict[str, Any]],
        user_id: int,
        sub_code: str,
    ) -> str | None:
        matching = [
            record
            for record in records
            if str(record.get("user_id")) == str(user_id)
            and str(record.get("sub_code", "")) == sub_code
        ]
        if not matching:
            return None
        latest = max(matching, key=lambda item: str(item.get("timestamp", "")))
        status = latest.get("status")
        return str(status) if status is not None else None

    def subscription_status(self, user_id: int) -> str | None:
        sub_code = self.sub_code(user_id)
        try:
            response = requests.get(
                self.api_url,
                params={
                    "code": self.sell_code,
                    "user_id": user_id,
                    "sub_code": sub_code,
                },
                headers={"User-Agent": "GoodKrutBot/1.0"},
                timeout=10,
            )
        except requests.RequestException as exc:
            raise LinkniError(f"Сетевая ошибка Linkni: {exc.__class__.__name__}") from None
        try:
            payload = response.json()
        except requests.JSONDecodeError:
            raise LinkniError(f"Linkni вернул некорректный ответ (HTTP {response.status_code})") from None
        if response.status_code >= 400:
            raise LinkniError(f"Linkni API вернул HTTP {response.status_code}")
        if not isinstance(payload, list):
            raise LinkniError("Linkni вернул неожиданный формат подписок")
        records = [record for record in payload if isinstance(record, dict)]
        return self.select_status(records, user_id, sub_code)


class LinkniWebhookServer:
    def __init__(self, database: "Database", host: str, port: int, path: str) -> None:
        self.database = database
        self.host = host
        self.port = port
        self.path = path if path.startswith("/") else f"/{path}"
        self.server = ThreadingHTTPServer((host, port), self._build_handler())
        self.thread = threading.Thread(target=self.server.serve_forever, name="linkni-webhook", daemon=True)

    def _build_handler(self) -> type[BaseHTTPRequestHandler]:
        database = self.database
        webhook_path = self.path

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                if self.path != webhook_path:
                    self.send_response(404)
                    self.end_headers()
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = 0
                raw_body = self.rfile.read(max(length, 0))
                try:
                    payload = json.loads(raw_body.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b'{"error":"invalid_json"}')
                    return
                if not isinstance(payload, dict):
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b'{"error":"invalid_payload"}')
                    return
                try:
                    user_id = int(payload["user_id"])
                    status = str(payload["status"])
                    sell_code = str(payload["sell_code"])
                    sub_code = str(payload["sub_code"])
                except (KeyError, TypeError, ValueError):
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b'{"error":"missing_fields"}')
                    return
                database.store_linkni_event(user_id, sell_code, sub_code, status, payload)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

            def log_message(self, format: str, *args: Any) -> None:
                logging.info("Linkni webhook: " + format, *args)

        return Handler

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        if self.thread.is_alive():
            self.thread.join(timeout=2)


class FlyerWebhookServer:
    def __init__(self, host: str, port: int, path: str) -> None:
        self.host = host
        self.port = port
        self.path = path if path.startswith("/") else f"/{path}"
        self.server = ThreadingHTTPServer((host, port), self._build_handler())
        self.thread = threading.Thread(target=self.server.serve_forever, name="flyer-webhook", daemon=True)

    def _build_handler(self) -> type[BaseHTTPRequestHandler]:
        webhook_path = self.path

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                if self.path != webhook_path:
                    self.send_response(404)
                    self.end_headers()
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = 0
                raw_body = self.rfile.read(max(length, 0))
                try:
                    payload = json.loads(raw_body.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    self.send_response(400)
                    self.end_headers()
                    return
                event_type = payload.get("type", "") if isinstance(payload, dict) else ""
                logging.info("Flyer webhook: type=%s payload=%s", event_type, raw_body.decode("utf-8", errors="replace")[:200])
                # Ответ {"status": true} для любого события (включая test)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(b'{"status":true}')

            def log_message(self, format: str, *args: Any) -> None:
                logging.info("Flyer webhook HTTP: " + format, *args)

        return Handler

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        if self.thread.is_alive():
            self.thread.join(timeout=2)


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self._create_schema()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT NOT NULL DEFAULT '',
                balance INTEGER NOT NULL DEFAULT 0,
                rub_balance INTEGER NOT NULL DEFAULT 0,
                inviter_id INTEGER,
                referral_confirmed INTEGER NOT NULL DEFAULT 0,
                last_bonus_date TEXT,
                bonus_streak INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS claims (
                user_id INTEGER NOT NULL,
                claim_key TEXT NOT NULL,
                amount INTEGER NOT NULL,
                claimed_at TEXT NOT NULL,
                PRIMARY KEY (user_id, claim_key)
            );

            CREATE TABLE IF NOT EXISTS promo_redemptions (
                user_id INTEGER NOT NULL,
                promo_code TEXT NOT NULL,
                redeemed_at TEXT NOT NULL,
                PRIMARY KEY (user_id, promo_code)
            );

            CREATE TABLE IF NOT EXISTS flyer_tasks (
                user_id INTEGER NOT NULL,
                task_key TEXT NOT NULL,
                signature TEXT NOT NULL,
                task_type TEXT NOT NULL DEFAULT '',
                name TEXT,
                links_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'incomplete',
                mandatory INTEGER NOT NULL DEFAULT 0,
                earn_eligible INTEGER NOT NULL DEFAULT 0,
                reward_credited INTEGER NOT NULL DEFAULT 0,
                first_seen_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, signature),
                UNIQUE (user_id, task_key)
            );

            CREATE TABLE IF NOT EXISTS orders (
                order_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                service_key TEXT NOT NULL,
                provider_service_id INTEGER,
                provider_order_id INTEGER,
                quantity INTEGER NOT NULL,
                target_link TEXT NOT NULL,
                total_coins INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS star_payments (
                telegram_payment_charge_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                payload TEXT NOT NULL,
                stars_amount INTEGER NOT NULL,
                coins_amount INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS linkni_events (
                user_id INTEGER NOT NULL,
                sell_code TEXT NOT NULL,
                sub_code TEXT NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                received_at TEXT NOT NULL,
                PRIMARY KEY (user_id, sell_code, sub_code)
            );

            CREATE TABLE IF NOT EXISTS earn_batches (
                user_id INTEGER PRIMARY KEY,
                batch_id TEXT NOT NULL,
                tasks_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS promos (
                code TEXT PRIMARY KEY,
                amount INTEGER NOT NULL,
                max_uses INTEGER NOT NULL DEFAULT 0,
                uses INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS utm_visits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                utm_source TEXT NOT NULL DEFAULT '',
                utm_medium TEXT NOT NULL DEFAULT '',
                utm_campaign TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            """
        )
        self._ensure_column("orders", "provider_service_id", "INTEGER")
        self._ensure_column("orders", "provider_order_id", "INTEGER")
        self._ensure_column("users", "banned", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("users", "mandatory_passed_at", "TEXT")
        self.connection.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(MOSCOW).isoformat(timespec="seconds")

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {
            str(row["name"])
            for row in self.connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def register_user(self, telegram_user: dict[str, Any], inviter_id: int | None = None) -> bool:
        user_id = int(telegram_user["id"])
        now = self._now()
        exists = self.connection.execute(
            "SELECT 1 FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        valid_inviter: int | None = None
        if not exists and inviter_id and inviter_id != user_id:
            inviter_exists = self.connection.execute(
                "SELECT 1 FROM users WHERE user_id = ?", (inviter_id,)
            ).fetchone()
            if inviter_exists:
                valid_inviter = inviter_id

        self.connection.execute(
            """
            INSERT INTO users (user_id, username, first_name, inviter_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                updated_at = excluded.updated_at
            """,
            (
                user_id,
                telegram_user.get("username"),
                telegram_user.get("first_name", ""),
                valid_inviter,
                now,
                now,
            ),
        )
        self.connection.commit()
        return not bool(exists)

    def get_user(self, user_id: int) -> sqlite3.Row:
        row = self.connection.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row is None:
            raise KeyError(user_id)
        return row

    def confirmed_referrals(self, user_id: int) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) AS total FROM users WHERE inviter_id = ? AND referral_confirmed = 1",
            (user_id,),
        ).fetchone()
        return int(row["total"])

    def confirm_referral(self, user_id: int) -> int | None:
        with self.connection:
            user = self.get_user(user_id)
            inviter_id = user["inviter_id"]
            if not inviter_id or user["referral_confirmed"]:
                return None
            changed = self.connection.execute(
                "UPDATE users SET referral_confirmed = 1, updated_at = ? "
                "WHERE user_id = ? AND referral_confirmed = 0",
                (self._now(), user_id),
            ).rowcount
            if not changed:
                return None
            self.connection.execute(
                "UPDATE users SET balance = balance + ?, updated_at = ? WHERE user_id = ?",
                (REFERRAL_REWARD, self._now(), inviter_id),
            )
            return int(inviter_id)

    def claim_daily_bonus(self, user_id: int) -> tuple[bool, int, int, int]:
        today = datetime.now(MOSCOW).date()
        with self.connection:
            user = self.get_user(user_id)
            last_bonus = (
                date.fromisoformat(user["last_bonus_date"])
                if user["last_bonus_date"]
                else None
            )
            if last_bonus == today:
                return False, 0, int(user["bonus_streak"]), int(user["balance"])
            streak = int(user["bonus_streak"]) + 1 if last_bonus == today - timedelta(days=1) else 1
            amount = min(500 + (streak - 1) * 100, 1000)
            self.connection.execute(
                """
                UPDATE users
                SET balance = balance + ?, last_bonus_date = ?, bonus_streak = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (amount, today.isoformat(), streak, self._now(), user_id),
            )
            balance = int(self.get_user(user_id)["balance"])
            return True, amount, streak, balance

    def claim_once(self, user_id: int, claim_key: str, amount: int) -> tuple[bool, int]:
        with self.connection:
            try:
                self.connection.execute(
                    "INSERT INTO claims (user_id, claim_key, amount, claimed_at) VALUES (?, ?, ?, ?)",
                    (user_id, claim_key, amount, self._now()),
                )
            except sqlite3.IntegrityError:
                return False, int(self.get_user(user_id)["balance"])
            self.connection.execute(
                "UPDATE users SET balance = balance + ?, updated_at = ? WHERE user_id = ?",
                (amount, self._now(), user_id),
            )
            return True, int(self.get_user(user_id)["balance"])

    def has_claim(self, user_id: int, claim_key: str) -> bool:
        return self.connection.execute(
            "SELECT 1 FROM claims WHERE user_id = ? AND claim_key = ?",
            (user_id, claim_key),
        ).fetchone() is not None

    def daily_claim_key(self, namespace: str) -> str:
        return f"{namespace}:{datetime.now(MOSCOW).date().isoformat()}"

    def store_linkni_event(
        self,
        user_id: int,
        sell_code: str,
        sub_code: str,
        status: str,
        payload: dict[str, Any],
    ) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO linkni_events (user_id, sell_code, sub_code, status, payload_json, received_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, sell_code, sub_code) DO UPDATE SET
                    status = excluded.status,
                    payload_json = excluded.payload_json,
                    received_at = excluded.received_at
                """,
                (
                    user_id,
                    sell_code,
                    sub_code,
                    status,
                    json.dumps(payload, ensure_ascii=False),
                    self._now(),
                ),
            )

    def latest_linkni_status(self, user_id: int, sell_code: str, sub_code: str) -> str | None:
        row = self.connection.execute(
            """
            SELECT status
            FROM linkni_events
            WHERE user_id = ? AND sell_code = ? AND sub_code = ?
            """,
            (user_id, sell_code, sub_code),
        ).fetchone()
        return str(row["status"]) if row else None

    def stats_snapshot(self) -> dict[str, int]:
        users = self.connection.execute("SELECT COUNT(*) AS total FROM users").fetchone()
        orders = self.connection.execute("SELECT COUNT(*) AS total FROM orders").fetchone()
        claims = self.connection.execute("SELECT COUNT(*) AS total FROM claims").fetchone()
        balance = self.connection.execute("SELECT COALESCE(SUM(balance), 0) AS total FROM users").fetchone()
        return {
            "users": int(users["total"]),
            "orders": int(orders["total"]),
            "claims": int(claims["total"]),
            "balance": int(balance["total"]),
        }

    def recent_users(self, limit: int = 10) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                """
                SELECT user_id, username, first_name, balance, updated_at
                FROM users
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        )

    def add_balance(self, user_id: int, amount: int) -> tuple[bool, int]:
        with self.connection:
            row = self.connection.execute(
                "SELECT balance FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if row is None:
                return False, 0
            self.connection.execute(
                "UPDATE users SET balance = balance + ?, updated_at = ? WHERE user_id = ?",
                (amount, self._now(), user_id),
            )
            return True, int(self.get_user(user_id)["balance"])

    def save_earn_batch(self, user_id: int, batch_id: str, tasks: list[dict[str, Any]]) -> None:
        now = self._now()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO earn_batches (user_id, batch_id, tasks_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    batch_id = excluded.batch_id,
                    tasks_json = excluded.tasks_json,
                    updated_at = excluded.updated_at
                """,
                (user_id, batch_id, json.dumps(tasks, ensure_ascii=False), now, now),
            )

    def get_earn_batch(self, user_id: int) -> tuple[str, list[dict[str, Any]]] | None:
        row = self.connection.execute(
            "SELECT batch_id, tasks_json FROM earn_batches WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            tasks = json.loads(str(row["tasks_json"]))
        except ValueError:
            tasks = []
        if not isinstance(tasks, list):
            tasks = []
        normalized = [task for task in tasks if isinstance(task, dict)]
        return str(row["batch_id"]), normalized

    def clear_earn_batch(self, user_id: int) -> None:
        with self.connection:
            self.connection.execute("DELETE FROM earn_batches WHERE user_id = ?", (user_id,))

    @staticmethod
    def flyer_task_key(signature: str) -> str:
        return hashlib.sha256(signature.encode("utf-8")).hexdigest()[:20]

    def upsert_flyer_tasks(
        self,
        user_id: int,
        tasks: list[dict[str, Any]],
        purpose: str,
    ) -> None:
        mandatory = 1 if purpose == "mandatory" else 0
        earn_eligible = 1 if purpose == "earn" else 0
        now = self._now()
        with self.connection:
            purpose_column = "mandatory" if purpose == "mandatory" else "earn_eligible"
            self.connection.execute(
                f"UPDATE flyer_tasks SET {purpose_column} = 0 WHERE user_id = ?",
                (user_id,),
            )
            for task in tasks:
                signature = str(task.get("signature", "")).strip()
                if not signature:
                    continue
                links = task.get("links")
                if not isinstance(links, list):
                    links = []
                links = [
                    str(link) for link in links
                    if isinstance(link, str) and link.startswith(("https://", "http://", "tg://"))
                ]
                self.connection.execute(
                    """
                    INSERT INTO flyer_tasks (
                        user_id, task_key, signature, task_type, name, links_json,
                        status, mandatory, earn_eligible, first_seen_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, signature) DO UPDATE SET
                        task_type = excluded.task_type,
                        name = excluded.name,
                        links_json = excluded.links_json,
                        status = excluded.status,
                        mandatory = MAX(flyer_tasks.mandatory, excluded.mandatory),
                        earn_eligible = MAX(flyer_tasks.earn_eligible, excluded.earn_eligible),
                        updated_at = excluded.updated_at
                    """,
                    (
                        user_id,
                        self.flyer_task_key(signature),
                        signature,
                        str(task.get("task", "")),
                        str(task["name"]) if task.get("name") is not None else None,
                        json.dumps(links, ensure_ascii=False),
                        str(task.get("status", "incomplete")),
                        mandatory,
                        earn_eligible,
                        now,
                        now,
                    ),
                )

    def get_flyer_tasks(self, user_id: int, purpose: str) -> list[sqlite3.Row]:
        column = "mandatory" if purpose == "mandatory" else "earn_eligible"
        return list(
            self.connection.execute(
                f"SELECT * FROM flyer_tasks WHERE user_id = ? AND {column} = 1 ORDER BY first_seen_at",
                (user_id,),
            ).fetchall()
        )

    def update_flyer_task(
        self,
        user_id: int,
        signature: str,
        status: str,
        reward: int,
    ) -> tuple[bool, int]:
        """Сохранить статус и атомарно начислить награду не более одного раза."""
        with self.connection:
            row = self.connection.execute(
                "SELECT earn_eligible, reward_credited FROM flyer_tasks "
                "WHERE user_id = ? AND signature = ?",
                (user_id, signature),
            ).fetchone()
            if row is None:
                return False, int(self.get_user(user_id)["balance"])
            credited = bool(
                status in FLYER_REWARDABLE_STATUSES
                and row["earn_eligible"]
                and not row["reward_credited"]
            )
            self.connection.execute(
                "UPDATE flyer_tasks SET status = ?, reward_credited = MAX(reward_credited, ?), "
                "updated_at = ? WHERE user_id = ? AND signature = ?",
                (status, int(credited), self._now(), user_id, signature),
            )
            if credited:
                self.connection.execute(
                    "UPDATE users SET balance = balance + ?, updated_at = ? WHERE user_id = ?",
                    (reward, self._now(), user_id),
                )
            return credited, int(self.get_user(user_id)["balance"])

    def redeem_promo(self, user_id: int, code: str) -> tuple[str, int, int]:
        normalized = code.strip().upper()
        # Check dynamic promos table first
        promo = self.connection.execute(
            "SELECT code, amount, max_uses, uses FROM promos WHERE code = ?",
            (normalized,),
        ).fetchone()
        if promo is None:
            # Fallback to hardcoded
            hardcoded = {"WELCOME": 1000, "START": 500}
            if normalized not in hardcoded:
                return "unknown", 0, int(self.get_user(user_id)["balance"])
            amount = hardcoded[normalized]
        else:
            amount = int(promo["amount"])
            if int(promo["max_uses"]) > 0 and int(promo["uses"]) >= int(promo["max_uses"]):
                return "expired", 0, int(self.get_user(user_id)["balance"])
        with self.connection:
            try:
                self.connection.execute(
                    "INSERT INTO promo_redemptions (user_id, promo_code, redeemed_at) VALUES (?, ?, ?)",
                    (user_id, normalized, self._now()),
                )
            except sqlite3.IntegrityError:
                return "used", 0, int(self.get_user(user_id)["balance"])
            self.connection.execute(
                "UPDATE users SET balance = balance + ?, updated_at = ? WHERE user_id = ?",
                (amount, self._now(), user_id),
            )
            if promo is not None:
                self.connection.execute(
                    "UPDATE promos SET uses = uses + 1 WHERE code = ?",
                    (normalized,),
                )
            return "ok", amount, int(self.get_user(user_id)["balance"])

    # --- CRM / Admin DB methods ---

    def find_user_by_username(self, username: str) -> sqlite3.Row | None:
        clean = username.strip().lstrip("@")
        return self.connection.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
            (clean,),
        ).fetchone()

    def user_orders(self, user_id: int, limit: int = 10) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                "SELECT * FROM orders WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        )

    def ban_user(self, user_id: int) -> bool:
        with self.connection:
            changed = self.connection.execute(
                "UPDATE users SET banned = 1, updated_at = ? WHERE user_id = ?",
                (self._now(), user_id),
            ).rowcount
            return bool(changed)

    def unban_user(self, user_id: int) -> bool:
        with self.connection:
            changed = self.connection.execute(
                "UPDATE users SET banned = 0, updated_at = ? WHERE user_id = ?",
                (self._now(), user_id),
            ).rowcount
            return bool(changed)

    def is_banned(self, user_id: int) -> bool:
        row = self.connection.execute(
            "SELECT banned FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return bool(row and int(row["banned"]))

    def mark_mandatory_passed(self, user_id: int) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE users SET mandatory_passed_at = ?, updated_at = ? WHERE user_id = ?",
                (self._now(), self._now(), user_id),
            )

    def mandatory_passed_at(self, user_id: int) -> str | None:
        row = self.connection.execute(
            "SELECT mandatory_passed_at FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row is None:
            return None
        return str(row["mandatory_passed_at"]) if row["mandatory_passed_at"] else None

    def set_balance(self, user_id: int, amount: int) -> tuple[bool, int]:
        with self.connection:
            row = self.connection.execute(
                "SELECT balance FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            if row is None:
                return False, 0
            self.connection.execute(
                "UPDATE users SET balance = ?, updated_at = ? WHERE user_id = ?",
                (amount, self._now(), user_id),
            )
            return True, amount

    def create_promo(self, code: str, amount: int, max_uses: int = 0) -> bool:
        try:
            self.connection.execute(
                "INSERT INTO promos (code, amount, max_uses, uses, created_at) VALUES (?, ?, ?, 0, ?)",
                (code.strip().upper(), amount, max_uses, self._now()),
            )
            self.connection.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def delete_promo(self, code: str) -> bool:
        with self.connection:
            changed = self.connection.execute(
                "DELETE FROM promos WHERE code = ?", (code.strip().upper(),)
            ).rowcount
            return bool(changed)

    def list_promos(self) -> list[sqlite3.Row]:
        return list(
            self.connection.execute("SELECT * FROM promos ORDER BY created_at DESC").fetchall()
        )

    def total_users(self) -> int:
        row = self.connection.execute("SELECT COUNT(*) AS total FROM users").fetchone()
        return int(row["total"])

    def active_users_today(self) -> int:
        today = datetime.now(MOSCOW).date().isoformat()
        row = self.connection.execute(
            "SELECT COUNT(*) AS total FROM users WHERE updated_at >= ?",
            (today,),
        ).fetchone()
        return int(row["total"])

    def top_referrers(self, limit: int = 10) -> list[tuple[int, str, int]]:
        rows = self.connection.execute(
            """
            SELECT u.user_id, u.username, COUNT(r.user_id) AS refs
            FROM users u
            JOIN users r ON r.inviter_id = u.user_id AND r.referral_confirmed = 1
            GROUP BY u.user_id
            ORDER BY refs DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [(int(r["user_id"]), str(r["username"] or ""), int(r["refs"])) for r in rows]

    def all_user_ids(self) -> list[int]:
        return [
            int(row["user_id"])
            for row in self.connection.execute("SELECT user_id FROM users").fetchall()
        ]

    def save_utm_visit(self, user_id: int, source: str, medium: str, campaign: str) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO utm_visits (user_id, utm_source, utm_medium, utm_campaign, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, source, medium, campaign, self._now()),
            )

    def utm_stats(self) -> list[tuple[str, str, str, int]]:
        rows = self.connection.execute(
            """
            SELECT utm_source, utm_medium, utm_campaign, COUNT(*) AS cnt
            FROM utm_visits
            GROUP BY utm_source, utm_medium, utm_campaign
            ORDER BY cnt DESC
            LIMIT 20
            """
        ).fetchall()
        return [(str(r["utm_source"]), str(r["utm_medium"]), str(r["utm_campaign"]), int(r["cnt"])) for r in rows]

    def utm_stats_today(self) -> list[tuple[str, str, str, int]]:
        today = datetime.now(MOSCOW).date().isoformat()
        rows = self.connection.execute(
            """
            SELECT utm_source, utm_medium, utm_campaign, COUNT(*) AS cnt
            FROM utm_visits
            WHERE created_at >= ?
            GROUP BY utm_source, utm_medium, utm_campaign
            ORDER BY cnt DESC
            LIMIT 20
            """,
            (today,),
        ).fetchall()
        return [(str(r["utm_source"]), str(r["utm_medium"]), str(r["utm_campaign"]), int(r["cnt"])) for r in rows]

    def utm_total(self) -> int:
        row = self.connection.execute("SELECT COUNT(*) AS total FROM utm_visits").fetchone()
        return int(row["total"])

    def create_order(
        self,
        user_id: int,
        service_key: str,
        provider_service_id: int,
        provider_order_id: int,
        quantity: int,
        target_link: str,
        total_coins: int,
    ) -> tuple[bool, int, int | None]:
        with self.connection:
            user = self.get_user(user_id)
            balance = int(user["balance"])
            if balance < total_coins:
                return False, balance, None
            self.connection.execute(
                "UPDATE users SET balance = balance - ?, updated_at = ? WHERE user_id = ?",
                (total_coins, self._now(), user_id),
            )
            cursor = self.connection.execute(
                """
                INSERT INTO orders (
                    user_id, service_key, provider_service_id, provider_order_id,
                    quantity, target_link, total_coins, status, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'processing', ?)
                """,
                (
                    user_id,
                    service_key,
                    provider_service_id,
                    provider_order_id,
                    quantity,
                    target_link,
                    total_coins,
                    self._now(),
                ),
            )
            return True, int(self.get_user(user_id)["balance"]), int(cursor.lastrowid)

    def apply_star_payment(
        self,
        user_id: int,
        charge_id: str,
        payload: str,
        stars_amount: int,
        coins_amount: int,
    ) -> tuple[bool, int]:
        with self.connection:
            exists = self.connection.execute(
                "SELECT 1 FROM star_payments WHERE telegram_payment_charge_id = ?",
                (charge_id,),
            ).fetchone()
            if exists:
                return False, int(self.get_user(user_id)["balance"])
            self.connection.execute(
                """
                INSERT INTO star_payments (
                    telegram_payment_charge_id, user_id, payload, stars_amount, coins_amount, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (charge_id, user_id, payload, stars_amount, coins_amount, self._now()),
            )
            self.connection.execute(
                """
                UPDATE users
                SET balance = balance + ?, updated_at = ?
                WHERE user_id = ?
                """,
                (coins_amount, self._now(), user_id),
            )
            return True, int(self.get_user(user_id)["balance"])


def inline_keyboard(rows: list[list[dict[str, str]]]) -> dict[str, Any]:
    return {"inline_keyboard": rows}


def callback_button(text: str, data: str) -> dict[str, str]:
    return {"text": text, "callback_data": data}


def url_button(text: str, url: str) -> dict[str, str]:
    return {"text": text, "url": url}


MAIN_KEYBOARD = {
    "keyboard": [
        ["Бесплатная накрутка 🔥", "Заработать монеты"],
        ["Ещё задания", "Профиль 😎"],
        ["🏆 Конкурс"],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
}


class Bot:
    def __init__(
        self,
        api: TelegramAPI,
        database: Database,
        username: str,
        flyer: FlyerAPI | None = None,
        linkni: LinkniAPI | None = None,
        socproof: SocProofAPI | None = None,
        botohub: BotoHubAPI | None = None,
    ) -> None:
        self.api = api
        self.db = database
        self.username = username
        self.flyer = flyer
        self.linkni = linkni
        self.socproof = socproof
        self.botohub = botohub
        self.pending_input: dict[int, str] = {}
        self.order_drafts: dict[int, dict[str, Any]] = {}
        self.running = True

    def stop(self, *_: Any) -> None:
        self.running = False

    def send(self, chat_id: int, text: str, markup: dict[str, Any] | None = None) -> None:
        self.api.send_message(chat_id, text, markup)

    def is_admin(self, user_id: int) -> bool:
        return user_id in ADMIN_IDS

    def main_keyboard(self, user_id: int) -> dict[str, Any]:
        rows = [row[:] for row in MAIN_KEYBOARD["keyboard"]]
        if self.is_admin(user_id):
            rows.append(["Админка"])
        return {
            "keyboard": rows,
            "resize_keyboard": True,
            "is_persistent": True,
        }

    @staticmethod
    def append_utm_to_text(text: str, utm_query: str) -> str:
        pattern = re.compile(r"https?://[^\s<>\"]+")

        def replace(match: re.Match[str]) -> str:
            url = match.group(0)
            parts = urlsplit(url)
            query = dict(parse_qsl(parts.query, keep_blank_values=True))
            for key, value in parse_qsl(utm_query, keep_blank_values=True):
                query[key] = value
            new_query = urlencode(query, doseq=True)
            return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))

        return pattern.sub(replace, text)

    def show_admin_menu(self, chat_id: int) -> None:
        self.send(
            chat_id,
            "🛠 <b>Админка</b>\n\n"
            "Управление ботом и пользователями.",
            inline_keyboard(
                [
                    [callback_button("📊 Статистика", "admin:stats")],
                    [callback_button("👥 Последние пользователи", "admin:recent")],
                    [callback_button("🏆 Топ рефереров", "admin:top_refs")],
                    [callback_button("🔎 Найти пользователя", "admin:user")],
                    [callback_button("💸 Начислить монеты", "admin:credit")],
                    [callback_button("📣 Рассылка", "admin:broadcast")],
                    [callback_button("🔗 UTM-ссылки", "admin:utm")],
                    [callback_button("🎟 Промокоды", "admin:promos")],
                ]
            ),
        )

    def show_admin_stats(self, chat_id: int) -> None:
        stats = self.db.stats_snapshot()
        active_today = self.db.active_users_today()
        self.send(
            chat_id,
            "📊 <b>Статистика</b>\n\n"
            f"Пользователей: <b>{stats['users']}</b>\n"
            f"Активных сегодня: <b>{active_today}</b>\n"
            f"Заказов: <b>{stats['orders']}</b>\n"
            f"Начислений/клеймов: <b>{stats['claims']}</b>\n"
            f"Суммарный баланс: <b>{stats['balance']}</b> монет",
            inline_keyboard([[callback_button("⬅️ В админку", "admin:menu")]]),
        )

    def show_admin_recent_users(self, chat_id: int) -> None:
        rows = self.db.recent_users(10)
        if not rows:
            self.send(chat_id, "Пользователей пока нет.")
            return
        lines = ["👥 <b>Последние пользователи</b>", ""]
        for row in rows:
            username = f"@{row['username']}" if row["username"] else "без username"
            lines.append(
                f"<code>{row['user_id']}</code> • {html.escape(str(row['first_name']))} • {username} • {int(row['balance'])} мон."
            )
        self.send(
            chat_id,
            "\n".join(lines),
            inline_keyboard([[callback_button("⬅️ В админку", "admin:menu")]]),
        )

    def show_admin_user(self, chat_id: int, target_user_id: int) -> None:
        try:
            user = self.db.get_user(target_user_id)
        except KeyError:
            self.send(chat_id, "Пользователь не найден.")
            return
        username = f"@{user['username']}" if user["username"] else "без username"
        banned = bool(int(user.get("banned", 0)))
        referrals = self.db.confirmed_referrals(target_user_id)
        orders = self.db.user_orders(target_user_id, 5)
        orders_text = ""
        if orders:
            orders_text = "\n\n<b>Последние заказы:</b>\n"
            for order in orders:
                orders_text += (
                    f"#{order['order_id']} • {order['service_key']} • "
                    f"{order['quantity']} шт. • {order['total_coins']} мон. • {order['status']}\n"
                )
        self.send(
            chat_id,
            "🔎 <b>Пользователь</b>\n\n"
            f"ID: <code>{user['user_id']}</code>\n"
            f"Имя: {html.escape(str(user['first_name']))}\n"
            f"Username: {username}\n"
            f"Баланс: <b>{int(user['balance'])}</b> монет\n"
            f"Рефералов: <b>{referrals}</b>\n"
            f"Бан: <b>{'🚫 да' if banned else '✅ нет'}</b>\n"
            f"Зарегистрирован: {html.escape(str(user['created_at']))}\n"
            f"Обновлён: {html.escape(str(user['updated_at']))}"
            f"{orders_text}",
            inline_keyboard(
                [
                    [
                        callback_button("💸 Начислить", f"admin:credit_to:{target_user_id}"),
                        callback_button("💳 Установить", f"admin:setbal:{target_user_id}"),
                    ],
                    [
                        callback_button(
                            "🔓 Разбанить" if banned else "🚫 Забанить",
                            f"admin:unban:{target_user_id}" if banned else f"admin:ban:{target_user_id}",
                        ),
                    ],
                    [callback_button("⬅️ В админку", "admin:menu")],
                ]
            ),
        )

    def broadcast_message(self, text: str, utm: str | None = None) -> tuple[int, int]:
        delivered = 0
        failed = 0
        payload = text if "<" in text else html.escape(text)
        if utm:
            payload = self.append_utm_to_text(payload, utm)
        for uid in self.db.all_user_ids():
            try:
                self.send(uid, payload)
                delivered += 1
                if ADMIN_BROADCAST_BATCH_DELAY > 0:
                    time.sleep(ADMIN_BROADCAST_BATCH_DELAY)
            except TelegramError:
                failed += 1
        return delivered, failed

    def show_admin_top_refs(self, chat_id: int) -> None:
        top = self.db.top_referrers(10)
        if not top:
            self.send(chat_id, "Пока нет рефералов.", inline_keyboard([[callback_button("⬅️ В админку", "admin:menu")]]))
            return
        lines = ["🏆 <b>Топ рефереров</b>", ""]
        for i, (uid, uname, refs) in enumerate(top, 1):
            name = f"@{uname}" if uname else f"<code>{uid}</code>"
            lines.append(f"{i}. {name} — <b>{refs}</b> реф.")
        self.send(
            chat_id,
            "\n".join(lines),
            inline_keyboard([[callback_button("⬅️ В админку", "admin:menu")]]),
        )

    def show_admin_promos(self, chat_id: int) -> None:
        promos = self.db.list_promos()
        lines = ["🎟 <b>Промокоды</b>", ""]
        if not promos:
            lines.append("Нет активных промокодов.")
        else:
            for p in promos:
                max_uses = int(p["max_uses"])
                uses = int(p["uses"])
                limit_text = f"{uses}/{max_uses}" if max_uses > 0 else f"{uses}/∞"
                lines.append(
                    f"<code>{p['code']}</code> — {int(p['amount'])} мон. • {limit_text}"
                )
        lines.append("")
        lines.append("Для создания: отправьте команду\n<code>/addpromo КОД СУММА [ЛИМИТ]</code>")
        lines.append("Для удаления: <code>/delpromo КОД</code>")
        self.send(
            chat_id,
            "\n".join(lines),
            inline_keyboard([[callback_button("⬅️ В админку", "admin:menu")]]),
        )

    def show_home(self, chat_id: int) -> None:
        # Telegram не позволяет прикрепить inline- и reply-клавиатуру к одному
        # сообщению, поэтому клавиатура меню активируется отдельной репликой.
        self.send(chat_id, "Выберите нужный раздел 👇", MAIN_KEYBOARD)
        text = (
            "Добро пожаловать в сервис бесплатной накрутки! 🚀\n\n"
            "С нашим ботом ты можешь бесплатно накрутить:\n\n"
            "👥 Подписчиков\n"
            "👁 Просмотры\n"
            "🔥 Реакции\n"
            "💰 Зарабатывай монеты:\n"
            "— Заходи каждый день за бонусом\n"
            "— Приглашай друзей по реферальной ссылке\n"
            "— Активируй заказы и прокачивай свой канал!"
        )
        docs = inline_keyboard(
            [
                [callback_button("📄 Ознакомьтесь с документами:", "docs")],
                [callback_button("Политика конфиденциальности ↗", "privacy")],
                [callback_button("Пользовательское соглашение ↗", "terms")],
            ]
        )
        self.send(chat_id, text, docs)

    @staticmethod
    def flyer_action_text(task_type: str) -> str:
        return {
            "start bot": "➕ Запустить бота",
            "subscribe channel": "➕ Подписаться",
            "give boost": "➕ Проголосовать",
            "follow link": "➕ Перейти",
            "perform action": "➕ Выполнить действие",
            "view posts": "➕ Посмотреть публикации",
        }.get(task_type, "➕ Выполнить задание")

    def flyer_keyboard(self, tasks: list[sqlite3.Row], purpose: str) -> dict[str, Any]:
        buttons: list[dict[str, str]] = []
        for task in tasks:
            if task["status"] not in FLYER_INCOMPLETE_STATUSES:
                continue
            try:
                links = json.loads(task["links_json"])
            except (TypeError, ValueError):
                links = []
            action = self.flyer_action_text(task["task_type"])
            name = (task["name"] or "").strip()
            label = f"{action}: {name}" if name else action
            for link in links:
                buttons.append(url_button(label[:64], link))

        rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
        callback_data = "flyer_free_check" if purpose == "mandatory" else "flyer_earn_check"
        rows.append([callback_button("✅ Проверить", callback_data)])
        return inline_keyboard(rows)

    def load_flyer_tasks(
        self,
        user_id: int,
        language_code: str | None,
        purpose: str,
        limit: int = 10,
    ) -> list[sqlite3.Row]:
        if self.flyer is None:
            return []
        tasks = self.flyer.get_tasks(user_id, language_code, limit=limit)
        tasks = tasks[:limit]
        self.db.upsert_flyer_tasks(user_id, tasks, purpose)
        return self.db.get_flyer_tasks(user_id, purpose)

    @staticmethod
    def is_telegram_url(value: str) -> bool:
        try:
            parsed = urlparse(value.strip())
        except ValueError:
            return False
        if parsed.scheme.lower() == "tg":
            return True
        host = (parsed.hostname or "").lower().removeprefix("www.")
        return parsed.scheme.lower() in {"http", "https"} and host in {
            "t.me",
            "telegram.me",
            "telegram.dog",
        }

    @classmethod
    def is_telegram_flyer_task(cls, task: dict[str, Any]) -> bool:
        links = task.get("links")
        return bool(
            isinstance(links, list)
            and links
            and all(isinstance(link, str) and cls.is_telegram_url(link) for link in links)
        )

    def grant_free_access(self, chat_id: int, user_id: int) -> None:
        inviter_id = self.db.confirm_referral(user_id)
        if inviter_id:
            try:
                self.send(inviter_id, f"🎉 Ваш реферал подтверждён. +{REFERRAL_REWARD} монет!")
            except TelegramError:
                logging.info("Не удалось уведомить реферера %s", inviter_id)
        # Записать время прохождения
        self.db.mark_mandatory_passed(user_id)
        # Награда за первую обязательную подписку
        claimed, balance = self.db.claim_once(user_id, "mandatory_first", MANDATORY_REWARD)
        reward_text = f"\n\n🎁 Бонус: <b>+{MANDATORY_REWARD} монет</b>! Баланс: <b>{balance}</b>" if claimed else ""
        self.send(
            chat_id,
            "✅ <b>Доступ открыт</b>\n\n"
            "Бесплатный раздел уже доступен.\n"
            f"Выберите нужную услугу ниже:{reward_text}",
            inline_keyboard(
                [
                    [callback_button("📢 Telegram", "paid_platform:telegram")],
                    [callback_button("🎵 TikTok", "paid_platform:tiktok")],
                ]
            ),
        )

    def _mandatory_cached(self, user_id: int) -> bool:
        """Вернуть True если обязательная подписка пройдена менее MANDATORY_CACHE_HOURS назад."""
        passed_at = self.db.mandatory_passed_at(user_id)
        if not passed_at:
            return False
        try:
            dt = datetime.fromisoformat(passed_at)
        except ValueError:
            return False
        elapsed = datetime.now(MOSCOW) - dt
        return elapsed < timedelta(hours=MANDATORY_CACHE_HOURS)

    def begin_order(self, chat_id: int, user_id: int, service_key: str) -> None:
        service = ORDER_SERVICES.get(service_key)
        if service is None:
            self.send(chat_id, "Неизвестный тариф.")
            return
        self.order_drafts.pop(user_id, None)
        self.pending_input[user_id] = f"order_quantity:{service_key}"
        self.send(
            chat_id,
            f"{service.intro}\n\n"
            f"Стоимость 1 {service.unit_name} — <b>{service.unit_price} монет</b>\n\n"
            f"Введите количество (минимум {service.minimum}):",
            {"force_reply": True, "selective": True},
        )

    @staticmethod
    def is_tiktok_url(value: str) -> bool:
        try:
            parsed = urlparse(value.strip())
        except ValueError:
            return False
        host = (parsed.hostname or "").lower().removeprefix("www.")
        return parsed.scheme.lower() in {"http", "https"} and host in {
            "tiktok.com",
            "vm.tiktok.com",
            "vt.tiktok.com",
            "m.tiktok.com",
        }

    @classmethod
    def normalize_target_link(cls, value: str, service: OrderService) -> str | None:
        candidate = value.strip()
        if service.platform == "telegram" and candidate.startswith("@") and len(candidate) > 1:
            return f"https://t.me/{candidate[1:]}"
        if service.platform == "telegram" and cls.is_telegram_url(candidate):
            return candidate
        if service.platform == "tiktok" and cls.is_tiktok_url(candidate):
            return candidate
        return None

    @staticmethod
    def paid_platform_label(platform: str) -> str:
        return "Telegram" if platform == "telegram" else "TikTok"

    @staticmethod
    def earn_batch_claim_key(batch_id: str) -> str:
        return f"earn_batch:{batch_id}"

    @staticmethod
    def is_supported_earn_url(value: str) -> bool:
        return value.startswith(("https://", "http://", "tg://"))

    @staticmethod
    def is_linkni_url(value: str) -> bool:
        """Linkni mini-app ссылки — не прямые подписки на каналы."""
        return "linkni" in value.lower()

    def build_earn_batch(self, user_id: int, language_code: str | None) -> tuple[str, list[dict[str, Any]]]:
        batch_id = hashlib.sha256(
            f"{user_id}:{datetime.now(MOSCOW).isoformat(timespec='seconds')}:{time.time_ns()}".encode("utf-8")
        ).hexdigest()[:16]
        tasks: list[dict[str, Any]] = []
        seen_urls: set[str] = set()

        # Приоритет 1: BotoHub (обычный режим — все ссылки сразу, по сегментам)
        if self.botohub is not None:
            for segment in ("c1", "c2", "c3", "c4", "c5", "c6"):
                if len(tasks) >= EARN_BATCH_SIZE:
                    break
                try:
                    payload = self.botohub.get_tasks(user_id, segment=segment)
                except BotoHubError as exc:
                    logging.warning("BotoHub get_tasks (%s): %s", segment, exc)
                    continue
                if bool(payload.get("skip")) or bool(payload.get("completed")):
                    continue
                for link in payload.get("tasks") or []:
                    if len(tasks) >= EARN_BATCH_SIZE:
                        break
                    normalized = str(link).strip()
                    if not normalized or normalized in seen_urls or not self.is_supported_earn_url(normalized):
                        continue
                    if self.is_linkni_url(normalized):
                        continue
                    tasks.append(
                        {
                            "provider": "botohub",
                            "segment": segment,
                            "url": normalized,
                        }
                    )
                    seen_urls.add(normalized)

        # Приоритет 2: Flyer (Telegram)
        if self.flyer is not None and len(tasks) < EARN_BATCH_SIZE:
            try:
                flyer_tasks = self.load_flyer_tasks(user_id, language_code, "earn", limit=10)
            except FlyerError as exc:
                logging.warning("Flyer get_tasks (earn batch): %s", exc)
                flyer_tasks = []
            for task in flyer_tasks:
                if len(tasks) >= EARN_BATCH_SIZE:
                    break
                if str(task["status"]) not in FLYER_INCOMPLETE_STATUSES:
                    continue
                try:
                    links = json.loads(task["links_json"])
                except (TypeError, ValueError):
                    links = []
                if not isinstance(links, list):
                    links = []
                for link in links:
                    if len(tasks) >= EARN_BATCH_SIZE:
                        break
                    normalized = str(link).strip()
                    if not normalized or normalized in seen_urls or not self.is_supported_earn_url(normalized):
                        continue
                    tasks.append(
                        {
                            "provider": "flyer",
                            "signature": str(task["signature"]),
                            "url": normalized,
                        }
                    )
                    seen_urls.add(normalized)

        # Приоритет 3: Flyer MAX
        if self.flyer is not None and len(tasks) < EARN_BATCH_SIZE:
            try:
                max_tasks = self.flyer.get_tasks_max(
                    chat_id=user_id, user_id=user_id,
                    user_locale=language_code, limit=10,
                )
            except FlyerError as exc:
                logging.warning("Flyer MAX get_tasks: %s", exc)
                max_tasks = []
            for task in max_tasks:
                if len(tasks) >= EARN_BATCH_SIZE:
                    break
                links = task.get("links")
                if not isinstance(links, list):
                    links = []
                for link in links:
                    if len(tasks) >= EARN_BATCH_SIZE:
                        break
                    normalized = str(link).strip()
                    if not normalized or normalized in seen_urls or not self.is_supported_earn_url(normalized):
                        continue
                    tasks.append(
                        {
                            "provider": "flyer_max",
                            "signature": str(task["signature"]),
                            "url": normalized,
                        }
                    )
                    seen_urls.add(normalized)

        tasks = tasks[:EARN_BATCH_SIZE]
        providers_count = {}
        for t in tasks:
            p = t.get("provider", "?")
            providers_count[p] = providers_count.get(p, 0) + 1
        logging.info("Earn batch for %s: total=%d breakdown=%s", user_id, len(tasks), providers_count)
        return batch_id, tasks

    def render_earn_batch(self, chat_id: int, batch_id: str, tasks: list[dict[str, Any]]) -> None:
        rows = [
            [url_button(f"Спонсор {index}", str(task["url"]))]
            for index, task in enumerate(tasks, start=1)
        ]
        rows.append([callback_button("✅ Проверить", "earn_verify")])
        count = len(tasks)
        reward = count * EARN_TASK_PRICE
        sponsors_word = "спонсор" if count == 1 else "спонсоров" if count >= 5 else "спонсора"
        self.send(
            chat_id,
            "💰 <b>Заработать монеты</b>\n\n"
            "Подпишись на спонсоров и забери награду.\n"
            f"За каждого спонсора — <b>{EARN_TASK_PRICE} монет</b>.\n\n"
            f"<b>{count} {sponsors_word} • {reward} монет</b>",
            inline_keyboard(rows),
        )

    def show_earn(self, chat_id: int, user_id: int, language_code: str | None, *, force_new: bool = False) -> None:
        current = None if force_new else self.db.get_earn_batch(user_id)
        if current is not None:
            batch_id, tasks = current
            if tasks:
                self.render_earn_batch(chat_id, batch_id, tasks)
                return
        batch_id, tasks = self.build_earn_batch(user_id, language_code)
        if not tasks:
            self.db.clear_earn_batch(user_id)
            self.send(
                chat_id,
                "Сейчас нет доступных заданий. Попробуйте позже.",
                inline_keyboard([[callback_button("📋 Ещё задания", "linkni_task")]]),
            )
            return
        self.db.save_earn_batch(user_id, batch_id, tasks)
        self.render_earn_batch(chat_id, batch_id, tasks)

    def verify_earn_batch(self, chat_id: int, user_id: int, language_code: str | None) -> None:
        current = self.db.get_earn_batch(user_id)
        if current is None:
            self.show_earn(chat_id, user_id, language_code, force_new=True)
            return
        batch_id, tasks = current
        if not tasks:
            self.db.clear_earn_batch(user_id)
            self.send(
                chat_id,
                "Сейчас нет доступных заданий. Попробуйте позже.",
                inline_keyboard([[callback_button("📋 Ещё задания", "linkni_task")]]),
            )
            return

        for task in tasks:
            provider = str(task.get("provider", ""))
            if provider == "flyer":
                if self.flyer is None:
                    self.send(chat_id, "Flyer не настроен.")
                    return
                status = self.flyer.check_task(str(task["signature"]))
                if status is None:
                    self.send(chat_id, "Не удалось проверить Flyer. Попробуйте ещё раз.")
                    return
                self.db.update_flyer_task(user_id, str(task["signature"]), status, 0)
                if status in FLYER_INCOMPLETE_STATUSES:
                    self.render_earn_batch(chat_id, batch_id, tasks)
                    return
            elif provider == "flyer_max":
                if self.flyer is None:
                    self.send(chat_id, "Flyer не настроен.")
                    return
                status = self.flyer.check_task_max(str(task["signature"]))
                if status is None:
                    self.send(chat_id, "Не удалось проверить задание. Попробуйте ещё раз.")
                    return
                if status in FLYER_INCOMPLETE_STATUSES:
                    self.render_earn_batch(chat_id, batch_id, tasks)
                    return
            elif provider == "botohub":
                if self.botohub is None:
                    self.send(chat_id, "BotoHub не настроен.")
                    return
                payload = self.botohub.get_tasks(user_id, segment=str(task.get("segment") or ""))
                remaining = {
                    str(link).strip()
                    for link in (payload.get("tasks") or [])
                    if isinstance(link, str)
                }
                if not bool(payload.get("completed")) and str(task["url"]) in remaining:
                    self.render_earn_batch(chat_id, batch_id, tasks)
                    return
            elif provider == "linkni":
                if self.linkni is None:
                    self.send(chat_id, "Linkni не настроен.")
                    return
                status = self.db.latest_linkni_status(
                    user_id,
                    str(task["sell_code"]),
                    str(task["sub_code"]),
                )
                if status != "subscribed":
                    self.render_earn_batch(chat_id, batch_id, tasks)
                    return

        reward = len(tasks) * EARN_TASK_PRICE
        claimed, balance = self.db.claim_once(
            user_id,
            self.earn_batch_claim_key(batch_id),
            reward,
        )
        self.db.clear_earn_batch(user_id)
        if claimed:
            self.send(
                chat_id,
                f"✅ Вам начислено <b>{reward} монет</b>.\n"
                f"Баланс: <b>{balance} монет</b>.",
                inline_keyboard([[callback_button("Заработать ещё", "earn_more")]]),
            )
        else:
            self.send(
                chat_id,
                "Награда за этот блок уже была выдана.",
                inline_keyboard([[callback_button("Заработать ещё", "earn_more")]]),
            )

    def show_paid_menu(self, chat_id: int) -> None:
        self.send(
            chat_id,
            "⭐ <b>Платные услуги</b>\n\n"
            "Выберите платформу и откройте каталог доступных услуг.\n\n"
            "Оплата монет доступна через <b>Telegram Stars</b>.",
            inline_keyboard(
                [
                    [callback_button("📢 Telegram", "paid_platform:telegram")],
                    [callback_button("🎵 TikTok", "paid_platform:tiktok")],
                    [callback_button("💳 Купить монеты за Stars", "buy_coins")],
                ]
            ),
        )

    def show_paid_platform(self, chat_id: int, platform: str) -> None:
        if platform == "telegram":
            text = "📢 <b>Telegram</b>\n\nВыберите услугу:"
            rows = [
                [callback_button("👥 Подписчики • 30 мон.", "order_type:followers_basic")],
                [callback_button("💎 Подписчики премиум • 400 мон.", "order_type:followers_premium")],
                [callback_button("👁 Просмотры • 2 мон.", "order_type:views")],
                [callback_button("🔥 Реакции • 15 мон.", "order_type:reactions")],
            ]
        else:
            text = "🎵 <b>TikTok</b>\n\nВыберите категорию:"
            rows = [
                [callback_button("Просмотры 👁", "order_type:tiktok_views")],
                [callback_button("Лайки ❤️", "order_type:tiktok_likes")],
            ]
        self.send(chat_id, text, inline_keyboard(rows))

    def show_coin_packages(self, chat_id: int) -> None:
        rows: list[list[dict[str, str]]] = []
        for key, package in COIN_PACKAGES.items():
            rows.append(
                [
                    callback_button(
                        f"{package.title} • {package.coins_amount} монет • {package.stars_amount} Stars",
                        f"buy_coins:{key}",
                    )
                ]
            )
        rows.append([callback_button("✍️ Свое количество Stars", "buy_coins_custom")])
        self.send(
            chat_id,
            "💳 <b>Покупка монет</b>\n\n"
            "Выберите пакет пополнения. Оплата проходит через <b>Telegram Stars</b>, "
            "а монеты зачисляются автоматически после успешного платежа.",
            inline_keyboard(rows),
        )

    def send_coin_invoice(self, chat_id: int, package_key: str) -> None:
        package = COIN_PACKAGES.get(package_key)
        if package is None:
            self.send(chat_id, "Пакет оплаты не найден.")
            return
        self.api.send_invoice(
            chat_id=chat_id,
            title=package.title,
            description=f"{package.description}\nНачисление: {package.coins_amount} монет.",
            payload=f"coins:{package_key}",
            currency="XTR",
            prices=[{"label": package.title, "amount": package.stars_amount}],
            start_parameter=f"coins-{package_key}",
        )

    def request_custom_stars_amount(self, chat_id: int, user_id: int) -> None:
        self.pending_input[user_id] = "buy_coins_custom"
        self.send(
            chat_id,
            "Введите количество <b>Stars</b> для пополнения.\n\n"
            f"Курс: <b>1 Star = 1000 монет</b>\n"
            f"Диапазон: от <b>{CUSTOM_STARS_MIN}</b> до <b>{CUSTOM_STARS_MAX}</b> Stars.",
            {"force_reply": True, "selective": True},
        )

    def send_custom_coin_invoice(self, chat_id: int, stars_amount: int) -> None:
        coins_amount = stars_amount * 1000
        self.api.send_invoice(
            chat_id=chat_id,
            title="Пополнение монет",
            description=(
                f"Пополнение на {stars_amount} Stars.\n"
                f"Начисление: {coins_amount} монет."
            ),
            payload=f"coins_custom:{stars_amount}",
            currency="XTR",
            prices=[{"label": f"{stars_amount} Stars", "amount": stars_amount}],
            start_parameter=f"coins-custom-{stars_amount}",
        )

    def handle_order_input(self, chat_id: int, user_id: int, text: str, pending: str) -> None:
        stage, _, service_key = pending.partition(":")
        service = ORDER_SERVICES.get(service_key)
        if service is None:
            self.pending_input.pop(user_id, None)
            self.order_drafts.pop(user_id, None)
            self.send(chat_id, "Тариф больше недоступен. Выберите услугу заново.")
            return

        if stage == "order_quantity":
            try:
                quantity = int(text)
            except ValueError:
                quantity = 0
            if quantity < service.minimum or quantity > 1_000_000:
                self.send(
                    chat_id,
                    f"Введите целое число от {service.minimum} до 1 000 000:",
                    {"force_reply": True, "selective": True},
                )
                return
            total = quantity * service.unit_price
            balance = int(self.db.get_user(user_id)["balance"])
            if balance < total:
                self.pending_input.pop(user_id, None)
                self.send(
                    chat_id,
                    f"Недостаточно монет. Стоимость заказа: <b>{total}</b>, "
                    f"ваш баланс: <b>{balance}</b>.\n\n"
                    "Заработайте монеты и попробуйте снова.",
                )
                return
            self.order_drafts[user_id] = {
                "service_key": service_key,
                "quantity": quantity,
                "total": total,
            }
            self.pending_input[user_id] = f"order_link:{service_key}"
            self.send(
                chat_id,
                f"Количество: <b>{quantity}</b>\n"
                f"Стоимость: <b>{total} монет</b>\n\n"
                f"{service.link_prompt}",
                {"force_reply": True, "selective": True},
            )
            return

        target_link = self.normalize_target_link(text, service)
        if target_link is None:
            example = (
                "https://t.me/channel или @channel"
                if service.platform == "telegram"
                else "https://www.tiktok.com/@username/video/123456789"
            )
            self.send(
                chat_id,
                f"Нужна ссылка формата {example}. Попробуйте ещё раз:",
                {"force_reply": True, "selective": True},
            )
            return
        draft = self.order_drafts.get(user_id)
        if not draft or draft.get("service_key") != service_key:
            self.pending_input.pop(user_id, None)
            self.send(chat_id, "Черновик заказа устарел. Выберите услугу заново.")
            return
        draft["target_link"] = target_link
        self.pending_input.pop(user_id, None)
        self.send(
            chat_id,
            "📋 <b>Проверьте заказ</b>\n\n"
            f"Услуга: {service.title}\n"
            f"Количество: <b>{draft['quantity']}</b>\n"
            f"Ссылка: {html.escape(target_link)}\n"
            f"Стоимость: <b>{draft['total']} монет</b>",
            inline_keyboard(
                [[
                    callback_button("✅ Подтвердить", "order_confirm"),
                    callback_button("❌ Отменить", "order_cancel"),
                ]]
            ),
        )

    def handle_admin_input(self, chat_id: int, user_id: int, text: str, pending: str) -> None:
        self.pending_input.pop(user_id, None)
        if pending == "admin:find_user":
            # Search by ID or username
            if text.isdigit():
                self.show_admin_user(chat_id, int(text))
            elif text.startswith("@"):
                row = self.db.find_user_by_username(text)
                if row:
                    self.show_admin_user(chat_id, int(row["user_id"]))
                else:
                    self.send(chat_id, "Пользователь не найден.")
            else:
                row = self.db.find_user_by_username(text)
                if row:
                    self.show_admin_user(chat_id, int(row["user_id"]))
                elif text.isdigit():
                    self.show_admin_user(chat_id, int(text))
                else:
                    self.send(chat_id, "Введите ID (число) или @username.")
        elif pending.startswith("admin:credit_amount:"):
            target_id = int(pending.split(":")[2])
            try:
                amount = int(text)
            except ValueError:
                self.send(chat_id, "Введите целое число.")
                self.pending_input[user_id] = pending
                return
            ok, balance = self.db.add_balance(target_id, amount)
            if ok:
                self.send(chat_id, f"✅ Начислено {amount} монет.\nНовый баланс: {balance} монет.")
                try:
                    self.send(target_id, f"💰 Вам начислено <b>{amount}</b> монет от администрации.\nБаланс: <b>{balance}</b> монет.")
                except TelegramError:
                    pass
            else:
                self.send(chat_id, "Пользователь не найден.")
        elif pending.startswith("admin:setbal_amount:"):
            target_id = int(pending.split(":")[2])
            try:
                amount = int(text)
            except ValueError:
                self.send(chat_id, "Введите целое число.")
                self.pending_input[user_id] = pending
                return
            if amount < 0:
                self.send(chat_id, "Баланс не может быть отрицательным.")
                self.pending_input[user_id] = pending
                return
            ok, new_bal = self.db.set_balance(target_id, amount)
            if ok:
                self.send(chat_id, f"✅ Баланс установлен: {new_bal} монет.")
            else:
                self.send(chat_id, "Пользователь не найден.")
        elif pending == "admin:broadcast_text":
            delivered, failed = self.broadcast_message(text)
            self.send(chat_id, f"📣 Рассылка завершена.\nДоставлено: {delivered}\nОшибок: {failed}")
        elif pending == "admin:utm_source":
            self.pending_input[user_id] = f"admin:utm_campaign:{text.strip()}"
            self.send(
                chat_id,
                "Введите название кампании (utm_campaign).\n"
                "Например: <code>promo_august</code>, <code>launch</code>, <code>test</code>",
                {"force_reply": True, "selective": True},
            )
        elif pending.startswith("admin:utm_campaign:"):
            source = pending.partition("admin:utm_campaign:")[2]
            campaign = text.strip()
            base = f"https://t.me/{self.username}?start=utm"
            lines = [
                "🔗 <b>UTM-ссылки готовы</b>\n",
                f"Источник: <b>{html.escape(source)}</b>",
                f"Кампания: <b>{html.escape(campaign)}</b>\n",
                f"📱 Соцсети:\n<code>{base}_{source}_social_{campaign}</code>\n",
                f"💬 Чаты:\n<code>{base}_{source}_chat_{campaign}</code>\n",
                f"📢 Реклама:\n<code>{base}_{source}_cpc_{campaign}</code>\n",
                f"🔗 Общая:\n<code>{base}_{source}_direct_{campaign}</code>",
            ]
            self.send(
                chat_id,
                "\n".join(lines),
                inline_keyboard([
                    [callback_button("📊 Статистика UTM", "admin:utm_stats")],
                    [callback_button("⬅️ В админку", "admin:menu")],
                ]),
            )

    def handle_addpromo(self, chat_id: int, text: str) -> None:
        parts = text.split(maxsplit=3)
        # /addpromo CODE AMOUNT [MAX_USES]
        if len(parts) < 3:
            self.send(chat_id, "Формат: <code>/addpromo КОД СУММА [ЛИМИТ]</code>")
            return
        code = parts[1].upper()
        try:
            amount = int(parts[2])
        except ValueError:
            self.send(chat_id, "Сумма должна быть числом.")
            return
        max_uses = 0
        if len(parts) >= 4:
            try:
                max_uses = int(parts[3])
            except ValueError:
                max_uses = 0
        ok = self.db.create_promo(code, amount, max_uses)
        if ok:
            limit_text = f"лимит {max_uses}" if max_uses > 0 else "безлимитный"
            self.send(chat_id, f"✅ Промокод <code>{code}</code> создан: {amount} монет, {limit_text}.")
        else:
            self.send(chat_id, f"Промокод <code>{code}</code> уже существует.")

    def handle_delpromo(self, chat_id: int, text: str) -> None:
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            self.send(chat_id, "Формат: <code>/delpromo КОД</code>")
            return
        code = parts[1].upper()
        ok = self.db.delete_promo(code)
        if ok:
            self.send(chat_id, f"✅ Промокод <code>{code}</code> удалён.")
        else:
            self.send(chat_id, f"Промокод <code>{code}</code> не найден.")

    def show_free(self, chat_id: int, user_id: int, language_code: str | None) -> None:
        # Кеш: если уже прошёл обязательную подписку менее MANDATORY_CACHE_HOURS назад — сразу доступ
        if self._mandatory_cached(user_id):
            self.grant_free_access(chat_id, user_id)
            return

        if self.flyer is not None or self.botohub is not None or self.linkni is not None:
            tasks = self._build_mandatory_tasks(user_id, language_code)
            if not tasks:
                self.grant_free_access(chat_id, user_id)
                return
            rows: list[list[dict[str, str]]] = []
            for index, task in enumerate(tasks, start=1):
                rows.append([url_button(f"Спонсор {index}", str(task["url"]))])
            rows.append([callback_button("✅ Проверить", "free_check")])
            self.send(
                chat_id,
                "Чтобы получить доступ к функциям бота, подпишись на каналы спонсоров:",
                inline_keyboard(rows),
            )
            self.send(chat_id, "После выполнения нажмите кнопку «Проверить».")
            return

        rows_static: list[list[dict[str, str]]] = []
        for index in range(0, len(FREE_SPONSORS), 2):
            rows_static.append([url_button(s.label, s.url) for s in FREE_SPONSORS[index : index + 2]])
        self.send(
            chat_id,
            "Чтобы получить доступ к функциям бота, необходимо выполнить все действия:",
            inline_keyboard(rows_static),
        )
        self.send(
            chat_id,
            "Для получения доступа к бесплатной накрутке подпишитесь на каналы спонсоров.",
            inline_keyboard([[callback_button("✅ Проверить", "free_check")]]),
        )

    def _build_mandatory_tasks(self, user_id: int, language_code: str | None) -> list[dict[str, Any]]:
        """Собрать обязательные задания: botohub → flyer (без linkni), до MANDATORY_TASK_LIMIT."""
        tasks: list[dict[str, Any]] = []
        seen_urls: set[str] = set()

        if self.botohub is not None:
            try:
                payload = self.botohub.get_tasks(user_id)
            except BotoHubError as exc:
                logging.warning("BotoHub get_tasks mandatory: %s", exc)
                payload = {}
            if not bool(payload.get("skip")):
                for link in payload.get("tasks") or []:
                    if len(tasks) >= MANDATORY_TASK_LIMIT:
                        break
                    normalized = str(link).strip()
                    if not normalized or normalized in seen_urls or not self.is_supported_earn_url(normalized):
                        continue
                    if self.is_linkni_url(normalized):
                        continue
                    if not self.is_telegram_url(normalized):
                        continue
                    tasks.append({"provider": "botohub", "url": normalized})
                    seen_urls.add(normalized)

        if self.flyer is not None and len(tasks) < MANDATORY_TASK_LIMIT:
            try:
                flyer_tasks = self.load_flyer_tasks(user_id, language_code, "mandatory", limit=MANDATORY_TASK_LIMIT)
            except FlyerError as exc:
                logging.warning("Flyer get_tasks (mandatory): %s", exc)
                flyer_tasks = []
            for task in flyer_tasks:
                if len(tasks) >= MANDATORY_TASK_LIMIT:
                    break
                if str(task["status"]) not in FLYER_INCOMPLETE_STATUSES:
                    continue
                try:
                    links = json.loads(task["links_json"])
                except (TypeError, ValueError):
                    links = []
                for link in links:
                    if len(tasks) >= MANDATORY_TASK_LIMIT:
                        break
                    normalized = str(link).strip()
                    if not normalized or normalized in seen_urls or self.is_linkni_url(normalized):
                        continue
                    if not self.is_telegram_url(normalized):
                        continue
                    tasks.append({"provider": "flyer", "signature": str(task["signature"]), "url": normalized})
                    seen_urls.add(normalized)

        return tasks[:MANDATORY_TASK_LIMIT]

    def _verify_mandatory(self, chat_id: int, user_id: int, language_code: str | None, callback_id: str) -> None:
        """Проверить выполнение обязательных заданий (flyer + botohub)."""
        tasks = self._build_mandatory_tasks(user_id, language_code)
        if not tasks:
            self.grant_free_access(chat_id, user_id)
            return

        for task in tasks:
            provider = str(task.get("provider", ""))
            if provider == "flyer":
                if self.flyer is None:
                    continue
                try:
                    status = self.flyer.check_task(str(task["signature"]))
                except FlyerError as exc:
                    logging.warning("Flyer check_task (mandatory): %s", exc)
                    self.api.answer_callback(callback_id, "Не удалось проверить подписку. Попробуйте ещё раз.", alert=True)
                    return
                if status is None:
                    self.api.answer_callback(callback_id, "Не удалось проверить подписку. Попробуйте ещё раз.", alert=True)
                    return
                self.db.update_flyer_task(user_id, str(task["signature"]), status, 0)
                if status in FLYER_INCOMPLETE_STATUSES:
                    self.api.answer_callback(callback_id, "Подписка пока не подтверждена. Подпишитесь и попробуйте снова.", alert=True)
                    return
            elif provider == "botohub":
                if self.botohub is None:
                    continue
                try:
                    payload = self.botohub.get_tasks(user_id)
                except BotoHubError as exc:
                    logging.warning("BotoHub check (mandatory): %s", exc)
                    self.api.answer_callback(callback_id, "Не удалось проверить подписку. Попробуйте ещё раз.", alert=True)
                    return
                remaining = {
                    str(link).strip()
                    for link in (payload.get("tasks") or [])
                    if isinstance(link, str)
                }
                if not bool(payload.get("completed")) and str(task["url"]) in remaining:
                    self.api.answer_callback(callback_id, "Подписка пока не подтверждена. Подпишитесь и попробуйте снова.", alert=True)
                    return

        self.grant_free_access(chat_id, user_id)

    def show_profile(self, chat_id: int, user_id: int) -> None:
        user = self.db.get_user(user_id)
        referrals = self.db.confirmed_referrals(user_id)
        referral_link = f"https://t.me/{self.username}?start={user_id}"
        text = (
            f"Баланс: <b>{int(user['balance'])}</b> монет\n\n"
            "Чтобы получить монеты:\n"
            "- Приглашайте рефералов по ссылке\n"
            "- Получайте ежедневный бонус\n\n"
            f"🔗 Ваша реферальная ссылка:\n<code>{html.escape(referral_link)}</code>\n"
            f"📊 Вы пригласили: <b>{referrals}</b>\n"
            f"За каждого реферала вы будете получать по {REFERRAL_REWARD} монет!\n\n"
            "ВНИМАНИЕ! Реферал засчитывается ТОЛЬКО после нажатия на кнопку "
            "бесплатной накрутки и после подписки на каналы спонсоров."
        )
        markup = inline_keyboard(
            [
                [callback_button("[💎] Ежедневный бонус", "daily_bonus")],
                [callback_button("Промокоды", "promo")],
            ]
        )
        self.send(chat_id, text, markup)

    def show_vip(self, chat_id: int, user_id: int) -> None:
        referrals = self.db.confirmed_referrals(user_id)
        self.send(
            chat_id,
            "👑 <b>VIP-услуги</b>\n\n"
            "Для доступа к VIP-услугам необходимо иметь минимум 10 подтверждённых рефералов.\n\n"
            f"📊 Ваших подтверждённых рефералов: <b>{referrals}/10</b>\n\n"
            "Реферал считается подтверждённым после того, как приглашённый нажал кнопку "
            "бесплатной накрутки и подписался на каналы спонсоров.",
        )



    def show_linkni_task(self, chat_id: int, user_id: int) -> None:
        if self.linkni is None:
            self.send(chat_id, "Сейчас нет доступных заданий. Попробуйте позже.")
            return
        claim_key = self.db.daily_claim_key(f"linkni:{self.linkni.sell_code}")
        if self.db.has_claim(user_id, claim_key):
            self.send(chat_id, "✅ Вы уже выполнили это задание и получили награду.")
            return
        self.send(
            chat_id,
            "🎯 Ваше задание готово.\n\n"
            f"Награда: <b>+{LINKNI_REWARD} монет</b>\n\n"
            "1. Откройте задание\n"
            "2. Подпишитесь на предложенный канал\n"
            "3. Вернитесь в бот и нажмите «Проверить»",
            inline_keyboard(
                [
                    [url_button("🖥 Открыть задание", self.linkni.task_url(user_id))],
                    [callback_button("✅ Проверить", "linkni_check")],
                ]
            ),
        )

    def check_memberships(self, user_id: int, sponsors: list[Sponsor]) -> list[str]:
        missing: list[str] = []
        for sponsor in sponsors:
            if not sponsor.chat_id:
                continue
            try:
                member = self.api.call("getChatMember", chat_id=sponsor.chat_id, user_id=user_id)
                status = member.get("status")
                is_member = status in {"creator", "administrator", "member"}
                if status == "restricted":
                    is_member = bool(member.get("is_member"))
                if not is_member:
                    missing.append(sponsor.label)
            except TelegramError as exc:
                logging.warning("Не удалось проверить канал %s: %s", sponsor.label, exc)
                missing.append(sponsor.label)
        return missing

    def check_flyer_tasks(
        self,
        user_id: int,
        purpose: str,
        reward: int = FLYER_TASK_REWARD,
    ) -> tuple[int, int, int, int]:
        """Вернуть (выполнено сейчас, осталось, ошибок проверки, начислено монет)."""
        if self.flyer is None:
            return 0, 0, 0, 0
        completed = 0
        errors = 0
        credited_total = 0
        for task in self.db.get_flyer_tasks(user_id, purpose):
            status = str(task["status"])
            if status in FLYER_INCOMPLETE_STATUSES:
                try:
                    checked_status = self.flyer.check_task(str(task["signature"]))
                except FlyerError as exc:
                    logging.warning("Flyer check_task: %s", exc)
                    errors += 1
                    continue
                if checked_status is None:
                    errors += 1
                    continue
                status = checked_status
            credited, _ = self.db.update_flyer_task(
                user_id,
                str(task["signature"]),
                status,
                reward,
            )
            if status not in FLYER_INCOMPLETE_STATUSES:
                completed += 1
            if credited and reward:
                credited_total += reward

        remaining = sum(
            task["status"] in FLYER_INCOMPLETE_STATUSES
            for task in self.db.get_flyer_tasks(user_id, purpose)
        )
        return completed, remaining, errors, credited_total

    def _handle_admin_callback(self, chat_id: int, user_id: int, data: str, callback_id: str) -> None:
        action = data.partition("admin:")[2]
        if action == "menu":
            self.show_admin_menu(chat_id)
        elif action == "stats":
            self.show_admin_stats(chat_id)
        elif action == "recent":
            self.show_admin_recent_users(chat_id)
        elif action == "top_refs":
            self.show_admin_top_refs(chat_id)
        elif action == "user":
            self.pending_input[user_id] = "admin:find_user"
            self.send(chat_id, "Введите ID пользователя или @username:", {"force_reply": True, "selective": True})
        elif action == "credit":
            self.pending_input[user_id] = "admin:find_user"
            self.send(chat_id, "Введите ID пользователя для начисления:", {"force_reply": True, "selective": True})
        elif action.startswith("credit_to:"):
            target_id = action.partition("credit_to:")[2]
            self.pending_input[user_id] = f"admin:credit_amount:{target_id}"
            self.send(chat_id, "Введите сумму для начисления (может быть отрицательной для списания):", {"force_reply": True, "selective": True})
        elif action.startswith("setbal:"):
            target_id = action.partition("setbal:")[2]
            self.pending_input[user_id] = f"admin:setbal_amount:{target_id}"
            self.send(chat_id, "Введите новый баланс:", {"force_reply": True, "selective": True})
        elif action.startswith("ban:"):
            target_id = int(action.partition("ban:")[2])
            self.db.ban_user(target_id)
            self.send(chat_id, f"🚫 Пользователь <code>{target_id}</code> забанен.")
            self.show_admin_user(chat_id, target_id)
        elif action.startswith("unban:"):
            target_id = int(action.partition("unban:")[2])
            self.db.unban_user(target_id)
            self.send(chat_id, f"🔓 Пользователь <code>{target_id}</code> разбанен.")
            self.show_admin_user(chat_id, target_id)
        elif action == "broadcast":
            self.pending_input[user_id] = "admin:broadcast_text"
            self.send(chat_id, "Введите текст рассылки (HTML поддерживается):", {"force_reply": True, "selective": True})
        elif action == "utm":
            self.pending_input[user_id] = "admin:utm_source"
            self.send(
                chat_id,
                "🔗 <b>UTM-генератор</b>\n\n"
                "Введите название источника (utm_source).\n"
                "Например: <code>vk</code>, <code>telegram</code>, <code>instagram</code>",
                {"force_reply": True, "selective": True},
            )
        elif action == "promos":
            self.show_admin_promos(chat_id)
        elif action == "utm_stats":
            total = self.db.utm_total()
            stats = self.db.utm_stats()
            today = self.db.utm_stats_today()
            lines = [f"📊 <b>UTM-статистика</b>\n\nВсего переходов: <b>{total}</b>\n"]
            if today:
                lines.append("<b>Сегодня:</b>")
                for src, med, camp, cnt in today:
                    label = f"{src}/{med}/{camp}" if med else f"{src}/{camp}"
                    lines.append(f"  {html.escape(label)} — <b>{cnt}</b>")
                lines.append("")
            if stats:
                lines.append("<b>Всего (топ-20):</b>")
                for src, med, camp, cnt in stats:
                    label = f"{src}/{med}/{camp}" if med else f"{src}/{camp}"
                    lines.append(f"  {html.escape(label)} — <b>{cnt}</b>")
            else:
                lines.append("Переходов по UTM пока нет.")
            self.send(
                chat_id,
                "\n".join(lines),
                inline_keyboard([
                    [callback_button("🔗 Создать UTM", "admin:utm")],
                    [callback_button("⬅️ В админку", "admin:menu")],
                ]),
            )
        else:
            self.api.answer_callback(callback_id, "Неизвестное действие.", alert=True)
            return
        self.api.answer_callback(callback_id)

    def handle_message(self, message: dict[str, Any]) -> None:
        if "text" not in message or "from" not in message:
            return
        chat_id = int(message["chat"]["id"])
        telegram_user = message["from"]
        user_id = int(telegram_user["id"])
        text = message["text"].strip()

        inviter_id: int | None = None
        utm_data: dict[str, str] | None = None
        if text.startswith("/start"):
            parts = text.split(maxsplit=1)
            if len(parts) == 2:
                payload = parts[1]
                if payload.isdigit():
                    inviter_id = int(payload)
                elif payload.startswith("utm_"):
                    # Format: utm_SOURCE_MEDIUM_CAMPAIGN
                    utm_parts = payload[4:].split("_", 2)
                    utm_data = {
                        "source": utm_parts[0] if len(utm_parts) >= 1 else "",
                        "medium": utm_parts[1] if len(utm_parts) >= 2 else "",
                        "campaign": utm_parts[2] if len(utm_parts) >= 3 else "",
                    }
        self.db.register_user(telegram_user, inviter_id)
        if utm_data:
            self.db.save_utm_visit(user_id, utm_data["source"], utm_data["medium"], utm_data["campaign"])

        if self.db.is_banned(user_id) and not self.is_admin(user_id):
            self.send(chat_id, "⛔ Ваш аккаунт заблокирован.")
            return

        if text.startswith("/start") or text in {"/menu", "Главное меню"}:
            self.pending_input.pop(user_id, None)
            self.order_drafts.pop(user_id, None)
            self.show_home(chat_id)
            return
        if text == "/profile":
            self.show_profile(chat_id, user_id)
            return

        pending = self.pending_input.get(user_id)
        if pending == "promo":
            self.pending_input.pop(user_id, None)
            status, amount, balance = self.db.redeem_promo(user_id, text)
            if status == "ok":
                self.send(chat_id, f"✅ Промокод активирован: +{amount} монет.\nБаланс: {balance} монет.")
            elif status == "used":
                self.send(chat_id, "Этот промокод вы уже использовали.")
            else:
                self.send(chat_id, "Промокод не найден. Проверьте написание и попробуйте снова.")
            return
        if pending == "buy_coins_custom":
            try:
                stars_amount = int(text)
            except ValueError:
                stars_amount = 0
            if stars_amount < CUSTOM_STARS_MIN or stars_amount > CUSTOM_STARS_MAX:
                self.send(
                    chat_id,
                    f"Введите целое число от {CUSTOM_STARS_MIN} до {CUSTOM_STARS_MAX}:",
                    {"force_reply": True, "selective": True},
                )
                return
            self.pending_input.pop(user_id, None)
            self.send_custom_coin_invoice(chat_id, stars_amount)
            return
        if pending and pending.startswith(("order_quantity:", "order_link:")):
            self.handle_order_input(chat_id, user_id, text, pending)
            return
        if pending and pending.startswith("admin:") and self.is_admin(user_id):
            self.handle_admin_input(chat_id, user_id, text, pending)
            return

        # Admin commands (free text)
        if self.is_admin(user_id):
            if text.startswith("/addpromo "):
                self.handle_addpromo(chat_id, text)
                return
            if text.startswith("/delpromo "):
                self.handle_delpromo(chat_id, text)
                return

        handlers = {
            "Бесплатная накрутка 🔥": lambda: self.show_free(
                chat_id, user_id, telegram_user.get("language_code")
            ),
            "Платная накрутка ⭐": lambda: self.show_paid_menu(chat_id),
            "Профиль 😎": lambda: self.show_profile(chat_id, user_id),
            "VIP-услуги 👑": lambda: self.show_vip(chat_id, user_id),
            "Заработать монеты": lambda: self.show_earn(
                chat_id, user_id, telegram_user.get("language_code"), force_new=True
            ),
            "Купить монеты": lambda: self.show_coin_packages(chat_id),
            "/paysupport": lambda: self.send(
                chat_id,
                "Поддержка по оплате: напишите администратору сервиса и укажите Telegram ID, дату и пакет покупки.",
            ),
            "Ещё задания": lambda: self.show_linkni_task(chat_id, user_id),
            "🏆 Конкурс": lambda: self.send(chat_id, "Сейчас конкурс рефералов не проводится."),
            "/help": lambda: self.send(chat_id, "Используйте кнопки меню. Вернуть меню: /menu"),
        }
        if self.is_admin(user_id):
            handlers["Админка"] = lambda: self.show_admin_menu(chat_id)
            handlers["/admin"] = lambda: self.show_admin_menu(chat_id)
        handler = handlers.get(text)
        if handler:
            handler()
        else:
            self.send(chat_id, "Не понял команду. Выберите действие в меню или отправьте /menu.")

    def handle_pre_checkout_query(self, query: dict[str, Any]) -> None:
        payload = str(query.get("invoice_payload", ""))
        if payload.startswith("coins:"):
            ok = payload.partition(":")[2] in COIN_PACKAGES
        elif payload.startswith("coins_custom:"):
            raw_amount = payload.partition(":")[2]
            ok = raw_amount.isdigit() and CUSTOM_STARS_MIN <= int(raw_amount) <= CUSTOM_STARS_MAX
        else:
            ok = False
        self.api.answer_pre_checkout_query(
            str(query["id"]),
            ok,
            None if ok else "Платёжный пакет больше недоступен.",
        )

    def handle_successful_payment(self, message: dict[str, Any]) -> None:
        payment = message.get("successful_payment")
        if not isinstance(payment, dict):
            return
        payload = str(payment.get("invoice_payload", ""))
        prefix, _, package_key = payload.partition(":")
        if prefix == "coins":
            package = COIN_PACKAGES.get(package_key)
            if package is None:
                logging.warning("Неизвестный платёжный payload: %s", payload)
                return
            stars_amount = package.stars_amount
            coins_amount = package.coins_amount
            payment_title = package.title
        elif prefix == "coins_custom":
            if not package_key.isdigit():
                logging.warning("Некорректный custom payload: %s", payload)
                return
            stars_amount = int(package_key)
            if stars_amount < CUSTOM_STARS_MIN or stars_amount > CUSTOM_STARS_MAX:
                logging.warning("Custom Stars amount out of range: %s", payload)
                return
            coins_amount = stars_amount * 1000
            payment_title = "Пополнение монет"
        else:
            return
        user_id = int(message["from"]["id"])
        chat_id = int(message["chat"]["id"])
        charge_id = str(payment.get("telegram_payment_charge_id", ""))
        credited, balance = self.db.apply_star_payment(
            user_id,
            charge_id,
            payload,
            stars_amount,
            coins_amount,
        )
        if credited:
            self.send(
                chat_id,
                "✅ <b>Оплата прошла успешно</b>\n\n"
                f"Пакет: <b>{payment_title}</b>\n"
                f"Начислено: <b>+{coins_amount} монет</b>\n"
                f"Текущий баланс: <b>{balance} монет</b>.",
            )
        else:
            self.send(chat_id, "Платёж уже был обработан ранее.")

    def handle_callback(self, callback: dict[str, Any]) -> None:
        callback_id = callback["id"]
        user = callback["from"]
        user_id = int(user["id"])
        message = callback.get("message")
        if not message:
            self.api.answer_callback(callback_id)
            return
        chat_id = int(message["chat"]["id"])
        self.db.register_user(user)
        data = callback.get("data", "")

        try:
            if data == "docs":
                self.send(chat_id, "Документы сервиса доступны кнопками ниже.")
            elif data == "privacy":
                self.send(
                    chat_id,
                    "🔒 <b>Политика конфиденциальности</b>\n\n"
                    "Бот хранит Telegram ID, имя, баланс, историю бонусов и рефералов. "
                    "Эти данные используются только для работы сервиса и не передаются третьим лицам.",
                )
            elif data == "terms":
                self.send(
                    chat_id,
                    "📄 <b>Пользовательское соглашение</b>\n\n"
                    "Используя бота, пользователь соглашается соблюдать правила Telegram и "
                    "не применять сервис для спама или иных нарушений.",
                )
            elif data in {"free_check", "flyer_free_check"}:
                self._verify_mandatory(chat_id, user_id, user.get("language_code"), callback_id)
            elif data == "service:followers":
                self.send(
                    chat_id,
                    "Выберите соцсеть:",
                    inline_keyboard(
                        [
                            [callback_button("📢 Telegram", "paid_platform:telegram")],
                            [callback_button("🎵 TikTok", "paid_platform:tiktok")],
                        ]
                    ),
                )
            elif data == "paid_menu":
                self.show_paid_menu(chat_id)
            elif data.startswith("paid_platform:"):
                self.show_paid_platform(chat_id, data.partition(":")[2])
            elif data == "buy_coins":
                self.show_coin_packages(chat_id)
            elif data == "buy_coins_custom":
                self.request_custom_stars_amount(chat_id, user_id)
            elif data.startswith("buy_coins:"):
                self.send_coin_invoice(chat_id, data.partition(":")[2])
            elif data == "service:views":
                self.begin_order(chat_id, user_id, "views")
            elif data == "service:reactions":
                self.begin_order(chat_id, user_id, "reactions")
            elif data.startswith("order_type:"):
                self.begin_order(chat_id, user_id, data.partition(":")[2])
            elif data == "order_cancel":
                self.pending_input.pop(user_id, None)
                self.order_drafts.pop(user_id, None)
                self.send(chat_id, "Заказ отменён.")
            elif data == "order_confirm":
                draft = self.order_drafts.pop(user_id, None)
                if not draft:
                    self.api.answer_callback(callback_id, "Черновик заказа не найден.", alert=True)
                    return
                if self.socproof is None:
                    self.api.answer_callback(callback_id, "API поставщика не настроен.", alert=True)
                    return
                service = ORDER_SERVICES[draft["service_key"]]
                balance = int(self.db.get_user(user_id)["balance"])
                if balance < int(draft["total"]):
                    self.api.answer_callback(callback_id, "На балансе недостаточно монет.", alert=True)
                    return
                provider_order_id = self.socproof.add_order(
                    service.provider_service_id,
                    str(draft["target_link"]),
                    int(draft["quantity"]),
                )
                created, new_balance, order_id = self.db.create_order(
                    user_id,
                    str(draft["service_key"]),
                    int(service.provider_service_id),
                    provider_order_id,
                    int(draft["quantity"]),
                    str(draft["target_link"]),
                    int(draft["total"]),
                )
                if not created or order_id is None:
                    self.api.answer_callback(callback_id, "Не удалось создать заказ.", alert=True)
                    return
                self.send(
                    chat_id,
                    "✅ <b>Заказ оформлен</b>\n\n"
                    f"Номер заказа: <b>#{order_id}</b>\n"
                    f"Номер у поставщика: <b>#{provider_order_id}</b>\n"
                    f"Платформа: {self.paid_platform_label(service.platform)}\n"
                    f"Услуга: {service.title}\n"
                    f"Количество: <b>{draft['quantity']}</b>\n"
                    f"Списано: <b>{draft['total']} монет</b>\n"
                    f"Остаток: <b>{new_balance} монет</b>\n\n"
                    "Заказ отправлен поставщику и сохранён в системе.",
                )
            elif data == "daily_bonus":
                claimed, amount, streak, balance = self.db.claim_daily_bonus(user_id)
                if not claimed:
                    self.api.answer_callback(callback_id, "Сегодня бонус уже получен.", alert=True)
                    return
                tomorrow = min(500 + streak * 100, 1000)
                self.send(
                    chat_id,
                    f"+{amount} монет.\n\n"
                    f"Серия: {streak} дн.\n"
                    f"Завтра будет {tomorrow} монет, если вернётесь за бонусом.\n"
                    f"Текущий баланс: {balance} монет.",
                )
            elif data == "promo":
                self.pending_input[user_id] = "promo"
                self.send(chat_id, "Введите промокод одним сообщением:", {"force_reply": True, "selective": True})
            elif data.startswith("admin:") and self.is_admin(user_id):
                self._handle_admin_callback(chat_id, user_id, data, callback_id)
            elif data in {"earn_menu", "earn_more"}:
                self.show_earn(chat_id, user_id, user.get("language_code"), force_new=True)
            elif data in {"earn_verify", "flyer_earn_check"}:
                self.verify_earn_batch(chat_id, user_id, user.get("language_code"))
            elif data == "linkni_task":
                self.show_linkni_task(chat_id, user_id)
            elif data == "linkni_check":
                if self.linkni is None:
                    self.api.answer_callback(callback_id, "Задание временно недоступно.", alert=True)
                    return
                sub_code = self.linkni.sub_code(user_id)
                status = self.db.latest_linkni_status(user_id, self.linkni.sell_code, sub_code)
                if status == "subscribed":
                    claim_key = self.db.daily_claim_key(f"linkni:{self.linkni.sell_code}")
                    claimed, balance = self.db.claim_once(user_id, claim_key, LINKNI_REWARD)
                    if claimed:
                        self.send(
                            chat_id,
                            f"✅ Подписка подтверждена. +{LINKNI_REWARD} монет.\n"
                            f"Баланс: {balance} монет.",
                        )
                    else:
                        self.api.answer_callback(callback_id, "Награда уже была получена.", alert=True)
                        return
                elif status == "not_subscribed":
                    self.api.answer_callback(
                        callback_id,
                        "Подписка не найдена. Выполните задание и повторите проверку.",
                        alert=True,
                    )
                    return
                elif status == "no_sponsors":
                    self.api.answer_callback(
                        callback_id,
                        "Сейчас у сервиса нет подходящих спонсоров. Попробуйте позже.",
                        alert=True,
                    )
                    return
                elif status is None:
                    self.api.answer_callback(
                        callback_id,
                        "Webhook ещё не подтвердил подписку. Откройте задание, выполните его и повторите проверку.",
                        alert=True,
                    )
                    return
                else:
                    logging.warning("Неизвестный статус Linkni: %s", status)
                    self.api.answer_callback(
                        callback_id,
                        "Статус задания ещё не подтверждён. Попробуйте позже.",
                        alert=True,
                    )
                    return
            elif data.startswith("task_check:"):
                task_day = data.partition(":")[2]
                if task_day != datetime.now(MOSCOW).date().isoformat():
                    self.api.answer_callback(callback_id, "Срок задания истёк. Откройте новое.", alert=True)
                    return
                claimed, balance = self.db.claim_once(user_id, f"task:{task_day}", TASK_REWARD)
                if claimed:
                    self.send(chat_id, f"✅ Задание принято. +{TASK_REWARD} монет.\nБаланс: {balance} монет.")
                else:
                    self.api.answer_callback(callback_id, "Это задание уже оплачено.", alert=True)
                    return
            else:
                self.api.answer_callback(callback_id, "Неизвестное действие.")
                return
            self.api.answer_callback(callback_id)
        except TelegramError:
            raise
        except FlyerError as exc:
            logging.warning("Flyer API: %s", exc)
            self.api.answer_callback(
                callback_id,
                "Сервис заданий временно недоступен. Попробуйте позже.",
                alert=True,
            )
        except BotoHubError as exc:
            logging.warning("BotoHub API: %s", exc)
            self.api.answer_callback(
                callback_id,
                f"Сервис BotoHub временно недоступен: {exc}",
                alert=True,
            )
        except SocProofError as exc:
            logging.warning("Soc-proof API: %s", exc)
            self.api.answer_callback(
                callback_id,
                f"Не удалось оформить заказ: {exc}",
                alert=True,
            )
        except Exception:
            logging.exception("Ошибка обработки callback %s", data)
            self.api.answer_callback(callback_id, "Не удалось выполнить действие. Попробуйте ещё раз.", alert=True)

    def handle_update(self, update: dict[str, Any]) -> None:
        if "message" in update:
            self.handle_message(update["message"])
            if "successful_payment" in update["message"]:
                self.handle_successful_payment(update["message"])
        elif "callback_query" in update:
            self.handle_callback(update["callback_query"])
        elif "pre_checkout_query" in update:
            self.handle_pre_checkout_query(update["pre_checkout_query"])

    def run(self) -> None:
        offset: int | None = None
        backoff = 1
        while self.running:
            try:
                payload: dict[str, Any] = {
                    "timeout": 30,
                    "allowed_updates": ["message", "callback_query", "pre_checkout_query"],
                }
                if offset is not None:
                    payload["offset"] = offset
                updates = self.api.call("getUpdates", **payload)
                backoff = 1
                for update in updates:
                    offset = int(update["update_id"]) + 1
                    try:
                        self.handle_update(update)
                    except TelegramError as exc:
                        logging.warning("Telegram API: %s", exc)
                    except Exception:
                        logging.exception("Необработанная ошибка в update %s", update.get("update_id"))
            except TelegramError as exc:
                logging.warning("Long polling прерван: %s; повтор через %s с", exc, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)


def configure_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(BASE_DIR / "bot.log", encoding="utf-8"),
        ],
    )


def main() -> int:
    configure_logging()
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        logging.error("В .env отсутствует BOT_TOKEN")
        return 2

    api = TelegramAPI(token)
    try:
        me = api.call("getMe")
        api.call("deleteWebhook", drop_pending_updates=False)
        api.call(
            "setMyCommands",
            commands=[
                {"command": "start", "description": "Запустить бота"},
                {"command": "menu", "description": "Открыть меню"},
                {"command": "profile", "description": "Мой профиль"},
                {"command": "paysupport", "description": "Поддержка по оплате"},
                {"command": "help", "description": "Помощь"},
            ],
        )
    except TelegramError as exc:
        logging.error("Не удалось подключиться к Telegram: %s", exc)
        return 3

    database = Database(BASE_DIR / "bot.sqlite3")
    flyer = FlyerAPI(FLYER_API_KEY) if FLYER_API_KEY else None
    botohub = BotoHubAPI(BOTOHUB_API_URL, BOTOHUB_AUTH_TOKEN) if BOTOHUB_AUTH_TOKEN else None
    linkni = (
        LinkniAPI(LINKNI_API_URL, LINKNI_SELL_CODE, LINKNI_APP_URL)
        if LINKNI_SELL_CODE and LINKNI_APP_URL
        else None
    )
    socproof = SocProofAPI(SOCPROOF_API_URL, SOCPROOF_API_KEY) if SOCPROOF_API_KEY else None
    linkni_webhook = None
    if linkni is not None:
        try:
            linkni_webhook = LinkniWebhookServer(
                database,
                LINKNI_WEBHOOK_HOST,
                LINKNI_WEBHOOK_PORT,
                LINKNI_WEBHOOK_PATH,
            )
            linkni_webhook.start()
        except OSError as exc:
            logging.error("Не удалось запустить Linkni webhook на %s:%s%s: %s", LINKNI_WEBHOOK_HOST, LINKNI_WEBHOOK_PORT, LINKNI_WEBHOOK_PATH, exc)
            return 4
    flyer_webhook = None
    if flyer is not None:
        try:
            flyer_webhook = FlyerWebhookServer(
                FLYER_WEBHOOK_HOST,
                FLYER_WEBHOOK_PORT,
                FLYER_WEBHOOK_PATH,
            )
            flyer_webhook.start()
        except OSError as exc:
            logging.error("Не удалось запустить Flyer webhook на %s:%s%s: %s", FLYER_WEBHOOK_HOST, FLYER_WEBHOOK_PORT, FLYER_WEBHOOK_PATH, exc)
    bot = Bot(api, database, me["username"], flyer, linkni, socproof, botohub)
    signal.signal(signal.SIGINT, bot.stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, bot.stop)
    logging.info(
        "Бот @%s запущен; Flyer API: %s; Flyer webhook: %s; BotoHub API: %s; Linkni API: %s; Linkni webhook: %s; Soc-proof API: %s",
        me["username"],
        "подключён" if flyer else "не настроен",
        f"http://{FLYER_WEBHOOK_HOST}:{FLYER_WEBHOOK_PORT}{FLYER_WEBHOOK_PATH}" if flyer_webhook else "не запущен",
        "подключён" if botohub else "не настроен",
        "подключён" if linkni else "не настроен",
        f"http://{LINKNI_WEBHOOK_HOST}:{LINKNI_WEBHOOK_PORT}{LINKNI_WEBHOOK_PATH}" if linkni_webhook else "не запущен",
        "подключён" if socproof else "не настроен",
    )
    bot.run()
    if flyer_webhook is not None:
        flyer_webhook.stop()
    if linkni_webhook is not None:
        linkni_webhook.stop()
    logging.info("Бот остановлен")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
