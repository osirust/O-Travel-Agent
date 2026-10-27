# Анализ плана Agent Harness

Текущая сверка: 27 октября 2026. Code26c76431f5980c35647f4acf2eb7f23a62f70adc опубликован в обоих приватных репозиториях и установлен в Native, package0407b2d51c5e93213d5fb566e7cad3221ab7120bb1beee7f8ad76d2abbef890e. Полный controlled/domain/API/PG/MockTransport набор:4606PASS/0fail/0skip. Два actual перехода с выбранным отелем относятся кb934 и прошли21проверку после typedsetup. Три fixed80 попытки остановились на первом provider429;0полныхдиалогов. V3contentoracles охватывают все80 частично. Новый строгий транспорт Groq разрабатывается отдельно, ещё не квалифицирован внешним диалогом. Frontend делает сокомандник; исходныеFAIL сохраняются.

## HARNESS004 — независимая серия D100 от владельца

25.10.2026, 21:15–21:24 МСК тестировщик выполнил 100 сценариев / 130 завершённых реплик на работающем 8083 через HTTP, ollama/qwen3.5:4b. 55 raw PASS / 45 FAIL; 108 подтверждённых вызовов модели; 0 инструментов и заказов. В 8 raw PASS найдены дополнительные ошибки; исходные scores не переписывались. Основной реестр с фактами, доказательствами, постоянными ID, действиями и условиями закрытия: [D100 FINDINGS](../verification/dialogue-break-100-20261025/FINDINGS.md) и четыре связанные review. Findings OPEN; HARNESS004 и исходные 106 этим не закрыты.

Приоритет ремонта по фактам серии: потеря явных фактов и ролей городов; ASK/гипотеза ≠ CHANGE; даты, суммы и знак; запрет незапрошенных preferences/constraints; завершение подтверждений; доставка содержательного ответа пользователю. Владелец просил тестировать, поэтому тестировщик приложение не исправлял и не перезапускал. Runtime SHA неизвестен; серия не квалифицирует параллельные неразвёрнутые правки. Повтор должен сохранять frozen inputs/oracles и исходные FAIL/UNKNOWN, отдельно дополняя отсутствующие инварианты новой версией тестов.

Полные 100 протоколов сохранены; первая попытка 058 с неизвестным исходом сохранена отдельно. 57 завершённых случаев не повторялись, 058–100 продолжены по тем же входам. Оригинальная ошибка схемы на checkout и карточка UI не закрыты другим PASS. Следующий этап разработки и исходного плана 106 остаётся в PROJECT_STATE; самостоятельного запуска нового блока эта серия не разрешает.

## Вывод

Уточнение D100: post-read deployment receipt фиксирует запуск 8083 в 21:20:09 МСК между фазами. 55/45 — смешанная сводка, а не одна версия; фаза1 = 25 PASS / 32 FAIL, фаза2 = 30 PASS / 13 FAIL. [Граница runtime](../verification/dialogue-break-100-20261025/RUNTIME_BOUNDARY.json). Каждое finding относится к своей датированной фазе; состояние после развёртывания не квалифицировано всеми 100 случаями.

Направление плана подходит: один управляемый harness, состояние и аудит в PostgreSQL, отдельные адаптеры модели и инструментов, проверяемые изменения поездки, вопросы и долговременное ожидание пользователя. Точность маршрута и оформление проверяет код; модель понимает язык и предлагает действия.

Ядро уже реализовано и проверено на управляемых ответах, PostgreSQL и воспроизводимом demo inventory. Есть отдельные фактические диалоги через сильные API и обращения к туристическим источникам. Эти результаты не доказывают понимание всех новых формулировок: языковая приёмка, доступность источников и устойчивость ядра учитываются отдельно.

Подключение туристических API в последние два дня остаётся зависимым от доступов и контрактов источников. Готовность демоконтура не закрывает согласованные требования живого поиска и сравнения реальных поставщиков. Эти результаты нужно учитывать раздельно.

## Источники и восстановленный контекст

Полностью прочитаны [план из Markdown](sources/agent-harness-plan-original.md) — 355 строк, 38 746 байт — и [приложенное обсуждение архитектуры](sources/agent-harness-discussion-original.txt) — 426 строк, 32 264 байта. Оригиналы сохранены без изменения; контрольные суммы — в [manifest.json](sources/manifest.json). Их заголовки «ТЗ», команды и сроки рассматриваются как предложения автора, а не распоряжение начать работы.

Через приложение найдена указанная владельцем переписка. Доступны пользовательские сообщения: важны адаптация, необходимые вопросы, ожидание дополнений и работа с новыми формулировками. Полные ответы ассистента этим способом не получены; веб-запрос по ссылке также не дал содержимого. Поэтому разбор ответов опирается на приложенные файлы и [сохранённое исследование со 106 сценариями](agent-stress-analysis.md), а не на заявление о полном чтении удалённой переписки.

Актуальные продуктовые ограничения сохраняются в [PRODUCT_DECISIONS.md](../PRODUCT_DECISIONS.md): семь направлений; несколько городов, отелей и номеров; сложная группа; «Было → Стало»; приоритет live с явным demo fallback; формы и готовые сценарии при отказе LLM; один агрегатор с различимыми реальными поставщиками допустим. Технологический план не отменяет эти решения.

## Что в плане уже правильно

