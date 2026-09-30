import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.zone import bump_serial, read_record_ip, set_record_ip, set_records_ip  # noqa: E402

ZONE = """$TTL 300
@\tIN\tSOA\tns1.example. admin.aikort.lol. (
\t\t2026090901\t; serial
\t\t3600 900 1209600 300 )
@\tIN\tNS\tns1.example.
@\tIN\tNS\tns2.example.
; mtproxy-ddns managed record — edited automatically, do not touch
mt\t60\tIN\tA\t201.7.22.165
"""


def test_read_record_ip():
    assert read_record_ip(ZONE, "mt") == "201.7.22.165"
    assert read_record_ip(ZONE, "www") is None


def test_set_record_ip_replaces_and_bumps_serial():
    out = set_record_ip(ZONE, "mt", "8.8.4.4", 60)
    assert read_record_ip(out, "mt") == "8.8.4.4"
    assert "201.7.22.165" not in out
    assert out.endswith("\n") and "8.8.4.4\n" in out  # trailing newline kept
    _, old, new = bump_serial(ZONE)
    assert old == "2026090901"
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")
    assert new == "2026090902" or new == f"{today}01"
    # serial in the rewritten zone is strictly greater
    assert new in out


def test_set_record_ip_inserts_when_missing():
    base = "\n".join(l for l in ZONE.splitlines() if "\tA\t" not in l and "managed record" not in l) + "\n"
    out = set_record_ip(base, "mt", "1.2.3.4", 60)
    assert read_record_ip(out, "mt") == "1.2.3.4"
    assert "managed record" in out


def test_set_records_ip_multi_replace_and_append_single_serial_bump():
    names = ["mt", "gacha", "free-mtproto"]
    out = set_records_ip(ZONE, names, "9.9.9.9", 30)
    for n in names:
        assert read_record_ip(out, n) == "9.9.9.9"
    # 'mtproto'-style prefix collision does not clobber 'mt'
    out2 = set_records_ip(out, ["mtproto"], "5.5.5.5", 30)
    assert read_record_ip(out2, "mt") == "9.9.9.9"
    assert read_record_ip(out2, "mtproto") == "5.5.5.5"
    # exactly one serial advance for the whole batch
    _, _old, new = bump_serial(ZONE)
    assert new in out


def test_serial_same_day_increments():
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")
    z = ZONE.replace("2026090901", f"{today}07")
    _, old, new = bump_serial(z)
    assert new == f"{today}08"


def test_no_soa_raises():
    try:
        bump_serial("mt 60 IN A 1.2.3.4\n")
        assert False, "expected ValueError"
    except ValueError:
        pass
