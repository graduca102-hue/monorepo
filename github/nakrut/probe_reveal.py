"""Проба: один запрос каталога + один reveal, печатаем полный ответ 429."""
import json, time
from garvis_parser import GarvisCatalogClient, INIT_DATA, LKBOT_USERNAME, API_BASE

c = GarvisCatalogClient(INIT_DATA, LKBOT_USERNAME)
c.load_cache()
if not c.ensure_session():
    print("нет сессии")
    raise SystemExit

# берём страницу каталога
import urllib.parse as up
params = {"q": "взаимные подписки", "random": "false", "limit": "50"}
data = c._request("GET", "/webapp/chat-catalog", params=params)
items = data.get("items", []) if data else []
print(f"каталог: {len(items)} чатов")

# делаем ОДИН reveal вручную, печатаем сырой ответ
if items:
    cid = items[0]["chatId"]
    body = json.dumps({"chatId": cid}).encode()
    headers = {
        **c._sign_headers("POST", "/webapp/chat-catalog/reveal", body, c.session_id),
        "Content-Type": "application/json",
        "x-telegram-init-data": c.init_data,
    }
    r = c.http.post(f"{API_BASE}/webapp/chat-catalog/reveal", data=body, headers=headers, timeout=30)
    print(f"reveal status: {r.status_code}")
    print(f"reveal body: {r.text}")
    print(f"headers retry-after: {r.headers.get('retry-after')}")
    print(f"headers: {dict(r.headers)}")
