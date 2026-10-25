# О!дин запрос — техническое ТЗ Agent Harness (25–29 октября 2026)

**Назначение:** backend, диалоговый агент и проверяемая оркестрация путешествия. Frontend делает отдельный участник; контракт API отдаётся ему в первый день.

## 0. Цель и принципиальные ограничения

- Любое новое сообщение пользователя должно обрабатываться как **намерение + изменения состояния + проверка выполнимости**, а не как поиск заготовленного ответа.
- Поддержать этапы: вдохновение → уточнение → поиск → сравнение → сборка поездки → пересборка → подтверждённое **демо-бронирование**.
- При отсутствии сведений агент задаёт **минимальный существенный вопрос** и переводит сессию в `WAITING_USER`, не продолжая самостоятельные действия.
- Принцип честности: до реального API все гостиничные тарифы, транспорт, наличие и бронирования маркируются как **синтетические/демонстрационные**. Без источника нельзя обещать действующую цену, реальное наличие, визу, доступность конкретного номера или время рейса.
- PostgreSQL — долговременный источник состояния. Каждый вход, выход, вызов LLM, вызов инструмента, переход, ошибка и утверждённая версия поездки оставляют **структурированный, доступный для проверки след**. Не отправлять все сетевые пакеты «через PostgreSQL»: внешние вызовы делает backend через Tool Gateway, который пишет журнал в БД.
- Живые API поставщиков подключаются по готовому интерфейсу 27–28 октября, но успешный `docker compose up --build` и сквозной демосценарий не должны зависеть от них.

## 1. Выбранный стек

- Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2 (async), Alembic, PostgreSQL 16/17; HTTPX для внешних интеграций; pytest, pytest-asyncio, Hypothesis (если успеем), Ruff.
- OpenAI-совместимый LLM клиент за интерфейсом `ModelProvider` с ограничением шагов, тайм-аутов и расходов; строгий структурированный ответ. Подключать LLM для свободной речи по мере готовности ключа. Без LLM возможен ограниченный офлайн-фолбэк, **не** обещать полноценное понимание произвольного языка.
- Один простой `AgentOrchestrator` со state machine; LangGraph — **только если исполнитель уже хорошо знаком**. При использовании LangGraph не дублировать несогласованные версии основного TripState в разных хранилищах.
- Никаких Redis, Celery, Kafka, микросервисов, векторной БД и «роя агентов» для MVP.

## 2. Зависимости и порядок обработки одного сообщения

```
Frontend POST /api/v1/sessions/{session_id}/messages
  -> Intake (idempotent client_message_id)
  -> PostgreSQL: message + run(CREATED)
  -> Load versioned TripState, short dialogue context, pending question
  -> LLM NLU: intent + schema-validated patch (no direct SQL)
  -> Resolve references ('первый вариант', 'верни как было')
  -> ApplyPatch + ConflictEngine (optimistic concurrency)
  -> Decision:
      A. WAITING_USER => one concise clarification; persist pending question
      B. OPTIONS => Tool Gateway -> Mock/Live Adapters -> Constraint Validator -> Ranker
      C. CART => rebuild quote from chosen offers -> confirmation
      D. BOOK => validate quote/version/explicit consent -> idempotent DEMO order
      E. RECOVERABLE_ERROR => persist and return safe response
  -> PostgreSQL: assistant message + events + state revision + run status
  -> response to frontend: chat message, status, trip, offers, cart, changes
```

Отдельно: **не держать транзакцию PostgreSQL открытой во время запроса LLM/API или ожидания ответа пользователя**. Читайте версию, запускайте долгую операцию, а при записи применяйте CAS-проверку версии (`UPDATE ... WHERE version = expected_version`). Если состояние изменилось, отбросьте устаревший результат и повторно рассчитайте затронутые части. Для короткой операции оформления используйте атомарную транзакцию и уникальный ключ идемпотентности.

## 3. Статусы state machine

