"""Build per-user subscription documents and share links.

A subscription bundles every active server so the client app fails over on its
own. The URL is stable: when a server is added/removed the next poll returns the
new set and clients recover without user action.

Formats:
  * ``v2ray``   — newline-delimited ``vless://`` URIs, base64-wrapped (v2rayNG, Happ, Streisand, ...)
  * ``singbox`` — full sing-box config JSON (sing-box, Hiddify)
  * ``clash``   — Clash / Clash.Meta YAML (Clash family)

MTProto proxies are handed out separately (``tg://proxy`` links) because they are
not valid entries in a VLESS subscription.
"""
from __future__ import annotations

import base64
import json
from typing import Iterable, Sequence
from urllib.parse import quote

import aiosqlite

from .config import settings
from .keys import mtproto_link


def _vless_uri(user: aiosqlite.Row, srv: aiosqlite.Row) -> str:
    label = f"{settings.brand}"
    if srv["country"]:
        label += f" {srv['country']}"
    label += f" · {srv['name']}"
    params = {
        "encryption": "none",
        "flow": "xtls-rprx-vision",
        "security": "reality",
        "sni": srv["reality_sni"],
        "fp": "chrome",
        "pbk": srv["reality_public_key"],
        "sid": user["short_id"],
        "type": "tcp",
    }
    query = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params.items())
    return f"vless://{user['uuid']}@{srv['host']}:{srv['xray_port']}?{query}#{quote(label)}"


def vless_uris(user: aiosqlite.Row, servers: Sequence[aiosqlite.Row]) -> list[str]:
    return [_vless_uri(user, s) for s in servers if s["enable_xray"] and s["reality_public_key"]]


def mtproto_links(user: aiosqlite.Row, servers: Sequence[aiosqlite.Row]) -> list[tuple[str, str]]:
    """Return ``(label, tg://proxy link)`` for every MTProto-enabled server."""
    out: list[tuple[str, str]] = []
    for s in servers:
        if not s["enable_mtproto"]:
            continue
        link = mtproto_link(s["host"], s["mtproto_port"], user["mtproto_secret"],
                            s["mtproto_faketls_domain"])
        name = f"{s['country'] + ' ' if s['country'] else ''}{s['name']}".strip()
        out.append((name, link))
    return out


# --- format renderers -----------------------------------------------------

def render_v2ray(user: aiosqlite.Row, servers: Sequence[aiosqlite.Row]) -> str:
    body = "\n".join(vless_uris(user, servers))
    return base64.b64encode(body.encode()).decode()


def _singbox_outbound(user: aiosqlite.Row, srv: aiosqlite.Row) -> dict:
    tag = f"{srv['name']}"
    return {
        "type": "vless",
        "tag": tag,
        "server": srv["host"],
        "server_port": srv["xray_port"],
        "uuid": user["uuid"],
        "flow": "xtls-rprx-vision",
        "packet_encoding": "xudp",
        "tls": {
            "enabled": True,
            "server_name": srv["reality_sni"],
            "utls": {"enabled": True, "fingerprint": "chrome"},
            "reality": {
                "enabled": True,
                "public_key": srv["reality_public_key"],
                "short_id": user["short_id"],
            },
        },
    }


def render_singbox(user: aiosqlite.Row, servers: Sequence[aiosqlite.Row]) -> str:
    proxies = [_singbox_outbound(user, s) for s in servers
               if s["enable_xray"] and s["reality_public_key"]]
    tags = [p["tag"] for p in proxies]
    outbounds = [
        {"type": "selector", "tag": "proxy", "outbounds": ["auto", *tags], "default": "auto"},
        {"type": "urltest", "tag": "auto", "outbounds": tags,
         "url": "https://www.gstatic.com/generate_204", "interval": "3m", "tolerance": 100},
        {"type": "direct", "tag": "direct"},
        *proxies,
    ]
    doc = {
        "log": {"level": "warn"},
        "dns": {
            "servers": [
                {"tag": "google", "address": "tls://8.8.8.8"},
                {"tag": "local", "address": "223.5.5.5", "detour": "direct"},
            ],
        },
        "inbounds": [
            {"type": "tun", "tag": "tun-in", "auto_route": True, "strict_route": True,
             "stack": "mixed", "sniff": True},
        ],
        "outbounds": outbounds,
        "route": {
            "auto_detect_interface": True,
            "final": "proxy",
            "rules": [
                {"action": "sniff"},
                {"protocol": "dns", "action": "hijack-dns"},
                {"ip_is_private": True, "outbound": "direct"},
            ],
        },
    }
    return json.dumps(doc, indent=2, ensure_ascii=False)


