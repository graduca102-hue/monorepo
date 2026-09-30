"""One-off: add a single server and provision it via the panel's own code.

Usage:
    .venv\\Scripts\\python.exe ops\\provision_one.py <host> <user> <password> [name]
"""
from __future__ import annotations

import asyncio
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app import provisioner  # noqa: E402


async def _log(msg: str) -> None:
    print(msg, flush=True)


async def main() -> None:
    host, user, password = sys.argv[1], sys.argv[2], sys.argv[3]
    name = sys.argv[4] if len(sys.argv) > 4 else host

    await db.init()
    existing = [s for s in await db.servers() if s["host"] == host]
    if existing:
        sid = existing[0]["id"]
        await db.set_server(sid, ssh_user=user, ssh_password=password, name=name)
        print(f"reusing server row id={sid} (updated creds)")
    else:
        sid = await db.add_server(name, host, user, password, 22)
        print(f"added server row id={sid}")

    ok = await provisioner.provision_server(sid, _log)
    srv = await db.get_server(sid)
    print("---")
    print(f"result: {'OK' if ok else 'FAILED'}  status={srv['status']}")
    if srv["last_error"]:
        print(f"last_error: {srv['last_error']}")
    print(f"reality_public_key: {srv['reality_public_key']}")
    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