- `NEW`: создана сессия.
- `DISCOVERY`: разбор неструктурированной идеи, выбор направления.
- `WAITING_USER`: сохранены вопрос или просьба пользователя «подожди, я добавлю»; выполнение полностью остановлено до следующего сообщения.
- `SEARCHING`: выполняются инструменты.
- `OPTIONS_READY`: есть варианты (или документированное отсутствие подходящих).
- `TRIP_DRAFT`: выбран вариант, собраны транспорт/размещение/трансферы.
- `AWAITING_CONFIRMATION`: зафиксирована версия корзины и требуется явное согласие.
- `BOOKED_DEMO`: создана одна имитированная бронь.
- `RECOVERABLE_ERROR`: сбой модели/инструмента; диалог и последняя согласованная версия сохранены.

**Переходы** должны проверять условия: нельзя `BOOKED_DEMO` без согласия, нельзя использовать предложение предыдущей версии TripState без перепроверки, нельзя автоматически устранять жёсткое ограничение пользователя ради поиска вариантов.

## 4. Схема TripState (доменная модель)

```json
{
  "session_id": "uuid",
  "version": 7,
  "status": "WAITING_USER",
  "intent": "MODIFY_TRIP",
  "origin": {"city_id": "geonames:...", "text": "Казань", "source": "explicit"},
  "destination_preference": {"kind": "sea", "country_ids": [], "city_ids": []},
  "date_window": {"from": "2026-11-01", "to": "2026-11-30", "nights": 7, "assumed": false},
  "travelers": {"adults": 2, "children_ages": [4, 11], "pets": []},
  "groups": [{"group_id": "family-1", "adults": 2, "children_ages": [4, 11]}],
  "legs": [],
  "stays": [],
  "budget": {"amount": 140000, "currency": "RUB", "scope": "whole_trip", "is_hard": true},
  "constraints": [
    {"key": "breakfast", "op": "eq", "value": true, "hard": true, "source_message_id": "uuid"}
  ],
  "preferences": [{"key": "near_sea", "weight": 0.9, "source_message_id": "uuid"}],
  "selected_offer_ids": [],
  "cart_quote_id": null,
  "pending_question": {"field": "origin", "text": "Из какого города планируете выезд?"},
  "assumptions": [],
  "warnings": []
}
```

### Нужна расширяемость под multi-city / multi-room

- `legs[]`: пункт отправления, прибытия, даты/время, timezone, mode, выбранное транспортное предложение.
- `stays[]`: город, check-in/check-out, обязательные условия, привязка к номерному размещению.
- `room_assignments[]`: room_id, перечень traveler_id, число взрослых и реальные возраста детей; у одного путешественника не может быть двух несовместимых размещений на одну ночь.
- `travelers[]`: уникальные ID участников; предпочтения, pets не путать с обычными путешественниками.
- `budget`: общий/за человека/за ночь, валюта, что входит в сумму; цену держать в денежных минимальных единицах, не `float`.
- Каждое поле снабжать provenance: `explicit`, `inferred`, `assumed`, `verified`, плюс ссылка на сообщение/инструмент. Предположения не становятся фактами без подтверждения.

**Правило пересборки:** новый текст превращается в операции `set`, `clear`, `add`, `remove`, `undo` над whitelist полей, не в замену всего TripState. При изменении дат инвалидировать затронутые предложения и quote, при смене ребёнка проверить размещение и транспорт, при изменении гостиницы не забывать остальные города/группы.

## 5. Перечень таблиц PostgreSQL

