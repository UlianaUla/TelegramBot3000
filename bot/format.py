"""Форматирование цен и статусов. Суммы в базе хранятся в копейках."""

STATUS_LABELS = {
    "new": "Новый",
    "ready": "Готов к выдаче",
    "done": "Выдан",
    "cancelled": "Отменён",
}

FULFILLMENT = "Самовывоз)))"
PAYMENT_METHOD = "при получении"
PAYMENT_YOOKASSA = "ЮKassa"
PICKUP_PLACE = "г. Учебный, ул. Книжная, д. 7"


def money(kopecks: int) -> str:
    rubles, kop = divmod(int(kopecks), 100)
    rubles_text = f"{rubles:,}".replace(",", " ")
    if kop:
        return f"{rubles_text},{kop:02d} ₽"
    return f"{rubles_text} ₽"


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status)
