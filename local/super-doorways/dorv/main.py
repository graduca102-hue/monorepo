import csv
import json
import mimetypes
import threading
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
STATE_PATH = BASE_DIR / "state.json"
USERS_PATH = BASE_DIR / "users.json"
TOKENS_PATH = BASE_DIR / "token.txt"
NAMES_PATH = BASE_DIR / "name.txt"
PHOTO_DIR = BASE_DIR / "photo"
DUMPS_DIR = BASE_DIR / "dumps"
BROADCAST_TEXT_PATH = BASE_DIR / "broadcast.txt"
DESCRIPTION_PHOTO_BASENAMES = ("description_pict", "description_photo")

PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".mp4"}
PRIVATE_UPDATE_FIELDS = (
    "message",
    "edited_message",
    "callback_query",
    "my_chat_member",
    "chat_member",
)
INVALID_TOKEN_RETRIES = 5
DEFAULT_WORKER_THREADS = 1
MAX_WORKER_THREADS = 64
REMOVED_TOKENS: set[str] = set()
TOKEN_FILE_LOCK = threading.Lock()

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return json.loads(json.dumps(default, ensure_ascii=False))
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_json(path: Path, data: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def read_lines(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"Файл не найден: {path}")
    with path.open("r", encoding="utf-8") as file:
        return [line.strip() for line in file if line.strip()]


def save_lines(path: Path, lines: list[str]) -> None:
    text = "\n".join(lines)
    if text:
        text += "\n"
    path.write_text(text, encoding="utf-8")


def load_photos(folder: Path) -> list[Path]:
    if not folder.exists():
        raise FileNotFoundError(f"Папка не найдена: {folder}")
    photos = sorted(
        [item for item in folder.iterdir() if item.is_file() and item.suffix.lower() in PHOTO_EXTENSIONS],
        key=lambda item: item.name.lower(),
    )
    if not photos:
        raise FileNotFoundError("В папке photo нет файлов .jpg/.jpeg или .mp4 для аватарки")
    return photos


def find_description_photo(base_dir: Path) -> Path | None:
    for basename in DESCRIPTION_PHOTO_BASENAMES:
        for extension in (".jpg", ".jpeg", ".mp4"):
            candidate = base_dir / f"{basename}{extension}"
            if candidate.exists() and candidate.is_file():
                return candidate
    return None


def pick_next(items: list[Any], state: dict[str, Any], key: str) -> Any:
    if not items:
        raise ValueError(f"Список для '{key}' пуст")
    index = int(state.get(key, 0))
    item = items[index % len(items)]
    state[key] = index + 1
    return item


def mask_token(token: str) -> str:
    if len(token) <= 12:
        return token
    return f"{token[:8]}...{token[-4:]}"


def get_worker_threads(state: dict[str, Any]) -> int:
    settings = state.setdefault("settings", {})
    value = int(settings.get("worker_threads", DEFAULT_WORKER_THREADS) or DEFAULT_WORKER_THREADS)
    return max(1, min(MAX_WORKER_THREADS, value))


def set_worker_threads(state: dict[str, Any], value: int) -> None:
    state.setdefault("settings", {})["worker_threads"] = max(1, min(MAX_WORKER_THREADS, value))


def prompt_worker_threads(state: dict[str, Any]) -> None:
    current = get_worker_threads(state)
    print(f"Текущее количество потоков: {current}")
    raw = input(f"Введите число потоков (1-{MAX_WORKER_THREADS}): ").strip()
    if not raw.isdigit():
        raise ValueError("Нужно ввести целое число")
    set_worker_threads(state, int(raw))
    print(f"Сохранено. Новое количество потоков: {get_worker_threads(state)}")


def prompt_description_text(current_text: str) -> str:
    print("Текущее описание:")
    print("-" * 40)
    print(current_text)
    print("-" * 40)
    print("Введите новое описание. Для завершения введите END на отдельной строке.")
    lines: list[str] = []
    while True:
        line = input()
        if line == "END":
            break
        lines.append(line)
    new_text = "\n".join(lines).strip()
    if not new_text:
        raise ValueError("Описание не может быть пустым")
    return new_text


def remove_token_from_storage(token: str) -> None:
    with TOKEN_FILE_LOCK:
        if token in REMOVED_TOKENS:
            return
        tokens = read_lines(TOKENS_PATH)
        filtered = [item for item in tokens if item != token]
        if len(filtered) != len(tokens):
            save_lines(TOKENS_PATH, filtered)
        REMOVED_TOKENS.add(token)
        print(f"Токен удалён из token.txt: {mask_token(token)}")


def is_unauthorized_error(error: Exception) -> bool:
    text = str(error)
    return "HTTP 401" in text and "Unauthorized" in text


def build_api_url(token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{token}/{method}"


def perform_request(request: Request) -> dict[str, Any]:
    try:
        with urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code} при запросе к Telegram API: {body}") from error
    except URLError as error:
        raise RuntimeError(f"Ошибка сети: {error}") from error
    data = json.loads(raw)
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API вернул ошибку: {data}")
    return data


def call_api(token: str, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    encoded_payload = {}
    for key, value in payload.items():
        encoded_payload[key] = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)

    last_error: Exception | None = None
    for attempt in range(1, INVALID_TOKEN_RETRIES + 1):
        data = urlencode(encoded_payload).encode("utf-8")
        request = Request(build_api_url(token, method), data=data, method="POST")
        request.add_header("Content-Type", "application/x-www-form-urlencoded; charset=utf-8")
        try:
            return perform_request(request)
        except Exception as error:
            last_error = error
            if is_unauthorized_error(error):
                print(f"Токен {mask_token(token)} вернул 401 Unauthorized. Проверка {attempt}/{INVALID_TOKEN_RETRIES}")
                if attempt == INVALID_TOKEN_RETRIES:
                    remove_token_from_storage(token)
                    raise RuntimeError(f"Токен удалён после {INVALID_TOKEN_RETRIES} ошибок 401 Unauthorized") from error
                continue
            raise
    raise last_error if last_error else RuntimeError("Не удалось выполнить запрос к Telegram API")

def build_multipart_body(fields: dict[str, str], file_field: str, file_path: Path) -> tuple[bytes, str]:
    boundary = f"----CodexBoundary{uuid.uuid4().hex}"
    body = bytearray()
    for key, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
        body.extend(value.encode("utf-8"))
        body.extend(b"\r\n")

    filename = file_path.name
    mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    file_bytes = file_path.read_bytes()

    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend((f'Content-Disposition: form-data; name="{file_field}"; ' f'filename="{filename}"\r\n').encode("utf-8"))
    body.extend(f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8"))
    body.extend(file_bytes)
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    return bytes(body), boundary


def call_api_with_file(token: str, method: str, fields: dict[str, str], file_field: str, file_path: Path) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, INVALID_TOKEN_RETRIES + 1):
        body, boundary = build_multipart_body(fields, file_field, file_path)
        request = Request(build_api_url(token, method), data=body, method="POST")
        request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        request.add_header("Content-Length", str(len(body)))
        try:
            return perform_request(request)
        except Exception as error:
            last_error = error
            if is_unauthorized_error(error):
                print(f"Токен {mask_token(token)} вернул 401 Unauthorized. Проверка {attempt}/{INVALID_TOKEN_RETRIES}")
                if attempt == INVALID_TOKEN_RETRIES:
                    remove_token_from_storage(token)
                    raise RuntimeError(f"Токен удалён после {INVALID_TOKEN_RETRIES} ошибок 401 Unauthorized") from error
                continue
            raise
    raise last_error if last_error else RuntimeError("Не удалось выполнить загрузку файла в Telegram API")


def set_profile_photo(token: str, file_path: Path) -> None:
    attach_name = "profile_file"
    suffix = file_path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        photo_payload = {"type": "static", "photo": f"attach://{attach_name}"}
    elif suffix == ".mp4":
        photo_payload = {"type": "animated", "animation": f"attach://{attach_name}"}
    else:
        raise ValueError(f"Неподдерживаемый формат аватарки: {file_path.name}. Нужен .jpg/.jpeg или .mp4")
    call_api_with_file(
        token=token,
        method="setMyProfilePhoto",
        fields={"photo": json.dumps(photo_payload, ensure_ascii=False)},
        file_field=attach_name,
        file_path=file_path,
    )


def maybe_update_description_photo_mtproto(config: dict[str, Any], bot_username: str, description_photo_path: Path | None) -> None:
    mtproto = config.get("mtproto") or {}
    if not bool(mtproto.get("enabled")) or description_photo_path is None or not bot_username:
        return
    api_id = int(mtproto.get("api_id") or 0)
    api_hash = str(mtproto.get("api_hash") or "").strip()
    session_name = str(mtproto.get("session_name") or "owner_session").strip()
    if not api_id or not api_hash:
        raise RuntimeError("Для owner-mode заполните config.json -> mtproto.api_id и mtproto.api_hash")
    try:
        from telethon.sync import TelegramClient
        from telethon import functions
    except ImportError as error:
        raise RuntimeError("Нужен Telethon. Установите: pip install -r requirements.txt") from error
    session_path = BASE_DIR / session_name
    with TelegramClient(str(session_path), api_id, api_hash) as client:
        uploaded = client.upload_file(str(description_photo_path))
        suffix = description_photo_path.suffix.lower()
        if suffix in {".jpg", ".jpeg"}:
            client(functions.photos.UploadProfilePhotoRequest(bot=bot_username, file=uploaded))
        elif suffix == ".mp4":
            client(functions.photos.UploadProfilePhotoRequest(bot=bot_username, video=uploaded))
        else:
            raise RuntimeError(f"Неподдерживаемый description_photo: {description_photo_path.name}")


def get_bot_identity(token: str) -> dict[str, Any]:
    return call_api(token, "getMe")["result"]


def update_bot(token: str, name: str, description: str, short_description_text: str, language_code: str, photo_path: Path, config: dict[str, Any], description_photo_path: Path | None) -> dict[str, str]:
    me = get_bot_identity(token)
    call_api(token, "setMyName", {"name": name, "language_code": language_code})
    call_api(token, "setMyDescription", {"description": description, "language_code": language_code})
    call_api(token, "setMyShortDescription", {"short_description": short_description_text, "language_code": language_code})
    set_profile_photo(token, photo_path)
    maybe_update_description_photo_mtproto(config, me.get("username", ""), description_photo_path)
    return {"id": str(me["id"]), "username": me.get("username", ""), "name": name, "photo": photo_path.name}


def update_description_for_token(token_index: int, total: int, token: str, description: str, language_code: str) -> dict[str, Any]:
    me = get_bot_identity(token)
    call_api(token, "setMyDescription", {"description": description, "language_code": language_code})
    return {"token_index": token_index, "total": total, "id": str(me["id"]), "username": me.get("username", "")}


def get_bot_state_bucket(state: dict[str, Any], bot_id: str) -> dict[str, Any]:
    return state.setdefault("bot_offsets", {}).setdefault(bot_id, {"last_update_id": 0})


def get_bot_users_bucket(users_db: dict[str, Any], me: dict[str, Any]) -> dict[str, Any]:
    bot_id = str(me["id"])
    bucket = users_db.setdefault("bots", {}).setdefault(
        bot_id,
        {
            "bot_id": bot_id,
            "username": me.get("username", ""),
            "first_name": me.get("first_name", ""),
            "private_users": {},
            "stats": {"total_private_users": 0, "last_collect_at": ""},
        },
    )
    bucket["username"] = me.get("username", "")
    bucket["first_name"] = me.get("first_name", "")
    return bucket


def extract_private_target(update: dict[str, Any]) -> dict[str, Any] | None:
    message = update.get("message") or update.get("edited_message")
    if message:
        chat = message.get("chat") or {}
        from_user = message.get("from") or {}
        if chat.get("type") == "private":
            return {
                "chat_id": chat.get("id"),
                "username": from_user.get("username") or chat.get("username", ""),
                "first_name": from_user.get("first_name") or chat.get("first_name", ""),
                "last_name": from_user.get("last_name") or chat.get("last_name", ""),
                "language_code": from_user.get("language_code", ""),
                "is_bot": bool(from_user.get("is_bot")),
            }
    callback = update.get("callback_query") or {}
    if callback:
        from_user = callback.get("from") or {}
        message = callback.get("message") or {}
        chat = message.get("chat") or {}
        if chat.get("type") == "private":
            return {
                "chat_id": chat.get("id"),
                "username": from_user.get("username", ""),
                "first_name": from_user.get("first_name", ""),
                "last_name": from_user.get("last_name", ""),
                "language_code": from_user.get("language_code", ""),
                "is_bot": bool(from_user.get("is_bot")),
            }
    member_update = update.get("my_chat_member") or update.get("chat_member")
    if member_update:
        chat = member_update.get("chat") or {}
        from_user = member_update.get("from") or {}
        if chat.get("type") == "private":
            return {
                "chat_id": chat.get("id"),
                "username": from_user.get("username") or chat.get("username", ""),
                "first_name": from_user.get("first_name") or chat.get("first_name", ""),
                "last_name": from_user.get("last_name") or chat.get("last_name", ""),
                "language_code": from_user.get("language_code", ""),
                "is_bot": bool(from_user.get("is_bot")),
            }
    return None

def collect_users_for_token(token: str, users_db: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    me = get_bot_identity(token)
    bot_id = str(me["id"])
    bot_bucket = get_bot_users_bucket(users_db, me)
    state_bucket = get_bot_state_bucket(state, bot_id)
    last_update_id = int(state_bucket.get("last_update_id", 0))
    added = 0
    touched = 0
    scanned_updates = 0
    batch_index = 0

    while True:
        response = call_api(
            token,
            "getUpdates",
            {
                "offset": last_update_id + 1,
                "limit": 100,
                "timeout": 0,
                "allowed_updates": list(PRIVATE_UPDATE_FIELDS),
            },
        )
        updates = response.get("result") or []
        if not updates:
            break
        batch_index += 1
        print(f"  пакет {batch_index}: получено {len(updates)} апдейтов, текущая база {len(bot_bucket['private_users'])}")

        for update in updates:
            scanned_updates += 1
            update_id = int(update.get("update_id", 0))
            if update_id > last_update_id:
                last_update_id = update_id
            target = extract_private_target(update)
            if not target or target.get("chat_id") is None:
                continue
            chat_key = str(target["chat_id"])
            existing = bot_bucket["private_users"].get(chat_key)
            if existing is None:
                added += 1
                existing = {"chat_id": target["chat_id"], "first_seen": now_iso()}
            touched += 1
            existing.update(
                {
                    "chat_id": target["chat_id"],
                    "username": target.get("username", ""),
                    "first_name": target.get("first_name", ""),
                    "last_name": target.get("last_name", ""),
                    "language_code": target.get("language_code", ""),
                    "is_bot": bool(target.get("is_bot")),
                    "last_seen": now_iso(),
                    "last_update_id": update_id,
                }
            )
            bot_bucket["private_users"][chat_key] = existing

    state_bucket["last_update_id"] = last_update_id
    bot_bucket["stats"]["total_private_users"] = len(bot_bucket["private_users"])
    bot_bucket["stats"]["last_collect_at"] = now_iso()
    return {
        "bot_id": bot_id,
        "username": me.get("username", ""),
        "added": added,
        "touched": touched,
        "scanned_updates": scanned_updates,
        "total_private_users": len(bot_bucket["private_users"]),
    }


def export_bot_users(bot_bucket: dict[str, Any]) -> tuple[Path, Path]:
    DUMPS_DIR.mkdir(parents=True, exist_ok=True)
    username = bot_bucket.get("username") or bot_bucket.get("bot_id") or "unknown_bot"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = DUMPS_DIR / f"{username}_users_{stamp}.json"
    csv_path = DUMPS_DIR / f"{username}_users_{stamp}.csv"
    users = list(bot_bucket.get("private_users", {}).values())

    payload = {
        "bot_id": bot_bucket.get("bot_id", ""),
        "username": bot_bucket.get("username", ""),
        "first_name": bot_bucket.get("first_name", ""),
        "exported_at": now_iso(),
        "total_private_users": len(users),
        "users": users,
    }
    with json_path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, separators=(",", ":"))
    with csv_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["chat_id", "username", "first_name", "last_name", "language_code", "is_bot", "first_seen", "last_seen", "last_update_id"],
            delimiter=";",
        )
        writer.writeheader()
        writer.writerows(users)
    return json_path, csv_path


