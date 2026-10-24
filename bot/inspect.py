"""Печать заказов из файла базы. Секрет бота и ключи подтверждения не выводятся.
"""

from bot.db import DB_PATH, Database
from bot.format import money, status_label


def main() -> None:
    db = Database(DB_PATH)
    orders = db.list_all_orders()
    print(f"Файл: {db.path}")
    print(f"Книг в каталоге: {db.product_count()}")
    print(f"Заказов: {len(orders)}")
    if not orders:
        return
    for order in orders:
        print(
            f"№ {order.id} | пользователь {order.user_id} | {order.customer_name} | "
            f"{money(order.total_kopecks)} | {status_label(order.status)} | {order.created_at}"
        )
        for item in order.items:
            print(
                f"    {item.title} | {money(item.price_kopecks)} × {item.quantity}"
            )


if __name__ == "__main__":
    main()
