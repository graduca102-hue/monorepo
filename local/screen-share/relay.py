"""Tiny public TCP relay, runs on the assigned server (srv2).

External clients hit  0.0.0.0:<pub>  and every connection is proxied to
127.0.0.1:<local> - which is the loopback port that the Windows box exposes
through an SSH reverse tunnel. Nothing is installed and no server config is
touched; this is just a process we start and kill.

Usage: python3 relay.py <pub_port> <local_port>
"""
import os
import socket
import sys
import threading

PUB = int(sys.argv[1])
LOCAL = int(sys.argv[2])
PIDFILE = "/tmp/kiro_screen_relay.pid"


def pump(a, b):
    try:
        while True:
            data = a.recv(65536)
            if not data:
                break
            b.sendall(data)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def handle(client):
    try:
        upstream = socket.create_connection(("127.0.0.1", LOCAL), timeout=10)
    except OSError:
        client.close()
        return
    threading.Thread(target=pump, args=(client, upstream), daemon=True).start()
    threading.Thread(target=pump, args=(upstream, client), daemon=True).start()


def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", PUB))
    srv.listen(64)
    with open(PIDFILE, "w") as f:
        f.write(str(os.getpid()))
    print(f"relay :{PUB} -> 127.0.0.1:{LOCAL}", flush=True)
    while True:
        client, _ = srv.accept()
        handle(client)


if __name__ == "__main__":
    main()
