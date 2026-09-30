"""Public subscription endpoint.

``GET /sub/<token>`` returns the right document for the requesting client
(sniffed from User-Agent) plus the Happ/v2rayNG/Hiddify metadata headers.
Everything else is a tiny landing page.
"""
from __future__ import annotations

from aiohttp import web

from .. import db, subscription
from ..config import settings


async def health(_: web.Request) -> web.Response:
    return web.Response(text="ok")


async def sub(request: web.Request) -> web.Response:
    token = request.match_info["token"]
    user = await db.get_user_by_token(token)
    if user is None:
        return web.Response(status=404, text="not found")

    servers = await db.active_servers()
    fmt = subscription.pick_format(request.headers.get("User-Agent", ""))
    body = subscription.render(user, servers, fmt)

    headers = subscription.sub_headers(user)
    headers["content-type"] = subscription.content_type(fmt)
    return web.Response(body=body.encode(), headers=headers)


async def happ_redirect(request: web.Request) -> web.Response:
    """Open in Happ. Handy as a plain https link in the bot."""
    token = request.match_info["token"]
    user = await db.get_user_by_token(token)
    if user is None:
        return web.Response(status=404, text="not found")
    raise web.HTTPFound(subscription.happ_link(user))


async def landing(_: web.Request) -> web.Response:
    html = (
        f"<!doctype html><meta charset=utf-8><title>{settings.brand}</title>"
        f"<style>body{{font:16px system-ui;margin:15vh auto;max-width:32rem;padding:0 1rem}}</style>"
        f"<h1>{settings.brand}</h1><p>Подписка открывается только по личной ссылке из бота.</p>"
        f"<p><a href='{settings.support_url}'>Поддержка</a></p>"
    )
    return web.Response(text=html, content_type="text/html")


def build_app() -> web.Application:
    app = web.Application()
    app.add_routes([
        web.get("/", landing),
        web.get("/healthz", health),
        web.get("/sub/{token}", sub),
        web.get("/happ/{token}", happ_redirect),
    ])
    return app


async def start_web() -> web.AppRunner:
    runner = web.AppRunner(build_app())
    await runner.setup()
    site = web.TCPSite(runner, settings.web_host, settings.web_port)
    await site.start()
    return runner
