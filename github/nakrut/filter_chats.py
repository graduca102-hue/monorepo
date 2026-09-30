"""
Фильтр собранных чатов:
  • только с joinUrl (реальная ссылка)
  • memberCount >= MIN_MEMBERS
  • тайтл/описание в нише: взаимные подписки / пиар / ВП / реакции / лайки / раскрутка
  • отсев эскорта / 18+ / интима и прочего мусора
Результат: garvis_filtered.txt (сортировка по участникам, по убыванию).
"""
import json

RAW_FILE = "garvis_chats_raw.json"
OUT = "garvis_filtered.txt"
MIN_MEMBERS = 1000

# ниша (достаточно совпадения одного)
NICHE = [
    "взаимн", "подписк", "пиар", "пиар", "вп", " вз", "вз ", "взаимка",
    "реакци", "лайк", "раскрут", "продвижен", "накрут", "актив",
    "pr chat", "пиар чат", "чат пиар", "vp", "подписчик",
]

# стоп-слова — сразу выкидываем
EXCLUDE = [
    "эскорт", "escort", "интим", "18+", "проститу", "досуг", "вирт",
    "sex", "секс", "путан", "индивидуалк", "шлюх", "порн", "porn",
    "adult", "nude", "leak", "слив", "разврат", "рабын", "феи",
    "знакомств",  # часто эскорт-прокси
    "ищу девушку", "ищу парня", "ищу общени", "лп лд", "лд лп",
    "лп/лд", "лд/лп", "ищу лп", "ищу лд", "ищу друз",
    "кэшбэк", "кэшбек", "за отзыв", "скидк", "товары за",
    "барахолк", "объявлени", "казино", "casino", "гэмбл", "gambling",
    "ставк", "букмекер", "crypto", "крипт", "nft", "trade", "трейд",
    "работа", "ворк", "work", "ваканси", "дпс", "груз", "логист",
    "обмен валют", "вейп", "жижа",
]


def text_of(chat):
    return f"{chat.get('title','')} {chat.get('description','')}".lower()


def main():
    with open(RAW_FILE, "r", encoding="utf-8") as f:
        chats = json.load(f)

    kept = []
    seen = set()
    stats = {"no_url": 0, "small": 0, "excluded": 0, "off_niche": 0, "kept": 0}

    for c in chats:
        url = c.get("joinUrl")
        if not url:
            stats["no_url"] += 1
            continue
        if url in seen:
            continue

        members = c.get("memberCount", 0) or 0
        if members < MIN_MEMBERS:
            stats["small"] += 1
            continue

        t = text_of(c)
        if any(kw in t for kw in EXCLUDE):
            stats["excluded"] += 1
            continue
        if not any(kw in t for kw in NICHE):
            stats["off_niche"] += 1
            continue

        seen.add(url)
        kept.append((url, members, c.get("title", "").strip()))
        stats["kept"] += 1

    kept.sort(key=lambda x: x[1], reverse=True)

    with open(OUT, "w", encoding="utf-8") as f:
        for url, members, title in kept:
            f.write(f"{url} | {members} | {title}\n")

    print(f"Всего в базе: {len(chats)}")
    print(f"  без ссылки:     {stats['no_url']}")
    print(f"  < {MIN_MEMBERS} подп.:   {stats['small']}")
    print(f"  эскорт/мусор:   {stats['excluded']}")
    print(f"  не по нише:     {stats['off_niche']}")
    print(f"  ОСТАВЛЕНО:      {stats['kept']}")
    print(f"\nФайл: {OUT}")
    print("\nТоп-25:")
    for url, members, title in kept[:25]:
        print(f"  {members:>7} | {url} | {title[:45]}")


if __name__ == "__main__":
    main()
