# Фактический отчёт складской приёмки

Команда: `python -m bot.stock_acceptance --ttl 30 --stock 3 --buyers 12`
Изолированные данные: `data\stock-tests\run-20261024-105225-e00e5b`
Повторов конкурентных сценариев: 5. Два HTTP-сервера с общей SQLite.

Это настоящие HTTP-запросы и остановки процессов на изолированном стенде; провайдер учебный, реальная ЮKassa и Telegram-видео не запускались.

## 01 ручная цепочка

Ожидалось: инварианты и точные результаты раздела 10 документа store-stock.md.
Результат: ПРОЙДЕНО
Длительность: 1.328 сек.

```json
{
  "triples": [
    [
      10,
      0,
      10
    ],
    [
      10,
      3,
      7
    ],
    [
      7,
      0,
      7
    ],
    [
      7,
      2,
      5
    ],
    [
      7,
      0,
      7
    ],
    [
      10,
      0,
      10
    ]
  ],
  "expected": [
    [
      10,
      0,
      10
    ],
    [
      10,
      3,
      7
    ],
    [
      7,
      0,
      7
    ],
    [
      7,
      2,
      5
    ],
    [
      7,
      0,
      7
    ],
    [
      10,
      0,
      10
    ]
  ]
}
```

## 02 1 единица / 20 покупателей ×5

Ожидалось: инварианты и точные результаты раздела 10 документа store-stock.md.
Результат: ПРОЙДЕНО
Длительность: 11.984 сек.

```json
[
  {
    "repeat": 1,
    "success": 1,
    "unavailable": 19,
    "technical_errors": 0,
    "triple": [
      1,
      1,
      0
    ]
  },
  {
    "repeat": 2,
    "success": 1,
    "unavailable": 19,
    "technical_errors": 0,
    "triple": [
      1,
      1,
      0
    ]
  },
  {
    "repeat": 3,
    "success": 1,
    "unavailable": 19,
    "technical_errors": 0,
    "triple": [
      1,
      1,
      0
    ]
  },
  {
    "repeat": 4,
    "success": 1,
    "unavailable": 19,
    "technical_errors": 0,
    "triple": [
      1,
      1,
      0
    ]
  },
  {
    "repeat": 5,
    "success": 1,
    "unavailable": 19,
    "technical_errors": 0,
    "triple": [
      1,
      1,
      0
    ]
  }
]
```

## 03 10 единиц / 50 покупателей ×5

Ожидалось: инварианты и точные результаты раздела 10 документа store-stock.md.
Результат: ПРОЙДЕНО
Длительность: 22.547 сек.

```json
[
  {
    "repeat": 1,
    "success": 10,
    "unavailable": 40,
    "technical_errors": 0,
    "triple": [
      10,
      10,
      0
    ]
  },
  {
    "repeat": 2,
    "success": 10,
    "unavailable": 40,
    "technical_errors": 0,
    "triple": [
      10,
      10,
      0
    ]
  },
  {
    "repeat": 3,
    "success": 10,
    "unavailable": 40,
    "technical_errors": 0,
    "triple": [
      10,
      10,
      0
    ]
  },
  {
    "repeat": 4,
    "success": 10,
    "unavailable": 40,
    "technical_errors": 0,
    "triple": [
      10,
      10,
      0
    ]
  },
  {
    "repeat": 5,
    "success": 10,
    "unavailable": 40,
    "technical_errors": 0,
    "triple": [
      10,
      10,
      0
    ]
  }
]
```

## 04 многотоварный откат

Ожидалось: инварианты и точные результаты раздела 10 документа store-stock.md.
Результат: ПРОЙДЕНО
Длительность: 1.344 сек.

```json
{
  "result": "unavailable",
  "triple": [
    3,
    0,
    3
  ],
  "orders": 0
}
```

## 05 повторы оформления

Ожидалось: инварианты и точные результаты раздела 10 документа store-stock.md.
Результат: ПРОЙДЕНО
Длительность: 1.625 сек.

```json
{
  "requests": 20,
  "unique_orders": 1,
  "changed_rejected": true
}
```

## 06 пять оплат и старые события

Ожидалось: инварианты и точные результаты раздела 10 документа store-stock.md.
Результат: ПРОЙДЕНО
Длительность: 1.188 сек.

```json
{
  "operations": [
    {
      "type": "receipt",
      "amount": 120000,
      "cost_amount": 70000
    }
  ],
  "triple": [
    0,
    0,
    0
  ]
}
```

## 07 отмена и срок

Ожидалось: инварианты и точные результаты раздела 10 документа store-stock.md.
Результат: ПРОЙДЕНО
Длительность: 1.203 сек.

```json
{
  "releases": 2,
  "triple": [
    2,
    0,
    2
  ],
  "receipts": 0
}
```

