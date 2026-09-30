#!/bin/bash
echo "=== sites-enabled ==="
ls -la /etc/nginx/sites-enabled/ /etc/nginx/conf.d/ 2>/dev/null
echo
for f in /etc/nginx/sites-enabled/* /etc/nginx/conf.d/*.conf; do
  [ -f "$f" ] || continue
  echo "----- $f"
  grep -E 'listen|server_name|ssl_certificate |proxy_pass|root ' "$f"
done
echo "=== listening 80/443 ==="
ss -tlnp | grep -E ':(80|443) '
echo "=== certbot domains ==="
ls /etc/letsencrypt/live/ 2>/dev/null