def send_message_to_chat(token: str, chat_id: int | str, text: str) -> int | str:
    call_api(token, "sendMessage", {"chat_id": chat_id, "text": text})
    return chat_id


def send_broadcast(token: str, bot_bucket: dict[str, Any], text: str, worker_threads: int) -> dict[str, int]:
    users = [user for user in bot_bucket.get("private_users", {}).values() if user.get("chat_id") is not None]
    total = len(users)
    if total == 0:
        return {"sent": 0, "failed": 0, "total": 0}
    worker_threads = max(1, min(worker_threads, total))
    sent = 0
    failed = 0
    completed = 0

    if worker_threads == 1:
        for user in users:
            try:
                send_message_to_chat(token, user["chat_id"], text)
                sent += 1
            except Exception as error:
                failed += 1
                print(f"  Ошибка -> {user['chat_id']}: {error}")
            completed += 1
            if completed % 25 == 0 or completed == total:
                print(f"  progress: {completed}/{total}")
    else:
        with ThreadPoolExecutor(max_workers=worker_threads) as executor:
            future_map = {executor.submit(send_message_to_chat, token, user["chat_id"], text): user["chat_id"] for user in users}
            for future in as_completed(future_map):
                chat_id = future_map[future]
                try:
                    future.result()
                    sent += 1
                except Exception as error:
                    failed += 1
                    print(f"  Ошибка -> {chat_id}: {error}")
                completed += 1
                if completed % 25 == 0 or completed == total:
                    print(f"  progress: {completed}/{total}")
    return {"sent": sent, "failed": failed, "total": total}