| Таблица | Содержание | Главные ограничения |
|---|---|---|
| `sessions` | session_id, status, version, state_jsonb, pending_question, created_at/updated_at | `session_id PK`, version >= 0 |
| `messages` | sender, text, session_id, client_message_id, timestamp, sequence | UNIQUE(session_id, client_message_id) |
| `agent_runs` | run_id, session_id, input_message_id, stage, status, model, timings | trace_id, status |
| `agent_events` | event_id, run_id, event_type, sanitized_payload_jsonb, status, timestamp | index on (session_id, created_at) |
| `llm_calls` | run_id, provider, request summary/schema version, response JSON, tokens, latency, error | никогда не логировать secrets |
| `tool_calls` | run_id, tool, args JSON, result JSON, source_type, started_at/ended_at, status | UNIQUE run/tool invocation ID |
| `state_revisions` | session_id, revision, patch, snapshot JSON, author, timestamp | UNIQUE(session_id, revision) |
| `offers` | offer_id, provider, destination_id, validity, price, currency, policies, payload JSONB | provider + external_id / demo id |
| `quotes` | quote_id, session_id, state_revision, breakdown JSONB, status, expires_at | stale quote blocked |
| `demo_orders` | order_id, session_id, quote_id, idempotency_key, created_at, status | UNIQUE(session_id, idempotency_key) |
| `geo_cities` | city_id, country, coordinates, timezone, aliases | geo index if useful |

Не надо строить настоящую event-sourcing платформу за четыре дня: `sessions.state_jsonb` — текущий снапшот для быстрого чтения; `state_revisions` и `agent_events` — аудит и возможность отката. `jsonb` хорошо подходит для гибкого состояния, а стабильные идентификаторы и уникальности храните обычными колонками.

Примеры событий: `USER_MESSAGE_RECEIVED`, `LLM_REQUEST_STARTED`, `LLM_RESPONSE_RECEIVED`, `PATCH_PROPOSED`, `PATCH_VALIDATED`, `STATE_COMMITTED`, `CLARIFICATION_REQUESTED`, `WAITING_FOR_USER`, `TOOL_CALL_STARTED`, `TOOL_CALL_COMPLETED`, `TOOL_CALL_FAILED`, `OFFERS_FILTERED`, `QUOTE_INVALIDATED`, `QUOTE_CREATED`, `BOOKING_CONFIRMED`, `RECOVERABLE_ERROR`.

Для каждого события храните `trace_id`, `run_id`, `session_id`, `state_revision`, `event_type`, `latency_ms`, `error_code` (при наличии), `source_type`, `schema_version` и очищенный от секретов payload. Не хранить банковские карты, токены, персональные документы, бесконечные сырые промпты; задайте срок хранения логов. Для диагноза достаточно структурированных решений и результатов инструментов, не требуется доступ к скрытым внутренним рассуждениям модели.

## 6. LLM-слой: не чат ради чата

Каждый пользовательский ход разбирается в строго типизированный `UserIntent`:

