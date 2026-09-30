"""MTProxy dynamic-DNS updater with block-aware rotation.

Every cycle:
  1. fetch the IP MT3S currently advertises (`/public-listener`);
  2. health-check the IP clients are using right now (the one in DNS) and the
     advertised one — a TCP connect to :443, optionally a FakeTLS handshake;
  3. publish an IP only if it passes the health check. If the live record looks
     blocked, poll the listener harder to grab a fresh, working IP;
  4. if nothing is reachable, keep the last record and (optionally) alert.

DNS writes go straight into the local BIND zone file (named-checkzone → SOA
serial bump → `rndc reload`).
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .cloudflare import CloudflareZone, find_zone_id
from .config import Config
from .health import reachable
from .residential_check import residential_ru_reachable
from .rucheck import ru_tcp_reachable
from .zone import read_record_ip, set_records_ip

log = logging.getLogger("mtproxy-ddns")

_cf_zone: CloudflareZone | None = None


def cf(cfg: Config) -> CloudflareZone:
    """Lazily build the Cloudflare client (one per process)."""
    global _cf_zone
    if _cf_zone is None:
        if not cfg.cf_api_token:
            raise TransientError("CF_API_TOKEN not set but dns_backend needs Cloudflare")
        zone_id = cfg.cf_zone_id or find_zone_id(cfg.cf_api_token, cfg.zone, cfg.http_timeout)
        _cf_zone = CloudflareZone(cfg.cf_api_token, zone_id, cfg.zone, cfg.http_timeout)
    return _cf_zone


def _uses_bind(cfg: Config) -> bool:
    return cfg.dns_backend in ("bind", "both")


def _uses_cf(cfg: Config) -> bool:
    return cfg.dns_backend in ("cloudflare", "both")


class TransientError(RuntimeError):
    """Recoverable — keep the current record, retry next cycle."""


@dataclass
class State:
    healthy: bool = True
    unhealthy_since: float = 0.0
    last_alert: float = 0.0
    ru_ip: str = ""
    ru_ok: object = None       # True / False / None (unknown), for ru_ip
    ru_at: float = 0.0
    published_at: float = field(default_factory=time.time)

    def mark_healthy(self) -> None:
        if not self.healthy:
            log.info("recovered — a reachable MTProxy IP is published again")
        self.healthy = True
        self.unhealthy_since = 0.0

    def mark_unhealthy(self) -> None:
        if self.healthy:
            self.unhealthy_since = time.time()
        self.healthy = False


def fetch_listener_ip(cfg: Config) -> str:
    req = urllib.request.Request(
        cfg.listener_url,
        headers={"Authorization": f"Bearer {cfg.api_token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg.http_timeout) as resp:
            raw = resp.read(65536)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise TransientError(f"listener request failed: {e}") from e
    try:
        data = json.loads(raw)
        ip = str(data["ipv4"]).strip()
    except (ValueError, KeyError, TypeError) as e:
        raise TransientError(f"bad listener response: {raw[:200]!r}") from e
    try:
        parsed = ipaddress.ip_address(ip)
    except ValueError as e:
        raise TransientError(f"not an IP: {ip!r}") from e
    if parsed.version != 4 or not parsed.is_global:
        raise TransientError(f"not a public IPv4: {ip}")
    return ip


def _run(cmd: list[str]) -> tuple[int, str]:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return p.returncode, (p.stdout + p.stderr).strip()


def current_record_ip(cfg: Config) -> str | None:
    """The IP clients are being handed right now. Read from whichever backend is
    authoritative (BIND when it is in play, otherwise Cloudflare)."""
    if _uses_bind(cfg):
        try:
            return read_record_ip(cfg.zone_file.read_text("utf-8"), cfg.record_name)
        except FileNotFoundError as e:
            raise TransientError(f"zone file missing: {cfg.zone_file}") from e
    try:
        return cf(cfg).get_ip(cfg.record_name)
    except Exception as e:  # noqa: BLE001 — any CF hiccup is transient
        raise TransientError(f"cloudflare read failed: {e}") from e


def records_all_match(cfg: Config, ip: str) -> bool:
    """True when every managed name already points at ``ip`` — used to force a
    rewrite when a name was just added to MTPROXY_RECORD_NAME."""
    if _uses_bind(cfg):
        try:
            text = cfg.zone_file.read_text("utf-8")
        except FileNotFoundError:
            return False
        return all(read_record_ip(text, name) == ip for name in cfg.record_names)
    try:
        return cf(cfg).all_match(cfg.record_names, ip)
    except Exception:  # noqa: BLE001
        return False


def apply_ip(cfg: Config, new_ip: str) -> None:
    if _uses_cf(cfg):
        try:
            cf(cfg).set_ip(cfg.record_names, new_ip, cfg.record_ttl)
        except Exception as e:  # noqa: BLE001
            if cfg.dns_backend == "cloudflare":
                raise TransientError(f"cloudflare update failed: {e}") from e
            log.warning("cloudflare update failed (BIND still primary): %s", e)
    if not _uses_bind(cfg):
        return
    st = cfg.zone_file.stat()  # preserve owner/group/mode — BIND reads as user "bind"
    original = cfg.zone_file.read_text("utf-8")
    updated = set_records_ip(original, cfg.record_names, new_ip, cfg.record_ttl)

    fd, tmp_path = tempfile.mkstemp(dir=str(cfg.zone_file.parent), prefix=".ddns-", suffix=".tmp")
    tmp = Path(tmp_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(updated)
        os.chmod(tmp, st.st_mode & 0o777)
        try:
            os.chown(tmp, st.st_uid, st.st_gid)
        except (PermissionError, OSError):
            pass
        rc, out = _run([cfg.named_checkzone, cfg.zone, str(tmp)])
        if rc != 0:
            raise TransientError(f"named-checkzone rejected the new zone: {out}")
        os.replace(tmp, cfg.zone_file)
    finally:
        if tmp.exists():
            tmp.unlink()

    rc, out = _run([cfg.rndc, "reload", cfg.zone])
    if rc != 0:
        cfg.zone_file.write_text(original, encoding="utf-8")
        _run([cfg.rndc, "reload", cfg.zone])
        raise TransientError(f"rndc reload failed, rolled back: {out}")


def _write_state(cfg: Config, ip: str) -> None:
    try:
        cfg.state_file.parent.mkdir(parents=True, exist_ok=True)
        cfg.state_file.write_text(ip + "\n", encoding="utf-8")
    except OSError as e:
        log.warning("could not persist state: %s", e)


def _alert(cfg: Config, text: str) -> None:
    if not (cfg.tg_bot_token and cfg.tg_chat_id):
        return
    try:
        body = urllib.parse.urlencode({"chat_id": cfg.tg_chat_id, "text": text}).encode()
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{cfg.tg_bot_token}/sendMessage", data=body, timeout=10
        )
    except OSError as e:
        log.warning("telegram alert failed: %s", e)


def ru_gate(cfg: Config, state: State, ip: str, force: bool = False) -> bool:
    """Is ``ip`` reachable from Russia (per check-host.net)? Cached per IP for
    ``ru_check_interval`` seconds. Returns True when the check is disabled or
    inconclusive — only a definite "blocked" verdict returns False."""
    if not cfg.ru_check or not ip:
        return True
    fresh = (ip == state.ru_ip and state.ru_at
             and (time.time() - state.ru_at) < cfg.ru_check_interval)
    if fresh and not force:
        return state.ru_ok is not False

    residential_host = getattr(cfg, "residential_gateway_host", "")
    source = "check-host.net"
    result = None
    if residential_host:
        source = "RU residential exit"
        result = residential_ru_reachable(
            ip, cfg.proxy_port, cfg.secret_hex, cfg.faketls_sni,
            residential_host, cfg.residential_gateway_port,
            cfg.residential_gateway_user, cfg.residential_gateway_password,
        )
        if result is None:
            log.debug("residential RU gateway unusable this cycle — falling back to check-host.net")
    if result is None:
        source = "check-host.net"
        result = ru_tcp_reachable(
            ip, cfg.proxy_port, cfg.ru_check_nodes,
            require_all=cfg.ru_check_require_all, min_ok=cfg.ru_check_min_ok,
        )

    state.ru_ip, state.ru_ok, state.ru_at = ip, result, time.time()
    if result is False:
        log.warning("%s NOT reachable from Russia (%s)", ip, source)
    elif result is None:
        log.debug("RU reachability check for %s inconclusive — ignoring", ip)
    return result is not False


def _candidate_ok(cfg: Config, state: State, ip: str, force_ru: bool = False) -> bool:
    return reachable(cfg, ip)[0] and ru_gate(cfg, state, ip, force=force_ru)


def _pick_target(cfg: Config, state: State, have: str | None, have_ok: bool,
                 listener_ip: str | None):
    """Choose an IP to publish that passes the health check. Returns (ip|None, why)."""
    if listener_ip and _candidate_ok(cfg, state, listener_ip):
        if have_ok and have == listener_ip:
            return have, "listener==record, healthy"
        return listener_ip, "healthy listener IP"
    if have_ok and have:
        return have, "keeping healthy record (listener IP unreachable)"

    # live record and listener IP both look blocked — hunt for a fresh working IP
    seen = {listener_ip} if listener_ip else set()
    for i in range(cfg.probe_retries):
        time.sleep(cfg.probe_retry_delay)
        try:
            ip = fetch_listener_ip(cfg)
        except TransientError:
            continue
        if ip in seen:
            continue
        seen.add(ip)
        if _candidate_ok(cfg, state, ip, force_ru=True):
            return ip, f"rotated to fresh healthy IP after {i + 1} retr{'y' if i == 0 else 'ies'}"
    return None, "no reachable IP after retries"


def run_once(cfg: Config, state: State) -> None:
    have = current_record_ip(cfg)

    try:
        listener_ip = fetch_listener_ip(cfg)
    except TransientError as e:
        listener_ip = None
        log.warning("listener unavailable: %s", e)

    have_ok = True
    if have and cfg.healthcheck != "off":
        ok, why = reachable(cfg, have)
        have_ok = ok
        if not ok:
            log.warning("published %s.%s = %s looks BLOCKED (%s)",
                        cfg.record_label, cfg.zone, have, why)

    if have and have_ok and not ru_gate(cfg, state, have):
        have_ok = False
        log.warning("published %s.%s = %s is up but NOT reachable from Russia — rotating",
                    cfg.record_label, cfg.zone, have)

    max_age = getattr(cfg, "max_ip_age", 0) or 0
    force_due = bool(max_age and time.time() - state.published_at >= max_age)

    if listener_ip is None and have_ok and not force_due:
        return  # nothing to do, current record still fine

    target, why = _pick_target(cfg, state, have, have_ok, listener_ip)

    if target is None:
        state.mark_unhealthy()
        down_for = int(time.time() - state.unhealthy_since) if state.unhealthy_since else 0
        log.error("no reachable MTProxy IP (listener=%s record=%s) — record left at %s, down %ss",
                  listener_ip, have, have or "nothing", down_for)
        if (cfg.alert_after_seconds and down_for >= cfg.alert_after_seconds
                and time.time() - state.last_alert >= cfg.alert_after_seconds):
            state.last_alert = time.time()
            _alert(cfg, f"⚠️ mtproxy-ddns: нет живого IP для {cfg.record_label}.{cfg.zone} "
                        f"уже {down_for // 60} мин. listener={listener_ip}, запись={have}")
        return

    state.mark_healthy()
    if target != have or not records_all_match(cfg, target):
        apply_ip(cfg, target)
        _write_state(cfg, target)
        log.info("%s.%s: %s -> %s (%s)", cfg.record_label, cfg.zone, have or "(none)", target, why)
        state.published_at = time.time()
    elif force_due:
        log.info("%s.%s: periodic refresh — %s still current listener IP (max_ip_age reached)",
                  cfg.record_label, cfg.zone, target)
        state.published_at = time.time()
    else:
        log.debug("%s.%s stays %s (%s)", cfg.record_label, cfg.zone, have, why)


def main() -> None:
    logging.basicConfig(
        level=os.getenv("MTPROXY_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = Config.from_env()
    dest = {"bind": str(cfg.zone_file), "cloudflare": "cloudflare-api",
            "both": f"{cfg.zone_file} + cloudflare-api"}.get(cfg.dns_backend, cfg.dns_backend)
    log.info(
        "started | %s -> %s (%s) ttl=%ss poll=%s/%ss healthcheck=%s:%s ru_check=%s max_ip_age=%ss",
        cfg.listener_url, f"{cfg.record_label}.{cfg.zone}", dest,
        cfg.record_ttl, cfg.poll_seconds, cfg.poll_seconds_blocked,
        cfg.healthcheck, cfg.proxy_port,
        (",".join(cfg.ru_check_nodes) if cfg.ru_check else "off"),
        cfg.max_ip_age or "off",
    )
    state = State()
    while True:
        try:
            run_once(cfg, state)
        except TransientError as e:
            log.warning("keeping current record: %s", e)
        except Exception:
            log.exception("unexpected error (record left as-is)")
        time.sleep(cfg.poll_seconds if state.healthy else cfg.poll_seconds_blocked)


if __name__ == "__main__":
    main()
