"""Offline checks: no network, no SSH. Run: python -m pytest -q  (or python tests/test_core.py)."""
from __future__ import annotations

import base64
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import keys
from app.provisioner import parse_server_lines
from app.provisioner.remote import (
    parse_x25519, xray_config, mtproto_config, norm_hex32, normalize_ad_tag,
)


class Row(dict):
    __getitem__ = dict.__getitem__


def _srv(**over):
    base = dict(
        id=1, name="NL-1", host="1.2.3.4", ssh_user="root", ssh_password="x", ssh_port=22,
        country="NL", xray_port=443, mtproto_port=8443,
        reality_dest="www.microsoft.com:443", reality_sni="www.microsoft.com",
        reality_public_key="PUBKEY", reality_private_key="PRIVKEY",
        mtproto_faketls_domain="www.microsoft.com", enable_xray=1, enable_mtproto=1,
    )
    base.update(over)
    return Row(base)


def _user(**over):
    base = dict(
        tg_id=555, username="bob", uuid="11111111-1111-1111-1111-111111111111",
        short_id="abcdef0123456789", proto_password="pw", sub_token="TOK",
        mtproto_secret="0123456789abcdef0123456789abcdef",
        status="trial", plan_id=None, expires_at=9999999999,
        traffic_limit_bytes=10 * 1024**3, traffic_used_bytes=123, device_limit=3,
        balance=0, trial_used=0, created_at=0,
    )
    base.update(over)
    return Row(base)


def test_parse_server_lines():
    txt = """
    1.2.3.4 root Pass123 Netherlands-1
    root:Secret:5.6.7.8:Germany
    9.9.9.9:2222:root:pw:FIN
    root pw
    """
    got = parse_server_lines(txt)
    ok = [g for g in got if not g.error]
    assert len(ok) == 3, got
    assert ok[0].host == "1.2.3.4" and ok[0].user == "root" and ok[0].name == "Netherlands-1"
    assert ok[1].host == "5.6.7.8" and ok[1].password == "Secret"
    assert ok[2].port == 2222 and ok[2].host == "9.9.9.9"
    assert any(g.error for g in got)


def test_parse_x25519_both_formats():
    a = "Private key: aaa\nPublic key: bbb\n"
    b = "PrivateKey: ccc\nPassword: ddd\n"
    # Xray >=25.x: "Password (PublicKey):" plus a trailing Hash32 line
    c = ("---XRAY-X25519---\nPrivateKey: eee\n"
         "Password (PublicKey): fff\nHash32: ggg\n---END---\n")
    assert parse_x25519(a) == ("aaa", "bbb")
    assert parse_x25519(b) == ("ccc", "ddd")
    assert parse_x25519(c) == ("eee", "fff")


def test_xray_config_has_client_and_shortid():
    import json
    cfg = json.loads(xray_config(_srv(), [_user(), _user(tg_id=9, short_id="ffff")]))
    inb = cfg["inbounds"][0]
    assert inb["port"] == 443
    ids = {c["id"] for c in inb["settings"]["clients"]}
    assert "11111111-1111-1111-1111-111111111111" in ids
    assert "ffff" in inb["streamSettings"]["realitySettings"]["shortIds"]


def test_mtproto_config_users():
    cfg = mtproto_config(_srv(), [_user()])
    assert "u555" in cfg and "0123456789abcdef0123456789abcdef" in cfg
    assert "PORT = 8443" in cfg
    # empty user set still yields a startable config
    assert "disabled" in mtproto_config(_srv(), [])


def test_norm_hex32():
    good = "3c09c680b76ee91a4c25ad51f742267d"
    assert norm_hex32(good) == good
    assert norm_hex32("  3C09C680B76EE91A4C25AD51F742267D ") == good
    assert norm_hex32("0x" + good) == good
    assert norm_hex32("") == ""
    assert norm_hex32("not-a-tag") == ""
    assert norm_hex32(good[:-1]) == ""  # too short
    assert normalize_ad_tag is norm_hex32  # back-compat alias


def test_mtproto_config_ad_tag():
    tag = "3c09c680b76ee91a4c25ad51f742267d"
    cfg = mtproto_config(_srv(), [_user()], tag)
    assert f'AD_TAG = "{tag}"' in cfg
    # no tag / invalid tag -> no AD_TAG line at all
    assert "AD_TAG" not in mtproto_config(_srv(), [_user()])
    assert "AD_TAG" not in mtproto_config(_srv(), [_user()], "garbage")


def test_mtproto_config_house_secret():
    house = "aaaabbbbccccddddeeeeffff00001111"
    cfg = mtproto_config(_srv(), [_user()], "", house)
    assert f'"house": "{house}"' in cfg
    # house secret keeps the proxy startable even with zero real users
    empty = mtproto_config(_srv(), [], "", house)
    assert "house" in empty and "disabled" not in empty
    # invalid house secret is dropped, empty user set falls back to placeholder
    assert "disabled" in mtproto_config(_srv(), [], "", "nope")


def test_subscription_render():
    from app import subscription
    srv = [_srv()]
    usr = _user()
    b64 = subscription.render_v2ray(usr, srv)
    decoded = base64.b64decode(b64).decode()
    assert decoded.startswith("vless://11111111-1111-1111-1111-111111111111@1.2.3.4:443")
    assert "pbk=PUBKEY" in decoded and "sid=abcdef0123456789" in decoded

    sb = subscription.render_singbox(usr, srv)
    assert '"public_key": "PUBKEY"' in sb

    cl = subscription.render_clash(usr, srv)
    assert "public-key: PUBKEY" in cl

    name, link = subscription.mtproto_links(usr, srv)[0]
    assert link.startswith("tg://proxy?server=1.2.3.4&port=8443&secret=ee")

    happ = subscription.happ_link(usr)
    assert happ.startswith("happ://add/")
    inner = happ.split("add/", 1)[1]
    assert inner.startswith("http") and inner.endswith("/sub/TOK")

    assert subscription.pick_format("Happ/1.0") == "v2ray"
    assert subscription.pick_format("clash-verge/1.0") == "clash"
    assert subscription.pick_format("sing-box 1.9") == "singbox"


def test_keys_shapes():
    assert len(keys.new_mtproto_secret()) == 32
    assert len(bytes.fromhex(keys.new_short_id())) == 8
    lk = keys.mtproto_link("h", 443, "00" * 16, "www.microsoft.com")
    assert lk.startswith("tg://proxy?server=h&port=443&secret=ee")


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
            n += 1
    print(f"\n{n} passed")
