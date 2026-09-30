"""Expose the local screen-share (127.0.0.1:LOCAL_HTTP) on srv2's public IP.

Chain:
    phone/browser -> srv2:0.0.0.0:PUB_PORT  (relay.py)
                  -> srv2:127.0.0.1:FWD_PORT (this SSH reverse tunnel)
                  -> windows:127.0.0.1:LOCAL_HTTP (serve.py -> ffmpeg)

No server config is changed. relay.py is copied to /tmp and started/killed by
this script. Uses paramiko (already in the bot venv) and the srv2 password from
servers/servers.json (never printed).
"""
import json
import pathlib
import select
import socket
import sys
import threading
import time

import paramiko

ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVERS = ROOT / "servers" / "servers.json"

SERVER_ID = "srv2"
LOCAL_HTTP = int(sys.argv[1]) if len(sys.argv) > 1 else 8790
PUB_PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8792
FWD_PORT = int(sys.argv[3]) if len(sys.argv) > 3 else 18790

REMOTE_RELAY = "/tmp/kiro_screen_relay.py"
REMOTE_LOG = "/tmp/kiro_screen_relay.log"
REMOTE_PID = "/tmp/kiro_screen_relay.pid"


def server_cfg():
    data = json.loads(SERVERS.read_text(encoding="utf-8"))
    for s in data["servers"]:
        if s["id"] == SERVER_ID:
            return s
    raise SystemExit(f"{SERVER_ID} not in servers.json")


def handle_channel(chan):
    try:
        sock = socket.create_connection(("127.0.0.1", LOCAL_HTTP), timeout=10)
    except OSError as e:
        print(f"local connect failed: {e}", flush=True)
        chan.close()
        return
    try:
        while True:
            r, _, _ = select.select([chan, sock], [], [])
            if chan in r:
                data = chan.recv(65536)
                if not data:
                    break
                sock.sendall(data)
            if sock in r:
                data = sock.recv(65536)
                if not data:
                    break
                chan.sendall(data)
    except OSError:
        pass
    finally:
        chan.close()
        sock.close()


def main():
    cfg = server_cfg()
    host = cfg["host"]

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host,
        port=int(cfg.get("port", 22)),
        username=cfg["user"],
        password=cfg["password"],
        timeout=20,
        banner_timeout=20,
        auth_timeout=20,
    )
    transport = client.get_transport()
    transport.set_keepalive(30)

    # push + (re)start the relay on the server
    sftp = client.open_sftp()
    sftp.put(str(pathlib.Path(__file__).with_name("relay.py")), REMOTE_RELAY)
    sftp.close()
    _in, _out, _err = client.exec_command(
        f"[ -f {REMOTE_PID} ] && kill \"$(cat {REMOTE_PID})\" 2>/dev/null; "
        f"rm -f {REMOTE_PID}; sleep 0.5; "
        f"setsid sh -c 'nohup python3 {REMOTE_RELAY} {PUB_PORT} {FWD_PORT} "
        f"</dev/null >{REMOTE_LOG} 2>&1 &'; sleep 1.5; "
        f"[ -f {REMOTE_PID} ] && echo RELAY_OK || (echo RELAY_FAIL; cat {REMOTE_LOG} 2>&1)"
    )
    _out.channel.recv_exit_status()
    status = _out.read().decode("utf-8", "replace").strip()
    print(f"relay: {status}", flush=True)
    if "RELAY_OK" not in status:
        client.close()
        raise SystemExit("relay did not start on srv2")

    transport.request_port_forward("127.0.0.1", FWD_PORT)
    print(
        f"tunnel up: http://{host}:{PUB_PORT}/  "
        f"(-> :{FWD_PORT} -> 127.0.0.1:{LOCAL_HTTP})",
        flush=True,
    )

    try:
        while True:
            chan = transport.accept(1.0)
            if chan is None:
                if not transport.is_active():
                    print("transport died", flush=True)
                    break
                continue
            threading.Thread(target=handle_channel, args=(chan,), daemon=True).start()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            client.exec_command(
                f"[ -f {REMOTE_PID} ] && kill \"$(cat {REMOTE_PID})\" 2>/dev/null; "
                f"rm -f {REMOTE_PID}"
            )
        except Exception:
            pass
        client.close()


if __name__ == "__main__":
    main()
