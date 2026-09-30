"""Runtime configuration, loaded from ``.env`` in the project root.

Every knob has a sane default so the panel boots with only ``BOT_TOKEN`` and
``ADMIN_IDS`` set. Values are read once at import time into :data:`settings`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Load ``.env`` with the file winning over the ambient environment.

    This project is meant to be self-contained; the surrounding shell may already
    export a ``BOT_TOKEN`` (e.g. kiro-bot's own), and silently inheriting it would
    make the panel run as the wrong bot.
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ[key.strip()] = val.strip().strip('"').strip("'")


_load_dotenv(BASE_DIR / ".env")


def _int_list(name: str) -> list[int]:
    raw = os.environ.get(name, "")
    return [int(x) for x in raw.replace(";", ",").split(",") if x.strip().lstrip("-").isdigit()]


@dataclass(frozen=True)
class Settings:
    # --- telegram ---
    bot_token: str = os.environ.get("BOT_TOKEN", "")
    admin_ids: list[int] = field(default_factory=lambda: _int_list("ADMIN_IDS"))

    # --- subscription web ---
    # Public origin that serves GET /sub/<token>. Put a domain behind Cloudflare
    # here in production; the raw host:port works for a first run.
    sub_base_url: str = os.environ.get("SUB_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
    web_host: str = os.environ.get("WEB_HOST", "0.0.0.0")
    web_port: int = int(os.environ.get("WEB_PORT", "8080"))
    brand: str = os.environ.get("BRAND", "MyVPN")
    support_url: str = os.environ.get("SUPPORT_URL", "https://t.me/")

    # --- trial / plans ---
    trial_days: int = int(os.environ.get("TRIAL_DAYS", "3"))
    trial_traffic_gb: int = int(os.environ.get("TRIAL_TRAFFIC_GB", "10"))
    device_limit: int = int(os.environ.get("DEVICE_LIMIT", "3"))

    # --- payments ---
    # Telegram Stars is always available. Star-to-currency is display only.
    stars_per_unit: int = int(os.environ.get("STARS_PER_UNIT", "1"))
    crypto_pay_token: str = os.environ.get("CRYPTO_PAY_TOKEN", "")  # @CryptoBot app token, optional

    # --- provisioning defaults (per-server overridable) ---
    xray_port: int = int(os.environ.get("XRAY_PORT", "443"))
    mtproto_port: int = int(os.environ.get("MTPROTO_PORT", "8443"))
    # REALITY steal-oneself target. www.microsoft.com is a poor choice — it emits
    # TLS records in a non-standard order that breaks REALITY's handshake relay
    # (clients get EOF, server logs "handshake did not complete successfully").
    # www.apple.com is a large, well-behaved TLS 1.3 / HTTP2 origin.
    reality_dest: str = os.environ.get("REALITY_DEST", "www.apple.com:443")
    reality_sni: str = os.environ.get("REALITY_SNI", "www.apple.com")
    ssh_timeout: int = int(os.environ.get("SSH_TIMEOUT", "600"))

    db_path: str = os.environ.get("DB_PATH", str(BASE_DIR / "data" / "vpn.db"))

    @property
    def has_stars(self) -> bool:
        return bool(self.bot_token)


settings = Settings()
(BASE_DIR / "data").mkdir(exist_ok=True)
