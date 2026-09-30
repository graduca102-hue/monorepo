"""Перепарсить уже собранные данные из raw JSON."""
import json, re

RAW_FILE = "garvis_chats_raw.json"
OUTPUT_FILE = "garvis_chats.txt"

with open(RAW_FILE, "r", encoding="utf-8") as f:
    chats = json.load(f)

print(f"Загружено {len(chats)} чатов из raw файла")


def extract(chat):
    title = chat.get("title", "").strip()
    desc = chat.get("description", "") or ""
    members = chat.get("memberCount", chat.get("membersCount", ""))

    # Ищем ссылки t.me в описании
    tme_links = re.findall(r"https?://t\.me/[A-Za-z0-9_+/]+", desc)

    link = None
    if tme_links:
        # Предпочитаем invite-ссылки (+xxx)
        for l in tme_links:
            if "/+" in l or "joinchat" in l:
                link = l
                break
        if not link:
            for l in tme_links:
                upart = l.split("t.me/")[-1].split("/")[0].split("?")[0]
                if not upart.lower().endswith("bot"):
                    link = l
                    break
        if not link:
            link = tme_links[0]

    # Поля объекта
    if not link:
        for key in ("link", "url", "invite_link", "inviteLink", "username", "chatUsername"):
            val = chat.get(key)
            if val and isinstance(val, str):
                if val.startswith("http"):
                    link = val
                elif val.startswith("@"):
                    link = "https://t.me/" + val.lstrip("@")
                else:
                    link = "https://t.me/" + val
                break

    if not link:
        link = f"[{title}]" if title else "[no-link]"

    parts = [link]
    if members:
        parts.append(str(members))
    if title:
        parts.append(title)
    return " | ".join(parts)


lines = []
for chat in chats:
    line = extract(chat)
    if line:
        lines.append(line)

with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    for line in lines:
        f.write(line + "\n")

print(f"Записано {len(lines)} строк в {OUTPUT_FILE}")
print()

with_link = sum(1 for l in lines if l.startswith("http"))
without_link = len(lines) - with_link
print(f"С ссылкой t.me: {with_link}")
print(f"Без ссылки (только название): {without_link}")
print()
print("Примеры:")
for line in lines[:20]:
    print(f"  {line}")
