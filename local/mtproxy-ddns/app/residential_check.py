"""Is a candidate MTProxy IP reachable *and does it actually speak MTProto* from
a real Russian residential/mobile connection?

`rucheck.py` (check-host.net) only has two Russian nodes and both sit on the
same datacenter ASN (AS210644, Moscow/SPb) — that network isn't behind the
consumer-ISP DPI equipment RKN actually filters through, so it can say
"reachable" for an IP that is, in practice, blocked for home/mobile users. This
module routes the real FakeTLS handshake through a Russian *residential* exit
(the owner's own maskify.su reseller pool, country-targeted with `-cc-RU`) via
HTTP CONNECT, so a positive result means a real ServerHello came back over an
actual residential Russian IP — not just an open TCP port on a hosting network.

Returns:
  True  — CONNECT succeeded and the FakeTLS handshake got a ServerHello back
  False — CONNECT succeeded but the handshake failed/timed out -> looks blocked
  None  — the residential gateway itself could not be used (auth failure, no
          credentials configured, tunnel refused) -> unknown, caller should not
          act on it
"""
from __future__ import annotations

import base64
import secrets
import socket

from .health import build_faketls_client_hello


def residential_ru_reachable(
    ip: str, port: int, secret_hex: str, sni: str,
    gateway_host: str, gateway_port: int, gateway_user: str, gateway_password: str,
    timeout: float = 10.0,
) -> bool | None:
    if not (gateway_host and gateway_user and gateway_password and len(secret_hex) == 32):
        return None
    login = f"{gateway_user}-cc-RU-s-{secrets.token_hex(6)}"
    try:
        secret = bytes.fromhex(secret_hex)
    except ValueError:
        return None
    try:
        with socket.create_connection((gateway_host, gateway_port), timeout=timeout) as s:
            s.settimeout(timeout)
            auth = base64.b64encode(f"{login}:{gateway_password}".encode()).decode()
            request = (
                f"CONNECT {ip}:{port} HTTP/1.1\r\n"
                f"Host: {ip}:{port}\r\n"
                f"Proxy-Authorization: Basic {auth}\r\n\r\n"
            ).encode()
            s.sendall(request)
            resp = b""
            while b"\r\n\r\n" not in resp and len(resp) < 8192:
                chunk = s.recv(4096)
                if not chunk:
                    break
                resp += chunk
            status_line = resp.split(b"\r\n", 1)[0]
            if b" 200 " not in status_line:
                return None  # gateway/auth problem, not a verdict on `ip`
            ch = build_faketls_client_hello(secret, sni)
            s.sendall(ch)
            head = s.recv(5)
            return head[:3] == b"\x16\x03\x03"
    except OSError:
        return None
