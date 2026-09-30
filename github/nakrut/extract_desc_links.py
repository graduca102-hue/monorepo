"""Извлекает t.me ссылки из описаний чатов (без запросов к API)."""
import json, re

RAW_FILE = "garvis_chats_raw.json"
OUT = "garvis_chats_desc_links.txt"

with open(RAW_FILE, "r", encoding="utf-8") as f:
    chats = json.load(f)

lines = []
for chat in chats:
    if not isinstance(chat, dict):
        continue
    title = (chat.get("title") or "").strip()
    desc = chat.get("description") or ""
    members = chat.get("memberCount", "")

    # уже есть готовая ссылка из reveal?
    link = chat.get("joinUrl")

    if not link:
        # ищем t.me в описании
        found = re.findall(r"https?://t\.me/[A-Za-z0-9_+/]+", desc)
        if found:
            # предпочитаем invite (+) и не-бот ссылки
            for l in found:
                if "/+" in l or "joinchat" in l:
                    link = l
                    break
            if not link:
                for l in found:
                    u = l.split("t.me/")[-1].split("/")[0].split("?")[0]
                    if not u.lower().endswith("bot"):
                        link = l
                        break
            if not link:
                link = found[0]

    if link:
        parts = [link]
        if members:
            parts.append(str(members))
        if title:
            parts.append(title)
        lines.append(" | ".join(parts))

# уникальность по ссылке
seen = set()
uniq = []
for l in lines:
    k = l.split(" | ")[0]
    if k not in seen:
        seen.add(k)
        uniq.append(l)

with open(OUT, "w", encoding="utf-8") as f:
    for l in uniq:
        f.write(l + "\n")

print(f"Чатов всего: {len(chats)}")
print(f"Ссылок извлечено (уникальных): {len(uniq)}")
print(f"Файл: {OUT}")
print()
print("Первые 20:")
for l in uniq[:20]:
    print(" ", l)
