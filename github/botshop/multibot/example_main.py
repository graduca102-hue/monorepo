from __future__ import annotations

import asyncio
import os

from aiohttp import web

from multibot import create_multibot_app


async def main() -> None:
    """
    Minimal entrypoint for production deployment.

    Required env vars:
    - POSTGRES_DSN=postgresql://user:pass@host:5432/dbname
    - PUBLIC_BASE_URL=https://your-domain.com
    - PORT=8080
    """

    postgres_dsn = os.environ["POSTGRES_DSN"]
    public_base_url = os.environ["PUBLIC_BASE_URL"]
    port = int(os.getenv("PORT", "8080"))

    app, _engine = await create_multibot_app(
        postgres_dsn=postgres_dsn,
        public_base_url=public_base_url,
    )

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()

    print(f"Multibot server started on 0.0.0.0:{port}")
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
