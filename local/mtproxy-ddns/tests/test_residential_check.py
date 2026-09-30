import base64
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import residential_check as rc  # noqa: E402

SECRET = "3c0c8c099abd558ceaab5f5187cbc83e"


class FakeSocket:
    def __init__(self, connect_response: bytes, handshake_reply: bytes):
        self._connect_response = connect_response
        self._handshake_reply = handshake_reply
        self._stage = "connect"
        self.sent: list[bytes] = []

    def settimeout(self, _timeout):
        pass

    def sendall(self, data: bytes):
        self.sent.append(data)

    def recv(self, _n: int) -> bytes:
        if self._stage == "connect":
            self._stage = "handshake"
            return self._connect_response
        return self._handshake_reply

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_connection(monkeypatch, sock: FakeSocket):
    monkeypatch.setattr(rc.socket, "create_connection", lambda *a, **k: sock)


def test_missing_credentials_is_none():
    assert rc.residential_ru_reachable("1.2.3.4", 443, SECRET, "sni", "", 80, "", "") is None


def test_bad_secret_is_none(monkeypatch):
    sock = FakeSocket(b"HTTP/1.1 200 Connection established\r\n\r\n", b"\x16\x03\x03\x00\x7a")
    _patch_connection(monkeypatch, sock)
    assert rc.residential_ru_reachable("1.2.3.4", 443, "not-hex", "sni", "gw", 80, "u", "p") is None


def test_connect_rejected_is_none(monkeypatch):
    sock = FakeSocket(b"HTTP/1.1 407 Proxy Authentication Required\r\n\r\n", b"")
    _patch_connection(monkeypatch, sock)
    assert rc.residential_ru_reachable("1.2.3.4", 443, SECRET, "sni", "gw", 80, "u", "p") is None


def test_connect_ok_and_handshake_ok_is_true(monkeypatch):
    sock = FakeSocket(b"HTTP/1.1 200 Connection established\r\n\r\n", b"\x16\x03\x03\x00\x7a")
    _patch_connection(monkeypatch, sock)
    result = rc.residential_ru_reachable("1.2.3.4", 443, SECRET, "sni", "gw", 80, "u", "p")
    assert result is True
    # CONNECT then the FakeTLS ClientHello — proxy auth login must carry the RU country tag
    auth_b64 = sock.sent[0].split(b"Basic ", 1)[1].split(b"\r\n", 1)[0]
    login = base64.b64decode(auth_b64).decode()
    assert "-cc-RU-" in login


def test_connect_ok_but_handshake_fails_is_false(monkeypatch):
    sock = FakeSocket(b"HTTP/1.1 200 Connection established\r\n\r\n", b"garbage")
    _patch_connection(monkeypatch, sock)
    result = rc.residential_ru_reachable("1.2.3.4", 443, SECRET, "sni", "gw", 80, "u", "p")
    assert result is False


def test_socket_error_is_none(monkeypatch):
    def _boom(*a, **k):
        raise OSError("no route")
    monkeypatch.setattr(rc.socket, "create_connection", _boom)
    assert rc.residential_ru_reachable("1.2.3.4", 443, SECRET, "sni", "gw", 80, "u", "p") is None
