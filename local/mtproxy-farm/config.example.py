"""Template for mtproxy-farm config. Copy to config.py and fill real secrets
(config.py is gitignored)."""
import os

PORT = int(os.environ.get("MTPROXY_PORT", "8443"))

USERS = {
    "sub1": "00000000000000000000000000000001",
    "sub2": "00000000000000000000000000000002",
}

MODES = {"classic": False, "secure": False, "tls": True}

TLS_DOMAIN = "swcdn.apple.com"
