"""Run ON srv2 with mtproxy-ddns STOPPED. Points the 10 farm subdomains at our
own servers (5 -> srv1, 5 -> srv2) as static records, removing any prior copies
(e.g. ones mtproxy-ddns had pinned to the MT3S IP). Backs up, validates, reloads.
Idempotent."""
import os, re, subprocess, time, pathlib

Z = pathlib.Path("/etc/bind/db.aikort.lol")
SRV1, SRV2 = "2.27.22.150", "31.77.145.160"
recs = {
    "mt": SRV1,
    "gacha": SRV1, "mtproto": SRV1, "vpn": SRV1, "gay": SRV1, "nogay": SRV1,
    "femboy": SRV2, "bitch": SRV2, "publick-mtproto": SRV2, "free-mtproto": SRV2, "proxy": SRV2,
}

txt = Z.read_text()
bak = f"/opt/mtproxy-ddns/backups/zone-farm2-{time.strftime('%Y%m%d_%H%M%S')}.db"
pathlib.Path(bak).write_text(txt)
print("backup:", bak)

name_re = re.compile(r"^(%s)\s" % "|".join(re.escape(n) for n in recs))
lines = [l for l in txt.split("\n")
         if not name_re.match(l) and l.strip() != "; mtproxy-farm static records"]
if lines and lines[-1].strip() == "":
    lines.pop()
lines.append("; mtproxy-farm static records")
for name, ip in recs.items():
    lines.append(f"{name}\t60\tIN\tA\t{ip}")
new = "\n".join(lines) + "\n"
new = re.sub(r"(\d{10})(\s*; [Ss]erial)", lambda m: str(int(m.group(1)) + 5) + m.group(2), new, count=1)

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
print("\n".join(Z.read_text().split("\n")[-14:]))