def ensure_broadcast_text() -> str:
    if BROADCAST_TEXT_PATH.exists():
        text = BROADCAST_TEXT_PATH.read_text(encoding="utf-8").strip()
        if text:
            return text
    print("Введите текст рассылки. Пустая строка завершит ввод.")
    lines: list[str] = []
    while True:
        line = input()
        if not line and lines:
            break
        if not line and not lines:
            raise ValueError(f"Текст рассылки пуст. Заполните {BROADCAST_TEXT_PATH.name} или введите текст.")
        lines.append(line)
    text = "\n".join(lines).strip()
    BROADCAST_TEXT_PATH.write_text(text + "\n", encoding="utf-8")
    return text


def choose_bot(tokens: list[str]) -> tuple[str, dict[str, Any]]:
    infos: list[tuple[str, dict[str, Any]]] = []
    print()
    print("Выберите бота:")
    for token in tokens:
        try:
            me = get_bot_identity(token)
            infos.append((token, me))
            label = f"@{me.get('username', '')}" if me.get("username") else me.get("id")
            print(f"{len(infos)}. {label}")
        except Exception as error:
            if token in REMOVED_TOKENS:
                print(f"- Удалён невалидный токен: {mask_token(token)}")
            else:
                print(f"- Ошибка getMe для {mask_token(token)}: {error}")
    if not infos:
        raise ValueError("В token.txt не осталось валидных токенов")
    raw_choice = input("Номер: ").strip()
    if not raw_choice.isdigit():
        raise ValueError("Нужно ввести номер бота")
    choice = int(raw_choice)
    if choice < 1 or choice > len(infos):
        raise ValueError("Номер бота вне диапазона")
    return infos[choice - 1]

