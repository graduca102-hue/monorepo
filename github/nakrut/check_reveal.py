import json

with open("garvis_debug_responses.json", "r", encoding="utf-8") as f:
    data = json.load(f)

for r in data:
    url = r["url"]
    if "reveal" in url:
        print(f"URL: {url}")
        print(f"Full data: {json.dumps(r['data'], ensure_ascii=False, indent=2)}")
        print()
