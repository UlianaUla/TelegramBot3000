import asyncio
import hashlib
import hmac
import json
import sqlite3
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,AsyncMock
from urllib.parse import urlencode
from aiohttp.test_utils import TestClient, TestServer
from bot.config import Settings
from bot.db import Database,SCHEMA
from bot import finance
from bot.web import validate_init_data,create_app
from bot.provider import sync_payment,start_refund
from bot.keyboards.menu import main_menu,BTN_FINANCE
from bot.render import render_order
from bot.finance_worker import tick
from bot.handlers.finance import summary as summary_command

SETTINGS=Settings('123456:synthetic-test-token',frozenset({900}), '', '', '')


def signed(uid=900,auth_date=None):
    data={'user':json.dumps({'id':uid}),'auth_date':str(int(time.time()) if auth_date is None else auth_date),'query_id':'synthetic-test'}
    secret=hmac.new(b'WebAppData',SETTINGS.bot_token.encode(),hashlib.sha256).digest()
    data['hash']=hmac.new(secret,'\n'.join(f'{k}={v}' for k,v in sorted(data.items())).encode(),hashlib.sha256).hexdigest()
    return urlencode(data)


class FinanceTests(unittest.TestCase):
    @staticmethod
    def seed(db):
        from bot.stock import receive
        for pid in range(1,7):
            receive(db,pid,100,'fixture',f'fixture-stock-{pid}')

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'shop.db';self.db=Database(self.path)
        self.seed(self.db)

    def make_payment(self,uid=1,provider='demo'):
        self.db.add_to_cart(uid,1)
        draft=self.db.create_checkout(uid,'Учебный покупатель')
        result=self.db.confirm(uid,draft.token,online=True)
        return finance.ensure_payment(self.db,result.order_id,provider,uid,'external-test' if provider=='yookassa' else None)

    def report(self):
        return finance.report(self.db,'2000-01-01','2001-01-01')

    def test_payment_refund_lifecycle_and_exact_formulas(self):
        p=self.make_payment()
        finance.demo_event(self.db,p['id'],'succeeded',1)
        finance.demo_event(self.db,p['id'],'succeeded',1)
        finance.expense(self.db,10000,'Доставка',900,'expense-key-01')
        finance.expense(self.db,10000,'Доставка',900,'expense-key-01')
        today=time.strftime('%Y-%m-%d',time.gmtime())
        report=finance.report(self.db,today,today)
        self.assertEqual(report['cash_flow'],54000)
        self.assertEqual(report['educational_result'],22000)
        finance.demo_event(self.db,p['id'],'refund_pending',900,manager=True)
        self.assertEqual(finance.report(self.db,today,today)['cash_flow'],54000)
        with self.assertRaises(ValueError):
            finance.demo_event(self.db,p['id'],'refund_succeeded',900,manager=True,fail=True)
        self.assertEqual(finance.report(self.db,today,today)['cash_flow'],54000)
        finance.demo_event(self.db,p['id'],'refund_succeeded',900,manager=True)
        finance.demo_event(self.db,p['id'],'refund_succeeded',900,manager=True)
        report=finance.report(Database(self.path),today,today)
        self.assertEqual((report['cash_flow'],report['educational_result']),(-10000,-10000))
        self.assertEqual(len(report['operations']),3)
        self.assertEqual(self.db.get_order(p['order_id']).payment_status,'refunded')

    def test_other_period_uses_confirmation_not_creation(self):
        p=self.make_payment()
        with patch('bot.finance.now',return_value='2026-09-30T23:59:59.000000+00:00'):
            finance.demo_event(self.db,p['id'],'succeeded',1)
        finance.demo_event(self.db,p['id'],'refund_pending',900,manager=True)
        with patch('bot.finance.now',return_value='2026-10-01T00:00:00.000000+00:00'):
            finance.demo_event(self.db,p['id'],'refund_succeeded',900,manager=True)
        sept=finance.report(self.db,'2026-09-01','2026-09-30')
        octo=finance.report(self.db,'2026-10-01','2026-10-31')
        self.assertEqual((sept['cash_flow'],sept['educational_result']),(64000,32000))
        self.assertEqual((octo['cash_flow'],octo['educational_result']),(-64000,-32000))

    def test_mismatch_and_wrong_owner_do_not_write(self):
        p=self.make_payment()
        kwargs=dict(status='succeeded',amount=p['amount'],currency='RUB',order_id=p['order_id'],
                    external_id='event',provider='demo',provider_id=None,author=1)
        for field,value in [('amount',1),('currency','USD'),('order_id',999),('provider_id','wrong'),('provider','yookassa')]:
            with self.assertRaises(ValueError):
                finance.apply_event(self.db,p['id'],**{**kwargs,field:value})
        with self.assertRaises(PermissionError):
            finance.demo_event(self.db,p['id'],'succeeded',2)
        with self.assertRaises(PermissionError):
            finance.demo_event(self.db,p['id'],'refund_pending',1)
        self.assertEqual(finance.payment(self.db,p['id'])['status'],'pending')
        self.assertEqual(finance.report(self.db,'2026-01-01','2026-12-31')['operations'],[])

    def test_parallel_events_create_one_receipt(self):
        p=self.make_payment()
        with ThreadPoolExecutor(2) as pool:
            results=list(pool.map(lambda _:finance.demo_event(self.db,p['id'],'succeeded',1),range(2)))
        self.assertEqual(sorted(results),[False,True])
        c=self.db._connect()
        try:
            self.assertEqual(c.execute("SELECT count(*) FROM finance_operations WHERE type='receipt'").fetchone()[0],1)
        finally:
            c.close()

    def test_cancellation_and_invalid_transitions(self):
        p=self.make_payment()
        finance.demo_event(self.db,p['id'],'canceled',1)
        finance.demo_event(self.db,p['id'],'succeeded',1)
        self.assertEqual(self.db.get_order(p['order_id']).stock_state,'payment_conflict')

    def test_atomic_rollback_on_journal_error(self):
        p=self.make_payment()
        with self.db._transaction() as c:
            c.execute("CREATE TRIGGER fail_receipt BEFORE INSERT ON finance_operations BEGIN SELECT RAISE(ABORT,'test error'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            finance.demo_event(self.db,p['id'],'succeeded',1)
        self.assertEqual(finance.payment(self.db,p['id'])['status'],'pending')
        self.assertEqual(self.db.get_order(p['order_id']).payment_status,'pending')

    def test_snapshot_cost_and_unknown_historic_cost(self):
        p=self.make_payment()
        with self.db._transaction() as c:
            c.execute('UPDATE products SET cost_kopecks=1 WHERE id=1')
        finance.demo_event(self.db,p['id'],'succeeded',1)
        c=self.db._connect()
        try:
            self.assertEqual(c.execute('SELECT cost_amount FROM finance_operations').fetchone()[0],32000)
        finally:
            c.close()
        p2=self.make_payment(2)
        with self.db._transaction() as c:
            c.execute('UPDATE order_items SET cost_kopecks=NULL WHERE order_id=?',(p2['order_id'],))
        finance.demo_event(self.db,p2['id'],'succeeded',2)
        today=time.strftime('%Y-%m-%d',time.gmtime())
        self.assertIsNone(finance.report(self.db,today,today)['educational_result'])

    def test_provider_snapshot_uses_same_checks(self):
        p=self.make_payment(provider='yookassa')
        obj=SimpleNamespace(id='external-test',metadata={'order_id':str(p['order_id'])},
                            amount=SimpleNamespace(value='1.00',currency='RUB'),status='succeeded')
        with patch('bot.provider.snapshot',return_value=obj):
            with self.assertRaises(ValueError):
                asyncio.run(sync_payment(self.db,SETTINGS,p))
        obj.amount.value='640.00'
        with patch('bot.provider.snapshot',return_value=obj):
            asyncio.run(sync_payment(self.db,SETTINGS,p))
            asyncio.run(sync_payment(self.db,SETTINGS,p))
        self.assertEqual(finance.payment(self.db,p['id'])['status'],'succeeded')

    def test_pending_and_canceled_provider_do_not_record_receipt(self):
        p=self.make_payment(provider='yookassa')
        obj=SimpleNamespace(id='external-test',metadata={'order_id':str(p['order_id'])},
            amount=SimpleNamespace(value='640.00',currency='RUB'),status='pending')
        with patch('bot.provider.snapshot',return_value=obj):
            asyncio.run(sync_payment(self.db,SETTINGS,p))
        self.assertEqual(finance.payment(self.db,p['id'])['status'],'pending')
        obj.status='canceled'
        with patch('bot.provider.snapshot',return_value=obj):
            asyncio.run(sync_payment(self.db,SETTINGS,p))
        c=self.db._connect()
        try:
            self.assertEqual(c.execute('SELECT count(*) FROM finance_operations').fetchone()[0],0)
        finally:
            c.close()

    def test_repeated_completed_refund_is_rejected(self):
        p=self.make_payment(provider='yookassa')
        for status in ['succeeded','refund_pending','refund_succeeded']:
            finance.apply_event(self.db,p['id'],status=status,amount=64000,currency='RUB',
                order_id=p['order_id'],external_id='test:'+status,provider='yookassa',provider_id=p['provider_id'],author=900)
        with patch('bot.provider.configure'):
            with self.assertRaises(ValueError):
                asyncio.run(start_refund(self.db,SETTINGS,finance.payment(self.db,p['id']),900))
        c=self.db._connect()
        try:
            self.assertEqual(c.execute("SELECT count(*) FROM finance_operations WHERE type='refund'").fetchone()[0],1)
        finally:
            c.close()

    def test_order_shows_payment_identifier(self):
        p=self.make_payment(provider='yookassa')
        self.db.attach_payment(p['order_id'],p['provider_id'],'https://example.com')
        self.assertIn('external-test',render_order(self.db.get_order(p['order_id'])))

    def test_provider_refund_only_after_verified_success(self):
        p=self.make_payment(provider='yookassa')
        finance.apply_event(self.db,p['id'],status='succeeded',amount=64000,currency='RUB',
            order_id=p['order_id'],external_id='real-test-receipt',provider='yookassa',
            provider_id=p['provider_id'],author='yookassa')
        finance.apply_event(self.db,p['id'],status='refund_pending',amount=64000,currency='RUB',
            order_id=p['order_id'],external_id='request-only',provider='yookassa',
            provider_id=p['provider_id'],author=900)
        with self.db._transaction() as c:
            c.execute("UPDATE payments SET refund_id='refund-test' WHERE id=?",(p['id'],))
        p=finance.payment(self.db,p['id'])
        obj=SimpleNamespace(id='refund-test',payment_id='external-test',
            amount=SimpleNamespace(value='640.00',currency='RUB'),status='canceled')
        with patch('bot.provider.snapshot',return_value=obj):
            with self.assertRaises(ValueError):
                asyncio.run(sync_payment(self.db,SETTINGS,p))
        self.assertEqual(finance.payment(self.db,p['id'])['status'],'refund_pending')
        obj.status='succeeded'
        with patch('bot.provider.snapshot',return_value=obj):
            asyncio.run(sync_payment(self.db,SETTINGS,p))
        self.assertEqual(finance.payment(self.db,p['id'])['status'],'refund_succeeded')

    def test_legacy_migration_preserves_orders_without_inventing_cost(self):
        path=Path(self.tmp.name)/'legacy.db'
        c=sqlite3.connect(path)
        c.executescript(SCHEMA)
        c.execute("INSERT INTO users VALUES (123,'telegram')")
        c.execute("INSERT INTO products VALUES (1,'Старая книга','Описание',64000,1)")
        c.execute("""INSERT INTO orders (user_id,customer_name,fulfillment,payment_method,
            total_kopecks,status,created_at,confirm_token) VALUES (123,'Аня','Самовывоз',
            'при получении',64000,'new','2026-01-01','legacy')""")
        c.execute("INSERT INTO order_items (order_id,product_id,title,price_kopecks,quantity) VALUES (1,1,'Старая книга',64000,1)")
        c.commit();c.close()
        migrated=Database(path)
        self.assertEqual(migrated.get_order_for_user(123,1).total_kopecks,64000)
        self.assertIsNone(migrated.get_order(1).items[0].cost_kopecks)


class AuthTests(unittest.TestCase):
    def test_finance_entry_is_visible(self):
        for role in [False,True]:
            self.assertIn(BTN_FINANCE,[b.text for row in main_menu(is_manager=role).keyboard for b in row])
    def test_signatures_role_age_and_duplicates(self):
        self.assertEqual(validate_init_data(signed(),SETTINGS),900)
        for raw in ['',signed(1),signed(auth_date=int(time.time())-4000),signed(auth_date=int(time.time())+100),signed().replace('900','901'),signed()+'&auth_date=1']:
            with self.assertRaises(PermissionError):
                validate_init_data(raw,SETTINGS)


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.db=Database(Path(self.tmp.name)/'api.db')
        FinanceTests.seed(self.db)
        self.client=TestClient(TestServer(create_app(self.db,SETTINGS)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def test_all_financial_requests_require_manager_signature(self):
        for raw in ['',signed(1),signed(auth_date=int(time.time())-4000),'invalid-data']:
            h={'X-Telegram-Init-Data':raw}
            for method,path,body in [('GET','/api/summary?start=2026-10-01&end=2026-10-31',None),
                                    ('POST','/api/expenses',{'amount':1,'note':'x','key':'key-0001'}),
                                    ('POST','/api/payments/unknown',{'action':'succeeded'})]:
                r=await self.client.request(method,path,json=body,headers=h)
                self.assertEqual(r.status,403)
        h={'X-Telegram-Init-Data':signed()}
        r=await self.client.post('/api/expenses',headers=h,json={'amount':10000,'note':'Реклама','key':'request-0001'})
        self.assertEqual(r.status,200)
        r=await self.client.get('/api/summary?start=2026-10-01&end=2026-10-31',headers=h)
        self.assertEqual(r.status,200)
        d=await r.json()
        self.assertEqual(d['expense'],10000)
        r=await self.client.post('/api/expenses',headers=h,json={'amount':-1,'note':'x','key':'request-0002'})
        self.assertEqual(r.status,400)
        r=await self.client.post('/api/expenses',headers=h,json={'amount':0,'note':'x','key':'request-0003'})
        self.assertEqual(r.status,400)
        c=self.db._connect()
        try:
            self.assertEqual(c.execute('SELECT count(*) FROM finance_operations').fetchone()[0],1)
        finally:
            c.close()

    async def test_archived_demo_actions_are_disabled(self):
        self.db.add_to_cart(1,1)
        draft=self.db.create_checkout(1,'Учебный клиент')
        result=self.db.confirm(1,draft.token,online=True)
        p=finance.ensure_payment(self.db,result.order_id,'demo',1)
        h={'X-Telegram-Init-Data':signed()}
        for action in ['succeeded','canceled','refund_pending','refund_succeeded','refund_error','check']:
            r=await self.client.post('/api/payments/'+p['id'],headers=h,json={'action':action})
            self.assertEqual(r.status,400)
        self.assertEqual(finance.payment(self.db,p['id'])['status'],'pending')

    async def test_provider_api_only_accepts_verified_status(self):
        self.db.add_to_cart(1,1)
        draft=self.db.create_checkout(1,'Учебный клиент')
        result=self.db.confirm(1,draft.token,online=True)
        p=finance.ensure_payment(self.db,result.order_id,'yookassa',1,'api-test-payment')
        h={'X-Telegram-Init-Data':signed()}
        path='/api/payments/'+p['id']
        r=await self.client.post(path,headers=h,json={'action':'succeeded','amount':64000})
        self.assertEqual(r.status,400)
        obj=SimpleNamespace(id='api-test-payment',metadata={'order_id':str(p['order_id'])},
                            amount=SimpleNamespace(value='640.00',currency='RUB'),status='succeeded')
        with patch('bot.provider.snapshot',return_value=obj):
            r=await self.client.post(path,headers=h,json={'action':'check','amount':1})
            self.assertEqual(r.status,200)
        self.assertEqual(finance.payment(self.db,p['id'])['status'],'succeeded')

    async def test_notifications_survive_restart_without_duplicate(self):
        self.db.add_to_cart(1,1)
        draft=self.db.create_checkout(1,'Учебный клиент')
        order=self.db.confirm(1,draft.token,online=True)
        p=finance.ensure_payment(self.db,order.order_id,'demo',1)
        bot=SimpleNamespace(send_message=AsyncMock())
        await tick(self.db,SETTINGS,bot)
        self.assertEqual(bot.send_message.await_count,0)
        finance.demo_event(self.db,p['id'],'succeeded',1)
        await tick(self.db,SETTINGS,bot)
        messages=[call.args[1] for call in bot.send_message.await_args_list]
        self.assertTrue(any('поступление' in text for text in messages))
        count=bot.send_message.await_count
        await tick(Database(self.db.path),SETTINGS,bot)
        self.assertEqual(bot.send_message.await_count,count)

    async def test_summary_command_checks_manager_and_formula(self):
        finance.expense(self.db,10000,'Реклама',900,'summary-key-1')
        today=time.strftime('%Y-%m-%d',time.gmtime())
        for uid in [1,900]:
            message=SimpleNamespace(from_user=SimpleNamespace(id=uid),
                text=f'/summary {today} {today}',answer=AsyncMock())
            await summary_command(message,self.db,SETTINGS)
            text=message.answer.await_args.args[0]
            if uid==900:
                self.assertIn('UTC',text)
                self.assertIn('Учебный результат',text)
                self.assertIn('−',text)
                self.assertIn('100',text)
            else:
                self.assertIn('только менеджеру',text)
                self.assertNotIn('100',text)


if __name__=='__main__':
    unittest.main()
