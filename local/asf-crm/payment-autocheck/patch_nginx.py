path = "/etc/nginx/sites-enabled/botshop-miniapp"
old = """    location /api/v1/ {
        proxy_pass http://127.0.0.1:8081;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
    }"""
new_block = """

    location = /webhooks/heleket {
        proxy_pass http://127.0.0.1:8080/webhooks/heleket;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
    }"""
data = open(path, encoding="utf-8").read()
count = data.count(old)
assert count == 2, f"expected 2 occurrences, found {count}"
data = data.replace(old, old + new_block, 1)
open(path, "w", encoding="utf-8").write(data)
print("PATCHED_OK")
