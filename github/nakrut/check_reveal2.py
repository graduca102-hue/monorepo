import json

with open("garvis_debug_responses.json", "r", encoding="utf-8") as f:
    data = json.load(f)

for i, r in enumerate(data):
    if "reveal" in r["url"]:
        print(f"--- Reveal #{i} ---")
        print(f"URL: {r['url']}")
        print(f"Method: {r.get('method')}")
        print(f"POST body: {r.get('post_data')}")
        print(f"Response: {json.dumps(r['data'], ensure_ascii=False)}")
        print()

# Also check session
for i, r in enumerate(data):
    if "session" in r["url"] and "chat-catalog" in r["url"]:
        print(f"--- Session #{i} ---")
        print(f"URL: {r['url']}")
        print(f"Method: {r.get('method')}")
        print(f"POST body: {r.get('post_data', '')[:300]}")
        print(f"Response: {json.dumps(r['data'], ensure_ascii=False)}")
        print()
