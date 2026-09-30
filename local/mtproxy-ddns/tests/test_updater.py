import os
import sys
import time
import types

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import updater as u  # noqa: E402


class Cfg(types.SimpleNamespace):
    pass


def base_cfg(**over):
    d = dict(
        record_name="mt", record_names=("mt",), record_label="mt",
        zone="aikort.lol", healthcheck="tcp", proxy_port=443,
        secret_hex="", faketls_sni="xapi.ozon.ru", tcp_timeout=1,
        probe_retries=3, probe_retry_delay=0, alert_after_seconds=300,
        tg_bot_token="", tg_chat_id="", record_ttl=30,
        ru_check=False, ru_check_nodes=("ru2.node.check-host.net",),
        ru_check_interval=240, ru_check_require_all=True, ru_check_min_ok=1,
    )
    d.update(over)
    return Cfg(**d)


def _st():
    return u.State()


def test_pick_target_keeps_when_listener_matches_and_healthy(monkeypatch):
    monkeypatch.setattr(u, "reachable", lambda cfg, ip: (True, "ok"))
    ip, why = u._pick_target(base_cfg(), _st(), have="1.1.1.1", have_ok=True, listener_ip="1.1.1.1")
    assert ip == "1.1.1.1" and "healthy" in why


def test_pick_target_takes_fresh_listener_ip(monkeypatch):
    monkeypatch.setattr(u, "reachable", lambda cfg, ip: (True, "ok"))
    ip, why = u._pick_target(base_cfg(), _st(), have="1.1.1.1", have_ok=True, listener_ip="2.2.2.2")
    assert ip == "2.2.2.2"


def test_pick_target_avoids_blocked_listener_ip_keeps_good_record(monkeypatch):
    monkeypatch.setattr(u, "reachable", lambda cfg, ip: (ip != "9.9.9.9", "x"))
    ip, why = u._pick_target(base_cfg(), _st(), have="1.1.1.1", have_ok=True, listener_ip="9.9.9.9")
    assert ip == "1.1.1.1" and "keeping healthy record" in why


def test_pick_target_hunts_for_working_ip_when_all_known_blocked(monkeypatch):
    good = {"5.5.5.5"}
    monkeypatch.setattr(u, "reachable", lambda cfg, ip: (ip in good, "x"))
    seq = iter(["9.9.9.9", "9.9.9.9", "5.5.5.5"])
    monkeypatch.setattr(u, "fetch_listener_ip", lambda cfg: next(seq))
    ip, why = u._pick_target(base_cfg(), _st(), have="8.8.8.8", have_ok=False, listener_ip="9.9.9.9")
    assert ip == "5.5.5.5" and "rotated" in why


def test_pick_target_gives_up(monkeypatch):
    monkeypatch.setattr(u, "reachable", lambda cfg, ip: (False, "blocked"))
    monkeypatch.setattr(u, "fetch_listener_ip", lambda cfg: "9.9.9.9")
    ip, why = u._pick_target(base_cfg(), _st(), have="8.8.8.8", have_ok=False, listener_ip="9.9.9.9")
    assert ip is None


def test_ru_gate_disabled_is_always_true():
    assert u.ru_gate(base_cfg(ru_check=False), _st(), "1.2.3.4") is True


def test_ru_gate_blocks_and_caches(monkeypatch):
    calls = []
    monkeypatch.setattr(u, "ru_tcp_reachable",
                        lambda *a, **k: (calls.append(1), False)[1])
    cfg, st = base_cfg(ru_check=True), _st()
    assert u.ru_gate(cfg, st, "1.2.3.4") is False
    assert u.ru_gate(cfg, st, "1.2.3.4") is False  # cached, no 2nd call
    assert len(calls) == 1


def test_ru_gate_inconclusive_is_not_treated_as_blocked(monkeypatch):
    monkeypatch.setattr(u, "ru_tcp_reachable", lambda *a, **k: None)
    assert u.ru_gate(base_cfg(ru_check=True), _st(), "1.2.3.4") is True


def test_ru_gate_prefers_residential_gateway_when_configured(monkeypatch):
    checkhost_calls = []
    monkeypatch.setattr(u, "ru_tcp_reachable",
                        lambda *a, **k: (checkhost_calls.append(1), True)[1])
    monkeypatch.setattr(u, "residential_ru_reachable", lambda *a, **k: False)
    cfg = base_cfg(ru_check=True, residential_gateway_host="gw", residential_gateway_port=80,
                    residential_gateway_user="u", residential_gateway_password="p")
    assert u.ru_gate(cfg, _st(), "1.2.3.4") is False  # residential verdict wins
    assert not checkhost_calls  # check-host.net never consulted


