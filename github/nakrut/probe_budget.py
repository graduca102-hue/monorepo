"""Измеряем reveal-бюджет: сколько reveal проходит подряд и с какой скоростью."""
import json, time
from garvis_parser import GarvisCatalogClient, INIT_DATA, LKBOT_USERNAME, API_BASE

c = GarvisCatalogClient(INIT_DATA, LKBOT_USERNAME)
c.load_cache()
if not c.ensure_session():
    print("нет сессии — обнови INIT_DATA")
    raise SystemExit

# каталог для актуальных chatId в текущей сессии
data = c._request("GET", "/webapp/chat-catalog",
                  params={"q": "взаимные подписки", "random": "false", "limit": "50"})
items = data.get("items", []) if data else []
print(f"каталог: {len(items)} чатов\n")

ok = 0
for i, chat in enumerate(items, 1):
    cid = chat["chatId"]
    body = json.dumps({"chatId": cid}).encode()
    headers = {
        **c._sign_headers("POST", "/webapp/chat-catalog/reveal", body, c.session_id),
        "Content-Type": "application/json",
        "x-telegram-init-data": c.init_data,
    }
    r = c.http.post(f"{API_BASE}/webapp/chat-catalog/reveal", data=body, headers=headers, timeout=30)
    if r.status_code == 200:
        ok += 1
        print(f"{i}: OK  {r.json().get('joinUrl')}")
    else:
        print(f"{i}: {r.status_code}  {r.text[:90]}")
        if r.status_code == 429:
            print(f"\n>>> уперлись в лимит после {ok} успешных reveal")
            break
    time.sleep(1.0)

print(f"\nИтого успешных: {ok}")
