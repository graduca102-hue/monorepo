import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import rucheck  # noqa: E402

NODES = ["ru2.node.check-host.net", "ru3.node.check-host.net"]


def _wire(monkeypatch, start, result):
    def fake_get(path, timeout):
        if path.startswith("/check-tcp"):
            return start
        return result
    monkeypatch.setattr(rucheck, "_get", fake_get)
    monkeypatch.setattr(rucheck.time, "sleep", lambda *_: None)


def test_all_nodes_connected(monkeypatch):
    _wire(monkeypatch,
          {"request_id": "x", "nodes": {n: [] for n in NODES}},
          {NODES[0]: [{"address": "1.2.3.4", "time": 0.05}],
           NODES[1]: [{"address": "1.2.3.4", "time": 0.06}]})
    assert rucheck.ru_tcp_reachable("1.2.3.4", 443, NODES, require_all=True) is True


def test_one_node_blocked_require_all(monkeypatch):
    _wire(monkeypatch,
          {"request_id": "x", "nodes": {n: [] for n in NODES}},
          {NODES[0]: [{"error": "Connection timed out"}],
           NODES[1]: [{"address": "1.2.3.4", "time": 0.06}]})
    assert rucheck.ru_tcp_reachable("1.2.3.4", 443, NODES, require_all=True) is False


def test_one_node_blocked_min_ok_1(monkeypatch):
    _wire(monkeypatch,
          {"request_id": "x", "nodes": {n: [] for n in NODES}},
          {NODES[0]: [{"error": "Connection timed out"}],
           NODES[1]: [{"address": "1.2.3.4", "time": 0.06}]})
    assert rucheck.ru_tcp_reachable("1.2.3.4", 443, NODES,
                                    require_all=False, min_ok=1) is True


def test_api_failure_is_none(monkeypatch):
    def boom(*_a, **_k):
        raise OSError("down")
    monkeypatch.setattr(rucheck, "_get", boom)
    assert rucheck.ru_tcp_reachable("1.2.3.4", 443, NODES) is None


def test_no_results_is_none(monkeypatch):
    _wire(monkeypatch,
          {"request_id": "x", "nodes": {n: [] for n in NODES}},
          {n: None for n in NODES})
    assert rucheck.ru_tcp_reachable("1.2.3.4", 443, NODES, poll_timeout=0.2) is None
