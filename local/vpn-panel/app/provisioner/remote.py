"""Remote-side artefacts: the one-time bootstrap script and the per-sync configs.

Everything here is a pure function of DB rows. ``provisioner.sync_server`` renders
these, pushes them over SFTP and restarts the services.
"""
from __future__ import annotations

import json
import re
from typing import Sequence

import aiosqlite

XRAY_CONFIG_PATH = "/usr/local/etc/xray/config.json"
XRAY_BIN = "/usr/local/bin/xray"
MTP_DIR = "/opt/mtprotoproxy"
MTP_CONFIG_PATH = f"{MTP_DIR}/config.py"


def bootstrap_script(xray_port: int, mtproto_port: int) -> str:
    return f"""
export DEBIAN_FRONTEND=noninteractive
if command -v apt-get >/dev/null 2>&1; then
    apt-get update -y -qq
    apt-get install -y -qq curl git ca-certificates python3 ufw >/dev/null
elif command -v dnf >/dev/null 2>&1; then
    dnf install -y -q curl git python3 >/dev/null
fi

# --- Xray-core (official installer, idempotent) ---
if [ ! -x {XRAY_BIN} ]; then
    curl -Ls https://github.com/XTLS/Xray-install/raw/main/install-release.sh -o /tmp/xray-install.sh
    bash /tmp/xray-install.sh install >/dev/null
fi

# --- mtprotoproxy (alexbers, per-user secrets) ---
if [ ! -d {MTP_DIR} ]; then
    git clone -q --depth 1 https://github.com/alexbers/mtprotoproxy.git {MTP_DIR}
fi
cat > /etc/systemd/system/mtprotoproxy.service <<'UNIT'
[Unit]
Description=mtprotoproxy
After=network.target
[Service]
Type=simple
WorkingDirectory={MTP_DIR}
ExecStart=/usr/bin/python3 {MTP_DIR}/mtprotoproxy.py
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload

# --- firewall (best effort) ---
if command -v ufw >/dev/null 2>&1; then
    ufw allow 22/tcp >/dev/null 2>&1 || true
    ufw allow {xray_port}/tcp >/dev/null 2>&1 || true
    ufw allow {mtproto_port}/tcp >/dev/null 2>&1 || true
    yes | ufw enable >/dev/null 2>&1 || true
fi

# --- REALITY keypair (printed for the panel to capture) ---
echo '---XRAY-X25519---'
{XRAY_BIN} x25519
echo '---END---'
"""


_KEY_RE_PRIV = re.compile(r"(?i)private\s*key[^:\n]*:\s*(\S+)")
_KEY_RE_PUB = re.compile(r"(?i)(?:public\s*key|password)[^:\n]*:\s*(\S+)")


def parse_x25519(output: str) -> tuple[str, str]:
    """Return ``(private_key, public_key)`` from ``xray x25519`` output.

    Xray's label wording has drifted across releases — all of these appear in
    the wild and must parse:
        ``Private key: X``      / ``Public key: Y``
        ``PrivateKey: X``       / ``Password: Y``
        ``PrivateKey: X``       / ``Password (PublicKey): Y``  + a ``Hash32:`` line
    The ``[^:\\n]*`` before the colon absorbs any ``(PublicKey)``-style suffix.
    """
    seg = output
    if "---XRAY-X25519---" in output:
        seg = output.split("---XRAY-X25519---", 1)[1].split("---END---", 1)[0]
    priv = _KEY_RE_PRIV.search(seg)
    pub = _KEY_RE_PUB.search(seg)
    if not priv or not pub:
        raise ValueError(f"cannot parse x25519 output:\n{output[-400:]}")
    return priv.group(1), pub.group(1)


def xray_config(srv: aiosqlite.Row, users: Sequence[aiosqlite.Row]) -> str:
    clients = [
        {"id": u["uuid"], "email": f"u{u['tg_id']}", "flow": "xtls-rprx-vision"}
        for u in users
    ]
    short_ids = sorted({u["short_id"] for u in users}) or [""]
    dest = srv["reality_dest"]
    server_names = [srv["reality_sni"]]
    doc = {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "tag": "vless-reality",
                "listen": "0.0.0.0",
                "port": srv["xray_port"],
                "protocol": "vless",
                "settings": {"clients": clients, "decryption": "none"},
                "streamSettings": {
                    "network": "tcp",
                    "security": "reality",
                    "realitySettings": {
                        "show": False,
                        "dest": dest,
                        "xver": 0,
                        "serverNames": server_names,
                        "privateKey": srv["reality_private_key"],
                        "shortIds": short_ids,
                    },
                },
                "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"]},
            }
        ],
        "outbounds": [
            {"protocol": "freedom", "tag": "direct"},
            {"protocol": "blackhole", "tag": "block"},
        ],
        "routing": {
            "rules": [
                {"type": "field", "ip": ["geoip:private"], "outboundTag": "block"},
                {"type": "field", "protocol": ["bittorrent"], "outboundTag": "block"},
            ]
        },
    }
    return json.dumps(doc, indent=2)


_HEX32_RE = re.compile(r"^[0-9a-f]{32}$")


def norm_hex32(raw: str) -> str:
    """Return a canonical 16-byte hex string (32 lowercase chars) or ``""``.

    Used for both the mtprotoproxy ``AD_TAG`` (proxy tag from @MTProxybot) and the
    static "house" MTProto secret. Tolerates surrounding whitespace and an
    accidental ``0x`` prefix; rejects anything else so a typo never lands in a
    server config.
    """
    t = (raw or "").strip().lower()
    if t.startswith("0x"):
        t = t[2:]
    return t if _HEX32_RE.match(t) else ""


# Back-compat alias — the admin import still calls it ``normalize_ad_tag``.
normalize_ad_tag = norm_hex32


def mtproto_config(
    srv: aiosqlite.Row,
    users: Sequence[aiosqlite.Row],
    ad_tag: str = "",
    house_secret: str = "",
) -> str:
    user_map = {f"u{u['tg_id']}": u["mtproto_secret"] for u in users}
    house = norm_hex32(house_secret)
    if house:
        # Always-on public secret for the sponsored proxy: it does not expire and
        # is the secret registered with @MTProxybot / shared in the public link.
        user_map["house"] = house
    if not user_map:  # mtprotoproxy refuses to start with no users
        user_map = {"disabled": "00000000000000000000000000000000"}
    lines = [
        "# generated by vpn-panel — do not edit by hand",
        f"PORT = {srv['mtproto_port']}",
        f"USERS = {json.dumps(user_map, indent=4)}",
        "MODES = {",
        "    'classic': False,",
        "    'secure': False,",
        "    'tls': True,",
        "}",
        f"TLS_DOMAIN = {json.dumps(srv['mtproto_faketls_domain'])}",
    ]
    tag = norm_hex32(ad_tag)
    if tag:
        # Promoted ("sponsored") channel: mtprotoproxy shows the channel bound to
        # this tag on @MTProxybot to everyone who connects through the proxy.
        lines.append(f"AD_TAG = {json.dumps(tag)}")
    return "\n".join(lines) + "\n"
