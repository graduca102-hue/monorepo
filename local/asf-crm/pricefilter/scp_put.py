"""Upload local files to a server from servers.json over SFTP (paramiko).

Usage: python scp_put.py <server_id> <local_path> <remote_path> [<local> <remote> ...]
Password is read from servers/servers.json, never printed.
"""
import json
import sys
import pathlib
import paramiko

ROOT = pathlib.Path(__file__).resolve().parents[3]
CFG = ROOT / "servers" / "servers.json"


def load_server(server_id: str) -> dict:
    data = json.loads(CFG.read_text(encoding="utf-8"))
    for s in data["servers"]:
        if s["id"] == server_id:
            return s
    raise SystemExit(f"server id not found: {server_id}")


def main() -> int:
    args = sys.argv[2:]
    if len(sys.argv) < 4 or len(args) % 2 != 0:
        raise SystemExit("usage: python scp_put.py <server_id> <local> <remote> [<local> <remote> ...]")
    s = load_server(sys.argv[1])
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(hostname=s["host"], username=s["user"], password=s["password"], timeout=20)
    sftp = client.open_sftp()
    for i in range(0, len(args), 2):
        local, remote = args[i], args[i + 1]
        sftp.put(local, remote)
        print(f"put {local} -> {remote} ({pathlib.Path(local).stat().st_size} bytes)")
    sftp.close()
    client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
