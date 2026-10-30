# Аудит приёмки перед интеграцией frontend

Дата: 27 октября 2026 года, Europe/Moscow. Опорная ревизия: `9ddfcda6dde46604e01c132f02b9a54a12cef628`.

Это read-only сопоставление плана, кода и уже сохранённых доказательств. Новых LLM, supplier HTTP, PostgreSQL-сценариев, restart или checkout здесь не выполнялось. Основной план и исходные корпуса не изменены. Полный файл `docs/research/harness-plan-review.md` прочитан; его исторические разделы не считаются новыми результатами текущей ревизии.

Во время аудита `providers.py` и `store.py` уже начали изменяться другими участниками работы. Их новые изменения этим документом не квалифицированы. Остальные проверенные ключевые файлы `harness.py`, `answering.py`, `domain.py`, `api.py`, `planning.py`, `inventory.py` совпадали с указанным HEAD после нормализации CRLF.

## Главный вывод

Закрыты значительные контролируемые части ядра, но весь план перед frontend пока не закрыт. Последний полный набор — 3489 PASS / 0 skip, а не 3489 проверенных языковых сценариев. [Checkpoint](transport-diagnostics-checkpoint-20261026.json) сам сохраняет `actual_closure=PENDING_FRESH_NATIVE_SEARCH_PREVIEW_SIMULATED_CONFIRM` и `full80_actual=NOT_COMPLETED`.

Продуктовые требования определяет [PRODUCT_DECISIONS.md](../PRODUCT_DECISIONS.md): семь направлений, сложная группа и несколько городов, сохранение условий, пересборка и тестовое оформление. Все 106 внешних stress-cases не превращаются автоматически в обязательство реализовать глобальные визы, реальные оплаты и любые виды транспорта. Для неподдержанного поведения нужны понятное ограничение и сохранение состояния; его нельзя объявлять реализованной функцией.

## Приоритеты до frontend

| Приоритет / постоянный ID | Подтверждённый остаток | Условие закрытия |
| --- | --- | --- |
| P0 — HARNESS-001 / TOUR-PINNED-HOTEL-TRANSFER-001 | Новый native 9dd поиск собирает полный MIXED-план и пять фото, но actual preview получает 503 `OFFER_UNAVAILABLE`. До заказа не дошёл. Старый успешный двухгородской 5f14 flow не включает actual Duffel-перелёты в рублёвую корзину. | На новом зафиксированном пакете пройти поиск → разрешённый source refresh/prebook → новый quote → явное simulated confirm → тот же order при идемпотентном повторе. При исчезнувшем тарифе сохранить честный отказ; другой тариф не подставлять без выбора/согласия. Затем повторить сложную группу: два проживания / четыре номера / три плеча с сохранением native EUR и проверяемым FX. |
| P0 — HARNESS-004 / H004-RATE-CAUSE-001 | Первый full-context Groq HTTP429 имеет неизвестную точную причину. Повтор на 9dd остановлен `MODEL_COOLDOWN` до HTTP. Успешный маленький JSON-запрос не подтверждает полный harness. | Обычный разрешённый вызов после действующего fence с wire metrics, безопасными rate facts и валидными переходами состояния. Затем устойчивые длинные диалоги на выбранном сильном профиле. Не сбрасывать cooldown и не заменять этот результат local Qwen. |
| P1 — ROUTER8-ASK-REPLY-001 | Просьба ограничить отель 5000 за ночь не меняет общий бюджет, но предметный ответ модели теряется: пользователь получает лишь «Поездка и показанные варианты не изменены». | Server-owned ответ сообщает, поддерживается ли отдельный nightly cap, не выдаёт его за whole-trip budget, повторяет действительно недостающий слот при просьбе показать план; state, pending и quote сохранены. Затем related actual turn. |
| P1 — HARNESS-004-COUNTRY-TYPO-001 / COMPOUND-001 / CONTEXT-MERGE-001 | Контекстная опечатка и несовместимые город/страна требуют связного повторного диалога; controlled helpers не квалифицируют все формулировки. | Сербия → «Белгород» с видимым контекстным пояснением; explicit Россия не переписывается. Стамбул/Сербия → вопрос о конфликте, «нет» удаляет отвергнутую proposal, «да» относится только к актуальному вопросу. Origin, люди, бюджет и правки сохраняются на всех ходах; нет внутренних `destination_city`/`check_in` в ответе. |
| P1 — HARNESS-008 | Частичная привязка ожиданий существенно отстаёт от объёма корпусов. Один запуск 80 разговоров сам по себе не даст 80 приёмочных результатов. | Для каждого действительно исполненного случая проверить его конкретные ожидаемые изменения, сохранённые поля, смысл ответа и побочные действия. Unbound/unsupported/not comparable указывать отдельно; исходные входы/FAIL сохранять. |

