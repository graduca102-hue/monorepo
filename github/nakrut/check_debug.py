import json

with open("garvis_debug_responses.json", "r", encoding="utf-8") as f:
    data = json.load(f)

for r in data:
    url = r["url"]
    d = r["data"]

    if "session" in url or ("chat-catalog" in url and "avatar" not in url):
        print(f"URL: {url}")
        if isinstance(d, dict):
            print(f"  Keys: {list(d.keys())}")
            for k, v in d.items():
                if isinstance(v, list) and len(v) > 0:
                    print(f"  {k}: [{len(v)} items]")
                    if isinstance(v[0], dict):
                        print(f"    first keys: {list(v[0].keys())}")
                        print(f"    sample: {json.dumps(v[0], ensure_ascii=False)[:400]}")
                else:
                    print(f"  {k}: {json.dumps(v, ensure_ascii=False)[:200]}")
        elif isinstance(d, list) and len(d) > 0:
            print(f"  List of {len(d)} items")
            if isinstance(d[0], dict):
                print(f"    first keys: {list(d[0].keys())}")
                print(f"    sample: {json.dumps(d[0], ensure_ascii=False)[:400]}")
        print()
