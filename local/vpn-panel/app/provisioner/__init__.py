"""Server lifecycle: parse pasted credentials, install, and keep configs in sync.

Public API:
    parse_server_lines(text)   -> list[ParsedServer]
    provision_server(sid, log) -> installs software + captures REALITY keys
    sync_server(sid, log)      -> re-renders configs from DB and restarts services
    sync_all(log)              -> sync every active server (call after user changes)
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from .. import db
from ..config import settings
from .remote import (
    MTP_CONFIG_PATH,
    XRAY_CONFIG_PATH,
    bootstrap_script,
    mtproto_config,
    parse_x25519,
    xray_config,
)
from .ssh import SSHError, connect

Logger = Callable[[str], Awaitable[None]]


async def _noop(_: str) -> None:  # default logger
    pass


@dataclass
class ParsedServer:
    name: str
    host: str
    user: str
    password: str
    port: int = 22
    error: str = ""


_HOSTISH = re.compile(r"^(?:\d{1,3}(?:\.\d{1,3}){3}|[a-z0-9.-]+\.[a-z]{2,})$", re.I)


def parse_server_lines(text: str) -> list[ParsedServer]:
    """Accept one server per line in flexible order.

    Supported shapes (``:`` or ``|`` or whitespace separated):
        host login password name
        login password host name
        host:login:password:name
        login:password:host:name
        host:port:login:password:name
    ``name`` is optional (defaults to the host).
    """
    out: list[ParsedServer] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p for p in re.split(r"[:|\s]+", line) if p]
        if len(parts) < 3:
            out.append(ParsedServer("", "", "", "", error=f"too few fields: {line!r}"))
            continue

        port = 22
        # optional numeric port as the 2nd field of a host:port:... form
        if len(parts) >= 4 and _HOSTISH.match(parts[0]) and parts[1].isdigit():
            port = int(parts[1])
            parts = [parts[0], *parts[2:]]

        host = user = password = name = ""
        if _HOSTISH.match(parts[0]):
            host, user, password, *rest = parts
        elif len(parts) >= 3 and _HOSTISH.match(parts[2]):
            user, password, host, *rest = parts
        else:
            out.append(ParsedServer("", "", "", "", error=f"no host found in: {line!r}"))
            continue
        name = " ".join(rest) if rest else host
        out.append(ParsedServer(name=name, host=host, user=user, password=password, port=port))
    return out


async def add_from_lines(text: str) -> tuple[list[int], list[str]]:
    """Insert every well-formed line as a ``new`` server row. Returns (ids, errors)."""
    ids: list[int] = []
    errors: list[str] = []
    for ps in parse_server_lines(text):
        if ps.error:
            errors.append(ps.error)
            continue
        sid = await db.add_server(ps.name, ps.host, ps.user, ps.password, ps.port)
        ids.append(sid)
    return ids, errors


# --- provisioning -------------------------------------------------------

async def provision_server(sid: int, log: Logger = _noop) -> bool:
    srv = await db.get_server(sid)
    if not srv:
        await log("сервер не найден")
        return False
    await db.set_server(sid, status="provisioning", last_error="")
    await log(f"⏳ {srv['name']} ({srv['host']}): подключаюсь по SSH…")
    try:
        async with connect(srv["host"], srv["ssh_user"], srv["ssh_password"],
                           srv["ssh_port"], timeout=30) as r:
            await log("✅ SSH ок. Ставлю Xray + mtprotoproxy (2–5 мин)…")
            res = await r.sudo_script(
                bootstrap_script(srv["xray_port"], srv["mtproto_port"]),
                timeout=settings.ssh_timeout,
            )
            priv, pub = parse_x25519(res.stdout)
            await db.set_server(sid, reality_private_key=priv, reality_public_key=pub)
            await log("🔑 REALITY-ключи получены. Заливаю конфиги…")
    except (SSHError, ValueError) as e:
        await db.set_server(sid, status="error", last_error=str(e)[:500])
        await log(f"❌ {e}")
        return False

    ok = await sync_server(sid, log)
    if ok:
        await db.set_server(sid, status="active")
        await log(f"🎉 {srv['name']} готов и раздаёт трафик.")
    return ok


async def sync_server(sid: int, log: Logger = _noop) -> bool:
    srv = await db.get_server(sid)
    if not srv:
        return False
    if not srv["reality_public_key"]:
        await log("сервер ещё не провижен (нет REALITY-ключей)")
        return False
    users = await db.active_users()
    ad_tag = await db.active_ad_tag()
    house_secret = await db.mtproto_house_secret()
    try:
        async with connect(srv["host"], srv["ssh_user"], srv["ssh_password"],
                           srv["ssh_port"], timeout=30) as r:
            if srv["enable_xray"]:
                await r.write_file(XRAY_CONFIG_PATH, xray_config(srv, users))
                chk = await r.run(f"/usr/local/bin/xray run -test -c {XRAY_CONFIG_PATH}", timeout=30)
                if not chk.ok:
                    raise SSHError(f"xray config test failed: {chk.stderr[-300:]}")
                await r.run("systemctl enable --now xray >/dev/null 2>&1; systemctl restart xray",
                            check=True, timeout=30)
            if srv["enable_mtproto"]:
                await r.write_file(
                    MTP_CONFIG_PATH, mtproto_config(srv, users, ad_tag, house_secret)
                )
                await r.run(
                    "systemctl enable --now mtprotoproxy >/dev/null 2>&1; "
                    "systemctl restart mtprotoproxy",
                    check=True, timeout=30,
                )
            await asyncio.sleep(2)
            state = await r.run(
                "systemctl is-active xray mtprotoproxy 2>/dev/null | tr '\\n' ' '"
            )
    except SSHError as e:
        await db.set_server(sid, status="error", last_error=str(e)[:500])
        await log(f"❌ sync {srv['name']}: {e}")
        return False

    await db.set_server(sid, last_sync_at=db.now(), last_error="")
    await log(f"↻ {srv['name']}: {len(users)} польз., службы: {state.stdout.strip()}")
    return True


async def sync_all(log: Logger = _noop) -> tuple[int, int]:
    ok = bad = 0
    for srv in await db.active_servers():
        if await sync_server(srv["id"], log):
            ok += 1
        else:
            bad += 1
    return ok, bad


async def probe_server(sid: int) -> str:
    """Quick health line for the admin server list."""
    srv = await db.get_server(sid)
    if not srv:
        return "нет"
    try:
        async with connect(srv["host"], srv["ssh_user"], srv["ssh_password"],
                           srv["ssh_port"], timeout=15) as r:
            res = await r.run("systemctl is-active xray mtprotoproxy 2>/dev/null | tr '\\n' '/'",
                              timeout=15)
            return res.stdout.strip().strip("/") or "?"
    except SSHError as e:
        return f"unreachable ({str(e)[:40]})"
