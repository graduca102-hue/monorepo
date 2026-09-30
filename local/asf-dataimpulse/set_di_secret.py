"""Store a provider credential in the shop's encrypted settings table.

Usage (on srv2):  /opt/asf/.venv/bin/python set_di_secret.py <key> <value>
Keys: di_login, di_password, sp_api_key. The value is never echoed back.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, "/opt/asf")
os.chdir("/opt/asf")

from app.db import Database  # noqa: E402
from app.security import SecretCipher  # noqa: E402


async def main() -> None:
    key, value = sys.argv[1], sys.argv[2]
    if key not in {"di_login", "di_password", "sp_api_key"}:
        raise SystemExit("unsupported key")
    db = Database(Path(os.getenv("DATABASE_PATH", "data/shop.db")),
                  SecretCipher(Path(os.getenv("ENCRYPTION_KEY_PATH", "data/.secret.key"))))
    await db.init()
    await db.set_setting(key, value, secret=True)
    stored = await db.get_setting(key, secret=True)
    print(f"{key} saved: {'yes' if stored == value else 'NO'} (len={len(stored)})")
    await db.close()


asyncio.run(main())