def run_profile_update_task(token_index: int, total: int, token: str, name: str, description: str, short_description_text: str, language_code: str, photo_path: Path, config: dict[str, Any], description_photo_path: Path | None) -> dict[str, Any]:
    info = update_bot(token, name, description, short_description_text, language_code, photo_path, config, description_photo_path)
    return {"token_index": token_index, "total": total, "info": info}


def run_profile_updates(tokens: list[str], config: dict[str, Any], state: dict[str, Any], worker_threads: int) -> None:
    names = read_lines(NAMES_PATH)
    photos = load_photos(PHOTO_DIR)
    description_photo_path = find_description_photo(BASE_DIR)
    description = str(config.get("description", ""))
    short_description_text = str(config.get("description_image", config.get("description_pict", config.get("short_description", ""))))
    language_code = str(config.get("language_code", ""))
    if not description:
        raise ValueError("В config.json должно быть заполнено поле 'description'")

    tasks = []
    total = len(tokens)
    for token_index, token in enumerate(tokens, start=1):
        tasks.append({
            "token_index": token_index,
            "total": total,
            "token": token,
            "name": pick_next(names, state, "name_index"),
            "photo_path": pick_next(photos, state, "photo_index"),
        })

    worker_threads = max(1, min(worker_threads, len(tasks))) if tasks else 1
    print(f"Обновление профилей: потоков {worker_threads}")
    results: list[dict[str, str]] = []
    errors: list[str] = []

    if worker_threads == 1:
        for task in tasks:
            print(f"[{task['token_index']}/{task['total']}] Обновление профиля бота {mask_token(task['token'])}")
            try:
                result = run_profile_update_task(task["token_index"], task["total"], task["token"], task["name"], description, short_description_text, language_code, task["photo_path"], config, description_photo_path)
                info = result["info"]
                results.append(info)
                print(f"  Успешно @{info['username'] or info['id']} -> имя='{info['name']}', фото='{info['photo']}'")
            except Exception as error:
                errors.append(f"[{task['token_index']}/{task['total']}] {error}")
                print(f"  Ошибка: {error}")
    else:
        with ThreadPoolExecutor(max_workers=worker_threads) as executor:
            future_map = {
                executor.submit(run_profile_update_task, task["token_index"], task["total"], task["token"], task["name"], description, short_description_text, language_code, task["photo_path"], config, description_photo_path): task
                for task in tasks
            }
            for future in as_completed(future_map):
                task = future_map[future]
                try:
                    result = future.result()
                    info = result["info"]
                    results.append(info)
                    print(f"[{task['token_index']}/{task['total']}] OK @{info['username'] or info['id']} -> name='{info['name']}', фото='{info['photo']}'")
                except Exception as error:
                    errors.append(f"[{task['token_index']}/{task['total']}] {error}")
                    print(f"[{task['token_index']}/{task['total']}] Ошибка: {error}")

    print()
    print(f"Успешно: {len(results)}")
    print(f"С ошибками: {len(errors)}")
    if errors:
        print("Ошибки:")
        for error in errors:
            print(error)