## 08 гонки и строгая граница срока

Ожидалось: инварианты и точные результаты раздела 10 документа store-stock.md.
Результат: ПРОЙДЕНО
Длительность: 9.375 сек.

```json
{
  "races": [
    {
      "winner": "payment",
      "state": "paid",
      "triple": [
        0,
        0,
        0
      ]
    },
    {
      "winner": "payment",
      "state": "paid",
      "triple": [
        0,
        0,
        0
      ]
    },
    {
      "winner": "payment",
      "state": "paid",
      "triple": [
        0,
        0,
        0
      ]
    },
    {
      "winner": "payment",
      "state": "paid",
      "triple": [
        0,
        0,
        0
      ]
    },
    {
      "winner": "payment",
      "state": "paid",
      "triple": [
        0,
        0,
        0
      ]
    }
  ],
  "strict_boundary": [
    {
      "seconds": 29,
      "state": "paid",
      "triple": [
        0,
        0,
        0
      ]
    },
    {
      "seconds": 30,
      "state": "refunded",
      "triple": [
        1,
        0,
        1
      ]
    },
    {
      "seconds": 31,
      "state": "refunded",
      "triple": [
        1,
        0,
        1
      ]
    }
  ]
}
```

## 09 поздняя оплата

Ожидалось: инварианты и точные результаты раздела 10 документа store-stock.md.
Результат: ПРОЙДЕНО
Длительность: 1.25 сек.

```json
{
  "receipt": 240000,
  "refund": 120000,
  "cost": 70000,
  "cash_flow": 120000,
  "result": 50000,
  "triple": [
    0,
    0,
    0
  ]
}
```

## 10 перезапуск с резервом

Ожидалось: инварианты и точные результаты раздела 10 документа store-stock.md.
Результат: ПРОЙДЕНО
Длительность: 3.281 сек.

```json
{
  "triple": [
    1,
    0,
    1
  ],
  "releases": 1,
  "startup_seconds": 1.063
}
```

## 11 авария и потерянный ответ возврата

Ожидалось: инварианты и точные результаты раздела 10 документа store-stock.md.
Результат: ПРОЙДЕНО
Длительность: 2.391 сек.

```json
{
  "receipt_rows": 1,
  "refund_rows": 1,
  "provider_refunds": 1,
  "attempts": 3,
  "triple": [
    1,
    0,
    1
  ]
}
```

## 12 авария до commit

Ожидалось: инварианты и точные результаты раздела 10 документа store-stock.md.
Результат: ПРОЙДЕНО
Длительность: 2.203 сек.

```json
{
  "orders_after_crash": 0,
  "orders_after_retry": 1,
  "triple": [
    1,
    1,
    0
  ]
}
```

## 13 доступ и подмена

Ожидалось: инварианты и точные результаты раздела 10 документа store-stock.md.
Результат: ПРОЙДЕНО
Длительность: 1.218 сек.

```json
{
  "unauthorized_stock": "403",
  "foreign_order": "403",
  "amount_and_TTL": "400",
  "data_unchanged": true
}
```

## 14 параметрическая конкуренция

Ожидалось: инварианты и точные результаты раздела 10 документа store-stock.md.
Результат: ПРОЙДЕНО
Длительность: 8.188 сек.

```json
[
  {
    "repeat": 1,
    "success": 3,
    "unavailable": 9,
    "technical_errors": 0,
    "triple": [
      3,
      3,
      0
    ]
  },
  {
    "repeat": 2,
    "success": 3,
    "unavailable": 9,
    "technical_errors": 0,
    "triple": [
      3,
      3,
      0
    ]
  },
  {
    "repeat": 3,
    "success": 3,
    "unavailable": 9,
    "technical_errors": 0,
    "triple": [
      3,
      3,
      0
    ]
  },
  {
    "repeat": 4,
    "success": 3,
    "unavailable": 9,
    "technical_errors": 0,
    "triple": [
      3,
      3,
      0
    ]
  },
  {
    "repeat": 5,
    "success": 3,
    "unavailable": 9,
    "technical_errors": 0,
    "triple": [
      3,
      3,
      0
    ]
  }
]
```

## Ручная сверка поздней оплаты

Два подтверждённых платежа по 1200 ₽ = 2400 ₽ поступлений; один полный поздний возврат 1200 ₽. Денежный поток = 2400 − 1200 = 1200 ₽. Себестоимость только принятой продажи Б = 700 ₽; результат = 2400 − 1200 − 700 = 500 ₽. Поздняя оплата А и её возврат имеют нулевую себестоимость и не меняют склад.

Незавершённое вне автоматической приёмки: запись реального Telegram/Mini App, оплата/возврат в вашем тестовом кабинете ЮKassa и публикация репозитория не выполнялись этим запуском.