"""Build Telethon .session files from LZT Market loginData."""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import logging
import struct
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DC_ADDRESSES = {
    1: "149.154.175.53",
    2: "149.154.167.51",
    3: "149.154.175.100",
    4: "149.154.167.91",
    5: "91.108.56.130",
}


def parse_login_data(raw: str) -> tuple[bytes, int]:
    """Parse LZT loginData.raw -> (auth_key, dc_id).

    Format: hex_auth_key:dc_id
    """
    parts = raw.rsplit(":", 1)
    auth_key_hex = parts[0]
    dc_id = int(parts[1]) if len(parts) > 1 else 2
    auth_key = bytes.fromhex(auth_key_hex)
    if len(auth_key) != 256:
        raise ValueError(f"Invalid auth_key length: {len(auth_key)} (expected 256)")
    return auth_key, dc_id


def build_string_session(auth_key: bytes, dc_id: int) -> str:
    """Build a Telethon StringSession from auth_key and dc_id."""
    dc_ip = DC_ADDRESSES.get(dc_id, DC_ADDRESSES[2])
    ip_bytes = ipaddress.ip_address(dc_ip).packed
    port = 443
    payload = struct.pack(">B4sH256s", dc_id, ip_bytes, port, auth_key)
    return "1" + base64.urlsafe_b64encode(payload).decode("ascii")


async def create_session_file(
    item_id: int,
    login_data_raw: str,
    api_id: int,
    api_hash: str,
    sessions_dir: Path,
) -> tuple[bool, str | None]:
    """Create a .session file from LZT loginData and verify it works.

    Returns (success, phone_or_error).
    """
    try:
        auth_key, dc_id = parse_login_data(login_data_raw)
    except (ValueError, TypeError) as e:
        return False, f"Невалидные данные сессии: {e}"

    session_string = build_string_session(auth_key, dc_id)
    dc_ip = DC_ADDRESSES.get(dc_id, DC_ADDRESSES[2])

    sessions_dir.mkdir(parents=True, exist_ok=True)
    session_path = sessions_dir / f"{item_id}.session"

    # Remove old session if exists
    if session_path.exists():
        session_path.unlink()

    from telethon import TelegramClient
    from telethon.sessions import StringSession

    # Verify the session works via StringSession
    str_client = TelegramClient(StringSession(session_string), api_id, api_hash)
    try:
        await str_client.connect()
        if not await str_client.is_user_authorized():
            await str_client.disconnect()
            return False, "Сессия не авторизована (аккаунт мог быть сброшен)"

        me = await str_client.get_me()
        phone = me.phone if me else None

        # Save as SQLite session file
        sqlite_client = TelegramClient(
            str(sessions_dir / f"{item_id}"), api_id, api_hash
        )
        sqlite_client.session.set_dc(dc_id, dc_ip, 443)
        sqlite_client.session.auth_key = str_client.session.auth_key
        sqlite_client.session.save()

        await str_client.disconnect()

        log.info(f"Session created for {item_id}: phone={phone}")
        return True, phone

    except Exception as e:
        try:
            await str_client.disconnect()
        except Exception:
            pass
        return False, str(e)


def extract_login_data(purchase_json: dict[str, Any]) -> str | None:
    """Extract loginData.raw from a purchase JSON."""
    purchase = purchase_json.get("purchase", {})
    item = purchase.get("item", {})

    # loginData can be a dict or nested
    login_data = item.get("loginData", {})
    if isinstance(login_data, dict):
        raw = login_data.get("raw")
        if raw and isinstance(raw, str) and ":" in raw:
            return raw

    # Also check login field directly
    login = item.get("login")
    if login and isinstance(login, str) and ":" in login and len(login) > 500:
        return login

    return None