def render_clash(user: aiosqlite.Row, servers: Sequence[aiosqlite.Row]) -> str:
    lines = [
        "mixed-port: 7890",
        "allow-lan: false",
        "mode: rule",
        "log-level: warning",
        "dns:",
        "  enable: true",
        "  enhanced-mode: fake-ip",
        "  nameserver: [https://8.8.8.8/dns-query, https://1.1.1.1/dns-query]",
        "proxies:",
    ]
    names: list[str] = []
    for s in servers:
        if not (s["enable_xray"] and s["reality_public_key"]):
            continue
        names.append(s["name"])
        lines.append(
            f"  - {{name: \"{s['name']}\", type: vless, server: {s['host']}, "
            f"port: {s['xray_port']}, uuid: {user['uuid']}, network: tcp, udp: true, "
            f"tls: true, flow: xtls-rprx-vision, servername: {s['reality_sni']}, "
            f"client-fingerprint: chrome, "
            f"reality-opts: {{public-key: {s['reality_public_key']}, short-id: {user['short_id']}}}}}"
        )
    joined = ", ".join(f'"{n}"' for n in names)
    lines += [
        "proxy-groups:",
        f"  - {{name: \"PROXY\", type: select, proxies: [\"AUTO\", {joined}]}}",
        f"  - {{name: \"AUTO\", type: url-test, url: \"https://www.gstatic.com/generate_204\", "
        f"interval: 180, proxies: [{joined}]}}",
        "rules:",
        "  - GEOIP,private,DIRECT,no-resolve",
        "  - MATCH,PROXY",
    ]
    return "\n".join(lines)


# --- client detection + dispatch ----------------------------------------

def pick_format(user_agent: str) -> str:
    ua = (user_agent or "").lower()
    if "clash" in ua or "mihomo" in ua or "stash" in ua:
        return "clash"
    if "sing-box" in ua or "hiddify" in ua:
        return "singbox"
    # v2rayNG, Happ, Streisand, Shadowrocket, NekoBox, default
    return "v2ray"


def render(user: aiosqlite.Row, servers: Sequence[aiosqlite.Row], fmt: str) -> str:
    return {
        "clash": render_clash,
        "singbox": render_singbox,
        "v2ray": render_v2ray,
    }[fmt](user, servers)


def content_type(fmt: str) -> str:
    return {
        "clash": "text/yaml; charset=utf-8",
        "singbox": "application/json; charset=utf-8",
        "v2ray": "text/plain; charset=utf-8",
    }[fmt]


# --- subscription headers (Happ / v2rayNG / Hiddify read these) ---------

def userinfo_header(user: aiosqlite.Row) -> str:
    total = user["traffic_limit_bytes"] or 0
    used = user["traffic_used_bytes"] or 0
    expire = user["expires_at"] or 0
    return f"upload=0; download={used}; total={total}; expire={expire}"


def sub_headers(user: aiosqlite.Row) -> dict[str, str]:
    title = base64.b64encode(settings.brand.encode()).decode()
    return {
        "profile-title": f"base64:{title}",
        "profile-update-interval": "1",
        "subscription-userinfo": userinfo_header(user),
        "support-url": settings.support_url,
        "profile-web-page-url": f"{settings.sub_base_url}/sub/{user['sub_token']}",
    }


# --- share links -------------------------------------------------------

def sub_url(user: aiosqlite.Row) -> str:
    return f"{settings.sub_base_url}/sub/{user['sub_token']}"


def happ_link(user: aiosqlite.Row) -> str:
    """Happ deep link that imports the subscription in one tap.

    ``happ://add/<data>``: if ``<data>`` is an http(s) URL Happ fetches the
    subscription from it directly; if it is base64 Happ decodes it first. We
    pass the raw URL — no encoding ambiguity across Happ versions.

    The subscription URL must be https with a CA-signed certificate on a domain
    the client's network can resolve — Happ refuses plain http / self-signed,
    and some ISPs blackhole wildcard-DNS hosts (sslip.io / nip.io).
    """
    return f"happ://add/{sub_url(user)}"
