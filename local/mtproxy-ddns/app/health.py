"""Reachability checks for a candidate MTProxy IP.

`tcp_ok` catches the common RKN block shapes (SYN drop / RST / null route).
`faketls_ok` is a best-effort MTProto FakeTLS ClientHello probe: a real proxy
that accepts the secret answers with a TLS ServerHello record (`16 03 03 ...`).
It is only meaningful when a raw secret is configured; treat a False from it as
"inconclusive" unless TCP also fails.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import socket
import struct
import time


def tcp_ok(ip: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def _client_hello(sni: str) -> bytearray:
    m = bytearray()
    m += b"\x16\x03\x01\x02\x00\x01\x00\x01\xfc\x03\x03" + os.urandom(32)
    m += b"\x20" + os.urandom(32)
    m += b"\x00\x22\x4a\x4a\x13\x01\x13\x02\x13\x03\xc0\x2b\xc0\x2f\xc0\x2c\xc0\x30\xcc\xa9"
    m += b"\xcc\xa8\xc0\x13\xc0\x14\x00\x9c\x00\x9d\x00\x2f\x00\x35\x00\x0a\x01\x00\x01\x91"
    m += b"\xda\xda\x00\x00\x00\x00"
    m += (len(sni) + 5).to_bytes(2, "big")
    m += (len(sni) + 3).to_bytes(2, "big") + b"\x00"
    m += len(sni).to_bytes(2, "big") + sni.encode("ascii")
    m += b"\x00\x17\x00\x00\xff\x01\x00\x01\x00\x00\x0a\x00\x0a\x00\x08\xaa\xaa\x00\x1d\x00"
    m += b"\x17\x00\x18\x00\x0b\x00\x02\x01\x00\x00\x23\x00\x00\x00\x10\x00\x0e\x00\x0c\x02"
    m += b"\x68\x32\x08\x68\x74\x74\x70\x2f\x31\x2e\x31\x00\x05\x00\x05\x01\x00\x00\x00\x00"
    m += b"\x00\x0d\x00\x14\x00\x12\x04\x03\x08\x04\x04\x01\x05\x03\x08\x05\x05\x01\x08\x06"
    m += b"\x06\x01\x02\x01\x00\x12\x00\x00\x00\x33\x00\x2b\x00\x29\xaa\xaa\x00\x01\x00\x00"
    m += b"\x1d\x00\x20" + os.urandom(32)
    m += b"\x00\x2d\x00\x02\x01\x01\x00\x2b\x00\x0b\x0a\xba\xba\x03\x04\x03\x03\x03\x02\x03"
    m += b"\x01\x00\x1b\x00\x03\x02\x00\x02\x3a\x3a\x00\x01\x00\x00\x15"
    m += (517 - len(m) - 2).to_bytes(2, "big")
    m += b"\x00" * (517 - len(m))
    return m


def build_faketls_client_hello(secret: bytes, sni: str) -> bytes:
    """A byte-accurate FakeTLS ClientHello, HMAC-signed with the proxy secret so
    a real MTProxy backend answers with a ServerHello. Split out from
    ``_faketls_attempt`` so a caller that already owns a socket (e.g. tunnelled
    through a residential proxy) can send/receive it without duplicating the
    ~40-line wire format."""
    ch = _client_hello(sni)
    ch[11:43] = b"\x00" * 32
    digest = bytearray(hmac.new(secret, bytes(ch), hashlib.sha256).digest())
    xor = struct.pack("<I", int(time.time()))
    for i in range(4):
        digest[28 + i] ^= xor[i]
    ch[11:43] = bytes(digest)
    return bytes(ch)


def _faketls_attempt(ip: str, port: int, secret: bytes, sni: str, timeout: float) -> bool:
    ch = build_faketls_client_hello(secret, sni)
    try:
        with socket.create_connection((ip, port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(ch)
            head = s.recv(5)
            return head[:3] == b"\x16\x03\x03"
    except OSError:
        return False


def faketls_ok(ip: str, port: int, secret_hex: str, sni: str, timeout: float,
               attempts: int = 3) -> bool:
    """True if the proxy answers a valid FakeTLS ClientHello with a ServerHello
    on at least one of ``attempts`` tries (the handshake is lossy even against
    healthy nodes, so a single miss is not a block)."""
    if len(secret_hex) != 32:
        return False
    try:
        secret = bytes.fromhex(secret_hex)
    except ValueError:
        return False
    for _ in range(max(1, attempts)):
        if _faketls_attempt(ip, port, secret, sni, timeout):
            return True
    return False


def reachable(cfg, ip: str) -> tuple[bool, str]:
    """Return (ok, reason). TCP is the gate; the FakeTLS probe is always run when
    a secret is configured, but only gates when ``healthcheck=faketls`` — under
    the default ``tcp`` mode its result is just logged so a persistent
    handshake failure across every IP is visible without breaking publishing.
    """
    mode = cfg.healthcheck
    if mode == "off":
        return True, "healthcheck=off"
    if not tcp_ok(ip, cfg.proxy_port, cfg.tcp_timeout):
        return False, "tcp connect failed"
    ft = None
    if cfg.secret_hex:
        ft = faketls_ok(ip, cfg.proxy_port, cfg.secret_hex, cfg.faketls_sni, cfg.tcp_timeout)
    if mode == "faketls":
        if not ft:
            return False, "faketls: no ServerHello"
        return True, "tcp+faketls ok"
    if ft is None:
        return True, "tcp ok"
    return True, f"tcp ok, faketls {'ok' if ft else 'NO-REPLY'}"
