from app.db import Database, sale_price


def make_db(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init()
    return db


def test_application_flow(tmp_path):
    db = make_db(tmp_path)
    db.submit_application(1, "u", "User", "sell accounts", "")
    assert db.get_supplier(1)["status"] == "pending"
    db.set_supplier_status(1, "approved", 99)
    # re-applying must not downgrade an approved supplier
    db.submit_application(1, "u", "User", "again", "")
    assert db.get_supplier(1)["status"] == "approved"
    db.set_supplier_status(2, "approved", 99)  # unknown user: no-op
    assert db.get_supplier(2) is None


def test_card_limit_per_category(tmp_path):
    db = make_db(tmp_path)
    ids = [db.create_product(1, 10, "Cat", f"t{i}", "description", 1.0, 50, 5) for i in range(5)]
    assert all(ids)
    assert db.create_product(1, 10, "Cat", "sixth", "description", 1.0, 50, 5) is None
    assert db.create_product(1, 11, "Other", "ok", "description", 1.0, 50, 5)
    assert db.create_product(2, 10, "Cat", "other supplier", "description", 1.0, 50, 5)
    assert db.delete_product(ids[0], 1)
    assert db.create_product(1, 10, "Cat", "after delete", "description", 1.0, 50, 5)
    assert not db.delete_product(ids[1], 2)  # foreign card


def test_stock_upload_dedup(tmp_path):
    db = make_db(tmp_path)
    pid = db.create_product(1, 10, "Cat", "t", "description", 2.0, 50, 5)
    assert db.add_stock(pid, ["a:1", "b:2", "a:1"]) == (2, 1)
    assert db.add_stock(pid, ["b:2", "c:3"]) == (1, 1)
    product = db.get_product(pid, 1)
    assert product["available"] == 3 and product["sold"] == 0
    assert db.supplier_summary(1) == {"cards": 1, "available": 3, "sold": 0, "earned": 0.0}
    assert db.stats()["stock"] == 3


def test_sales_notifications(tmp_path):
    import sqlite3

    db = make_db(tmp_path)
    db.init()  # re-running the migration must be harmless
    pid = db.create_product(1, 10, "Cat", "t", "description", 2.5, 50, 5)
    db.add_stock(pid, ["a", "b", "c"])
    # emulate botshop reserving two lines for an order
    conn = sqlite3.connect(db.path)
    conn.execute(
        "INSERT INTO supplier_orders (uuid, order_number, product_id, supplier_id, quantity, supplier_unit_usd, created_at)"
        " VALUES ('sup-1', 'SP-1', ?, 1, 2, 2.5, '2026-01-01 00:00:00')",
        (pid,),
    )
    conn.execute("UPDATE stock_items SET sold_at = 'x', order_uuid = 'sup-1' WHERE id IN (SELECT id FROM stock_items LIMIT 2)")
    conn.commit()
    conn.close()
    assert db.supplier_summary(1) == {"cards": 1, "available": 1, "sold": 2, "earned": 5.0}
    pending = db.pending_sale_notifications()
    assert [(p["order_number"], p["title"], p["quantity"]) for p in pending] == [("SP-1", "t", 2)]
    db.mark_sale_notified("sup-1")
    assert db.pending_sale_notifications() == []


def test_sale_price():
    assert sale_price(2, 50) == 3.0
    assert sale_price(0.33, 50) == 0.5
