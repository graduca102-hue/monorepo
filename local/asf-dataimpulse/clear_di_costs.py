"""Drop the four placeholder DataImpulse cost settings.

They were written before the shop learned to read the provider's real rate from
the top-up log; a stored guess is worse than no value, because the menu then
sells at a price nobody verified.
"""
import sqlite3

KEYS = (
    "di_cost_residential",
    "di_cost_residential_premium",
    "di_cost_mobile",
    "di_cost_datacenter",
)

conn = sqlite3.connect("/opt/asf/data/shop.db")
for key in KEYS:
    conn.execute("DELETE FROM settings WHERE key = ?", (key,))
conn.commit()
rows = list(conn.execute("SELECT key FROM settings WHERE key LIKE 'di_%'"))
print("remaining di_* settings:", [r[0] for r in rows])
