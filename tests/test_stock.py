import tempfile
import sqlite3
import unittest
from pathlib import Path
from bot.db import Database
from bot import stock
from bot.web import create_app
from bot.stock_lab import SETTINGS


class StockUnitTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.db=Database(Path(self.tmp.name)/'shop.sqlite3')

    def test_new_stock_is_zero_and_receipt_is_idempotent(self):
        self.assertEqual(stock.view(self.db)['inventory'][0]['on_hand'],0)
        a=stock.receive(self.db,1,10,900,'receipt-key-001')
        b=stock.receive(self.db,1,10,900,'receipt-key-001')
        self.assertEqual(a,b)
        with self.assertRaises(ValueError):
            stock.receive(self.db,1,11,900,'receipt-key-001')
        self.assertEqual(stock.view(self.db)['inventory'][0]['on_hand'],10)
        self.assertTrue(stock.check_invariants(self.db))

    def test_database_rejects_invalid_stock_even_without_api(self):
        stock.receive(self.db,1,1,900,'receipt-key-001')
        for sql in ['UPDATE inventory SET on_hand=-1','UPDATE inventory SET reserved=2','UPDATE inventory SET on_hand=1.5','UPDATE inventory SET reserved=-1']:
            with self.assertRaises(sqlite3.IntegrityError):
                with self.db._transaction() as c:
                    c.execute(sql)
        self.assertTrue(stock.check_invariants(self.db))

    def test_production_has_no_lab_crash_or_payment_controls(self):
        app=create_app(self.db,SETTINGS)
        paths=[str(route.resource) for route in app.router.routes()]
        self.assertFalse(any('/test/' in path for path in paths))
        self.assertIsNone(self.db.fault_hook)
        self.assertIsNone(self.db.clock)


if __name__=='__main__':
    unittest.main()
