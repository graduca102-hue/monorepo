"""One-off: dump ASF residential pool + one client's state from the SOUS API.
Run on srv2 from /opt/asf with .venv. Prints no secrets.
Usage: .venv/bin/python /tmp/diag_resi.py <telegram_user_id>
"""
import asyncio
import json
import sqlite3
import sys

import aiohttp

sys.path.insert(0, "/opt/asf")
from app.config import Config  # noqa: E402
from app.security import SecretCipher  # noqa: E402

USER_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 904643211


async def get(s, base, key, path, **params):
    async with s.get(
        f"{base}{path}",
        params=params or None,
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    ) as r:
        txt = await r.text()
        print(f"\n>>> GET {path} {params or ''} -> {r.status}")
        try:
            print(json.dumps(json.loads(txt), ensure_ascii=False, indent=2)[:2500])
        except Exception:
            print(txt[:2000])


async def main() -> None:
    cfg = Config.from_env()
    cipher = SecretCipher(cfg.encryption_key_path)
    con = sqlite3.connect("data/shop.db")
    row = con.execute("SELECT value,is_secret FROM settings WHERE key='sous_api_key'").fetchone()
    key = cipher.decrypt(row[0]) if row and row[1] else (row[0] if row else "")
    key = key or cfg.sous_api_key
    base = cfg.sous_api_base

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
        await get(s, base, key, "/profile")
        await get(s, base, key, "/proxy/residential/traffic")
        await get(s, base, key, "/proxy/services")
        await get(s, base, key, f"/proxy/residential/clients/asf{USER_ID}")
        await get(s, base, key, f"/proxy/services/orders/525")


asyncio.run(main())