- `DISCOVER`, `SEARCH`, `COMPARE`, `SELECT`, `MODIFY`, `ADD_CONSTRAINT`, `REMOVE_CONSTRAINT`, `UNDO`, `ASK_ABOUT`, `REQUEST_CHECKOUT`, `CONFIRM_BOOKING`, `CANCEL`, `WAIT_FOR_MORE`, `OFF_TOPIC`, `UNSUPPORTED`.
- Возвращать `patch_ops[]`, `referents[]` (например, offer #2), `hard_constraints[]`, `soft_preferences[]`, `unknowns[]`, `conflicts[]` и короткую нейтральную интерпретацию.
- Никакого прямого SQL, произвольных Python вызовов, генерации `offer_id` или «бронирования» по одному текстовому утверждению LLM.
- Нельзя выводить `not_mentioned = false`. Отсутствие сведений — `unknown`, а не отрицание.
- Последняя явная коррекция пользователя заменяет предыдущее значение того же поля; не затрагивает остальные поля. Для сомнительной анафоры «первый вариант» требуется сверка истории предложений.
- Если схема невалидна — один контролируемый повтор; затем статус `RECOVERABLE_ERROR`, вся история сохранена.
- Контекст: системные правила + текущий структурированный TripState + pending question + ограниченное окно последних сообщений + релевантные записи о выбранных предложениях. Не отправлять всю БД/все сообщения каждый раз.
- Ограничить число вызовов LLM/инструментов, токены и время. Все ответы LLM валидировать по Pydantic.

## 7. Движок уточняющих вопросов

**Политика:** сначала выполнить безопасную доступную часть работы; уточнить только то, без чего текущая стадия невозможна. Один содержательный вопрос за ход, иногда одна компактная связанная пара.

Примеры:

1. «Хочу к морю, не знаю куда» → предложить классы направлений/общие идеи, затем спросить город выезда, если нужен поиск транспорта.
2. «Москва → Казань, 12 ноября» без числа людей → спросить состав путешественников перед подбором тарифов.
3. «С нами ребёнок» → спросить возраст перед оценкой совместимости номера и цены.
4. «Бюджет 100 тысяч» при группе → если неясно, спросить общий или на человека.
5. «Только прямой рейс, но с пересадкой» → выявить конфликт и спросить приоритет.
6. «Подожди, я напишу дополнения» → статус `WAITING_USER`, `pending_question=null`, никаких дальнейших вызовов.
7. «Новый вариант на 2 дня позже» → изменить только даты, сохранить остальные ограничения, повторно проверить предложения.
8. «Давай бронировать» → показать актуальную корзину и сумму; создать заказ только после отдельного подтверждения именно этой версии.

Хранить `pending_question` в БД со списком необходимых полей и причиной блокировки. Новое сообщение может одновременно ответить на прошлый вопрос и добавить несколько условий — **не ограничиваться распознаванием одного поля**. После восстановления сравнить состояние с новой версией и продолжить с пропущенного этапа.

## 8. Универсальный синтетический Travel Inventory

**Не делайте 30 жёстко заданных сценариев и ответы под них.** Сделайте генератор согласованных демо-предложений поверх офлайн-справочника городов. Например, GeoNames `cities15000` + русские/английские альтернативные названия + timezone; проверить лицензию и указать атрибуцию.

- `GeoResolver.resolve("Питер") -> Санкт-Петербург`, "Лондон" может быть неоднозначен → уточнение.
- `MockStayProvider.search(city_id, dates, guests, requirements)`: строит детерминированный каталог виртуальных объектов для известных городов; применяет реальные правила вместимости, тарифов и сборов; выдаёт несколько тарифов, поставщиков и ограничений.
- `MockTransportProvider.search(legs, travelers, dates)`: создаёт полностью **демо-маршруты** и детерминированные цены/время. Не показывать вымышленные авиакомпании/рейсы как настоящие.
- `MockTransferProvider`, `MockPriceCheck`, `MockBookingProvider` — такие же интерфейсы, как будущие LiveAdapter.
- Генерацию делать воспроизводимой: стабильный seed из canonical city ID / конкретного объекта / сезона / тарифа. Цены должны быть последовательны между изменениями, а не случайны на каждом сообщении. Сезон, гости, ночи, питание, отмена, номера, транспорт и сборы влияют на итог.
- Дайте каждый вариант `offer_id`, `source_type="demo_generated"`, `price_kind="synthetic"`, `valid_until`, `constraints`, `breakdown`, `assumptions`.
- При невозможности подтвердить детали для неизвестного города: корректно спросить уточнение или показать структурный план без цены, а не выдумывать реально существующий отель.
- Наличие города в справочнике ≠ гарантия существования транспортного маршрута. Синтетические предложения маркировать и в API, и в UI.

Для поставщика определите единый интерфейс:

```python
class InventoryProvider(Protocol):
    async def search_stays(self, request: StaySearch) -> list[StayOffer]: ...
    async def search_transport(self, request: TransportSearch) -> list[TransportOffer]: ...
    async def search_transfers(self, request: TransferSearch) -> list[TransferOffer]: ...
    async def reprice(self, quote: TripQuote) -> TripQuote: ...
```

`MockProvider` реализовать сейчас, `LiveProvider` подключить позже. У `LiveProvider` могут быть неполные покрытия и ограничения. Если он отказал, явно покажите, какие части остались демо, не смешивайте живые и вымышленные цены в одну «подтверждённую реальную стоимость».

## 9. Validator/Ranker — код, а не вера в LLM

Жёсткие валидаторы:
- правильная календарная дата и local timezone;
- check-in/check-out, количество ночей, позднее прибытие;
- одна согласованная хронология legs/stays/transfers;
- совместимость возраста детей, числа гостей, правил размещения, животных и условий отмены;
- корректное распределение участников по нескольким комнатам;
- итоговая полная стоимость в единых денежных единицах, валюта, scope бюджета;
- каждый выбранный компонент имеет существующий `offer_id` и совпадает с актуальной версией поездки;
- нельзя бронировать без явного подтверждения и повторной проверки цены;
- при изменении состава/дат старый quote недействителен.

После фильтрации по hard constraints — ранжирование по мягким желаниям (время пути, местоположение, стоимость, удобство, отмена, питание). Список факторов и причины исключения возвращать как объяснимый JSON. Текст ответа LLM строить только по этим фактам.

## 10. Обработка ошибок

- LLM timeout/429/invalid output: сохранить вход и run, возможен один backoff retry, вернуть `RECOVERABLE_ERROR` + полезное сообщение; не терять состояние.
- HTTP провайдера 500/429: timeout/circuit breaker, переход на демо (с явной маркировкой) или честное отсутствие подтверждённых данных.
- Crash посреди инструмента: статус `IN_FLIGHT` остаётся в журнале; при следующем запросе определить, повторяем ли чтение или проверяем результат. Запрещено повторно создавать заказ без идемпотентности.
- Race: два одновременных сообщения одной сессии обрабатываются с version/CAS; устаревшая поисковая выдача не применится поверх новой.
- Duplicate input: `UNIQUE(session_id, client_message_id)` и повтор прежнего ответа/run.
- Duplicate booking: `UNIQUE(session_id,idempotency_key)`.
- Prompt injection из описания гостиницы: воспринимать только как контент; Tool Gateway валидирует вызовы и не даёт внешнему тексту менять правила агента.

## 11. Контракт API для Frontend

```
POST /api/v1/sessions -> {session_id, trip_version}
POST /api/v1/sessions/{id}/messages
  BODY {client_message_id, text, expected_version?}
GET  /api/v1/sessions/{id} -> полный снимок состояния
GET  /api/v1/sessions/{id}/messages -> история
GET  /api/v1/sessions/{id}/events?after=<cursor> -> события/статусы
POST /api/v1/sessions/{id}/offers/{offer_id}/select
POST /api/v1/sessions/{id}/checkout/preview
POST /api/v1/sessions/{id}/checkout/confirm
  BODY {quote_id, quote_version, idempotency_key, confirmed: true}
GET  /health
```

Единый формат ответа frontend:

```json
{
  "session_id": "uuid",
  "run_id": "uuid",
  "status": "WAITING_USER",
  "assistant_message": "Сколько лет ребёнку?",
  "pending_question": {"field": "children_ages", "required": true},
  "trip_version": 8,
  "trip": {},
  "offers": [],
  "cart": null,
  "changes": [],
  "warnings": [],
  "data_mode": "demo"
}
```

Первые 1–2 дня достаточно обычного JSON + polling. SSE для прогресса («проверяю даты → ищу варианты → собираю поездку») добавить, только если backend стабильный. Не заставляйте frontend разбирать свободный текст для восстановления состояния: все предложения, карта, корзина и разница должны приходить структурированно.

## 12. Матрица классов входных сообщений

Покрыть тестами **типы намерений**, а не только формулировки:

| Тип | Пример | Ожидаемое поведение |
|---|---|---|
| Приветствие | «Привет» | короткое начало, без поиска |
| Идея | «Хочу куда-нибудь в тепло» | направления + ключевое уточнение |
| Точный запрос | «Казань — Сочи, 5–10 ноября, двое» | поиск без лишних вопросов |
| Нет города выезда | «В Стамбул с ребёнком» | уточнить origin, предложить подготовку |
| Неуказанный возраст | «Нас трое, один ребёнок» | уточнить возраст для тарифа |
| Относительная дата | «На следующие выходные» | конвертировать в дату по timezone, показать интерпретацию |
| Коррекция | «Нет, вылетаем 12-го» | заменить только дату |
| Удаление | «Собака уже не едет» | убрать ограничение/питомца |
| Противоречие | «7 ночей, вернуться через 3 дня» | объяснить, спросить приоритет |
| Изменение бюджета | «Снизь на 20 тысяч» | пересчитать, проверить соблюдение hard constraints |
| Изменение направления | «А лучше в Казань» | сохранить прочие параметры |
| Предыдущее предложение | «Верни первый вариант» | resolve offer from history |
| Отмена изменений | «Откати последнее» | восстановить предыдущую ревизию |
| Сравнение | «А что дешевле целиком?» | сравнить все обязательные компоненты |
| Группа/номера | «9 человек, три номера» | group assignment, проверить вместимость |
| Multi-city | «Стамбул 3 дня, Анталья 4 ночи» | 2 stays + legs, связать даты |
| Уточнение к уже заданному вопросу | «Из Казани, 2 взрослых, бюджет 120» | распознать сразу все три поля |
| Просьба подождать | «Подожди, допишу ещё» | WAITING_USER без вызовов |
| Неизвестное направление | «В город X» | clarify/unsupported, не придумать отель |
| Некорректная дата | «31 ноября» | невалидная дата, попросить исправить |
| Вопрос про правила | «С питомцем можно?» | запрос на проверку свойств тарифа |
| Погода/визы | «Точно будет 30 градусов и без визы?» | не давать необоснованных гарантий |
| Запрос пояснения | «Почему этот?» | объяснить на основе score/breakdown |
| Оформление | «Бронируй» | preview и подтверждение |
| Повтор оформления | двойной клик | один demo_order |
| Отказ API | 429/timeout | сохранить state, сообщить и fallback |
| Безопасность | инструкция в описании отеля | игнорировать внедрённую команду |
| Нерелевантный запрос | «Расскажи анекдот» | короткий ответ/возврат к задаче |
| Другая речь | «Need pet-friendly stay» | попытаться извлечь те же поля |
| Длинное многосоставное сообщение | 20 условий вместе | извлечь все, не спрашивать уже заданное |

Покрыть также короткие ответы «да», «нет», «тот второй», «оба», «не это», «без него» и восстановление после ошибки при следующем сообщении.

## 13. Test Harness + метрики в PostgreSQL

Каждый тест: `fixture_state`, `turns[]`, `expected_state_delta`, `expected_status`, `must_ask_fields`, `forbidden_mutations`, `hard_invariants`, `allowed_outcomes`.

Не проверять точное совпадение LLM-фраз; сравнивать **состояние, действия, вопросы и соблюдение ограничений**.

Минимальные автоматические тесты:
- 20 одноходовых на извлечение и уточнение;
- 15 многосообщенческих (повороты/undo/context);
- 10 multi-city/group/multi-room;
- 10 противоречий и impossible searches;
- 10 ошибок LLM/API/конкуренции/двойного бронирования;
- несколько новых случайных формулировок без подгонки промптов.

Внутренние критерии готовности (не официальные метрики Островка): 0 нарушений hard constraints на регрессии; 0 дублей заказов; 0 потерь подтверждённых ограничений в 15 диалогах; 100% восстановление сохранённого state после искусственного рестарта в заданных тестах; корректные режимы WAITING_USER и RECOVERABLE_ERROR.

Отчёт по логам: `completion_rate`, `clarification_count`, `time_to_first_option`, `constraint_violation_count`, `unhandled_intent_rate`, `llm_schema_error_rate`, `tool_error_rate`, `latency_ms`, `p95`, `run_recovery_success`, `cost_usd`. Нужны для отладки и финальной защиты; не начинать с красивого аналитического дашборда.

## 14. График работ

### 25 октября — foundation и контракт
- PostgreSQL в Docker Compose; FastAPI `/health`; Alembic; все базовые таблицы.
- Session/messages/state revisions/events.
- `TripState` в Pydantic, API контракт и тестовый JSON для участника frontend.
- Mock provider выдаёт воспроизводимые предложения для нескольких городов, `source_type=demo`.
- Конец дня: сквозной stub flow + сохранение/восстановление сессии.

### 26 октября — интеллектуальное ядро
- LLM NLU в строгом JSON; patch engine; resolve references.
- Question Engine: спросить и **ждать пользователя**; несколько независимых полей в одном сообщении.
- Сложная группа, multi-room и multi-city на уровне модели данных и в одном-двух end-to-end тестах.
- Constraint Validator, ranking, пересборка + объяснение изменений.
- Конец дня: многосообщенческий тест после перезапуска контейнера.

### 27 октября — интеграция и надёжность
- Доработка полного mock checkout, reprice, idempotency, error recovery.
- Запуск test harness, исправление hard constraints и неточных уточнений.
- Подключение **одного** живого API через `LiveProvider` при наличии ключа, квот и времени; иначе оставить demo, а не блокировать сдачу.
- Frontend получает стабильный JSON, версии trip и quote.

### 28 октября — freeze и приёмка
- Дополнительный LiveProvider только если уже работает и не угрожает MVP.
- Crash/restart, 429/timeout, concurrent messages, duplicate booking, invalid city/date, unknown query.
- Проверка `docker compose up --build` на чистом окружении, UI на localhost:8080, сидирование без ручных команд.
- README, демонстрационное видео, понятные подписи синтетических данных.
- К вечеру — freeze функциональности.

### 29 октября до 10:00 (UTC+3)
- Сдача по правилам: main, тег `submission`, публичное видео, Docker build.

## 15. Definition of Done (жёсткие критерии)

1. Любое сообщение сохраняется в PostgreSQL до вызова модели.
2. На любое пользовательское сообщение возвращается либо безопасный результат, либо вопрос, либо честная ошибка; не бесконечная загрузка.
3. `WAITING_USER` переживает перезапуск приложения.
4. Новое сообщение может сразу исправлять/дополнять несколько полей.
5. Агент не теряет ранее подтверждённые ограничения.
6. Незнакомая формулировка не сводится к списку заготовленных ответов.
7. Предложения/цены только из результата Mock/LiveProvider и с видимым типом источника.
8. Нет фиктивных реальных цен/виз/рейсов или обещания бронирования.
9. Все выбранные предложения проходят deterministic checks.
10. Старый quote нельзя подтвердить после изменения состояния.
11. Повтор `confirm` не создаёт дубликат заказа.
12. Нарушение LLM/API не удаляет историю и не повреждает TripState.
13. Frontend по API умеет показать вопрос, варианты, состав поездки, изменения, корзину и подтверждение.
14. Все зависимости стартуют с `docker compose up --build` и портом 8080.
15. На защите можно показать полный сценарий с *незнакомыми* запросами, а не только заученное демо.

## 16. Что точно не делать до сдачи

Не строить автономный multi-agent swarm, не обучать новую LLM, не делать vector database и сложную долгосрочную память раньше TripState, не принимать оплату, не обещать реальные поставки без API, не превращать PostgreSQL в транспорт шины внешних HTTP вызовов, не полагаться только на regex и заранее известные города/фразы, не переключать продемонстрированный mock-сценарий на нестабильное live API без fallback.

---

Источники:
- Задание: https://docs.ostrovok.tech/s/0ee4d8b2-ae50-4dda-a2b5-112fcdb5398d
- Технические правила: https://docs.ostrovok.tech/s/b9e6ec5e-5ad6-4091-9c85-a48eee9b21cc
- OpenAI Structured Outputs: https://developers.openai.com/api/docs/guides/structured-outputs
- PostgreSQL JSONB: https://www.postgresql.org/docs/current/datatype-json.html
- PostgreSQL locks: https://www.postgresql.org/docs/current/explicit-locking.html
- GeoNames dump/licensing: https://download.geonames.org/export/dump/readme.txt
- LangGraph persistence/interrupts (альтернатива): https://github.com/langchain-ai/docs/blob/main/src/oss/langgraph/persistence.mdx
