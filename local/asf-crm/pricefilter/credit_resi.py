"""One-off: run ASF's residential reconcile for one customer, the same call the
proxy menu makes, so their paid-for GB is allocated onto their sub-agent now.
Run on srv2 from /opt/asf with .venv:  .venv/bin/python /tmp/credit_resi.py <tg_id>
"""
import asyncio
import sys

import aiohttp

sys.path.insert(0, "/opt/asf")
from app.clients import SousClient  # noqa: E402
from app.config import Config  # noqa: E402
from app.db import Database  # noqa: E402
from app.security import SecretCipher  # noqa: E402
from app.handlers_user import _reconcile_residential_client  # noqa: E402

USER_ID = int(sys.argv[1])


async def main() -> None:
    cfg = Config.from_env()
    db = Database(cfg.database_path, SecretCipher(cfg.encryption_key_path))
    await db.init()

    async def sous_key() -> str:
        return await db.get_setting("sous_api_key", secret=True)

    timeout = aiohttp.ClientTimeout(total=35, connect=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        sous = SousClient(session, cfg.sous_api_base, sous_key)
        ent = await db.get_residential_entitlement(USER_ID)
        print(f"entitlement before: {ent} GB")
        client, entitlement, pending = await _reconcile_residential_client(sous, db, USER_ID)
        print(f"client: {client}")
        print(f"entitlement={entitlement} pending={pending}")


asyncio.run(main())
