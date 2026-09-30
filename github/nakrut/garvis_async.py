"""
Асинхронный async-клиент Garvis Catalog: httpx + ECDSA-подпись.
"""

import asyncio
import base64
import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone

import httpx
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature


API_BASE = "https://api.garvisbot.org"
SESSION_PATH = "/webapp/chat-catalog/session"


def b64url(data: bytes) -> str:
    return base64.b64encode(data).decode().replace("+", "-").replace("/", "_").rstrip("=")


def sha256_b64url(data: bytes) -> str:
    return b64url(hashlib.sha256(data).digest())


def parse_retry_at(text: str) -> float | None:
    m = re.search(r"retry_at=(\S+)", text or "")
    if not m:
        return None
    raw = m.group(1).rstrip(";,.\"' ")
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
    except ValueError:
        return None


class AsyncGarvisClient:
    """Асинхронный клиент API каталога Garvis, привязанный к одному аккаунту."""

    def __init__(self, init_data: str, lkbot_username: str, cache_file: str,
                 name: str = "?", http: httpx.AsyncClient | None = None):
        self.init_data = init_data
        self.lkbot_username = lkbot_username
        self.cache_file = cache_file
        self.name = name
        self.session_id: str | None = None
        self.session_expires_at: str | None = None
        self.private_key = None
        self.http = http or httpx.AsyncClient(timeout=30.0, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "*/*",
            "Origin": "https://webapp.garvisbot.org",
            "Referer": "https://webapp.garvisbot.org/",
        })

    def _public_raw(self) -> bytes:
        return self.private_key.public_key().public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        )

    def load_cache(self) -> bool:
        if not os.path.exists(self.cache_file):
            return False
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            pem = data.get("privateKeyPem", "").encode()
            if not pem:
                return False
            self.private_key = serialization.load_pem_private_key(pem, password=None)
            self.session_id = data.get("sessionId")
            self.session_expires_at = data.get("expiresAt")
            return bool(self.session_id)
        except Exception:
            return False

    def save_cache(self):
        try:
            pem = self.private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            ).decode()
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump({
                    "privateKeyPem": pem,
                    "sessionId": self.session_id,
                    "expiresAt": self.session_expires_at,
                }, f, indent=2)
        except Exception:
            pass

    def session_is_fresh(self) -> bool:
        if not self.session_id or not self.session_expires_at:
            return False
        try:
            dt = datetime.fromisoformat(self.session_expires_at.replace("Z", "+00:00"))
            return (dt - datetime.now(timezone.utc)).total_seconds() > 30
        except Exception:
            return False

    def _sign_headers(self, method: str, path_with_query: str, body: bytes,
                      session_id: str) -> dict:
        timestamp = str(int(time.time() * 1000))
        nonce = str(uuid.uuid4())
        body_hash = sha256_b64url(body)
        message = f"{method}\n{path_with_query}\n{body_hash}\n{timestamp}\n{nonce}\n{session_id}"
        sig_der = self.private_key.sign(message.encode(), ec.ECDSA(hashes.SHA256()))
        r_int, s_int = decode_dss_signature(sig_der)
        sig_fixed = r_int.to_bytes(32, "big") + s_int.to_bytes(32, "big")
        return {
            "x-garvis-catalog-session": session_id,
            "x-garvis-catalog-timestamp": timestamp,
            "x-garvis-catalog-nonce": nonce,
            "x-garvis-catalog-proof": b64url(sig_fixed),
        }

    def _session_body(self) -> bytes:
        return json.dumps({
            "publicKey": b64url(self._public_raw()),
            "lkbotUsername": self.lkbot_username,
        }).encode()

    async def bootstrap_session(self) -> bool:
        if self.private_key is None:
            self.private_key = ec.generate_private_key(ec.SECP256R1())

        body = self._session_body()
        headers = {"Content-Type": "application/json", "x-telegram-init-data": self.init_data}
        try:
            resp = await self.http.post(f"{API_BASE}{SESSION_PATH}", content=body, headers=headers)
        except Exception:
            return False

        if resp.status_code == 429:
            wait = parse_retry_at(resp.text)
            if wait is None:
                wait = float(resp.headers.get("retry-after", 30))
            # долгое ожидание bootstrap не делаем, просто говорим "не смогли"
            self._bootstrap_wait_hint = wait
            return False

        if resp.status_code != 200:
            return False

        data = resp.json()
        self.session_id = data["sessionId"]
        self.session_expires_at = data.get("expiresAt")
        self.save_cache()
        return True

    async def renew_session(self) -> bool:
        if not self.private_key or not self.session_id:
            return False
        body = self._session_body()
        headers = {
            **self._sign_headers("POST", SESSION_PATH, body, self.session_id),
            "Content-Type": "application/json",
            "x-telegram-init-data": self.init_data,
        }
        try:
            resp = await self.http.post(f"{API_BASE}{SESSION_PATH}", content=body, headers=headers)
        except Exception:
            return False
        if resp.status_code != 200:
            return False
        data = resp.json()
        self.session_id = data["sessionId"]
        self.session_expires_at = data.get("expiresAt")
        self.save_cache()
        return True

    async def ensure_session(self) -> bool:
        if self.session_is_fresh():
            return True
        if self.private_key and self.session_id and await self.renew_session():
            return True
        return await self.bootstrap_session()

    async def _request(self, method: str, path: str, params: dict | None = None,
                       body_dict: dict | None = None):
        """Возвращает (status_code, json_or_none). Без ретраев/ожиданий."""
        from urllib.parse import urlencode

        if not await self.ensure_session():
            return 0, None

        query = "?" + urlencode(params) if params else ""
        full_path = path + query
        body = json.dumps(body_dict).encode() if body_dict is not None else b""

        headers = {
            **self._sign_headers(method, full_path, body, self.session_id),
            "x-telegram-init-data": self.init_data,
        }
        if body_dict is not None:
            headers["Content-Type"] = "application/json"

        try:
            resp = await self.http.request(
                method, f"{API_BASE}{full_path}",
                content=body if body_dict is not None else None,
                headers=headers,
            )
        except Exception:
            return 0, None

        if resp.status_code == 200:
            try:
                return 200, resp.json()
            except Exception:
                return 200, None
        return resp.status_code, resp.text

    async def catalog_page(self, query: str, cursor: str | None, limit: int = 50):
        params = {"q": query, "random": "false", "limit": str(limit)}
        if cursor:
            params["cursor"] = cursor
        return await self._request("GET", "/webapp/chat-catalog", params=params)

    async def reveal(self, chat_id: str):
        return await self._request("POST", "/webapp/chat-catalog/reveal",
                                   body_dict={"chatId": chat_id})

    async def aclose(self):
        try:
            await self.http.aclose()
        except Exception:
            pass
