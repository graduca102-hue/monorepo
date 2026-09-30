"""Общие утилиты: загрузка Telethon-сессий + получение initData мини-аппа Garvis."""
import sqlite3
import base64
import ipaddress
import struct
import os
from urllib.parse import urlparse, parse_qs, unquote

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.messages import RequestWebViewRequest

API_ID = 991478
API_HASH = "734e8f613e71e80b778c9d034361cf39"
BOT = "hellogarvisbot"
WEBAPP_URL = "https://webapp.garvisbot.org/?lkbot_username=hellogarvisbot"
SESSIONS_DIR = "sessions"


def session_file_to_string(path: str) -> str | None:
    """Читает auth_key/dc из .session (любая схема) → StringSession строка."""
    try:
        con = sqlite3.connect(path)
        row = con.execute(
            "SELECT dc_id, server_address, port, auth_key FROM sessions"
        ).fetchone()
        con.close()
        if not row:
            return None
        dc_id, server, port, auth_key = row
        if not auth_key or len(auth_key) != 256:
            return None
        ip_bytes = ipaddress.ip_address(server).packed
        # Telethon StringSession формат: version(1) + dc(B) + ip(4s) + port(H) + key(256s)
        payload = struct.pack(">B4sH256s", dc_id, ip_bytes, port, auth_key)
        return "1" + base64.urlsafe_b64encode(payload).decode("ascii")
    except Exception:
        return None


def list_sessions() -> list[str]:
    return [f for f in os.listdir(SESSIONS_DIR) if f.endswith(".session")]


def extract_init_data(web_url: str) -> str | None:
    # фрагмент: tgWebAppData=<initData>&tgWebAppVersion=...
    # parse_qs декодирует значение ОДИН раз — это и есть корректный initData
    # (внутри user остаётся url-encoded, как ждёт сервер для проверки хэша)
    frag = urlparse(web_url).fragment
    params = parse_qs(frag)
    data = params.get("tgWebAppData")
    return data[0] if data else None


async def get_init_data(session_path: str) -> tuple[str | None, str]:
    """
    Открывает мини-апп Garvis через аккаунт → возвращает (initData, статус).
    """
    ss = session_file_to_string(session_path)
    if not ss:
        return None, "bad-session-file"

    client = TelegramClient(StringSession(ss), API_ID, API_HASH)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            return None, "unauthorized"

        # /start (мягко, без падения)
        try:
            await client.send_message(BOT, "/start")
        except Exception:
            pass

        res = await client(RequestWebViewRequest(
            peer=BOT, bot=BOT, platform="android",
            from_bot_menu=False, url=WEBAPP_URL,
        ))
        init = extract_init_data(res.url)
        await client.disconnect()
        if init:
            return init, "ok"
        return None, "no-initdata"
    except Exception as e:
        try:
            await client.disconnect()
        except Exception:
            pass
        return None, f"error:{type(e).__name__}:{e}"