def run_update_description_only(tokens: list[str], config: dict[str, Any], worker_threads: int) -> None:
    current_text = str(config.get("description", ""))
    new_description = prompt_description_text(current_text)
    language_code = str(config.get("language_code", ""))
    config["description"] = new_description
    save_json(CONFIG_PATH, config)
    print("Новое описание сохранено в config.json")

    total = len(tokens)
    worker_threads = max(1, min(worker_threads, total)) if total else 1
    print(f"Обновление описания у ботов: потоков {worker_threads}")
    success = 0
    errors: list[str] = []

    if worker_threads == 1:
        for token_index, token in enumerate(tokens, start=1):
            print(f"[{token_index}/{total}] Обновление описания для {mask_token(token)}")
            try:
                result = update_description_for_token(token_index, total, token, new_description, language_code)
                success += 1
                print(f"  Успешно @{result['username'] or result['id']} -> описание обновлено")
            except Exception as error:
                errors.append(f"[{token_index}/{total}] {error}")
                print(f"  Ошибка: {error}")
    else:
        with ThreadPoolExecutor(max_workers=worker_threads) as executor:
            future_map = {
                executor.submit(update_description_for_token, token_index, total, token, new_description, language_code): token_index
                for token_index, token in enumerate(tokens, start=1)
            }
            for future in as_completed(future_map):
                token_index = future_map[future]
                try:
                    result = future.result()
                    success += 1
                    print(f"[{token_index}/{total}] OK @{result['username'] or result['id']} -> description обновлён")
                except Exception as error:
                    errors.append(f"[{token_index}/{total}] {error}")
                    print(f"[{token_index}/{total}] Ошибка: {error}")

    print()
    print(f"Успешно обновлено: {success}")
    print(f"С ошибками: {len(errors)}")
    if errors:
        print("Ошибки:")
        for error in errors:
            print(error)