Отдельный наблюдаемый пробел спецификации: `requested_duration_not_lost` для «7 ночей», «4 ночи», «4 дня» в [PILOT_V2](../../fixtures/evaluation/oracle_bindings/PILOT_V2.md) остаётся `UNBOUND / NOT_REPRESENTED`. В `TripState` и operation schema нет duration-only поля, в `date_slots.py` нет этого пути. Сохранённая полная история содержит исходный текст, но это не адресное каноническое требование к остановке и не гарантия его использования после выхода из recent-history window. Это **не новый доказанный actual FAIL**. Проверка нужна отдельно: сохранить ночи до уточнения заезда; дни и ночи не приравнивать молча.

## Состояние восьми основных блоков

| Блок | Что подтверждено | Что не следует объявлять закрытым |
| --- | --- | --- |
| HARNESS-001 | Actual LiteAPI multiroom/age/fee evidence в старом typed mixed flow; на 9dd actual hotel + Duffel EUR + CBR + independent DEMO transfer + media. Нативные суммы сохраняются. | Новый actual refresh/checkout, supplier comparison и LIVE_BOOKABLE. `supplier_id/name` лишь переносятся в `providers.py`; `supplier_verified=false`. Две записи SANDBOX/indicative не доказывают сопоставимых независимых поставщиков. |
| HARNESS-002 | Канонические ID, структурные/domain guards, room/group ownership, версия source policy. | Численные пределы — engineering bounds. Provider facts и полнота естественно-языкового извлечения — другие уровни. Нет основания переделывать уже проверенную схему ради числа тестов. |
| HARNESS-003 | Typed undo создаёт новую ревизию, не возрождает quote/consent/order; история помечена historical-only. | Неоднозначные «отмени»/«верни первый» и selective references требуют actual NLU. Публичного setter `Leg.mode` в inspected schema нет: STATE09 готовит train через Store, что прямо указано в `test_corpus_harness.py:308`. Это не доказанная возможность пользователя переключить транспорт в чате. |
| HARNESS-004 | Все исходные 80 просмотрены ранее; множество controlled/cached fixes и безопасные canonical answers. Четыре новых fixed-transcript диалога исполнены, пятый начат. | Full80, full106, произвольная полнота извлечения, актуальная приёмка всех заявленных исправлений. Устаревший Gemma FAIL не закрывается PASS другой модели; неподходящий профиль можно держать неквалифицированным/выключенным. |
| HARNESS-005 | Persistent message identity, payload hash, последовательность, CAS, one active run, идемпотентные quote/order и cooldown в перечисленном scope. | Exactly-once внешнего HTTP не доказан. Строка плана «remote40/day+2/run» устарела для выбранных direct-профилей; внутренний cap не является остатком vendor-квоты. |
| HARNESS-006 | Backend contract, OpenAPI17paths, owner-bound медиа вне economic hash, небольшой debug example. Native GET media200 и неизменность тарифа подтверждены. | Финальный frontend сознательно делает другой участник. Его интеграция/UX пока не квалифицированы. Фото конкретного номера, destination/Serp fallback, права перепубликации не доказаны самим image URL. |
| HARNESS-007 | Контролируемые fault-boundaries, один настоящий PostgreSQL crash/restart с WAL recovery, сохранение state/idempotency в проверенном scope. | Произвольная потеря диска/все распределённые исходы. Это ограничение доказательства, не обязательство сегодня искусственно разрушать рабочую среду. |
| HARNESS-008 | Все 106 IDs имеют disposition; 71 case имеет частичные предметные assertions. | 35 case не исполнялись в отмеченных этапах; full-language исходного106 — 0. D250 импорт не является запуском корпуса. |

## Независимая сверка приёмочных данных

Проверены чтением и чистыми Python-проекциями, без сетевых вызовов:

