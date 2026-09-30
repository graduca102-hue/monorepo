"""Per-user secret material.

Every user gets one set of secrets that is reused across every server and every
protocol. A leak is revoked per-user (drop the client from every node config),
never per-node — same model the analysed commercial services use.
"""
from __future__ import annotations

import secrets
import uuid


def new_uuid() -> str:
    """VLESS client id."""
    return str(uuid.uuid4())


def new_short_id() -> str:
    """REALITY shortId: hex, even length, 2..16 chars. 8 bytes is the common choice."""
    return secrets.token_hex(8)


def new_proto_password() -> str:
    """Password for Hysteria2 / Trojan-style transports (kept for future use)."""
    return secrets.token_hex(12)


def new_sub_token() -> str:
    """Opaque, rotatable handle that appears in the subscription URL."""
    return secrets.token_urlsafe(18)


def new_mtproto_secret() -> str:
    """Raw 16-byte MTProto secret as 32 hex chars (the FakeTLS ``ee`` prefix and
    the domain suffix are added when the shareable link is built)."""
    return secrets.token_hex(16)


def mtproto_link(host: str, port: int, raw_secret: str, faketls_domain: str) -> str:
    """Build a ``tg://proxy`` link for a FakeTLS (``ee``) MTProto secret."""
    dom_hex = faketls_domain.encode().hex()
    secret = f"ee{raw_secret}{dom_hex}"
    return f"tg://proxy?server={host}&port={port}&secret={secret}"
