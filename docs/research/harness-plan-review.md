# Анализ плана Agent Harness

Первичный анализ: 25 октября 2026 года до разрешения реализации. Ниже сохранён исторический разбор; он не является действующим запретом начинать. Позднее владелец разрешил настройку и реализацию. Backend/Docker/демоконтур и четыре внешних источника проверены в пределах [текущего состояния](../../PROJECT_STATE.md). Gemma отвечала upstream429; текущий API-профиль Dots/Xing также имеет отказы. Локальная Qwen подключена к harness; отдельные серии прошли, качество исходного106-корпуса ещё не квалифицировано. Полноценный frontend готовит другой участник.

## HARNESS004 — независимая серия D100 от владельца

25.10.2026, 21:15–21:24 МСК тестировщик выполнил 100 сценариев / 130 завершённых реплик на работающем 8083 через HTTP, ollama/qwen3.5:4b. 55 raw PASS / 45 FAIL; 108 подтверждённых вызовов модели; 0 инструментов и заказов. В 8 raw PASS найдены дополнительные ошибки; исходные scores не переписывались. Основной реестр с фактами, доказательствами, постоянными ID, действиями и условиями закрытия: [D100 FINDINGS](../verification/dialogue-break-100-20261025/FINDINGS.md) и четыре связанные review. Findings OPEN; HARNESS004 и исходные 106 этим не закрыты.

Приоритет ремонта по фактам серии: потеря явных фактов и ролей городов; ASK/гипотеза ≠ CHANGE; даты, суммы и знак; запрет незапрошенных preferences/constraints; завершение подтверждений; доставка содержательного ответа пользователю. Владелец просил тестировать, поэтому тестировщик приложение не исправлял и не перезапускал. Runtime SHA неизвестен; серия не квалифицирует параллельные неразвёрнутые правки. Повтор должен сохранять frozen inputs/oracles и исходные FAIL/UNKNOWN, отдельно дополняя отсутствующие инварианты новой версией тестов.

Полные 100 протоколов сохранены; первая попытка 058 с неизвестным исходом сохранена отдельно. 57 завершённых случаев не повторялись, 058–100 продолжены по тем же входам. Оригинальная ошибка схемы на checkout и карточка UI не закрыты другим PASS. Следующий этап разработки и исходного плана 106 остаётся в PROJECT_STATE; самостоятельного запуска нового блока эта серия не разрешает.

## Вывод

Уточнение D100: post-read deployment receipt фиксирует запуск 8083 в 21:20:09 МСК между фазами. 55/45 — смешанная сводка, а не одна версия; фаза1 = 25 PASS / 32 FAIL, фаза2 = 30 PASS / 13 FAIL. [Граница runtime](../verification/dialogue-break-100-20261025/RUNTIME_BOUNDARY.json). Каждое finding относится к своей датированной фазе; состояние после развёртывания не квалифицировано всеми 100 случаями.

Направление плана подходит: один управляемый harness, состояние и аудит в PostgreSQL, отдельные адаптеры модели и инструментов, проверяемые изменения поездки, вопросы и долговременное ожидание пользователя. Точность маршрута и оформление проверяет код; модель понимает язык и предлагает действия.

Сейчас можно спроектировать и впоследствии проверить ядро на управляемых ответах `ModelProvider` и воспроизводимом demo inventory. Это не докажет понимание новых формулировок. Для такого доказательства требуется отдельно подключённая LLM и диалоги, не использованные при подготовке промптов. Универсальный генератор предложений не заменяет языковую модель.

Подключение туристических API в последние два дня остаётся зависимым от доступов и контрактов источников. Готовность демоконтура не закрывает согласованные требования живого поиска и сравнения реальных поставщиков. Эти результаты нужно учитывать раздельно.

## Источники и восстановленный контекст

Полностью прочитаны [план из Markdown](sources/agent-harness-plan-original.md) — 355 строк, 38 746 байт — и [приложенное обсуждение архитектуры](sources/agent-harness-discussion-original.txt) — 426 строк, 32 264 байта. Оригиналы сохранены без изменения; контрольные суммы — в [manifest.json](sources/manifest.json). Их заголовки «ТЗ», команды и сроки рассматриваются как предложения автора, а не распоряжение начать работы.

