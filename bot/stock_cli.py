"""Explicit receipts and isolated lab preparation. No silent opening stock."""
import argparse
import json
from pathlib import Path
import sys
from bot.config import load_settings
from bot.db import Database,DB_PATH
from bot import stock


def main():
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser()
    subs=parser.add_subparsers(dest='command',required=True)
    show=subs.add_parser('show');show.add_argument('--db',type=Path,default=DB_PATH)
    receipt=subs.add_parser('receive')
    receipt.add_argument('--product',type=int,required=True)
    receipt.add_argument('--quantity',type=int,required=True)
    receipt.add_argument('--key',required=True)
    prep=subs.add_parser('prepare-test')
    prep.add_argument('--db',required=True);prep.add_argument('--stock',type=int,default=1)
    prep.add_argument('--buyers',type=int,default=20);prep.add_argument('--ttl',type=int,default=120)
    args=parser.parse_args()
    if args.command=='prepare-test':
        from bot.stock_lab import safe_path
        path=safe_path(args.db)
        if path.exists():
            parser.error('Тестовая база уже существует; выберите новое имя. Автоматического сброса нет.')
        if not 1<=args.stock<=1000000 or not 1<=args.buyers<=200:
            parser.error('Положительный запас и 1–200 тестовых покупателей')
        db=Database(path,reservation_seconds=args.ttl)
        with db._transaction() as c:
            c.execute('UPDATE products SET price_kopecks=120000,cost_kopecks=70000 WHERE id IN (1,2)')
        stock.receive(db,1,args.stock,'test-fixture','initial-test-receipt')
        for uid in range(1000,1000+args.buyers):
            db.add_to_cart(uid,1)
        print('Подготовлена изолированная база:',path)
        print('Покупатели:',args.buyers,'/ запас:',args.stock,'/ срок:',args.ttl)
        return
    if args.command=='receive':
        settings=load_settings()
        if not settings.manager_ids:
            parser.error('Сначала укажите менеджера в MANAGER_IDS')
        db=Database(reservation_seconds=settings.reservation_seconds)
        ident=stock.receive(db,args.product,args.quantity,min(settings.manager_ids),args.key)
        print('Поступление записано, ID:',ident)
    else:
        db=Database(args.db)
    print(json.dumps(stock.view(db),ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
