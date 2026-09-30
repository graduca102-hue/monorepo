"""
Парсер ссылок на чаты из Garvis Bot — через API напрямую.
ECDSA P-256 подпись для X-Garvis-Catalog-Proof.

Ключи и сессия кэшируются в garvis_session.json, чтобы не жечь bootstrap budget.
429 с retry_at парсится и скрипт ждёт до указанного времени.

python garvis_parser.py              # сбор + reveal
python garvis_parser.py --reveal     # только reveal (без сбора новых чатов)
python garvis_parser.py --collect    # только сбор
"""

import json
import sys
import os
import time
import uuid
import hashlib
import base64
import re
from datetime import datetime, timezone

try:
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
except ImportError:
    print("❌ pip install cryptography")
    sys.exit(1)

try:
    import requests
except ImportError:
    print("❌ pip install requests")
    sys.exit(1)

OUTPUT_FILE = "garvis_chats.txt"
RAW_FILE = "garvis_chats_raw.json"
SESSION_FILE = "garvis_session.json"
API_BASE = "https://api.garvisbot.org"
SESSION_PATH = "/webapp/chat-catalog/session"

SEARCH_QUERIES = [
    "взаимные подписки",
    "взаимная подписка",
    "взаимные реакции",
    "взаимный пиар",
    "пиар чат",
    "пиар",
    "вп чат",
    "взаимный лайк",
    "раскрутка",
    "продвижение",
]

INIT_DATA = "query_id=AAFVWrJxAAAAAFVasnFVA2X5&user=%7B%22id%22%3A1907513941%2C%22first_name%22%3A%22Fiat%22%2C%22last_name%22%3A%22%22%2C%22username%22%3A%22dsgasdgasdg%22%2C%22language_code%22%3A%22ru%22%2C%22allows_write_to_pm%22%3Atrue%2C%22photo_url%22%3A%22https%3A%5C%2F%5C%2Ft.me%5C%2Fi%5C%2Fuserpic%5C%2F320%5C%2FwdRXFLZYV-tJq-CQip3EILDWKRGi-MKjeDWFyiGP2HY.svg%22%7D&auth_date=1788141466&signature=9ZZuL_QKF8d5GhNLmYQ4GFyhCg939FB7ZQeqrWso7m9dMCXXjXRACb1bJm0jisGc4LGDUMjDDJ0MIKM2O4tyCQ&hash=11ad04c85a3083fb2f36f2fd959188279ce0135db5d3d563ee9b9872047eb596"

LKBOT_USERNAME = "hellogarvisbot"

REVEAL_DELAY = 0.6      # пауза между reveal-запросами
PAGE_DELAY = 1.0        # пауза между страницами каталога
MAX_WAIT_SECONDS = 3600 # максимум ждать по retry_at


def b64url(data: bytes) -> str:
    return base64.b64encode(data).decode().replace("+", "-").replace("/", "_").rstrip("=")


def sha256_b64url(data: bytes) -> str:
    return b64url(hashlib.sha256(data).digest())


def parse_retry_at(text: str) -> float | None:
    """Из '...retry_at=2026-08-31T02:31:39.501412+00:00' → сколько секунд ждать."""
    m = re.search(r"retry_at=(\S+)", text or "")
    if not m:
        return None
    raw = m.group(1).rstrip(";,.\"' ")
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = (dt - datetime.now(timezone.utc)).total_seconds()
        return max(0.0, delta)
    except ValueError:
        return None


def wait_for(seconds: float, reason: str):
    seconds = min(seconds, MAX_WAIT_SECONDS)
    if seconds <= 0:
        return
    target = datetime.now().timestamp() + seconds
    print(f"  ⏳ {reason}: жду {int(seconds)}с (до {time.strftime('%H:%M:%S', time.localtime(target))})")
    remaining = seconds
    while remaining > 0:
        step = min(30, remaining)
        time.sleep(step)
        remaining -= step
        if remaining > 0:
            print(f"     ... осталось {int(remaining)}с")