Через приложение найдена указанная владельцем переписка. Доступны пользовательские сообщения: важны адаптация, необходимые вопросы, ожидание дополнений и работа с новыми формулировками. Полные ответы ассистента этим способом не получены; веб-запрос по ссылке также не дал содержимого. Поэтому разбор ответов опирается на приложенные файлы и [сохранённое исследование со 106 сценариями](agent-stress-analysis.md), а не на заявление о полном чтении удалённой переписки.

Актуальные продуктовые ограничения сохраняются в [PRODUCT_DECISIONS.md](../PRODUCT_DECISIONS.md): семь направлений; несколько городов, отелей и номеров; сложная группа; «Было → Стало»; приоритет live с явным demo fallback; формы и готовые сценарии при отказе LLM; один агрегатор с различимыми реальными поставщиками допустим. Технологический план не отменяет эти решения.

## Что в плане уже правильно

План прямо разделяет PostgreSQL и HTTP: БД хранит состояние, сообщения, ревизии и журнал, а внешние вызовы выполняет backend. Долгие LLM/API-вызовы и ожидание пользователя вынесены за пределы транзакции. Это следует сохранить, а не считать отсутствующей защитой. PostgreSQL удерживает блокировки до завершения транзакции; `async` сам по себе не освобождает её. [Блокировки PostgreSQL](https://www.postgresql.org/docs/17/explicit-locking.html)

Также полезны: типизированный результат модели, whitelist инструментов, отдельные проверки ограничений, инвалидирование старой корзины, ограниченные повторы, идемпотентный демозаказ и обычный JSON/polling для первой интеграции frontend. Собственная небольшая FSM допустима. Не нужно добавлять брокер, рой агентов или LangGraph только ради названия архитектуры.

Если всё же выбирать LangGraph, нужно единственное авторитетное состояние и долговременный checkpointer. При resume узел с `interrupt()` выполняется заново с начала; действия до паузы должны выдерживать повтор. Checkpoint не создаёт общей транзакции с произвольными таблицами заказов. [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)

## Что требует уточнения до контракта

Первоначальные факты относятся к неизменённому Markdown-источнику. Основные HARNESS-блоки ниже обновлены по коду и относящимся проверкам текущего этапа; это не заявление о готовности всего продукта. Последний полный прогон713 PASS, новые targeted-наборы и actual-model результаты учитываются отдельно. Подробности — PROJECT_STATE.md и docs/verification/.

| Постоянный ID | Актуальный статус | Внесённый код и доказательство | Что ещё требуется |
| --- | --- | --- | --- |
| HARNESS-001 | **MIXED_CORE_TESTED / ACTUAL_TRIP_FLOW_OPEN** | `planning.py`, `orchestrator.py` собирают покрытие stays/rooms/legs, источник каждого компонента, сопоставимую валюту, fees/allocation/hard evidence, before→after. `api.preview` перепроверяет каждый внешний компонент, отзывает старые quotes, ограничивает TTL источником;16PG/API mixed-тестов прошли. | Нужен сквозной actual-source поиск многогородской группы→refresh→simulated confirm. SANDBOX/поисковая цена не становятся LIVE_BOOKABLE. Сравнимость разных реальных поставщиков ещё не доказана. |
| HARNESS-002 | **CLOSED_CANONICAL_SCHEMA_SCOPE** | `domain.py`: стабильные traveler/group/stay/room/leg ID, структурная валидация, изменение source policy — часть новой версии. Исправлен reorder группы при update age; related domain tests. | Численные guards остаются engineering bounds, не утверждённым продуктовым пределом. Проверка реальных room/fee-фактов идёт отдельно через provider_facts. |
| HARNESS-003 | **UNDO_CANCEL_TYPED_SCOPE_TESTED / NLU_REFERENCE_OPEN** | `Store.prepare_undo`+CAS создают новую revision, не восстанавливают quote/consent/order; cancel отдельно останавливает run. `history.py` показывает первый план как historical-only, STATE09 typed fixture восстанавливает hotelA и сохраняет актуальный транспорт, oldquote409. | Языковые неоднозначные «отмена/верни первый» и все варианты ссылок требуют actual NLU. Public leg-mode setter пока не добавлен; STATE09 transport setup типизирован через Store. |
| HARNESS-004 | **CONTROLLED_GUARDS_TESTED / OWNER100_RUNNING_FROZEN / FULL_NLU_OPEN** | Контекст, адресные даты/dateproposal, money scope и безопасный аудит имеют controlled/API/PG проверки; полный1601pytestPASS. На отдельном8084 frozen85160ef/GLM4.5Flash actual3turnpilot PASS ограниченныхassertions. | Владелец выполняет100диалогов, sourceCнеобновляется. EdatefixнеactualNLU;compoundtarget+инаяправкаASK/noop. CFactual потерял Стамбул; полный106NLU открыт. |
| HARNESS-005 | **CLOSED_LISTED_PERSISTENT_GUARDS_SCOPE** | Sequence/client_message_id/payload hash/CAS/1active-run/idempotent quote/order проверены API+PG; remote budget40/day+2/run, local500/day+1/run, cooldown durable. | Не заявляется exactly-once внешнего HTTP. Катастрофические распределённые исходы не закрываются этими проверками. |
| HARNESS-006 | **BACKEND_CONTRACT_AND_DEBUG_UI_TESTED / FINAL_UI_DEFERRED_BY_OWNER** | `FRONTEND_CONTRACT.md`, OpenAPI и работающий `/debug` с chat/state/DEMO/pause/resume. Playwright: desktop+mobile,2plans, control flow, literal XSS-safe text,0model calls/console errors в UI smoke. | Финальный frontend делает сокомандник по указанию владельца; его интеграция/UX не квалифицированы. API-кнопка убрана из пользовательского debug-header; отключениеSwagger в сдаваемом режиме ещё настраивается. |
| HARNESS-007 | **TESTED_FAULT_BOUNDARIES / UNTESTED_FAILURES_OPEN** | `test_fault_matrix.py`+`test_harness_review.py`:13PG-case PASS. Отдельный новый Compose project: настоящий PostgreSQL SIGKILL137→503JSON→WAL recovery, trip/version/pause/token/order и idempotency сохранены, partial intake0. `database-crash-recovery-20261025.json`, старые500/socket.gaierror FAIL сохранены. | Это контролируемый crash/restart одного узла, не доказательство произвольной потери диска/сети/всех distributed commit исходов. |
| HARNESS-008 | **ALL106_MAPPED /71PARTIAL_EXECUTED /35NOT_EXECUTED** | `corpus-map.json`rev3:71sourceIDs имеют предметные typed/domain/adapter/PG/historical assertions, включая все14 прежних stateful gaps; initial и новые отчёты раздельны. Последний общий прогон713 PASS, последующие новые наборы проходят targeted tests. | Full-language PASS исходных106=0. Отдельная7case/11turndevelopment LLM-suite не подменяет корпус. Нужны actual106 execution, case-specific oracles и исправления результатов; не делать106PASS из проверки метаданных. |


CAS проверяет ожидаемую версию при записи, но сам не задаёт порядок двух сообщений. Относительные «минус 20 тысяч» и «установи 100 тысяч» нельзя повторно применять в произвольном порядке после конфликта. Вместе с актуальным snapshot должны согласованно фиксироваться ревизия, инвалидирование quote и относящийся результат. [Изоляция PostgreSQL](https://www.postgresql.org/docs/17/transaction-iso.html)

«Всё в PostgreSQL» не означает неограниченные сырые промпты, скрытые рассуждения модели, паспортные данные или сетевые пакеты. Нужны очищенные события, идентификаторы попыток, связи с ревизиями и ограниченные результаты. Сеть и БД не образуют одной транзакции; сбой между ними должен оставлять понятный статус, а не выдуманное подтверждение.

## HARNESS-004 — текущие finding и условия закрытия

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

## Следующий checkpoint: ответы, резервные модели, чистый запуск

**HARNESS-004-INITIAL-ROUTE-002 — CLOSED_CACHED_EXTRACTION_API_PG_SCOPE.** В прямо присланном ownerZAI100-009 turn1 модельназвалаКазаньвreply,ноневыдалаstayop. Добавленground_initial_route: толькоодинявныйначальныйknown-cityмаршрут, согласованныйorigin,безexistingstay/leg/routeops/вопросов/гипотез/истории/negativehotelintent. Заполняетсятолькогород;даты/номераневыдумываются; raw/checkedнормализацииразделены. Exactcachedturn1→Казаньсохранена→вопросзаезда, включаясохранениеявного'детейпоканебудет'. Related95/105targetchecksPASS;неuniversalNLU/неactualnewmodelreplay.

**HARNESS-004-REPLY-LOSS-001 — CLOSED_CONTROLLED_AND_CACHED_REPLAY_SCOPE.** RawanswersпоказанногопримерасодержаливерныйсоставипояснениеDEMО,ноplanssummaryихзаменяла. No-opinfoнеповторяетsearch, сохраняетplans/pending/activequote; реальныеданные+вопроссохраняютобе части. Canonicalparty/source/cart/hypoответыбезIDs; suggested_actionpreviewтолькодляownedcurrentplan/version,безquote/ordercreation. Exactcachedturn6–9нареальнойpre-turn6revisionпрошли(APIсоздаётновыеtrip/sessionIDsиобнуляетversionдляизоляции). Источникfixtures/evaluation/owner-zai009-extractions-20261026.json, миниатрибуцияowner009-attribution-20261026.json; другие100нечитались. Полный1844PASSвfull-suite-20261026-release.xml; последняяliteralnegativepartyпоправка23relatedPASS. Genericнеподдержанныевопросы/fullNLUOPEN.

**HARNESS-004-INFO-INTAKE-003 — CLOSED_RELATED_REGRESSION_SCOPE.** Первыйобщийпрогонновойлогики1821PASS/5FAILубиралнужныеpartybindings. Sameoraclesвосстановлены: topic-boundunansweredLLMquestionsсохраняются, filled/staleнерелевантныесбрасываются, code-ownedpendingclarificationнеудаляется. Cartselectorsпривязаныкplan/variant,немогутиспользоватьordinalребёнка;negation/hypothesisнеaction,politesearch/ellipsisнеподавляютсяиз-за?. Parent75pure+38targetPGиindependentpeer11PASS. OriginalFAILXMLсохранён, не заменёнPASSдругогопути.

**HARNESS-004-CF-ADD-CHILD-001 — OPEN_ACTUAL_EXTRACTION_MISMATCH.** GemmaCFP03updatechild-2вместоaddновогоребёнка7,rawbudget98kвместо80k. Денежныйguardисправилтолькобюджет. Нужнаотдельнаяintentcoverage/relatedmodelпроверка:добавленровно1новыйchild,старыеучастникинеизменены,бюджет80k,explicitwait. Nemotron3/3boundedcasesне закрылGemmafinding.

CF:Gemma/NemotronдобавленыявнымиFreeпрофилями,defaultLlamaсохранён.6actualrequestsсоsamecaseprompthashes:Gemma2/3,Nemotron3/3;не100диалогов. Publishedguardedrunnerпоумолчанию0calls,explicit--run≤6,nooverwriteoldresults. ZAIrootcallsпослеowner100start0,8084не обновлялся.

**COMPOSE-COLDSTART-001 — CLOSED_CURRENT_DOCKER_DEMO_SCOPE.** Freshархивcb0/newPG/no18keys/no9flags:attempt1FAILиз-заНАШЕЙinternalnetwork; directbridgehealth200. Freshattempt2ordinarybridgePASS13checks/restarts; freshattempt3oneactualup--build--waitPASS11checks/DEMOquote/order/idempotence.0model/external/toolcalls,old8081/8083/8084IDs/volumesнеизменены. Owncontainersdownбез-v,volumes/imagesсохранены;sourceархивнеизменён. Это не всеОС/offlineemptycache/finalfrontend.

QA25-EVAL-INTEGRITY-01: limitedpatterncheck219trackedtextfiles/0strongpatterns,notfullmanualcompliance. SECURITYscopeвсегдачастичный; зависимостиPyPI/OSVисходно0knownadvisoriesнеравнывсемуsource/OSsecurity. ФронтендконтрактуточняетLLMvsdataисточник,условияcachedplans иread-onlypreviewaction.
