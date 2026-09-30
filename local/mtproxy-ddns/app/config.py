from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_env_file() -> None:
    """Minimal .env loader (no external dependency). Existing env wins."""
    path = Path(os.getenv("MTPROXY_ENV_FILE") or (Path(__file__).resolve().parent.parent / ".env"))
    try:
        text = path.read_text("utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


_load_env_file()


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, "") or default)


def _i(name: str, default: int) -> int:
    return int(os.getenv(name, "") or default)


def _b(name: str, default: bool) -> bool:
    v = os.getenv(name, "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    listener_url: str
    api_token: str
    zone: str
    record_names: tuple[str, ...]
    zone_file: Path
    record_ttl: int
    # DNS backend: "bind" (edit local zone file), "cloudflare" (CF API),
    # or "both" (write BIND + CF — used while migrating nameservers)
    dns_backend: str
    cf_api_token: str
    cf_zone_id: str
    poll_seconds: float
    poll_seconds_blocked: float
    http_timeout: float
    rndc: str
    named_checkzone: str
    state_file: Path
    # force a fresh listener lookup + republish at least this often, even when
    # the published record still looks healthy (0 disables). Health/RU checks
    # can only see block *shapes* they know how to test for (TCP reset, no RU
    # route) — they cannot see mid-session DPI drops, so this is a periodic
    # safety net independent of them.
    max_ip_age: float
    # health checking / block-aware rotation
    proxy_port: int
    secret_hex: str
    faketls_sni: str
    healthcheck: str          # "tcp" | "faketls" | "off"
    tcp_timeout: float
    probe_retries: int         # extra listener polls when the live IP looks blocked
    probe_retry_delay: float
    alert_after_seconds: float
    tg_bot_token: str
    tg_chat_id: str
    # "reachable from Russia?" check via check-host.net
    ru_check: bool
    ru_check_nodes: tuple[str, ...]
    ru_check_interval: float
    ru_check_require_all: bool
    ru_check_min_ok: int
    # optional: real Russian *residential* exit (HTTP CONNECT) to run the RU
    # check through instead — check-host.net's RU nodes are a single
    # datacenter ASN, not behind the consumer-ISP DPI RKN actually filters at.
    residential_gateway_host: str
    residential_gateway_port: int
    residential_gateway_user: str
    residential_gateway_password: str

    @property
    def record_name(self) -> str:
        """Canonical record name (first) — the one we read the live IP from."""
        return self.record_names[0]

    @property
    def record_label(self) -> str:
        return self.record_names[0] if len(self.record_names) == 1 else \
            f"{{{','.join(self.record_names)}}}"

    @classmethod
    def from_env(cls) -> "Config":
        token = (os.getenv("MT3S_API_TOKEN") or "").strip()
        if not token:
            raise RuntimeError("MT3S_API_TOKEN не задан")
        zone = (os.getenv("MTPROXY_ZONE") or "aikort.lol").strip().rstrip(".")
        zone_file = os.getenv("MTPROXY_ZONE_FILE") or f"/etc/bind/db.{zone}"
        return cls(
            listener_url=(os.getenv("MT3S_LISTENER_URL")
                          or "https://api.confmtbot.com/public-listener").strip(),
            api_token=token,
            zone=zone,
            record_names=tuple(
                n.strip() for n in (os.getenv("MTPROXY_RECORD_NAME") or "mt").split(",") if n.strip()
            ) or ("mt",),
            zone_file=Path(zone_file),
            record_ttl=_i("MTPROXY_RECORD_TTL", 60),
            dns_backend=(os.getenv("MTPROXY_DNS_BACKEND") or "bind").strip().lower(),
            cf_api_token=(os.getenv("CF_API_TOKEN") or "").strip(),
            cf_zone_id=(os.getenv("CF_ZONE_ID") or "").strip(),
            poll_seconds=_f("MTPROXY_POLL_SECONDS", 10),
            poll_seconds_blocked=_f("MTPROXY_POLL_SECONDS_BLOCKED", 3),
            http_timeout=_f("MTPROXY_HTTP_TIMEOUT", 5),
            rndc=os.getenv("MTPROXY_RNDC", "rndc"),
            named_checkzone=os.getenv("MTPROXY_NAMED_CHECKZONE", "named-checkzone"),
            state_file=Path(os.getenv("MTPROXY_STATE_FILE")
                            or str(Path(__file__).resolve().parent.parent / "data" / "last_ip")),
            max_ip_age=_f("MTPROXY_MAX_IP_AGE", 300),
            proxy_port=_i("MTPROXY_PROXY_PORT", 443),
            secret_hex=(os.getenv("MTPROXY_SECRET_HEX") or "").strip().lower(),
            faketls_sni=(os.getenv("MTPROXY_FAKETLS_SNI") or "xapi.ozon.ru").strip(),
            healthcheck=(os.getenv("MTPROXY_HEALTHCHECK") or "tcp").strip().lower(),
            tcp_timeout=_f("MTPROXY_TCP_TIMEOUT", 4),
            probe_retries=_i("MTPROXY_PROBE_RETRIES", 6),
            probe_retry_delay=_f("MTPROXY_PROBE_RETRY_DELAY", 2),
            alert_after_seconds=_f("MTPROXY_ALERT_AFTER_SECONDS", 300),
            tg_bot_token=(os.getenv("MTPROXY_TG_BOT_TOKEN") or "").strip(),
            tg_chat_id=(os.getenv("MTPROXY_TG_CHAT_ID") or "").strip(),
            ru_check=_b("MTPROXY_RUCHECK", False),
            ru_check_nodes=tuple(
                n.strip() for n in (os.getenv("MTPROXY_RUCHECK_NODES")
                                    or "ru2.node.check-host.net,ru3.node.check-host.net").split(",")
                if n.strip()
            ),
            ru_check_interval=_f("MTPROXY_RUCHECK_INTERVAL", 240),
            ru_check_require_all=_b("MTPROXY_RUCHECK_REQUIRE_ALL", True),
            ru_check_min_ok=_i("MTPROXY_RUCHECK_MIN_OK", 1),
            residential_gateway_host=(os.getenv("MTPROXY_RESIDENTIAL_HOST") or "").strip(),
            residential_gateway_port=_i("MTPROXY_RESIDENTIAL_PORT", 80),
            residential_gateway_user=(os.getenv("MTPROXY_RESIDENTIAL_USER") or "").strip(),
            residential_gateway_password=(os.getenv("MTPROXY_RESIDENTIAL_PASSWORD") or "").strip(),
        )