- Исходный106: SHA256 `17b24b8e38fc81a1317213075aa769c6c7c3722a395f648c73fc5e9f4fa2ed2e`; 106 records.
- Присланные oracle106: SHA256 `4bf487cf049438fc02a9ac1996da7dac3959b9d914e40a01bdaf63566489e20e`; 106 records. ID-order, messages и test_setup совпадают с исходником, mismatches 0/0/0.
- `corpus-map.json` revision3: 34 `PARTIAL_DETERMINISTIC_NLU_PENDING` + 23 `UNSUPPORTED_GUARD_CHECKED` + 14 `PARTIAL_HARNESS_VERIFIED_NLU_PENDING` = 71 partial. Остальные35: 19 `UNSUPPORTED_SCOPE`, 10 `NLU_PENDING`, 4 `BLOCKED_ORACLE`, 1 `BLOCKED_PROVIDER_DATA`, 1 `BLOCKED_UI_FIXTURE`. Исходные приоритеты этих35 — 19 P0 / 16 P1; они не отменяют согласованный продуктовый периметр.
- `oracle_bindings/bindings.jsonl` revision1: 106 `UNBOUND`, 185 checks `UNBOUND`, непустых `implementation_ref` — 0. Это спецификация, не результаты. Отдельный pilot-v2: 34 BOUND typed checks + 8 UNBOUND обязательств; не четыре полностью принятых cases.
- Исходные80: SHA256 `81a7e59c65d41da8dfc6101fc0429c455c259561e44f6a9a79f0dc99323058ca`, 80 cases / 697 turns. `after80-oracles-v1.json`: 65 case-specific checks, 53 bound turns, 6 case-specific cases, fully_bound_cases=0. Остальные644 turns не получили собственных predicates в этом manifest; generic invariants всё равно полезны, но проверяют другой слой.
- D250: SHA256 `f5625f0278ea7bbb20afea555cf49d2e4ba221bb171405494dc0a4ea8157796f`, 50 cases × 6 = 300 turns. Авторские статусы249 BOUND / 48 UNBOUND / 3 BLOCKED не являются нашими PASS. Девять fault recipes ещё не означают исполненные инъекции. Source DTO/DSL не совпадает с текущими TripState/Operation; требуется versioned binding, а не прямое исполнение.

## Проверенные actual-артефакты и границы

- [Старый actual group flow](actual-group-flow-20261026.json): успешный mixed test только в описанном там scope; actual flight coverage0, поскольку EUR тогда отклонён. Это не новый EUR/FX end-to-end.
- [Первый native media flow](travel-media-native-20261026-initial.json): настоящий static content и источниковые предложения, отсутствующий transfer сохранён как failure, а не переписан PASS.
- Прочитан private `/home/aegml/evaluation-runs-after80/travel-media-9ddfcda-57986683/report.json`: search completed / ready=true / MIXED, media200, priced_snapshot_unchanged=true, preview503 `OFFER_UNAVAILABLE`, orders0, model_calls0. Это успех поиска/фото и честный отказ оформления, не успех checkout.
- Прочитан private `/home/aegml/evaluation-runs-after80/groq-canary-9ddfcda-d7755c5e/report.json`: `MODEL_COOLDOWN`, llm_calls0. Нового provider429 в этой попытке нет.
- [Portable Docker smoke](frontend-handoff-smoke-20261026.json) относится к `54cdd31`, новой БД и отсутствующим ключам, без внешнего NLU/поиска. Новый feature-пакет требует своей проверки чистого запуска; наличие SQL на ПК владельца не является предусловием другого компьютера.

## Как завершать проверяемыми этапами

До передачи тестировщику нужен один датированный release manifest: commit/package, runtime profile, параметры бюджета вызовов, actual tools/source types, сведения о выполненных и невыполненных assertions, пути к полным разговорам и точные известные ограничения.

Содержательные диалоговые цепочки должны охватывать: конфликт географии с «да/нет»; уточнения состава и возраста; сложную группу по комнатам; одновременные правки нескольких остановок/бюджета/возвращения; паузу, рестарт/дополнение и явный resume; выбор/preview/согласие и stale quote; неоднозначную отмену/историческую ссылку. Ответы тестировщика должны следовать фактически заданным вопросам, иначе старый fixed transcript может стать `NOT_COMPARABLE`, что нельзя использовать для сокрытия независимого повреждения состояния.

Main plan стоит привести к одной текущей таблице после относящихся результатов: сейчас в нём остаются 2477/3434/713 как разные «последние» счётчики, старые frozen8084/policy40/day и одновременно OPEN/CLOSED date-target blocks. Историю сохранить, но обозначить датированной историей и связать aliases. Этот документационный долг не требует повторять уже закрытые controlled stages.

Код/исходные FAIL/корпуса в данном аудите не исправлялись и не запускались. Успех соответствующего нового actual этапа должен быть добавлен отдельным доказательством, а не выведен из этого текста.