def test_ru_gate_falls_back_to_checkhost_when_residential_gateway_unusable(monkeypatch):
    monkeypatch.setattr(u, "residential_ru_reachable", lambda *a, **k: None)
    monkeypatch.setattr(u, "ru_tcp_reachable", lambda *a, **k: False)
    cfg = base_cfg(ru_check=True, residential_gateway_host="gw", residential_gateway_port=80,
                    residential_gateway_user="u", residential_gateway_password="p")
    assert u.ru_gate(cfg, _st(), "1.2.3.4") is False


def test_pick_target_rotates_past_ru_blocked_listener(monkeypatch):
    monkeypatch.setattr(u, "reachable", lambda cfg, ip: (True, "ok"))
    ru = {"7.7.7.7": True}  # only this IP is reachable from RU
    monkeypatch.setattr(u, "ru_tcp_reachable", lambda ip, *a, **k: ru.get(ip, False))
    seq = iter(["3.3.3.3", "7.7.7.7"])
    monkeypatch.setattr(u, "fetch_listener_ip", lambda cfg: next(seq))
    cfg, st = base_cfg(ru_check=True), _st()
    ip, why = u._pick_target(cfg, st, have="3.3.3.3", have_ok=False, listener_ip="3.3.3.3")
    assert ip == "7.7.7.7" and "rotated" in why


def test_run_once_skips_when_healthy_and_not_due(monkeypatch):
    monkeypatch.setattr(u, "current_record_ip", lambda cfg: "1.1.1.1")
    fetch_calls = []
    monkeypatch.setattr(u, "fetch_listener_ip", lambda cfg: fetch_calls.append(1) or "1.1.1.1")
    monkeypatch.setattr(u, "reachable", lambda cfg, ip: (True, "ok"))
    monkeypatch.setattr(u, "records_all_match", lambda cfg, ip: True)
    cfg = base_cfg(max_ip_age=300, ru_check=False)
    st = _st()
    st.published_at = time.time()  # freshly published, nowhere near the cap
    u.run_once(cfg, st)
    assert st.published_at == pytest.approx(time.time(), abs=2)


def test_run_once_forces_periodic_refresh_past_max_age(monkeypatch):
    monkeypatch.setattr(u, "current_record_ip", lambda cfg: "1.1.1.1")
    monkeypatch.setattr(u, "fetch_listener_ip", lambda cfg: "1.1.1.1")
    monkeypatch.setattr(u, "reachable", lambda cfg, ip: (True, "ok"))
    monkeypatch.setattr(u, "records_all_match", lambda cfg, ip: True)
    applied = []
    monkeypatch.setattr(u, "apply_ip", lambda cfg, ip: applied.append(ip))
    monkeypatch.setattr(u, "_write_state", lambda cfg, ip: None)
    cfg = base_cfg(max_ip_age=300, ru_check=False)
    st = _st()
    st.published_at = time.time() - 301  # older than max_ip_age
    u.run_once(cfg, st)
    assert not applied  # same IP everywhere — nothing to actually rewrite in DNS
    assert st.published_at == pytest.approx(time.time(), abs=2)  # but the clock restarted


def test_run_once_max_age_zero_never_forces(monkeypatch):
    monkeypatch.setattr(u, "current_record_ip", lambda cfg: "1.1.1.1")
    fetch_calls = []
    monkeypatch.setattr(u, "fetch_listener_ip", lambda cfg: fetch_calls.append(1) or "1.1.1.1")
    monkeypatch.setattr(u, "reachable", lambda cfg, ip: (True, "ok"))
    monkeypatch.setattr(u, "records_all_match", lambda cfg, ip: True)
    cfg = base_cfg(max_ip_age=0, ru_check=False)
    st = _st()
    old = st.published_at = time.time() - 10_000
    u.run_once(cfg, st)
    assert st.published_at == old  # disabled — never touched


def test_state_transitions():
    s = u.State()
    assert s.healthy
    s.mark_unhealthy()
    assert not s.healthy and s.unhealthy_since > 0
    first = s.unhealthy_since
    s.mark_unhealthy()
    assert s.unhealthy_since == first  # not reset while still unhealthy
    s.mark_healthy()
    assert s.healthy and s.unhealthy_since == 0.0