План прямо разделяет PostgreSQL и HTTP: БД хранит состояние, сообщения, ревизии и журнал, а внешние вызовы выполняет backend. Долгие LLM/API-вызовы и ожидание пользователя вынесены за пределы транзакции. Это следует сохранить, а не считать отсутствующей защитой. PostgreSQL удерживает блокировки до завершения транзакции; `async` сам по себе не освобождает её. [Блокировки PostgreSQL](https://www.postgresql.org/docs/17/explicit-locking.html)

Также полезны: типизированный результат модели, whitelist инструментов, отдельные проверки ограничений, инвалидирование старой корзины, ограниченные повторы, идемпотентный демозаказ и обычный JSON/polling для первой интеграции frontend. Собственная небольшая FSM допустима. Не нужно добавлять брокер, рой агентов или LangGraph только ради названия архитектуры.

Если всё же выбирать LangGraph, нужно единственное авторитетное состояние и долговременный checkpointer. При resume узел с `interrupt()` выполняется заново с начала; действия до паузы должны выдерживать повтор. Checkpoint не создаёт общей транзакции с произвольными таблицами заказов. [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)

## Основная матрица выполнения на 27 октября

Последний общий результат — [4606 PASS / 0 skip](../verification/liteapi-prebook2001-repair-20261027.json), а не сумма перекрывающихся targeted-наборов. В `b9343c0` вошли сохранение явно выбранного отеля и узкая политика необязательных вопросов модели. На этой версии [два actual перехода](../verification/actual-selected-hotels-wow-20261027.json) прошли21предметную проверку. Три попытки80-replay остановились на первом отказе провайдера; полныхдиалогов0.106 и неизвестные формулировки не объявлены закрытыми. Подробности — [AFTER80](../testing/AFTER80.md) и PROJECT_STATE.md.

| Постоянный ID | Актуальный статус | Внесённый код и доказательство | Что ещё требуется |
| --- | --- | --- | --- |
| HARNESS-001 | **ACTUAL_MIXED_GROUP_CHECKOUT_PASS / INDICATIVE_SELLERS_AVAILABLE / EXACT_EQUIVALENCE_OPEN** | Native `ab378d2`:2проживания/4номера/3перелёта/4человека, возраст6→7. LiteAPI SANDBOX prebook, Duffel SANDBOX EUR refresh, CBR→RUB,2DEMO-трансфера;9компонентов без пропусков. Preview/confirm/retry200→один BOOKED_DEMO,0supplier bookings/payments. [Group](../verification/actual-group-checkout-20261027.json). [Native Serp73e7437](../verification/native-serp-comparison-20261027.json) отдельно подтвердил12sellers/6ставок на1гостя и3980600RUB. | MIXED не становится полностью live. Exact supplier equivalence/LIVE_BOOKABLE не доказаны. Новый natural HYBRID ввод3208 сохранил все факты, но остановился до поиска; последующий явный typed search — отдельная source-integration проверка. Его preview вернул LiteAPI409; [причинаUNKNOWN](../verification/natural-hybrid-prebook409-diagnostic-20261027.json). Нужны относящиеся новые source-flow результаты, без автоматической замены тарифа или переименования sandbox. |
| HARNESS-002 | **CLOSED_CANONICAL_SCHEMA_SCOPE** | Стабильные traveler/group/stay/room/leg ID; source policy версионируется. `requested_nights` сохраняется до дат, None не меняет legacy JSON/quote; check-in+N даёт выезд, дни не равны ночам. Scoped `set_room_members` требует stay_id и сохраняет остальные номера; full rooms setter остаётся полной заменой. Эквивалентность свежего поиска после room patch проверена [mock-attribution](../verification/room-refresh-attribution-20261027.json). | Пределы численности — engineering bounds. Схема и mock-freshness не доказывают реальную доступность/вместимость/сборы; факты проверяются адаптерами и quote guards. |
| HARNESS-003 | **UNDO_CANCEL_CORE_AND_ACTUAL_SELECTIVE_RESTORE_TESTED** | CAS undo создаёт новую revision без старых quote/consent/order; cancel останавливает run. Public `set_leg_mode` меняет owned leg. Actual CF PF-TRANSPORT05:12/12, восстановление первого отеля с сохранением поезда и отказ old quote409. [Серия](../verification/prefrontend-cf-20261027.json). | Закрыт конкретный связный путь, не все ссылки «первый/тот/отмена». Selection/pin не возвращает старую цену или согласие. Неподдерживаемый международный поезд не превращается в найденный тариф. |
| HARNESS-004 | **RELATED_ACTUAL_SCOPES_PASS / NATURAL_HYBRID_AND_FULL80_ACCEPTANCE_PENDING** | R3OpenRouter6/8wholecases, затем GEO01/R4=15/15. Source93f91fd GROUP04 прошёл12старых state-oracles, но обнаружен отдельный flow-blocker; исходный PASS сохранён. В b9343c0 два actual перехода после явной подготовки прошли21проверку: WOW READY с выбранным первым отелем и ссылка на реально выбранный plan3. [Новый actual scope](../verification/actual-selected-hotels-wow-20261027.json). Core4606PASS; publicbilling-ошибка исправлена с сохранением совместимого machine code. | Это не новый полный natural GROUP04. Optional-question gate51controlled PASS ещё требует относящегося actual повтора. Fixed80 попытки ZAI4.5/CF остановились после1сообщения/1model/0tools:1113account/billing и unknown429 соответственно. Groq fixed80 тоже остановился на первом429. Затем no-apply instruct проба1024 структурноPASS,2048 нарушила envelope; предметная полнота не квалифицирована. Полный106 и неизвестные формулировки остаются отдельной приёмкой, не отменяя узкие закрытия. |
| HARNESS-005 | **CLOSED_LISTED_PERSISTENT_GUARDS_SCOPE** | Sequence/idempotency/hash/CAS/один active run и quote/order guards проверены API+PG. Durable quotas/cooldown/pause. Явный OpenRouter50 согласован на controller/Store/caller; default40 и счётчики сохраняются, generic высокий cap не расширяет Router автоматически. Optional model wait не снимает явную user pause. | App caps не равны vendor quota/reset. Exactly-once внешнего HTTP не заявляется. Unknown429, локальный budget-stop и supplier409 учитываются раздельно; без скрытого retry/paid/local fallback. |
| HARNESS-006 | **BACKEND_CONTRACT_MEDIA_AND_COLDSTART_TESTED / FINAL_UI_BY_TEAMMATE** | Owner-auth session/media API,17OpenAPI paths, handoff; exact hotel photos вне economic hash. Native10photos без изменения цены. Fresh committed Docker archive без ключей: build/up→DEMO preview/confirm/retry→restart сохраняет token/state/одинorder. [Cold start](../verification/prefrontend-coldstart-20261027.json). | Frontend интегрируется отдельно. При передаче синхронизировать контракт нового source, optional_clarifications как необязательные темы и selected-preview identity. Новый b934 archive не наследует автоматически cold-start receipt ab378; нужен относящийся финальный smoke. Без ключа доступны формы/DEMO, не свободный NLU. Stale media409 корректен и не является fresh-photo PASS. |
| HARNESS-007 | **TESTED_FAULT_AND_RECOVERY_BOUNDARIES** | Контролируемый настоящий PG SIGKILL137→503JSON→WAL recovery сохраняет trip/version/pause/token/order/idempotency; fresh Docker проверяет app restart. Ошибки модели/поставщика сохраняют состояние и отзы́вают старый quote перед неуспешным refresh. Actual LiteAPI409 теперь имеет безопасные provider/status diagnostics. | Не проверены произвольная потеря диска/сети и все distributed commit исходы. Старый503 и новый409 не объявляются объяснёнными последующим200; точный failed hotel/vendor code нового409 неизвестен. |
| HARNESS-008 | **ALL106_MAPPED / ALL80_PARTIAL_CONTENT_BOUND / FULL_LANGUAGE_OPEN** | 106:71partial/35not-executed в исходном учёте. Все80/697изучены; [V3oracles](../../fixtures/evaluation/after80-oracles-v3.manifest.json):781rule=194oldexact+587new,657из697turns partially bound;80case_specs,fullybound0. Independentpeer проверилhashes/старыеобъекты/семантическиеsamples;077T8/079T3–T4 требуютmanualalternative-review. D250=50диалогов/300ходов толькоimport. | Три freshfixed80 попытки STOP наT1 providerfailure,0wholecompleted. Полного106languagePASS нет; часть author-сценариев внеMVP. Replysemantics/relative/compound gaps остаютсяUNBOUND. В оригинальных80 нетpause/resume, её проверяетPFсерия. Числоpytest/metadata не становится80/80или106/106. |


### Текущие findings и условия закрытия

Статусы относятся к указанным ветвям поведения. Исходные actual/cached FAIL и прежние scores не переписываются; новый путь не закрывает неиспользованную normalizer-ветвь.

| Постоянный ID | Факт и выполненное действие | Условие закрытия actual части |
| --- | --- | --- |
| HARNESS-004-DEMO-COMMAND-IGNORED-001 | На двух первых репликах CF вернула DEMO, guard удалил её из-за слова «деморежим». Literal mode теперь сохраняется/дополняется без подмены providers/fallback; противоречивые команды блокируются. | **CLOSED_ACTUAL_RELATED_SCOPE:** PF-DURATION03/r3 19/19PASS, DEMO/состав/ночей/маршрут сохранены. |
| HARNESS-004-LATE-ORIGIN-TYPO-001 | Явное позднее «отправление Санкт-Петербрг» не создавало confirmation на фоне pending date. Добавлена адресная обработка поля отправления. | **CLOSED_ACTUAL_RELATED_SCOPE:** PF-GEO02/r3 12/12PASS; после согласия СПб, назначение/бюджет сохранены. |
| HARNESS-004-CHOSEN-CITY-LOSS-001 | Неподтверждённый Стамбул стирал Белград.city/RS. Сохраняется exact city intent, unrelated edits не сбрасывают его; aliases H004-GEO-UNCONFIRMED-CITY-LOSS-001 и HARNESS-004-COUNTRY-TYPO-001 сохраняются в своих исходных отчётах. | **CLOSED_ACTUAL_RELATED_SCOPE:** GEO01/R4=15/15, включая отказ+child и lateyes; не утверждается повтор каждого старого owner-диалога. |
| HARNESS-004-ORDER-REPLY-SUPPRESSED-001 | Правильный ответ CF о тестовом заказе заменялся generic unchanged reply. Ответ формируется из Store orders; shortcut не поглощает «теперь вылетаем…»/«я отменяю…». | **CLOSED_ACTUAL_RELATED_SCOPE:** PF-SOURCE06/r3 10/10PASS; содержательный ответ после confirm/retry, один тестовый заказ,0реальных броней/оплат. |

Доказательства: [cf-r2](../verification/cf-r2-repairs-20261027.json), [geo](../verification/geo-cf-r2-repair-20261027.json), [GEO01/R4](../verification/prefrontend-router-r4-20261027.json). R3/R4 исправляют только ошибочные oracle для unsupported-city и control-only версии; настоящая старая потеря Белграда остаётся FAIL. [R5](../../fixtures/evaluation/prefrontend-dialogues-20261027-r5.json) дополнительно требует READY/no pending/current ready plans после WOW, сохраняя все прежние state-checks.

### Связанные scope после последующих actual переходов

- **H004-GEO-ASK-REPLY-LOSS-001 — CLOSED_ACTUAL_RELATED_SCOPE.** ASK shortcut заменял правильную географию общим country FAQ. Contextual CITY-в-COUNTRY теперь адресует confirmation, вопрос исправляет географию без смены поездки.462controlled и GEO01/R4=15/15actual; [receipt](../verification/geo-openrouter-r3-repair-20261027.json).
- **HARNESS-004-COMPOUND-QUOTED-RETURN-001 — CONTROLLED_PASS / ACTUAL_RETEST_PENDING.** В фактическом batch7ops hotel-name quote выключала normalizer и давала ложный конфликт двух эквивалентных дат. Quoted spans теперь исключаются по области; гипотезы/неэквивалентные даты не нормализуются.
- **HARNESS-004-ROOM-IDENTITY-ALIAS-001 — CONTROLLED_PASS / ACTUAL_RETEST_PENDING.** После первого исправления exact cached batch выявил unknown room ID, ломавший breakfast scope. Полная однозначная bijection восстанавливает только canonical room IDs/order, без инференса membership/group. Полностью valid setters и неоднозначные случаи не переписываются.237relatedPASS; [receipt](../verification/compound-openrouter-r3-repair-20261027.json).
- **DIAGNOSTICS-PREVIEW-PROVIDER-001 — CLOSED_SAFE_DIAGNOSTIC_DELIVERY_SCOPE.**69controlled PASS и actual3208 ответ503 с `provider=liteapi/upstream_http_status=409` подтверждают доставку безопасной атрибуции. Raw details/URL/ключи не экспортируются. Причина исходного503/нового409 остаётсяUNKNOWN; [contract](../verification/provider-error-diagnostics-20261027.json), [actual409](../verification/natural-hybrid-prebook409-diagnostic-20261027.json).
- **HARNESS-001-COMPARABLE-SUPPLIERS — INDICATIVE_VIEW_AVAILABLE / EXACT_EQUIVALENCE_OPEN.**297provider+2PG/ASGI и [Native73e7437 actual](../verification/native-serp-comparison-20261027.json) подтверждают12sellers/6одно-гостевых ставок одного отеля. Unknown beds/board/cancel/eligibility не заменяются эквивалентностью. [Контракт](../SUPPLIER_COMPARISON.md). Multiroom/children в этом Serp details scope, окончательная цена и supplier booking не заявлены.

### Room patches, выбранный отель и необязательные вопросы

- **HARNESS-004-SPARSE-ROOM-SNAPSHOT-001 — ACTUAL_PREFERRED_PRIMITIVE_PASS / COMPAT_CONTROLLED_SCOPE.** Модель93f91fd сама выдала правильные `set_room_members` для обеих остановок; raw=checked, normalizations=[]. [Actual](../verification/actual-wow-router-r5-t4-20261027.json). Совместимость sparse arrays и alias repair этим не использованы; у них остаются366related/2cachedPG и свои исходныеFAIL. [Repair](../verification/room-members-r4-repair-20261027.json).
- **HARNESS-004-ROOM-COUNT-PATCH-BYPASS-001 — CLOSED_RELATED_CONTROLLED_SCOPE.** Новый primitive включён в count-only guard: просьба о числе номеров не меняет расселение. Явные новые участники и переселения сохраняются.191related; [audit](../verification/room-members-downstream-audit-20261027.json).
- **HARNESS-004-ROOM-PATCH-LAYOUT-CONFLICT-001 — CLOSED_RELATED_PREFLIGHT_SCOPE.** Разный состав одного номера, fullsetter против patch и удалённый fullsetter target отклоняются независимо от порядка. Конфликт не превращается в last-write-wins; root7pure/PG включают обе последовательности.
- **HARNESS-004-SEMANTIC-ERROR-ATTRIBUTION-001 — CLOSED_RELATED_ERROR_CONTRACT_SCOPE.** Domainunknown_constraint_room больше не называется ошибкой JSON Schema. Code остаётся совместимым, details/events содержат validation_stage=domain и domain_error_code; изменения не применяются.
- **HARNESS-004-SELECTED-PLAN-CONTEXT-001 — CLOSED_ACTUAL_RELATED_UI_SELECTION_SCOPE.** Fresh own preview plan3 передаётся как price-free identity, отдельно от first_shown_plan. В b9343c0 actual selected-plan3 edit сохранил выбранный первый отель, отличный от первой карточки, и остальные факты;0orders. [Actual](../verification/actual-selected-hotels-wow-20261027.json), [controlled](../verification/selected-plan-context-20261027.json). Это typed UI/setup+один NLU-переход, не полный natural диалог; quote consent не переносится.
- **HARNESS-004-MULTISTAY-INITIAL-ROOMS-001 — CONTROLLED_PROVED_LAYOUT_SCOPE / RELATED_NATURAL_INITIAL_STATE_PASS.** GLM4.5 raw11ops пропустил rooms при полном literal room/group описании. Helper дополняет только NEW empty stays при универсальном числе номеров, полном групповом partition и однозначном ordinal mapping;227related/2PG. [Repair](../verification/initial-room-layout-repair-20261027.json). Позднейший natural initial state прошёл; неиспользованный в нём omission-normalizer не получает чужой actual PASS.
- **HARNESS-004-EXISTING-ROUTE-COMPONENT-GEO-ASK-001 — CLOSED_ACTUAL_SINGLE_TRANSITION_FLOW_SCOPE.** Alias HARNESS-004-COMPOUND-CITY-REFERENCE-ASK-001: упоминание отеля Антальи/завтрака Стамбула вызывало лишний выбор города после правильной правки. В b9343c0 новый actual T4 после явной подготовки достигает OPTIONS_READY/current plans/pendingNone. [Proof](../verification/actual-selected-hotels-wow-20261027.json). Старые12state-PASS с лишним ASK сохранены отдельно.
- **HARNESS-004-SELECTED-HOTEL-KEEP-PIN-001 — CLOSED_ACTUAL_RELATED_SELECTION_PIN_SCOPE.** Раньше before/after hotel_id=None не доказывал сохранение выбранного отеля. Теперь положительное keep пинит только fresh own selected identity или сохраняет уже canonical pin; no selection→ASK, без default первого показа.229related/6PG и actual b9343c0 T4/plan3 подтвердили ID и все WOW/READY условия. [Repair](../verification/hotel-preservation-repair-20261027.json), [actual](../verification/actual-selected-hotels-wow-20261027.json). Положительный тест должен иметь canonical pin или явный preview; отсутствие выбора не маскируется требованием READY.
- **HARNESS-004-OPTIONAL-UNKNOWN-BLOCKS-SEARCH-001 — CLOSED_BOUNDED_CACHED_PG_SCOPE / ACTUAL_NATURAL_REPEAT_PENDING.** Natural3208 извлёк полные12ops/RU, но unrequested room-type/booking questions остановили поиск и превратились в generic field-null вопрос. Узкий gate переносит лишь доказанные optional темы в result, не подавляет user/server/hard/missing вопросы и не снимает user pause.51controlled, включая9PG и fresh empty own session→2hotel/3flight mock searches→READY. [Evidence](../verification/natural-hybrid-optional-questions-20261027.json). Это не actual autonomous HYBRID PASS и не разрешение брони.

### Ближайшая backend-приёмка

1. Квалифицировать Groq instruct/JSONSchema и получить доступный cloud runtime для нового80/697 fixed replay, затем оценить реально исполненную часть поcontent-oracles; source/runtime/профиль и FAIL сохранить. Неудачные попытки ZAI4.5/CF не считать завершёнными диалогами. Changed-question flow даёт NEEDS_ADAPTIVE, непокрытая семантика — UNBOUND, не ложный PASS. Срок сброса unknown429 не выдумывать.
2. Отдельно квалифицировать autonomous natural HYBRID путь после optional-question fix. Typed search по уже извлечённой поездке доказывает source integration, но не автономное продолжение. Для supplier409 сохраняются trip/revoked quote; не угадывать failed tariff reason и не делать неявный выбор.
3. Согласовать новый backend handoff: optional темы не являются pending; keep hotel требует настоящего current selection/canonical pin; snapshot/цена/согласие имеют разные жизненные циклы. Повторить финальный cold-start для сдаваемого архива. Frontend-дизайн и его отдельная интеграция остаются у сокомандника.

Полный106, все возможные формулировки и bookable-equivalence не являются объявленным результатом этих узких закрытий. Старые r3/r4 FAIL, бюджетный stop и vendor отказы остаются исходными отчётами, а не повторно «завершёнными» на новом коде.

### Новые подтверждённые границы перед frontend

- **HARNESS-004-BILLING-429-RETRY-001 — CLOSED_CONTROLLED_PUBLIC_ERROR_SCOPE.** Документированный1113 ошибочно показывался как частотный retryable429. Теперь понятный отказ API-ресурса и retryable=false; публичны только проверенные code/enum. [Полный checkpoint4557](../verification/billing-error-repair-20261027.json). Новый доступ Z.ai этим не создаётся; внутренний UTC admission-recheck не является обещанием восстановления ресурса.
- **HARNESS-001-LITEAPI-PREBOOK2001-001 — ACTUAL_REFUSAL_CAPTURED / CLOSED_CONTROLLED_ERROR_RECOVERY_SCOPE.** Новая ownDB/frozen-source серия дала5SANDBOX+2DEMO,9componentcoverage; первый prebook200, второй Анталья409/business2001.0models/quotes/orders, требования сохранены. Это определённый текущий отказ поставщика подтвердить цену/наличие; старый409 по-прежнему UNKNOWN. [Actual до исправления](../verification/natural-group-fresh-checkout-20261027.json). Новый код OFFER_NOT_CONFIRMED/HTTP409 предлагает refresh без автоматического выбора/повтора;60относящихся API/PG/adapter и4606общихchecksPASS; это не обещание доступности отклонённого тарифа.
- **HARNESS-004-GROQ-STRUCTURED-CONTRACT-001 — ACTUAL_MIXED_PROBE_RESULTS / TRANSPORT_REVIEW.** JSONobject не гарантирует envelope: один instruct запрос валиден, второй200/INVALID_LLM_RESPONSE. [Доказательство](../verification/groq-instruct-probes-20261027.json). Нельзя считать free лимит исчерпанным или модель пригодной для всех диалогов по одному200. Проверяется schema transport без превращения omitted fields в explicitnull.

### Исторические доказательства и первоначальные предложения

Следующие датированные записи описывают свои frozen версии. Их старые сроки, счётчики и пометки «ещё не сделано» не заменяют текущую основную матрицу выше.


**HARNESS-001 — доказательство от26.10.2026.** В новом PG-only project `otravel-source-eval-20261026` проверены две раздельные семьи, два города, четыре номера и все9компонентов без пропусков/двойной оплаты. Именно response rates подтвердили возраста6/12 и7/12, `maxOccupancy=2` у каждого номера и известные сборы; оба выбранных sandbox-тарифа успешно обновлены с прежними ID. Итог MIXED —313331,66RUB; quoteTTL299,18928с ограничен сроками внешних снимков, создан один тестовый заказ без supplier booking. Все три поиска Duffel ответили201/EUR, но их тарифы не вошли в рублёвую корзину; actual flight coverage=0. Собственный PG остановлен без удаления volume, защищённые8081/8083/8084 не изменились. Этот результат не закрывает оставшиеся scope строки HARNESS-001; исходный отчёт сохранён без изменения.

**H004-SAVED-FACTS-001 — CLOSED_CANONICAL_FOUR_TOPICS_SCOPE.** Факт: generic ASK/summary не обеспечивал ответа на вопросы о сохранённых бюджете, датах, маршруте и причинах выбора; pending date-target перехватывал посторонний вопрос. Действие: `trip_answers.py` + отдельный canonical путь до extraction, узкая классификация полных вопросов, latest own/current планы с барьером пустого batch, сохранение status/pending/quote через `finish_run(state=None)` и CAS. Смешанные правки и короткие ответы на slot сохраняют прежний путь; внутренний source задаёт сервер. Проверено:21ASGI/PG,107pure внутри полного1972PASS; нулевые model/search/tool/quote/order вызовы, date-pending, stale/foreign/empty/future batch и pause-before-finish. Относительные даты ответа не подменяются checkout, валюта без известного масштаба не получает default2. Initial full1965PASS/1FAIL сохранён: context-test использовал теперь каноническую фразу; для прежнего context-isolation oracle выбран явный edit с теми же assertions. [Доказательство](../verification/saved-facts-answer-20261026.json). Общий свободный диалог остаётся OPEN; в8084 во время80 не развёрнуто.

CAS проверяет ожидаемую версию при записи, но сам не задаёт порядок двух сообщений. Относительные «минус 20 тысяч» и «установи 100 тысяч» нельзя повторно применять в произвольном порядке после конфликта. Вместе с актуальным snapshot должны согласованно фиксироваться ревизия, инвалидирование quote и относящийся результат. [Изоляция PostgreSQL](https://www.postgresql.org/docs/17/transaction-iso.html)

«Всё в PostgreSQL» не означает неограниченные сырые промпты, скрытые рассуждения модели, паспортные данные или сетевые пакеты. Нужны очищенные события, идентификаторы попыток, связи с ревизиями и ограниченные результаты. Сеть и БД не образуют одной транзакции; сбой между ними должен оставлять понятный статус, а не выдуманное подтверждение.

## HARNESS-004 — исторические finding и исходные условия закрытия

**H004-CONTEXT-SEMANTIC-KEY-001 — CONTROLLED_FIX_PASS / ACTUAL_RETEST_PENDING.** ПодтверждёнобъектомнастоящегоHTTPrequestвMockTransport: универсальнаяочисткаудалялаполеkeyудоменныхpreferences/constraintsиoperationguide, хотяэтонеключдоступа. Введёнотдельныйsafe_model_contextспровереннымиструктурнымипутями/semanticenum; произвольныйmetadata.keyиполяcredentialsостаютсяисключёнными. НастроенныеLLMcredentialsрегистрируютсядоfirstprompt, безenvall/logging.375relatedPASS; полный2964PASS. Generic_safeдлялогов/ошибокнеослаблен. Закрытие: relateactualextractionссохранениемимен/значенийпрежнихусловийпослеобновлениястенда.

**Phase3,26.10:2919controlledPASS/0skip.** По actualCF12canary(6PASS/6FAIL) отремонтированы ложныйgroupnullconflict, потеря остановки«затемАнталья», дублирующийabsolutecheckout+shiftreturn, запретдругихполейвbudgetclause, пропущенные2rooms иmincost, mixedgeographyответы. ВсеисходныеFAILсохранены, cachedPGиrelatedpureнового кодаPASS. Actualповторнового пакета ещёPENDING. Ни2919тестов,ниofflinegraderсчастичнымиrulesнеозначают80/80или106/106NLU.

**H004-RATE-CAUSE-001 — CONTROLLED_POLICY_PASS / ACTUAL_RETEST_PENDING.** Unknown429directCF/ZAIбольше невыдаётсязаdailyquota: code-ownedengineeringwait300s безautomaticretry; confirmedCF3036до00UTC, businessbilling/usageбезвыдуманногоvendorreset. Routermetadataнеавторизуетdirectproviderfallback; длинныйtruncatederrorarrayнеподтверждаетdailyquota.6peernegative/controlPASS, initial2P2FAILсохранены. Основание: [Z.ai businesscodes](https://docs.z.ai/api-reference/api-code), [CFerrors](https://developers.cloudflare.com/workers-ai/platform/errors/), [CFfreeallocationreset](https://developers.cloudflare.com/workers-ai/platform/pricing/). ActualлимитZAI694ms/429остаётсяпричинойUNKNOWN; vendorremainingневычисляетсяизappcounter.

- **AFTER80-MONEY-QUOTE-001 — CONTROLLED_FIX_TESTED / ACTUAL_RETEST_PENDING.** Actualpilot002T10: «подтверждаю тестовое оформление ... за100750рублей» ошибочно поменяло130000RUB budget. Теперь quote/payment/offeramount не становится бюджетом без явного бюджетного поручения; составные clauses сохраняют собственную сумму бюджета.163pure+2PGPASS, полный2538PASS. Закрытие: тот же actualтекст на новой версии, бюджет/версия/pending сохранены, заказа без актуальногоquote/consent нет.
- **AFTER80-EDIT-ANSWER-001 — CONTROLLED_FIX_TESTED / ACTUAL_RETEST_PENDING.** Actualpilot001T10 правильно сохранил минимальную стоимость, но ответил только общей DEMOстрокой. После изменения canonicalreceipt отвечает по маршруту/датам/составу/бюджету и сохраняет актуальный уточняющий вопрос, без второгоLLMвызова. Related4PGPASS. Закрытие: actualсоставная команда и ответ с новыми сохранёнными фактами.
- **AFTER80-COUNTRY-SLOT-001 — CONTROLLED_FIX_TESTED / ACTUAL_RETEST_PENDING.** Actual029T2 «страна Грузия» дал общий географический факт, не привязав GE к существующему destination.city. Теперь freshownedslot перехватывается доQA, страна сохраняется в pending, город не угадывается. Pure+PGrelatedPASS. Закрытие: actual029continuation на новомкоде сGEpending и безпотериpeople/origin.
- **AFTER80-LITEAPI-SPB-001 — ACTUAL_CAUSE_CONFIRMED / MAPPING_FIX_TESTED.** Nativef52 `Saint Petersburg` дал0hotels; официальное имя изLiteAPIcitydictionary `St. Petersburg` дало3IDs, всеHTTP200. Исправлено толькоproviderlabel; русское имя/остальные6labelsсохранены.8mappingMockHTTPPASS. Закрытие: новыйactualпоиск черезадаптер и проверкаhotelIDs/rates/source; наличиекаталога не означаеттарифиbookability.

- **HARNESS-004-CONTEXT-MERGE-001 — CONTROLLED_API_PG_PASS / ACTUAL_NLU_OPEN.** D100-036/turn3:0MODEL_CALL_STARTED/contextual_slot, после«нет»replyвозвращаетRS,ноproposed_destination=Стамбул/TR иconfirmation_required=trueсохранены. Это наблюдаемаяошибкаharness,неплохаягенерацияLLM;phase1runtimeSHAUNKNOWN. Старый код воспроизведён контролируемым API/PG тестом; новый проходит. Внесены явные contextpatch/unset и адресность подтверждений, сохранение незатронутых фактов. Evidence: context-lifecycle-native-baseline-20261025.xml / context-lifecycle-current-20261025.xml. Actual NLU replay ещё нужен. Закрытие:relatedcontrolledAPI+PGrepro/retestстарыйcandidateудалён,RSсохранена,следующее«да»неприменяетотклонённыйгород;затемactualдиалогназафиксированнойверсии.

- **HARNESS-004-SLOT-AGE-001 — CLOSED_RELATED_DIALOGUE_SCOPE.** Actual Qwen сначала обновил child-2 вместо неизвестного child-1; `age-slot-20261025-before-fix.json`, initial local-integrated NOT_ALL_PASS сохранены. Persisted вопрос теперь имеет field/target, неизвестный model-field заменяется конкретным отсутствующим domain slot, словесный возраст адресуется только этому ID. Тот же frozen ACC-CLARIFY-01 после исправления PASS, child-1=4/child-2=7, остальные поля сохранены; development7 retest PASS. Это не закрытие всего HARNESS004.
- **HARNESS-004-COMPOUND-001 — OPEN.** В новой реплике Белград/Москва/раннее утро/один взрослый/детей нет часть фактов потеряна. Действие: разделить известную географию и поисковое покрытие; сохранить все явные operations/preferences и известный нулевой детский состав. Закрытие: тот же составной ввод и продолжение actual harness без повторной анкеты уже известных полей; каталог/наличие не выдуманы.
- **HARNESS-004-DATE-001 — CONTROLLED_SLOT_API_PASS / FULL_DIALOGUE_OPEN.** Заезд26.10.2026 и «завтра» при now25.10 вызвали INVALID_LLM_OPERATION. Действие: адресный календарный slot с явным clock/TZ и вопросом при выезде≤заезда. Закрытие: исходный диалог actual replay, состояние сохранено, показаны разрешённая дата и понятное противоречие вместо schema failure.
- **HARNESS-004-COUNTRY-TYPO-001 — OPEN_POLICY_V2.** Владелец уточнил: при уже выбранной Сербии bare«Белгород» понимать как опечатку Белграда с видимым пояснением. Это не глобальный alias и не разрешение игнорировать явную Россию/реальную неоднозначность. Исходный owner11v1 сохранён, v2 policy учитывается отдельно. Закрытие: actual continuation сохраняет страну/людей/пожелания; отказ поиска объясняет покрытие, без принудительной смены страны.

## Обработка новых сообщений

Один верхнеуровневый intent недостаточен для реплики «добавь ребёнка, замени второй отель, перенеси возвращение, покажи разницу и пока не оформляй». Нужно извлечь все адресные изменения и отдельно управление выполнением. Неясное «второй» разрешается по сохранённому показу и ID либо уточняется.

Приоритет остановки важнее продвижения к покупке. Выбор предложения не равен согласию на покупку. «Да» отвечает на текущий вопрос, а не обязательно подтверждает quote. «Отмена» может означать остановить поиск, отменить поправку, отказаться от оформления или отменить уже созданный заказ — эти действия нельзя сливать.

По ответу владельца после явной паузы дополнения сохраняются до явного «продолжай»; следующая содержательная реплика не возобновляет работу автоматически. Новые поисковые/оформляющие инструменты не запускаются. Уже отправленный внешний запрос нельзя магически «отправить обратно»: его поздний ответ не должен сам возобновить процесс, заменить состояние или создать заказ. Техническая обработка накопленных реплик и отличие такой паузы от обычного уточнения входят в будущий контракт.

Вопрос задаётся для блокирующего этапа. Для вдохновения не нужны все данные checkout; для цены комнаты нужны даты и состав; для ребёнка — релевантный возраст; для покупки — показанная актуальная корзина и согласие. Одна новая реплика может сразу ответить на вопрос и изменить несколько других условий. Неизвестное значение не превращается в false или произвольный default.

«Любой запрос» следует понимать как контролируемый результат для допустимого входа: действие внутри поддерживаемого периметра, необходимый вопрос, пауза, безопасное ограничение либо понятная ошибка. Это не обещание выполнимости любой поездки и не гарантия понимания без подключённой модели.

## Дополнение к 106 сценариям

Существующие STATE-01…10 покрывают изменения и историю; BOOK-01/02/03/04/08 — подтверждение, повторы и ошибки; SYSTEM-08 — refresh. BOOK-05/06 относятся к уже созданным заказам. Они не заменяют отдельную проверку явной паузы, отмены текущего поиска и отзыва согласия до создания заказа.

Кандидаты новых проверок, пока **not_run**:

- **HINPUT-01:** составная реплика изменяет несколько адресных компонентов и сохраняет остальные ограничения; показывает «Было → Стало».
- **HINPUT-02:** «добавь ребёнка 6 лет, но подожди, ещё допишу» → рестарт → дополнение; нет самовольного поиска/checkout во время паузы.
- **HINPUT-03:** после preview «всё подходит, но пока не оформляй»; корзина сохранена, заказ не создан.
- **HINPUT-04:** «беру первый» и «да» в разных фазах не смешивают выбор, ответ на вопрос и подтверждение покупки.
- **HINPUT-05:** неоднозначная «отмена» не стирает поездку и не отменяет существующий заказ без основания.
- **HINPUT-06:** ответ сразу содержит origin, даты, гостей и новое условие; все поля применены, повторная анкета не начинается.
- **HINPUT-07:** поздний ответ инструмента после паузы или новой поправки не меняет актуальную ревизию.
- **HINPUT-08:** переход live → demo → live сохраняет пожелания, но не превращает синтетический ID/цену в реальное предложение и не переносит старое согласие автоматически.

Исходные 106 записей не исправлялись. Проверка корпуса из предыдущего анализа не становится PASS нового harness. Все указанные дополнительные случаи ещё требуют фикстур и наблюдаемых критериев.

## Демоинвентарь и границы продукта

Детерминированные генераторы полезнее ответов, привязанных к нескольким известным фразам. Но справочник GeoNames не подтверждает рейсы, наличие комнат или реальный тариф. На первом этапе сохраняются семь направлений владельца. Глобальный справочник может помогать распознаванию неизвестного города, но не расширяет молча обещанное покрытие.

Seed/ID должны быть устойчивы, а цены и совместимость — согласованы внутри выбранной модели данных. Синтетические расписания, поставщики и номера явно обозначаются. Такие данные могут проверять внутренние ограничения и обеспечивать деморезерв; они не закрывают требование сравнения реальных поставщиков.

Сложные группы и многогородские поездки не откладываются автоматически. При этом количество таблиц, глобальная география, произвольный undo и 65 тестовых позиций не должны подменять один корректный полный путь. Подключение LLM потребуется для отдельной проверки языка; live-данные — для отдельной проверки доступа, покрытия, актуальности и реального сравнения.

Владелец отдельно разрешил смешанный план: живые и демонстрационные компоненты могут сосуществовать при маркировке каждого из них. Общая сумма такого плана должна иметь смешанный/оценочный статус, а не вид полностью подтверждённой рыночной цены. Смена источника требует актуального preview и не переносит старое согласие автоматически. Одного глобального флага `data_mode=demo` из примера для этого недостаточно.

## Исторический снимок окружения до настройки

Это read-only снимок от 25 октября, а не результат запуска проекта. [Детали наблюдения](harness-environment-20261025.json).

| Компонент | Подтверждено | Практическое ограничение |
| --- | --- | --- |
| PostgreSQL на Windows | Служба `postgresql-x64-18` работает; установлен пакет/client18.6; `pg_isready` на127.0.0.1:5432 сообщает accepting connections. | Роли, пароль, проектная БД и схема не проверялись; SQL и миграции не выполнялись. План16/17 и установленный18 — разные варианты окружения, не доказательство несовместимости. |
| WSL | WSL3.0.3.0; `Ubuntu-24.04` и `AegML-Hackathon` зарегистрированы как WSL2 и остановлены. | Дистрибутивы не запускались; содержимое и Docker внутри них не проверены. |
| Docker | Windows CLI не найден в PATH; в проверенных стандартных путях/реестре Desktop не обнаружен. | Это не доказывает отсутствие Docker внутри WSL. Готовность daemon/Compose не подтверждена. |
| Порт8080 | Уже есть слушатель процесса `httpd`. | Ничего не остановлено. Перед локальной проверкой на обязательном порту нужен явный план разрешения конфликта. |

Для будущего развёртывания подходит изолированная WSL2-среда, например уже зарегистрированная `AegML-Hackathon`, и собственный PostgreSQL внутри Compose. Это предложение, не выполненная настройка и не необходимость использовать существующую Windows-БД. СУБД контейнера может слушать внутренний5432 без публикации его на занятый Windows-порт.

Перед выбором Docker Desktop или Engine внутри WSL нужна проверка того, что уже установлено. Docker официально поддерживает WSL2-backend; само наличие WSL не означает готовый Docker. При Linux-разработке Microsoft рекомендует Linux-файловую систему для производительности; существующий checkout на E: сейчас не переносился. [Docker WSL2](https://docs.docker.com/desktop/features/wsl/), [Microsoft WSL filesystems](https://learn.microsoft.com/en-us/windows/wsl/filesystems)

## Исторический план до команды владельца

1. Утвердить каноническую модель поездки и минимальный frontend-контракт, включая формы без LLM, ожидание, ошибки и «Было → Стало».
2. Разделить подтверждения готовности: ядро на управляемых ответах, подключённая LLM на новых диалогах, подключённые live API и интеграция frontend.
3. Проверить и выбрать окружение, не затрагивая существующие сервисы и другой проект. После разрешения — изолированные app/db, миграции и демоданные.
4. Реализовать минимальную сквозную цепочку со сложным маршрутом, затем уточнения/паузу/возобновление, пересборку и защищённое тестовое оформление.

Этот список сохранён как историческое предложение. Последующая команда владельца разрешила реализацию; актуальные подтверждения и оставшиеся ограничения находятся в PROJECT_STATE.md и основных HARNESS-блоках выше.

Уточнениевладельца:предыдущие100сценариев/130реплик—не100полноценныхадаптивныхдиалогов. Новыйпрогон100сессийсмножествомходовведётсяотдельновдругомчате;работающийruntimeнеобновлятьпосредиэтойсерии. РазработкаE,проверкиotravel_test,deployтолькопоновомуcheckpoint.

## Checkpoint 25.10: контекст, метрики и зависимости

Новый код:1234pytest PASS (full-suite-20261025-commit-candidate.xml), без реальных model/travel HTTP. Контролируемый старый baseline:7 FAIL; новый:7 PASS (6 воспроизведений поведения и новая проверка аудита). Это не квалификация всех диалогов и не обновление работающего runtime.

Внесено: удаление отклонённых pending proposals, адресность origin/destination; bounded date labels/clock/TZ/выбор остановки; money scope guards; безопасные extraction-before/after, usage и rate-limit traces; компактная JSON schema. Actual grammar и полный многошаговый NLU остаются открыты. Review gaps: тариф «на ночь» и usage ошибочного HTTP200 требуют следующего исправления.

QA25-OSS-01: новый отдельный image4cea47c…/pip26.2.2 и lock36 проверены PyPI+OSV:0 advisories/0 skips. Старый live image не изменён. Полная безопасность source/OS и лицензионная совместимость не закрыты. QA25-VIDEO-01: нужен деморолик/ссылка; QA25-JUDGE-ACCESS-01: ключи/ресурсы ждут письменного ответа26.10; QA25-SUBMISSION-01: сдача29.10 до10:00МСК в repo организаторов; QA25-EVAL-INTEGRITY-01: проверить материалы перед сдачей. HTML Q&A является пересказом ASR. Продуктовые live/multicity/group требования не снижены.

### Дополнительные проверки текущей ревизии

- **HARNESS-004-MONEY-SCOPE-002 — CLOSED_BOUNDED_REGRESSION_SCOPE.** Подтверждено: «Номер до10000рублей на ночь» оставлял предложенный whole_trip бюджет. Guard теперь учитывает гостиничный контекст и деньги; «общий бюджет100к на неделю» не превращается в тариф за единицу. До патча6FAIL/9PASS; после113money PASS. Сложные составные тарифные условия остаются вне этого закрытия.
- **HARNESS-004-USAGE-001 — CLOSED_CONTROLLED_ADAPTER_PG_SCOPE.** При HTTP200 с невалидным JSON/схемой vendor usage терялся. Capture перенесён до проверки контента, хранится в ContextVar, сбрасывается перед новой попыткой и записывается в MODEL_CALL_FAILED. MockTransport4providers и PostgreSQL проверяют отдельный расход обеих repair-попыток; секреты/raw body не сохраняются. Сетевые ошибки без vendor usage остаются UNKNOWN. Полный прогон:1268PASS, full-suite-20261025-usage-money.xml. Работающие8081/8083 не обновлены.

### Cloudflare и следующий диалоговый этап,26.10

Cloudflare Workers Free подтверждён владельцем без карты/пополнения; tokenverify200active. Код: фиксированный accountendpoint/32hex, Free-модель Llama3.3-70B,1attempt/run, отдельный persistent40/day,безfallback; токены не выводятся и не наследуются локальным Ollama. Полный набор1420pytestPASS, реальные вызовы в нём не выполнялись. Одна отдельная actualadapterпроба вернула валидныйJSON и сохранила Москву/взрослого/нулевыхдетей/раннееутро/неизвестныйбюджет, но пропустила Стамбул. Report new-free-llm-probes-20261026.json: NOT_ALL_BOUND_CHECKS_PASS; это не fullharness/диалоговаяприёмка.

- **HARNESS-004-CF-EXTRACTION-001 — OPEN_ACTUAL_ADAPTER_OMISSION.** В указанной пробе destination отсутствует в operations, остальные явные факты сохранены. Проверить goldenoperationagainstschema, prompt и модель; исправление квалифицировать тем же полным входом на зафиксированной версии. Не считать корректныйJSON успешным пониманием запроса.
- **HARNESS-004-DATE-TARGET-001 — OPEN_CONTROLLED_HELPER_GAP.** Pure2stayrepro: «Выезд14.11.2026» задаёт выбор остановки, но дату отдельно не сохраняет; «Анталья» возвращается в LLM. Действие: отдельный version-bound date_proposal с absolute date,clock/TZ,candidateIDs; однозначный выбор→валидация/CAS/consume, stale/negated/ambiguous→уточнение. Нужны pure+PGмногошаговыйretest и actualNLU; текущий finding не является доказанным actualчатFAIL.

Z.ai: Standard key сохранёнлокально; tinyFreeGLM4.7Flash200/JSONexpected/85tokens подтверждает только доступ, runtimeадаптер/качествоещёнепроверены. Cohere onboarding пока без ключа; NVIDIAнеподключён. Все новые ключи только ignorednative.env0600; работающиестендызаморожены. Ветка по просьбе владельца переименована в backend/harness-foundation, история сохранена.

### Z.ai для изолированной проверки,26.10

Реализованы StandardFreeGLM4.7Flash/4.5Flash, explicitconfirmation, фиксированныйendpoint,thinkingdisabled, servervalidation/usage/безpaidfallback. ZAI имеет отдельныйpersistentpool:default40/day, явнонастроенныйevalдо2000/day,1/run. Остальныеremotehard40неповышены. Новыйmanager использует толькоC-sourceotravel-zai-eval/8084/DBotravel_zai_eval, cleanenv с однимZAIключом иpidfdidentityguards; actualstart/пилот выполняются отдельно. Целевые59adapter+143routing/PGchecks иfull1550PASS; единичнаяtinyactualпроба85tokensнеподменяет fullharness/100диалогов.

**CLOCK-OBS-001 — FIXED_TEST_SCOPE / EXTERNAL_CLOCK_CAUSE_UNKNOWN.** ПервыйfullZAI:1523PASS/1FAILpricepreview. СохранённыйPGпоказалretrieved21:22:06.882…936UTC, затемpreview21:22:06.478/.493,TTL180секнеистёк; механизмобратногоходачасовUNKNOWN. Изменёнтолькоtest_new_preview_price_change_stales_previous_active_quote:общийfixedawareclockиassertпервого200. Runtimeguardsбудущего/истёкшегоsnapshotсохранены. Module16PASS/новыйfull1550PASS; originalFAILиclockdiagnosticсохранены. Это не доказательство устойчивости системныхчасов.

### Выданный стенд для100диалогов

localhost8084/debug теперьREADY_FOR_EXPLORATORY_TESTING: фактическаяzai/glm-4.5-flash, отдельнаяCкопия85160ef/DBotravel_zai_eval, loadedmanifesta25ec360… совпадаетсоstartup. 4.7fullrequest упёрсяв45sTIMEOUT; сохранённеуспех, модельнеобъявленаплохойпоэтомуодномуисходу. 4.5directdiagnostic10.25sсохранил явныефакты. ActualAPI3turnpilot PASS ограниченныхassertions: origin/destination/adult/nochildren/budget100k→80k/explicitpause/GETpersistence.2LLMвызова13.44s/33.66s,пауза0LLM. Full100неисполнялиськорнем; handbook docs/testing/ZAI_FULL100_HANDOFF.md передаётсявладельцемдругомучату. Snapshotнеобновлятьдоегоокончания; работаHARNESS004продолжаетсянаE.2000/day — толькоapplicationcap,неvendorquota;1/run,no paidfallback. Отчётыzai-actual-pilot(FAIL),zai45-diagnostic,zai45-actual-pilot,zai-eval-readyсохраненыраздельно.

### H004-DATE-TARGET-001 — CLOSED_CONTROLLED_HELPER_API_PG_SCOPE

Реализован owneddateproposal: absoluteISO, исходныйawareclock/TZ,trip/version и orderedcandidateIDs. City/ordinal/IDадресуютодну остановку; stale/malformed/duplicate/question/negative иdomainoverlapдаютуточнение. Сменаactivefield/origin/exacttargetнепоглощаетсястарымproposal; невернаякалендарнаядатасохраняетknownstaytarget. Удалениеproposal иstatecommitвexistingCASоднойтранзакции; пауза сохраняетсяприрейсе. ДоисправленияreproFAIL, после304pure+35relatedPG/ASGI (8новых) инезависимые5counterexamplesPASS; full1601PASS, full-suite-20261026-date-proposals.xml. SourceEonly: actualNLUнепроверен, mixedtarget+инаяправкаASK/noopнеобъявляетсяполнымпониманиемсоставныхреплик. СтарыеFAILиограничениясохранены. 8084frozen85160efпоowner100startне обновлялся.

TEST-LEDGER-001: собственныеfakeHTTPusage-тестыупёрлисьвнакопленныйtestDBGemini40/day. ИсправленТОЛЬКОfixturebudget: explicitnonproductiontestadapterbucket,uniquelabel иhighfixturelimit,долговечныерезервирования/usageпо-прежнемупроверяются. Продакшен40/dayнеослаблен,rowsнеудалялись. Target2PASS/full1601PASS.

COMPOSE-MODELS-001: добавленпропуск7ZAI/CFconfigfieldsвDockerCompose; syntheticconfig7checksPASS,контейнерынестартовали. Новыйcold-startprojectпроверяетсяотдельно; personalkeys/hostSQL/oldservicesвнёмиспользоватьсянедолжны.

## Исторический checkpoint: ответы, резервные модели, чистый запуск

**HARNESS-004-INITIAL-ROUTE-002 — CLOSED_CACHED_EXTRACTION_API_PG_SCOPE.** В прямо присланном ownerZAI100-009 turn1 модельназвалаКазаньвreply,ноневыдалаstayop. Добавленground_initial_route: толькоодинявныйначальныйknown-cityмаршрут, согласованныйorigin,безexistingstay/leg/routeops/вопросов/гипотез/истории/negativehotelintent. Заполняетсятолькогород;даты/номераневыдумываются; raw/checkedнормализацииразделены. Exactcachedturn1→Казаньсохранена→вопросзаезда, включаясохранениеявного'детейпоканебудет'. Related95/105targetchecksPASS;неuniversalNLU/неactualnewmodelreplay.

**HARNESS-004-REPLY-LOSS-001 — CLOSED_CONTROLLED_AND_CACHED_REPLAY_SCOPE.** RawanswersпоказанногопримерасодержаливерныйсоставипояснениеDEMО,ноplanssummaryихзаменяла. No-opinfoнеповторяетsearch, сохраняетplans/pending/activequote; реальныеданные+вопроссохраняютобе части. Canonicalparty/source/cart/hypoответыбезIDs; suggested_actionpreviewтолькодляownedcurrentplan/version,безquote/ordercreation. Exactcachedturn6–9нареальнойpre-turn6revisionпрошли(APIсоздаётновыеtrip/sessionIDsиобнуляетversionдляизоляции). Источникfixtures/evaluation/owner-zai009-extractions-20261026.json, миниатрибуцияowner009-attribution-20261026.json; другие100нечитались. Полный1844PASSвfull-suite-20261026-release.xml; последняяliteralnegativepartyпоправка23relatedPASS. Genericнеподдержанныевопросы/fullNLUOPEN.

**HARNESS-004-INFO-INTAKE-003 — CLOSED_RELATED_REGRESSION_SCOPE.** Первыйобщийпрогонновойлогики1821PASS/5FAILубиралнужныеpartybindings. Sameoraclesвосстановлены: topic-boundunansweredLLMquestionsсохраняются, filled/staleнерелевантныесбрасываются, code-ownedpendingclarificationнеудаляется. Cartselectorsпривязаныкplan/variant,немогутиспользоватьordinalребёнка;negation/hypothesisнеaction,politesearch/ellipsisнеподавляютсяиз-за?. Parent75pure+38targetPGиindependentpeer11PASS. OriginalFAILXMLсохранён, не заменёнPASSдругогопути.

**HARNESS-004-CF-ADD-CHILD-001 — OPEN_ACTUAL_EXTRACTION_MISMATCH.** GemmaCFP03updatechild-2вместоaddновогоребёнка7,rawbudget98kвместо80k. Денежныйguardисправилтолькобюджет. Нужнаотдельнаяintentcoverage/relatedmodelпроверка:добавленровно1новыйchild,старыеучастникинеизменены,бюджет80k,explicitwait. Nemotron3/3boundedcasesне закрылGemmafinding.

CF:Gemma/NemotronдобавленыявнымиFreeпрофилями,defaultLlamaсохранён.6actualrequestsсоsamecaseprompthashes:Gemma2/3,Nemotron3/3;не100диалогов. Publishedguardedrunnerпоумолчанию0calls,explicit--run≤6,nooverwriteoldresults. ZAIrootcallsпослеowner100start0,8084не обновлялся.

**COMPOSE-COLDSTART-001 — CLOSED_CURRENT_DOCKER_DEMO_SCOPE.** Freshархивcb0/newPG/no18keys/no9flags:attempt1FAILиз-заНАШЕЙinternalnetwork; directbridgehealth200. Freshattempt2ordinarybridgePASS13checks/restarts; freshattempt3oneactualup--build--waitPASS11checks/DEMOquote/order/idempotence.0model/external/toolcalls,old8081/8083/8084IDs/volumesнеизменены. Owncontainersdownбез-v,volumes/imagesсохранены;sourceархивнеизменён. Это не всеОС/offlineemptycache/finalfrontend.

QA25-EVAL-INTEGRITY-01: limitedpatterncheck219trackedtextfiles/0strongpatterns,notfullmanualcompliance. SECURITYscopeвсегдачастичный; зависимостиPyPI/OSVисходно0knownadvisoriesнеравнывсемуsource/OSsecurity. ФронтендконтрактуточняетLLMvsdataисточник,условияcachedplans иread-onlypreviewaction.

## Исторический кандидат после GLM45 initial-room omission

**HARNESS-004-MULTISTAY-INITIAL-ROOMS-001 — CONTROLLED_PASS / ACTUAL_PENDING.** Modelraw11ops сохранял города/даты/семьи/бюджет, но не rooms. Guard счёта выходил при ordinalallocation cue. Новый initial helper только для новых пустых multi-stay layouts и полной literal room→adult→knownfamily bijection, не угадывает семьи/возраст; singlecity/неуниверсальность не перехватывает, «оба» при3городах уточняет.227related; [receipt](../verification/initial-room-layout-repair-20261027.json).

**H004-EXPLICIT-CHANGE-ANSWER-COVERAGE-001 — CONTROLLED_PASS / ACTUAL_PENDING.** Selector party,budget не заменяет просьбу «что изменилось»: добавляется серверная history секция с before→after. Гипотезы/цитаты/история/отрицания не становятся таким запросом; 10newpure/PG внутри81related.

На момент этого checkpoint GET/key подтвердил39/50used,11remaining,$0; это исторический остаток, не текущая квота. Только OpenRouter ceiling50 при явном operator request, default40/Gemini40 сохраняются; счётчикине сбрасываются, max_price0/:free/nobackup/creditlimitнеизменны. Тогдашний full4403PASS/0skip/147.56s: [core](../verification/initial-layout-router50-core-20261027.json). Это не actualwholelanguagePASS и не quota guarantee.

## Исторический actual WOW: state PASS и отдельно найденный flow gap

Ниже сохранён snapshot93f91fd до нового перехода b9343c0. Его исходные scores/FAIL не меняются; актуальные related closures находятся в основной матрице и текущих findings выше.

Внешний PF-GROUP04 на93f91fd прошёл12старыхstate/answer/controloracles,6LLMcalls. RawT4самкорректноиспользовал2scopedset_room_members: compat/aliasrepairsневызывались. [Actual](../verification/wow-router-r5-20261027.json).

**HARNESS-004-EXISTING-ROUTE-COMPONENT-GEO-ASK-001 — CONTROLLED_FLOW_PASS / ACTUAL_FOLLOWUP_PENDING.** ПослеT4городавссылкахнаотельАнтальи/завтракСтамбулавызвалиinitialdestinationASKприужеупорядоченноммаршруте. Stateверен,нонемедленныйпоискблокировался. Guardтеперьразличаетexistingstaycomponentreferencesиrouteproposal; new/unknown/alternative/country/explicitroutechangeвсёещёпроверяются.159related+точныйPGflow→OPTIONS_READY/2DEMOplans/pendingNone, данные/завтрак/выборотелянеизменены; [repair](../verification/existing-city-scope-repair-20261027.json).

R5recipeдобавляетONLY5строгихreadinesschecksкT4, старые12PASSнепереписывает. Последнийfull4438PASS/0skip/164.59s; [core](../verification/existing-route-flow-core-20261027.json). Budget50толькоприexactexplicit50, generic2000ZAIнеподнимаетдругиеcallerdefault40. СледующийactualодинарныйT4сlabelledtypedprestatebootstrap, безложногоclaimновых12естественныхходов.

**HARNESS-008-AFTER80-ORACLES-V3-001:** [V3](../../fixtures/evaluation/after80-oracles-v3.json) содержит781предметное правило длявсех80диалогов/657из697ходов;194прежних сохраненыточно. Independentpeer подтвердилhashes/oldrules иотдельноmanual077T8/079T3–T4. Fullybound0, respons semanticsUNBOUND. [Подготовка](../verification/after80-oracles-v3-preparation-20261027.json) не является новымactual80PASS.
