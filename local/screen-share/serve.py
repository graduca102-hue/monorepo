"""Live screen-share over MJPEG (like a Discord screen share, but as a web link).

Captures the whole desktop with ffmpeg (gdigrab) and re-serves it as an
multipart/x-mixed-replace MJPEG stream that any browser can open.

Access is gated by a random token in the URL path:  http://host:PORT/<token>

stdlib only - nothing gets installed into the bot's venv.
"""

import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FFMPEG = os.environ.get(
    "FFMPEG_PATH", r"D:\ffmpeg-master-latest-win64-gpl-shared\bin\ffmpeg.exe"
)
TOKEN = os.environ.get("SCREEN_TOKEN", "changeme")
PORT = int(os.environ.get("SCREEN_PORT", "8790"))
FPS = os.environ.get("SCREEN_FPS", "12")
WIDTH = os.environ.get("SCREEN_WIDTH", "1280")
QUALITY = os.environ.get("SCREEN_QUALITY", "7")  # ffmpeg -q:v, lower = better

PAGE = b"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>screen</title>
<style>html,body{margin:0;background:#000;height:100%}
img{display:block;width:100%;height:100%;object-fit:contain}</style>
</head><body><img src="stream" alt="loading..."></body></html>"""


def ffmpeg_cmd():
    return [
        FFMPEG, "-hide_banner", "-loglevel", "error",
        "-f", "gdigrab", "-framerate", FPS, "-i", "desktop",
        "-vf", f"scale={WIDTH}:-2",
        "-q:v", QUALITY,
        "-f", "mpjpeg", "-",
    ]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _auth_ok(self):
        return self.path.rstrip("/").split("/")[1:2] == [TOKEN] or self.path.startswith(
            f"/{TOKEN}/"
        )

    def do_GET(self):
        parts = self.path.strip("/").split("/")
        if not parts or parts[0] != TOKEN:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if len(parts) == 1:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(PAGE)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(PAGE)
            return

        if parts[1] != "stream":
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        self.send_response(200)
        self.send_header(
            "Content-Type", "multipart/x-mixed-replace; boundary=ffmpeg"
        )
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()

        proc = subprocess.Popen(
            ffmpeg_cmd(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        try:
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            proc.kill()
            proc.wait()


def main():
    print(f"screen-share on :{PORT} path /{TOKEN}  (ffmpeg={FFMPEG})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    sys.exit(main())
