import re

with open('garvis_page.js', 'r', encoding='utf-8') as f:
    text = f.read()

print(f"File size: {len(text)}")

# Search for X- headers in quotes
print("\n=== X-Headers ===")
for m in re.finditer(r'"X-[A-Za-z-]+"', text):
    s = max(0, m.start()-80)
    e = min(len(text), m.end()+80)
    print(f'{text[s:e]}')
    print('---')

# Search for chat-catalog
print("\n=== chat-catalog ===")
for m in re.finditer(r'chat.catalog|chatCatalog|chat_catalog', text, re.IGNORECASE):
    s = max(0, m.start()-100)
    e = min(len(text), m.end()+200)
    print(f'{text[s:e]}')
    print('---')

# Search for headers assignment
print("\n=== headers patterns ===")
for m in re.finditer(r'headers\s*[=:]\s*\{', text):
    s = max(0, m.start()-50)
    e = min(len(text), m.end()+300)
    print(f'{text[s:e]}')
    print('---')

# Search for proof/nonce in any form
print("\n=== proof/nonce ===")
for kw in ['proof', 'nonce', 'timestamp']:
    for m in re.finditer(re.escape(kw), text, re.IGNORECASE):
        s = max(0, m.start()-80)
        e = min(len(text), m.end()+80)
        line = text[s:e].replace('\n', ' ')
        print(f'[{kw}] {line}')
        print()