class GarvisCatalogClient:
    def __init__(self, init_data: str, lkbot_username: str, cache_file: str = SESSION_FILE):
        self.init_data = init_data
        self.lkbot_username = lkbot_username
        self.cache_file = cache_file
        self.session_id = None
        self.session_expires_at = None
        self.private_key = None
        self.http = requests.Session()
        self.http.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "*/*",
            "Origin": "https://webapp.garvisbot.org",
            "Referer": "https://webapp.garvisbot.org/",
        })

    # ---------- ключи и кэш сессии ----------

    def _public_raw(self) -> bytes:
        return self.private_key.public_key().public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        )

    def load_cache(self) -> bool:
        """Загружает ключи + sessionId из файла."""
        if not os.path.exists(self.cache_file):
            return False
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            pem = data.get("privateKeyPem", "").encode()
            if not pem:
                return False
            self.private_key = serialization.load_pem_private_key(pem, password=None)
            self.session_id = data.get("sessionId")
            self.session_expires_at = data.get("expiresAt")
            if not self.session_id:
                return False
            print(f"  📂 Сессия из кэша: {self.session_id} (до {self.session_expires_at})")
            return True
        except Exception as e:
            print(f"  ⚠️ Кэш сессии повреждён: {e}")
            return False

    def save_cache(self):
        try:
            pem = self.private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            ).decode()
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump({
                    "privateKeyPem": pem,
                    "sessionId": self.session_id,
                    "expiresAt": self.session_expires_at,
                }, f, indent=2)
        except Exception as e:
            print(f"  ⚠️ Не удалось сохранить сессию: {e}")

    def session_is_fresh(self) -> bool:
        if not self.session_id or not self.session_expires_at:
            return False
        try:
            dt = datetime.fromisoformat(self.session_expires_at.replace("Z", "+00:00"))
            return (dt - datetime.now(timezone.utc)).total_seconds() > 30
        except Exception:
            return False

    # ---------- подпись ----------

    def _sign_headers(self, method: str, path_with_query: str, body: bytes, session_id: str) -> dict:
        timestamp = str(int(time.time() * 1000))
        nonce = str(uuid.uuid4())
        body_hash = sha256_b64url(body)
        message = f"{method}\n{path_with_query}\n{body_hash}\n{timestamp}\n{nonce}\n{session_id}"
        sig_der = self.private_key.sign(message.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
        r_int, s_int = decode_dss_signature(sig_der)
        sig_fixed = r_int.to_bytes(32, "big") + s_int.to_bytes(32, "big")
        return {
            "x-garvis-catalog-session": session_id,
            "x-garvis-catalog-timestamp": timestamp,
            "x-garvis-catalog-nonce": nonce,
            "x-garvis-catalog-proof": b64url(sig_fixed),
        }

    # ---------- сессия ----------

    def _session_body(self) -> bytes:
        return json.dumps({
            "publicKey": b64url(self._public_raw()),
            "lkbotUsername": self.lkbot_username,
        }).encode()

    def bootstrap_session(self, _retry: int = 0) -> bool:
        """Новая сессия (тратит bootstrap budget). Только если нет валидной."""
        if self.private_key is None:
            self.private_key = ec.generate_private_key(ec.SECP256R1())
            print("  🔑 Ключи сгенерированы (P-256)")

        body = self._session_body()
        headers = {
            "Content-Type": "application/json",
            "x-telegram-init-data": self.init_data,
        }
        try:
            resp = self.http.post(f"{API_BASE}{SESSION_PATH}", data=body, headers=headers, timeout=30)
        except Exception as e:
            print(f"  ❌ Session timeout: {e}")
            return False

        if resp.status_code == 429:
            wait = parse_retry_at(resp.text)
            if wait is None:
                wait = float(resp.headers.get("retry-after", 30))
            if _retry >= 2:
                print(f"  ⛔ bootstrap budget исчерпан, ждать {int(wait)}с — прекращаю попытки")
                return False
            wait_for(wait + 2, "bootstrap budget исчерпан")
            return self.bootstrap_session(_retry + 1)

        if resp.status_code != 200:
            print(f"  ❌ Session failed: {resp.status_code} {resp.text[:200]}")
            return False

        data = resp.json()
        self.session_id = data["sessionId"]
        self.session_expires_at = data.get("expiresAt")
        self.save_cache()
        print(f"  ✅ Новая сессия: {self.session_id}")
        return True

    def renew_session(self) -> bool:
        """
        Продление сессии: POST /session, подписанный текущим sessionId.
        Не тратит bootstrap budget.
        """
        if not self.private_key or not self.session_id:
            return False
        body = self._session_body()
        headers = {
            **self._sign_headers("POST", SESSION_PATH, body, self.session_id),
            "Content-Type": "application/json",
            "x-telegram-init-data": self.init_data,
        }
        try:
            resp = self.http.post(f"{API_BASE}{SESSION_PATH}", data=body, headers=headers, timeout=30)
        except Exception as e:
            print(f"  ⚠️ Renew timeout: {e}")
            return False

        if resp.status_code == 429:
            wait = parse_retry_at(resp.text) or float(resp.headers.get("retry-after", 20))
            wait_for(wait + 2, "renew rate limit")
            return self.renew_session()

        if resp.status_code != 200:
            return False

        data = resp.json()
        self.session_id = data["sessionId"]
        self.session_expires_at = data.get("expiresAt")
        self.save_cache()
        print(f"  🔄 Сессия продлена: {self.session_id}")
        return True

    def ensure_session(self) -> bool:
        """Гарантирует валидную сессию: кэш → renew → bootstrap."""
        if self.session_is_fresh():
            return True
        if self.private_key and self.session_id and self.renew_session():
            return True
        return self.bootstrap_session()

    # ---------- запросы ----------

    def _request(self, method: str, path: str, params: dict = None,
                 body_dict: dict = None, _retry: int = 0, wait_on_429: bool = True):
        from urllib.parse import urlencode

        if not self.ensure_session():
            return None

        query = "?" + urlencode(params) if params else ""
        full_path = path + query
        body = json.dumps(body_dict).encode() if body_dict is not None else b""

        headers = {
            **self._sign_headers(method, full_path, body, self.session_id),
            "x-telegram-init-data": self.init_data,
        }
        if body_dict is not None:
            headers["Content-Type"] = "application/json"

        try:
            resp = self.http.request(
                method, f"{API_BASE}{full_path}",
                data=body if body_dict is not None else None,
                headers=headers, timeout=30,
            )
        except Exception as e:
            if _retry >= 2:
                return None
            time.sleep(3)
            return self._request(method, path, params, body_dict, _retry + 1, wait_on_429)

        if resp.status_code == 200:
            return resp.json()

        if resp.status_code == 429:
            if not wait_on_429:
                return None  # быстрый выход — для ротации аккаунтов
            if _retry >= 4:
                return None
            wait = parse_retry_at(resp.text)
            if wait is None:
                wait = float(resp.headers.get("retry-after", 10))
            wait_for(wait + 1, "rate limit")
            return self._request(method, path, params, body_dict, _retry + 1, wait_on_429)

        if resp.status_code in (401, 428):
            if _retry >= 2:
                return None
            self.session_expires_at = None  # форсим renew/bootstrap
            if not self.ensure_session():
                return None
            return self._request(method, path, params, body_dict, _retry + 1)

        if _retry == 0:
            print(f"  ❌ {method} {path}: {resp.status_code} {resp.text[:120]}")
        return None

    def search_chats(self, query: str, limit: int = 50) -> list:
        items_all = []
        cursor = None
        page = 0
        while True:
            page += 1
            params = {"q": query, "random": "false", "limit": str(limit)}
            if cursor:
                params["cursor"] = cursor

            data = self._request("GET", "/webapp/chat-catalog", params=params)
            if not data:
                break

            items = data.get("items", [])
            cursor = data.get("nextCursor")
            total = data.get("totalCount", "?")
            items_all.extend(items)
            print(f"  📥 [{query}] стр.{page}: +{len(items)} (в API: {total})")

            if not cursor or len(items) < limit:
                break
            time.sleep(PAGE_DELAY)
        return items_all

    def reveal_chat(self, chat_id: str, wait_on_429: bool = True) -> str | None:
        data = self._request("POST", "/webapp/chat-catalog/reveal",
                             body_dict={"chatId": chat_id}, wait_on_429=wait_on_429)
        return data.get("joinUrl") if data else None


# ---------- файлы ----------

def load_existing():
    chats, ids = [], set()
    if os.path.exists(RAW_FILE):
        try:
            with open(RAW_FILE, "r", encoding="utf-8") as f:
                for chat in json.load(f):
                    if isinstance(chat, dict):
                        cid = str(chat.get("chatId", ""))
                        if cid and cid not in ids:
                            ids.add(cid)
                            chats.append(chat)
        except Exception:
            pass
    return chats, ids


def existing_links() -> set:
    links = set()
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    links.add(line.split(" | ")[0])
    return links


def append_line(line: str, seen: set) -> bool:
    key = line.split(" | ")[0]
    if key in seen:
        return False
    seen.add(key)
    with open(OUTPUT_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    return True


def save_raw(chats):
    tmp = RAW_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(chats, f, ensure_ascii=False, indent=2)
    os.replace(tmp, RAW_FILE)


# ---------- main ----------

def main():
    print("=" * 60)
    print("🔍 Garvis Chat Parser — API Direct (collect + reveal в одной сессии)")
    print("=" * 60)
    print(f"📄 {os.path.abspath(OUTPUT_FILE)}\n")

    all_chats, chat_ids = load_existing()
    seen_links = existing_links()
    # chatId, для которых уже есть ссылка (из прошлых прогонов)
    revealed_ids = {str(c.get("chatId")) for c in all_chats if c.get("joinUrl")}
    print(f"📂 Загружено: {len(all_chats)} чатов, {len(revealed_ids)} с ссылками\n")

    client = GarvisCatalogClient(INIT_DATA, LKBOT_USERNAME)
    client.load_cache()

    print("🔐 Проверяю сессию...")
    if not client.ensure_session():
        print("\n❌ Сессия недоступна. Варианты:")
        print("   • подожди до времени retry_at и запусти снова")
        print("   • обнови INIT_DATA свежим значением из DevTools")
        sys.exit(1)
    print()

    # индекс чата в all_chats по chatId, чтобы дописывать joinUrl
    by_id = {str(c.get("chatId")): c for c in all_chats}

    revealed = 0
    errors = 0
    consecutive_errors = 0

    print("=" * 40)
    print("📡 Сбор + Reveal по запросам")
    print("=" * 40)

    for query in SEARCH_QUERIES:
        print(f"\n🔍 '{query}'")
        cursor = None
        page = 0

        while True:
            page += 1
            params = {"q": query, "random": "false", "limit": "50"}
            if cursor:
                params["cursor"] = cursor

            data = client._request("GET", "/webapp/chat-catalog", params=params)
            if not data:
                print(f"  ⚠️ стр.{page}: нет данных, перехожу к следующему запросу")
                break

            items = data.get("items", [])
            cursor = data.get("nextCursor")
            total = data.get("totalCount", "?")
            print(f"  📥 стр.{page}: {len(items)} чатов (в API: {total})")

            # reveal каждого чата с этой страницы В ТОЙ ЖЕ СЕССИИ
            for chat in items:
                cid = str(chat.get("chatId", ""))
                if not cid:
                    continue

                # добавляем в базу, если новый
                if cid not in chat_ids:
                    chat_ids.add(cid)
                    all_chats.append(chat)
                    by_id[cid] = chat

                # пропускаем уже раскрытые
                if cid in revealed_ids:
                    continue

                title = chat.get("title", "")
                members = chat.get("memberCount", "")

                url = client.reveal_chat(cid)
                if url:
                    by_id[cid]["joinUrl"] = url
                    revealed_ids.add(cid)
                    if append_line(f"{url} | {members} | {title}", seen_links):
                        revealed += 1
                        if revealed <= 5 or revealed % 25 == 0:
                            print(f"    🔗 [{revealed}] {url} | {members} | {title[:40]}")
                    consecutive_errors = 0
                else:
                    errors += 1
                    consecutive_errors += 1
                    if consecutive_errors >= 30:
                        print(f"    ⛔ 30 ошибок подряд — сохраняю и останавливаюсь")
                        save_raw(all_chats)
                        _final_report(all_chats, revealed, errors)
                        return

                time.sleep(REVEAL_DELAY)

            save_raw(all_chats)

            if not cursor or len(items) < 50:
                break
            time.sleep(PAGE_DELAY)

    save_raw(all_chats)
    _final_report(all_chats, revealed, errors)


def _final_report(all_chats, revealed, errors):
    total_lines = len(existing_links())
    print(f"\n{'='*60}")
    print(f"✅ Готово!")
    print(f"   Чатов в базе: {len(all_chats)}")
    print(f"   Reveal за этот запуск: {revealed} (ошибок: {errors})")
    print(f"   Всего ссылок в файле: {total_lines}")
    print(f"   Файл: {os.path.abspath(OUTPUT_FILE)}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
