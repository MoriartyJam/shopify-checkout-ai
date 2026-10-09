# Контракт синтетических данных v2

Набор имитирует подтверждённые структуры Shopify, не содержит реальных клиентов
и не утверждает психологические причины отказа.

## Сырые данные

- `synthetic_pixel_events.jsonl` — стандартные события Shopify Web Pixels;
- `synthetic_abandoned_checkouts.jsonl` — ответы типа `AbandonedCheckout`;
- `synthetic_abandonments.jsonl` — ответы типа `Abandonment`.

Ранняя остановка до отправки контакта присутствует в Pixel events, но не создаёт
строку `AbandonedCheckout`.

## Подготовленные данные

- `recommendation_cases.csv` — все сценарии;
- `train.csv`, `validation.csv`, `test.csv` — разбиение по `client_id`.

`detected_pattern` и `recommended_action` — синтетические учебные метки. Они не
приходят из Shopify. Финальные правила и пороги должны проверяться на результатах
реального магазина.

## Конфиденциальность

Email, телефон, имя и точный адрес намеренно отсутствуют. Синтетические ID нельзя
использовать как пример разрешения на обработку настоящих персональных данных.

## Реальные Web Pixel events

`POST /api/pixel-events` принимает только события этапов воронки и сохраняет их в
`data/raw/real_pixel_events.jsonl`. Pixel передаёт технический `clientId`, домен
магазина, агрегаты checkout и тип ошибки. Email, телефон, имя, адрес и введённое
покупателем значение ошибочного поля не передаются.

`src/build_real_dataset.py` группирует события по `checkout.token`, добавляет
тестовые/Admin-метки из `data/raw/test_scenarios.jsonl` и создаёт
`data/processed/real_checkout_cases.csv`. Факт завершения берётся из
`checkout_completed`; метка незавершённого тестового сценария явно помечается как
`test_annotation`, потому что отсутствие события само по себе ещё не доказывает
окончательный отказ клиента.

`customerCohortId` — локальный обезличенный идентификатор для связи нескольких
сценариев одного синтетического клиента. Это не Shopify Customer ID и не PII.

## Shopify Admin facts

`data/raw/shopify_admin_checkout_facts.jsonl` содержит минимальные нормализованные
факты из Admin GraphQL: результат checkout, псевдоним клиента, количество его
предыдущих заказов и технические ID. Исходные Customer ID и recovery URL не
сохраняются.

Завершённые заказы соединяются с Pixel по `Order.checkoutToken`. Для
`AbandonedCheckout` GraphQL не отдаёт тот же токен: используется безопасный
`checkoutPathToken`, извлечённый из pathname страницы и recovery URL. Query string,
recovery key и полный URL в события и датасет не записываются.