def run_collect_users(tokens: list[str], users_db: dict[str, Any], state: dict[str, Any]) -> None:
    print("Сбор пользователей идёт последовательно: сначала 1 бот, потом следующий.")
    total = len(tokens)
    summaries = []
    for index, token in enumerate(tokens, start=1):
        print()
        print(f"[{index}/{total}] Выгрузка пользователей для бота {mask_token(token)}")
        try:
            summary = collect_users_for_token(token, users_db, state)
            summaries.append(summary)
            save_json(USERS_PATH, users_db)
            save_json(STATE_PATH, state)
            label = f"@{summary['username']}" if summary["username"] else summary["bot_id"]
            print(f"[{index}/{total}] Готово {label}: новых {summary['added']}, обновлено {summary['touched']}, апдейтов {summary['scanned_updates']}, всего пользователей {summary['total_private_users']}")
        except Exception as error:
            print(f"[{index}/{total}] Ошибка при сборе пользователей: {error}")
    if not summaries:
        print("Собрать пользователей не удалось.")


def run_dump_users(users_db: dict[str, Any]) -> None:
    bots = list(users_db.get("bots", {}).values())
    if not bots:
        print("В users.json пока нет пользователей. Сначала выполните сбор.")
        return
    total = len(bots)
    for index, bot_bucket in enumerate(bots, start=1):
        total_users = len(bot_bucket.get("private_users", {}))
        label = f"@{bot_bucket.get('username', '')}" if bot_bucket.get("username") else bot_bucket.get("bot_id", "")
        print(f"[{index}/{total}] Экспорт {label} ({total_users} пользователей)")
        json_path, csv_path = export_bot_users(bot_bucket)
        print(f"  JSON-файл: {json_path}")
        print(f"  CSV-файл: {csv_path}")


