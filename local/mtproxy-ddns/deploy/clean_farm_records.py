"""Run ON srv2 with mtproxy-ddns STOPPED. Removes the old static mtproxy-farm
records (which pointed at our own servers) so mtproxy-ddns can re-manage those
names against the MT3S listener IP. Backs up, validates, reloads."""
import os, re, subprocess, time, pathlib

Z = pathlib.Path("/etc/bind/db.aikort.lol")
FARM = ["gacha", "mtproto", "vpn", "gay", "nogay", "femboy", "bitch",
        "publick-mtproto", "free-mtproto", "proxy"]

txt = Z.read_text()
bak = f"/opt/mtproxy-ddns/backups/zone-cleanfarm-{time.strftime('%Y%m%d_%H%M%S')}.db"
pathlib.Path(bak).write_text(txt)
print("backup:", bak)

name_re = re.compile(r"^(%s)\s" % "|".join(re.escape(n) for n in FARM))
kept = [l for l in txt.split("\n")
        if not name_re.match(l) and l.strip() != "; mtproxy-farm static records"]
new = "\n".join(kept)
if not new.endswith("\n"):
    new += "\n"

tmp = str(Z) + ".tmp"
pathlib.Path(tmp).write_text(new)
r = subprocess.run(["named-checkzone", "aikort.lol", tmp], capture_output=True, text=True)
print(r.stdout.strip())
if r.returncode != 0:
    os.unlink(tmp); raise SystemExit("checkzone failed: " + r.stderr)
st = os.stat(Z)
os.chmod(tmp, st.st_mode & 0o777); os.chown(tmp, st.st_uid, st.st_gid)
os.replace(tmp, Z)
r = subprocess.run(["rndc", "reload", "aikort.lol"], capture_output=True, text=True)
print(r.stdout.strip(), r.stderr.strip())
print("--- tail ---")
print("\n".join(Z.read_text().split("\n")[-8:]))
