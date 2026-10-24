"""Проверка заказа в файле SQLite, без Telegram."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from bot.db import Database


class ShopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "shop.db"
        self.db = Database(self.path)
        from bot.stock import receive
        for pid in range(1,7):
            receive(self.db,pid,100,'fixture',f'fixture-stock-{pid}')

    def test_catalog_is_stored_in_database(self) -> None:
        products = self.db.list_products()
        self.assertGreaterEqual(len(products), 5)
        self.assertTrue(all(product.price_kopecks > 0 for product in products))
        again = Database(self.path)
        self.assertEqual(again.product_count(), len(products))

    def test_purchase_survives_reopen_and_price_change(self) -> None:
        buyer = 101
        other = 202
        self.db.add_to_cart(buyer, 1)
        self.db.add_to_cart(buyer, 5)
        self.db.change_quantity(buyer, 1, 1)
        self.assertTrue(self.db.remove_from_cart(buyer, 5))

        lines, total = self.db.get_cart(buyer)
        self.assertEqual([(line.product_id, line.quantity) for line in lines], [(1, 2)])
        self.assertEqual(total, 128000)

        ok, error = self.db.change_quantity(buyer, 1, -5)
        self.assertFalse(ok)
        self.assertIn("не может", error)
        lines, total = self.db.get_cart(buyer)
        self.assertEqual(lines[0].quantity, 2)
        self.assertEqual(total, 128000)

        self.assertIsNone(self.db.create_checkout(other, "Борис"))
        other_lines, other_total = self.db.get_cart(other)
        self.assertEqual(other_lines, [])
        self.assertEqual(other_total, 0)

        draft = self.db.create_checkout(buyer, "Аня")
        self.assertIsNotNone(draft)
        assert draft is not None
        first = self.db.confirm(buyer, draft.token)
        second = self.db.confirm(buyer, draft.token)
        self.assertEqual(first.kind, "created")
        self.assertEqual(second.kind, "duplicate")
        self.assertEqual(first.order_id, second.order_id)
        self.assertEqual(len(self.db.list_all_orders()), 1)

        empty_cart, _empty_total = self.db.get_cart(buyer)
        self.assertEqual(empty_cart, [])
        self.assertIsNone(self.db.create_checkout(buyer, "Аня"))

        connection = sqlite3.connect(self.path)
        connection.execute("UPDATE products SET price_kopecks = 10000 WHERE id = 1")
        connection.commit()
        connection.close()

        reopened = Database(self.path)
        own = reopened.list_orders(buyer)
        self.assertEqual(len(own), 1)
        self.assertEqual(own[0].items[0].price_kopecks, 64000)
        self.assertEqual(own[0].items[0].quantity, 2)
        self.assertEqual(own[0].total_kopecks, 128000)
        self.assertEqual(own[0].status, "new")
        self.assertEqual(own[0].payment_status, "on_receipt")
        self.assertEqual(reopened.list_orders(other), [])
        self.assertIsNone(reopened.get_order_for_user(other, own[0].id))

        updated = reopened.set_status(own[0].id, "ready")
        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertEqual(updated.status, "ready")
        self.assertEqual(reopened.list_new_orders(), [])
        self.assertEqual(reopened.get_order_for_user(buyer, own[0].id).status, "ready")  # type: ignore[union-attr]

    def test_parallel_confirm_creates_one_order(self) -> None:
        self.db.add_to_cart(1, 2)
        draft = self.db.create_checkout(1, "Кира")
        assert draft is not None
        results: list[str] = []
        barrier = threading.Barrier(2)

        def worker() -> None:
            barrier.wait()
            results.append(self.db.confirm(1, draft.token).kind)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(sorted(results), ["created", "duplicate"])
        self.assertEqual(len(self.db.list_all_orders()), 1)

    def test_yookassa_is_not_paid_until_marked(self) -> None:
        self.db.add_to_cart(1, 1)
        draft = self.db.create_checkout(1, "Аня")
        assert draft is not None
        created = self.db.confirm(1, draft.token, online=True)
        self.assertEqual(created.kind, "created")
        self.assertEqual(created.payment_status, "pending")
        self.assertEqual(created.payment_method, "ЮKassa")
        self.assertEqual(self.db.list_new_orders(), [])
        again = self.db.confirm(1, draft.token, online=True)
        self.assertEqual(again.kind, "duplicate")
        self.assertEqual(again.order_id, created.order_id)
        self.assertEqual(len(self.db.list_all_orders()), 1)
        from bot.finance import ensure_payment, demo_event
        p = ensure_payment(self.db, created.order_id, 'demo', 1)
        demo_event(self.db, p['id'], 'succeeded', 1)
        visible = self.db.list_new_orders()
        self.assertEqual(len(visible), 1)
        self.assertEqual(visible[0].payment_status, "succeeded")

    def test_zero_quantity_is_rejected_by_database(self) -> None:
        self.db.ensure_user(1)
        connection = sqlite3.connect(self.path)
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO cart_items (user_id, product_id, quantity) VALUES (1, 1, 0)"
            )
        connection.close()


if __name__ == "__main__":
    unittest.main()
