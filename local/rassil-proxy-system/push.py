"""Push a local file to a server path via srv_ssh.py (base64, no scp)."""
import base64, pathlib, subprocess, sys

ROOT = pathlib.Path(r"C:\Users\ewg\Desktop\kiro-bot")


def _run(server: str, cmd: str):
    r = subprocess.run(
        [sys.executable, str(ROOT / "srv_ssh.py"), server, cmd],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(r.stdout)
        print(r.stderr)
        raise SystemExit("remote command failed")
    return r.stdout


def push(server: str, local: str, remote: str, chunk: int = 6000) -> None:
    import hashlib
    data = pathlib.Path(local).read_bytes()
    b64 = base64.b64encode(data).decode()
    tmp = remote + ".b64.tmp"
    _run(server, f": > '{tmp}'")
    for i in range(0, len(b64), chunk):
        _run(server, f"printf %s '{b64[i:i+chunk]}' >> '{tmp}'")
    out = _run(
        server,
        f"umask 077; base64 -d '{tmp}' > '{remote}' && rm -f '{tmp}' && "
        f"wc -c '{remote}' && md5sum '{remote}'",
    )
    print(out)
    print("local md5:", hashlib.md5(data).hexdigest())


if __name__ == "__main__":
    push(sys.argv[1], sys.argv[2], sys.argv[3])