def run_broadcast(tokens: list[str], users_db: dict[str, Any], worker_threads: int) -> None:
    token, me = choose_bot(tokens)
    bot_id = str(me.get("id", ""))
    bot_bucket = users_db.get("bots", {}).get(bot_id)
    if not bot_bucket or not bot_bucket.get("private_users"):
        print("Для этого бота нет сохранённых пользователей. Сначала выполните сбор.")
        return

    text = ensure_broadcast_text()
    label = f"@{me.get('username', '')}" if me.get("username") else bot_id
    total_users = len(bot_bucket.get("private_users", {}))
    print()
    print(f"Бот: {label}")
    print(f"Получателей: {total_users}")
    print(f"Потоков: {worker_threads}")
    print("Начать рассылку? [y/N]")
    confirm = input("> ").strip().lower()
    if confirm not in {"y", "yes", "да", "д"}:
        print("Рассылка отменена.")
        return

    summary = send_broadcast(token, bot_bucket, text, worker_threads)
    print()
    print(f"Рассылка завершена. Успешно: {summary['sent']}, ошибок: {summary['failed']}, всего: {summary['total']}")


def print_menu(worker_threads: int) -> None:
    print()
    print("=== Инструмент Telegram-ботов ===")
    print("1. Обновить имя / описание / фото")
    print("2. Собрать пользователей из getUpdates")
    print("3. Сделать дамп пользователей")
    print("4. Рассылка по сохранённым пользователям")
    print("5. Изменить только описание у ботов")
    print(f"6. Настроить потоки (сейчас: {worker_threads})")
    print("0. Выход")


def main() -> None:
    config = load_json(
        CONFIG_PATH,
        {
            "description": "",
            "description_image": "",
            "language_code": "",
            "mtproto": {
                "enabled": False,
                "api_id": 0,
                "api_hash": "",
                "session_name": "owner_session",
            },
        },
    )
    state = load_json(
        STATE_PATH,
        {
            "name_index": 0,
            "photo_index": 0,
            "bot_offsets": {},
            "settings": {"worker_threads": DEFAULT_WORKER_THREADS},
        },
    )
    users_db = load_json(USERS_PATH, {"bots": {}})

    while True:
        worker_threads = get_worker_threads(state)
        print_menu(worker_threads)
        choice = input("Выберите действие: ").strip()

        try:
            tokens = read_lines(TOKENS_PATH)
            if choice not in {"0", "3", "6"} and not tokens:
                print("В token.txt нет токенов.")
                continue

            if choice == "1":
                run_profile_updates(tokens, config, state, worker_threads)
                save_json(STATE_PATH, state)
            elif choice == "2":
                run_collect_users(tokens, users_db, state)
                save_json(USERS_PATH, users_db)
                save_json(STATE_PATH, state)
            elif choice == "3":
                run_dump_users(users_db)
            elif choice == "4":
                run_broadcast(tokens, users_db, worker_threads)
            elif choice == "5":
                run_update_description_only(tokens, config, worker_threads)
            elif choice == "6":
                prompt_worker_threads(state)
                save_json(STATE_PATH, state)
            elif choice == "0":
                save_json(USERS_PATH, users_db)
                save_json(STATE_PATH, state)
                print("Выход.")
                break
            else:
                print("Неизвестный пункт меню.")
        except KeyboardInterrupt:
            print()
            print("Операция прервана.")
        except Exception as error:
            print(f"Ошибка: {error}")


if __name__ == "__main__":
    main()

