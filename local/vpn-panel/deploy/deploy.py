"""Deploy vpn-panel to a server listed in ``servers/servers.json``.

Usage:
    python deploy/deploy.py srv1

What it does (idempotent):
  1. packs ``app/`` + ``run.py`` + ``requirements.txt`` into a tar.gz
  2. uploads it over SFTP to ``/opt/vpn-panel``
  3. creates a venv there and installs requirements
  4. writes ``/opt/vpn-panel/.env`` from the LOCAL ``.env`` (BOT_TOKEN / ADMIN_IDS)
     plus production overrides (public SUB_BASE_URL = ``http://<host>:<port>``)
  5. installs + (re)starts the ``vpn-panel`` systemd unit
  6. verifies ``/healthz`` and Telegram ``getMe``

The bot token lives only in the local ``.env`` and on the target server — never
in the repo. Server password is read from ``servers/servers.json`` and never printed.
"""
from __future__ import annotations

import io
import json
import pathlib
import sys
import tarfile
import time

import paramiko

PROJ = pathlib.Path(__file__).resolve().parents[1]
ROOT = PROJ.parents[1]  # kiro-bot repo root
CFG = ROOT / "servers" / "servers.json"

REMOTE_DIR = "/opt/vpn-panel"
SERVICE = "vpn-panel"
WEB_PORT = 8080
INCLUDE = ["app", "run.py", "requirements.txt"]


def load_server(server_id: str) -> dict:
    data = json.loads(CFG.read_text(encoding="utf-8"))
    for s in data["servers"]:
        if s["id"] == server_id:
            return s
    raise SystemExit(f"server id not found: {server_id}")


def read_local_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for raw in (PROJ / ".env").read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip()
    return env


def make_tar() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in INCLUDE:
            path = PROJ / name
            tar.add(path, arcname=name, filter=_scrub)
    return buf.getvalue()


def _scrub(ti: tarfile.TarInfo) -> tarfile.TarInfo | None:
    parts = pathlib.PurePosixPath(ti.name).parts
    if any(p in ("__pycache__", ".venv", "data") or p.endswith(".pyc") for p in parts):
        return None
    return ti


def prod_env(local: dict[str, str], host: str) -> str:
    merged = {
        "BOT_TOKEN": local.get("BOT_TOKEN", ""),
        "ADMIN_IDS": local.get("ADMIN_IDS", ""),
        "SUB_BASE_URL": f"http://{host}:{WEB_PORT}",
        "WEB_HOST": "0.0.0.0",
        "WEB_PORT": str(WEB_PORT),
        "BRAND": local.get("BRAND", "MIT VPN"),
        "SUPPORT_URL": local.get("SUPPORT_URL", "https://t.me/MITVPNROBOT"),
        "TRIAL_DAYS": local.get("TRIAL_DAYS", "3"),
        "TRIAL_TRAFFIC_GB": local.get("TRIAL_TRAFFIC_GB", "10"),
        "DEVICE_LIMIT": local.get("DEVICE_LIMIT", "3"),
        "STARS_PER_UNIT": local.get("STARS_PER_UNIT", "1"),
        "XRAY_PORT": local.get("XRAY_PORT", "443"),
        "MTPROTO_PORT": local.get("MTPROTO_PORT", "8443"),
        "REALITY_DEST": local.get("REALITY_DEST", "www.microsoft.com:443"),
        "REALITY_SNI": local.get("REALITY_SNI", "www.microsoft.com"),
        "SSH_TIMEOUT": local.get("SSH_TIMEOUT", "600"),
    }
    return "".join(f"{k}={v}\n" for k, v in merged.items())


UNIT = f"""[Unit]
Description=vpn-panel (Telegram VPN shop + subscription server)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={REMOTE_DIR}
ExecStart={REMOTE_DIR}/.venv/bin/python {REMOTE_DIR}/run.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
"""


def sh(ssh: paramiko.SSHClient, cmd: str, *, timeout: int = 600) -> tuple[int, str]:
    _in, out, err = ssh.exec_command(cmd, timeout=timeout)
    text = out.read().decode("utf-8", "replace") + err.read().decode("utf-8", "replace")
    return out.channel.recv_exit_status(), text.strip()


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python deploy/deploy.py <server_id>")
    srv = load_server(sys.argv[1])
    host = srv["host"]
    local_env = read_local_env()
    if not local_env.get("BOT_TOKEN"):
        raise SystemExit("local .env has no BOT_TOKEN")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, username=srv["user"], password=srv["password"],
                timeout=25, banner_timeout=25, auth_timeout=25)

    print(f"[*] {host}: preparing {REMOTE_DIR}")
    sh(ssh, f"mkdir -p {REMOTE_DIR}")
    sftp = ssh.open_sftp()
    sftp.putfo(io.BytesIO(make_tar()), f"{REMOTE_DIR}/_bundle.tar.gz")
    sftp.putfo(io.BytesIO(prod_env(local_env, host).encode()), f"{REMOTE_DIR}/.env")
    sftp.chmod(f"{REMOTE_DIR}/.env", 0o600)
    sftp.close()

    steps = [
        (f"cd {REMOTE_DIR} && tar xzf _bundle.tar.gz && rm _bundle.tar.gz", "unpack"),
        (f"test -d {REMOTE_DIR}/.venv || python3 -m venv {REMOTE_DIR}/.venv", "venv"),
        (f"{REMOTE_DIR}/.venv/bin/pip -q install --upgrade pip", "pip"),
        (f"{REMOTE_DIR}/.venv/bin/pip -q install -r {REMOTE_DIR}/requirements.txt", "deps"),
        (f"{REMOTE_DIR}/.venv/bin/python -m py_compile {REMOTE_DIR}/run.py", "compile"),
    ]
    for cmd, label in steps:
        print(f"[*] {label} …")
        code, text = sh(ssh, cmd)
        if code != 0:
            print(text)
            raise SystemExit(f"step '{label}' failed ({code})")

    print("[*] systemd unit")
    sftp = ssh.open_sftp()
    sftp.putfo(io.BytesIO(UNIT.encode()), f"/etc/systemd/system/{SERVICE}.service")
    sftp.close()
    sh(ssh, "systemctl daemon-reload")
    sh(ssh, f"systemctl enable {SERVICE} >/dev/null 2>&1")
    sh(ssh, f"systemctl restart {SERVICE}")

    # best-effort firewall
    sh(ssh, f"command -v ufw >/dev/null && ufw allow {WEB_PORT}/tcp >/dev/null 2>&1 || true")

    time.sleep(4)
    code, text = sh(ssh, f"systemctl is-active {SERVICE}; "
                         f"curl -s -o /dev/null -w 'health:%{{http_code}}' http://127.0.0.1:{WEB_PORT}/healthz; "
                         f"echo; journalctl -u {SERVICE} -n 8 --no-pager")
    print(text)
    ssh.close()
    print(f"\n[✓] deployed. subscription base: http://{host}:{WEB_PORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
