# Передача backend для frontend

Снимок на26октября2026. Здесь карта проекта и фактический контракт интеграции. Экраны, дизайн и продуктовое ТЗ определяет команда отдельно.

## Репозиторий и версия

Основной приватный репозиторий: [ostrovok-hackathon-2026/aegml](https://github.com/ostrovok-hackathon-2026/aegml). Рабочая ветка — `backend/harness-foundation`; `main` может отставать. Зеркало — [osirust/O-Travel-Agent](https://github.com/osirust/O-Travel-Agent), та же ветка.

```bash
git clone --branch backend/harness-foundation https://github.com/ostrovok-hackathon-2026/aegml.git
cd aegml
docker compose up --build -d
```

Участнику нужен доступ к приватному репозиторию и работающий Docker с Compose. Первая сборка требует интернета. PostgreSQL создаётся в Docker, отдельная установка SQL-сервера не нужна.

Backend: `http://localhost:8080`; тестовый чат: `/debug`; схема API: `/openapi.json`, интерактивная документация: `/docs`. Если порт занят, задайте `APP_PORT` в своём `.env`. Чужой `localhost:8084` — не удалённый адрес сервера: на другом компьютере нужен свой запущенный backend.

Без ключей работают загрузка DEMO-поездки, формы, управление и ответы по некоторым сохранённым фактам. Свободный чат требует доступного LLM API. В репозитории нет личных ключей; не переносите их в frontend, Vite-переменные или Git. `scripts/after80_*_backend.py` и `configure_keys.py` обслуживают отдельный стенд владельца в WSL, они не нужны для переносимого Docker-запуска.

## Карта файлов

| Где | Что находится |
| --- | --- |
| [README.md](../README.md) | Запуск и текущая версия продукта. |
| [FRONTEND_CONTRACT.md](FRONTEND_CONTRACT.md) | HTTP-маршруты, авторизация, поля ответов, polling, формы, pause/resume, preview/confirm и ошибки. |
| [frontend/openapi.json](frontend/openapi.json) | Экспорт фактической схемы backend для генерации типов; актуальный сервер также отдаёт `/openapi.json`. |
| [frontend/api-examples.json](frontend/api-examples.json) | Ответы проверенного DEMO-потока, очищенные от токенов. Это примеры структуры, не активные предложения. |
| [PRODUCT_DECISIONS.md](PRODUCT_DECISIONS.md) | Согласованные цели и продуктовые ограничения. |
| [API_SETUP.md](API_SETUP.md), [.env.example](../.env.example) | Настройки модели, туристических источников и CORS. |
| [docker-compose.yml](../docker-compose.yml), [Dockerfile](../Dockerfile) | Приложение и собственная БД в Docker. |
| [otravel/api.py](../otravel/api.py) | Реальные маршруты FastAPI и схемы входных HTTP-запросов. |
| [otravel/domain.py](../otravel/domain.py) | TripState: путешественники, группы, остановки, номера, плечи маршрута, бюджет и ограничения. |
| [otravel/harness.py](../otravel/harness.py) | Разбор реплики, проверка операций, уточнения, пересборка и текст ответа. |
| [otravel/store.py](../otravel/store.py), [migrations](../migrations) | Сессии, ревизии, сообщения, события, планы, корзины и тестовые заказы в PostgreSQL. |
| [otravel/providers.py](../otravel/providers.py), [orchestrator.py](../otravel/orchestrator.py), [planning.py](../otravel/planning.py) | Адаптеры модели и туристических API, сборка и проверка поездки. |
| [otravel/static](../otravel/static) | Временный debug-интерфейс: `debug.html`, `chat.css`, `chat.js`. Можно смотреть последовательность API-вызовов; это не финальный дизайн. |
| [testing/AFTER80.md](testing/AFTER80.md), [verification](verification) | Проверки, доказательства и открытые ограничения. |
| [research/harness-plan-review.md](research/harness-plan-review.md) | План с постоянными HARNESS-ID и условиями закрытия задач. |
| [fixtures/evaluation](../fixtures/evaluation), [tests](../tests) | Данные и автоматические проверки. Их наличие не означает приёмки всех языковых сценариев. |

## Минимальная последовательность интеграции

1. `POST /api/v1/sessions {}` создаёт сессию. Сохранить `session_id` и однократно выданный `session_token`; дальнейшие запросы отправлять с `Authorization: Bearer ...`.
2. Восстановление страницы: `GET /api/v1/sessions/{id}` и `GET .../messages`. Snapshot содержит актуальные `trip`, `state_version`, `status`, `waiting_reason`, `pending_question`.
3. Отправка текста: `POST .../messages` с `text`, заранее сохранённым `client_message_id` и `expected_version`. Ответ202 содержит `run_id`, а не готовый ответ агента.
4. Poll `GET /api/v1/runs/{run_id}` до завершения. Показывать `run.result.reply`; планы лежат в `run.result.result.plans`, изменения — в `run.result.changes`. После завершения перечитать snapshot.
5. Прогресс: `GET .../events?after=<next_cursor>`. Это JSON-polling; SSE и поток токенов сейчас не реализованы.
6. Для работы без ключей: `POST .../demo`. Для форм: `POST .../commands`; реальные ID и версию брать из snapshot.
7. Корзина: `POST .../checkout/preview` по выбранному текущему `plan_id`. Подтверждать только показанный quote через `POST .../checkout/confirm` с его версиями и сохранённым `idempotency_key`.

Ответ «да», выбор карточки и подтверждение конкретного quote — разные действия. После409 перечитать состояние; новую сумму автоматически не подтверждать. Не генерировать новый UUID при повторе операции с неизвестным сетевым исходом.

## Данные для отображения

- Деньги — целые `amount_minor`/`total_minor` плюс `currency`; для RUB/USD/EUR это копейки/центы. Сумму не извлекать из текста агента.
- Состав, группы, комнаты и маршрут — из `trip`; адресные ID нужны запросам, пользовательские подписи формируются отдельно.
- Причины ожидания — из `pending_question` и `waiting_reason`. При `user_pause` дополнения сохраняются, работа продолжается только по явной команде.
- Источник модели и источник тарифа различаются. Показывать `DEMO`, `SANDBOX`, `LIVE_INDICATIVE`, `LIVE_BOOKABLE` по компоненту. Sandbox не является реальной ценой, indicative не обещает наличие.
- При наличии `result.search_explanation` использовать его текст/summary для объяснения источников и отклонённых тарифов. Отсутствие поля не является ошибкой.
- Отсутствующий новый `plans` не стирает прежнюю актуальную выдачу. `plans_preserved` обозначает сохранённый batch; цены им не обновляются.
- `BOOKED_DEMO`, `is_demo=true`, `booking_created_at_supplier=false` означают только имитацию оформления.

Отдельный frontend-origin надо добавить в `CORS_ORIGINS`; по умолчанию предусмотрены localhost5173 и3000. Можно использовать dev-proxy, чтобы браузер обращался к backend через тот же origin.

## Текущие границы

Последний полный кодовый прогон —2964PASS/0skip. Это не2964диалога и не приёмка всех80/106языковых сценариев. Сквозной смешанный API-путь проверен на двух городах и четырёх номерах; остаются зависимости от квот и поведения внешних моделей.

Фотографии загружаются отдельно: `GET /api/v1/sessions/{id}/media?plan_id=<id>&state_version=<version>` с тем же Bearer-токеном. Контракт и пример — [HOTEL_MEDIA.md](HOTEL_MEDIA.md). Поля `photos` в тарифе нет: отсутствие снимка не должно мешать выбору. В `/debug` есть небольшой пример вывода.

Справочный пересчёт RUB/USD/EUR сохраняет исходные суммы поставщиков. Если источник курса недоступен, общий итог для разных валют не подтверждается. Готового SSE/streaming endpoint нет. Реальная покупка и оплата не выполняются.

Полные исходные80переписок и приватные runtime-журналы находятся у владельца в ignored `.local` и WSL. В Git передаются код, очищенные отчёты и тестовые данные; токены доступа и `.env` отдельно не публикуются.
