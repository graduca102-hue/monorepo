from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class SecretCipher:
    def __init__(self, key_path: Path) -> None:
        self.key_path = key_path
        self._fernet = Fernet(self._load_or_create_key())

    def _load_or_create_key(self) -> bytes:
        env_key = os.getenv("SECRET_ENCRYPTION_KEY", "").strip()
        if env_key:
            return env_key.encode()
        if self.key_path.exists():
            return self.key_path.read_bytes().strip()
        key = Fernet.generate_key()
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        self.key_path.write_bytes(key)
        try:
            self.key_path.chmod(0o600)
        except OSError:
            pass
        return key

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode()).decode()
        except (InvalidToken, ValueError) as exc:
            raise RuntimeError("Cannot decrypt a stored secret") from exc


def mask_secret(value: str) -> str:
    if not value:
        return "не задан"
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:4]}…{value[-4:]}"
