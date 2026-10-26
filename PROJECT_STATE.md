# O!Travel Agent — текущее состояние,26.10.2026

## Цель

Продолжать HARNESS-001…008: backend/harness, API и проверяемый запуск. Frontend делает сокомандник. Владелец сократил запланированные100 до80полных адаптивных диалогов в другом чате; весь набор разбирать после его передачи. Сейчас разрешён и разобран только прямо присланный ZAI100-009.

## Freeze — не нарушать

- 8084: **zai/glm-4.5-flash**, C-копия `/home/aegml/projects/otravel-zai-eval`, БД `otravel_zai_eval`. API source `85160effde07ff891295ba437754b328ebdf258a`, профиль `330a2b7`. Manifest `a25ec3607131e26be4ea879c0166e4a0dc4010fef96c6c7281c4c0969852537d` совпал со startup.
- На8084 только LLM и DEMO-каталог; туристические ключи не переданы.2000/day — предел приложения, не квота поставщика;1/run, без fallback. Промпт: docs/testing/ZAI_FULL100_HANDOFF.md.
- После начала100 не менять модель/код/ключи/БД/порт/C-копию, не restart/sync и не делать свои ZAI-генерации. Старые8081(Groq)/8083(Ollama4B) тоже сохранять. Разработка — E:/work/aegml; обычные проверки — только otravel_test.
- Служебная квитанция zai-eval-backend-20261026.json менялась read-only status: не восстанавливать/не коммитить вслепую. Чужие100/legacyD100 файлы не редактировать и не стадить.

## Опубликованная база текущей доработки

**5f14db4**, опубликован в обоих private-репозиториях: origin ostrovok-hackathon-2026/aegml и cv osirust/O-Travel-Agent. Ветка `backend/harness-foundation`, DraftPR3. Main самостоятельно не меняли. Перед push staged-secret-scan:8настроенных секретов,0совпадений,0подозрительных JSONполей.

- **1844pytestPASS**: docs/verification/full-suite-20261026-release.xml. Позднее расширение literal «детей пока не будет» —23relatedPASS. Это код/PG/mock HTTP, не1844NLUдиалога.
- H004-INITIAL-ROUTE-002: cached turn1 из009 терял Казань в ops. Bounded route_intake заполняет только явно указанную начальную city без дат/номеров; history/hypothesis/question/ambiguous/no-hotel/transport-only/operation-cap не дополняются. Raw и checked разделены.
- H004-REPLY-LOSS-001: общий plans-summary заменял полезные ответы. conversational отвечает по каноническому составу/source/cart/hypo, сохраняет NO-OP планы/pending/quote, не повторяет поиск. Реальные изменения+вопрос сохраняются. preview_cart — только подсказка для current owned plan/version, не quote/order.
- Exact cached turns6–9 и реальные domain facts revision5 проверены в новой API/PGсессии. Никаких новых LLMвызовов; другие диалоги не читались. Универсальные вопросы/fullNLU остаются OPEN.
- Первые5регрессий нового info-guard сохранены (1821PASS/5FAIL), исправлены с теми же oracles: релевантные незаполненные party/missing-data вопросы сохраняют binding, stale/irrelevant отсекаются, server clarification не удаляется. Cart ordinal привязан к плану, не ребёнку; negation/hypothesis не action; polite/ellipsis search не подавляется из-за?.
- H004-DATE-TARGET-001 закрыт в controlled helper/API/PG scope: absolute date proposal с clock/TZ/trip/version/IDs, точный выбор, CAS cleanup.304pure/35relatedPG, peer5PASS. Compound selector+иная правка пока ASK/noop. В8084 не развёрнут.

## Дополнительные проверки

- Cloudflare Free: ключ active, пользователь подтвердил отсутствие карты/оплаты. Explicit profiles Llama70(default), Gemma4, Nemotron3.6actualCFcalls на3одинаковых cases: Gemma2/3, Nemotron3/3 — только bounded extraction. Gemma CFP03 updatechild-2 вместо нового ребёнка, rawbudget98k→guard80k; add-child остаётся OPEN. Никаких ZAI calls/изменений8084. Guarded runner scripts/evaluate_cf_models.py без --run ничего не вызывает.
- Clean Docker, source cb0eadc: attempt1FAIL из-за НАШЕЙ internalсети; ordinary bridge freshattempt2PASS с app/dbrestart; freshattempt3PASS ровно up--build--wait/newvolume→DEMOquote→1order/idempotence.0model/tool/externalcalls, старые сервисы неизменны. Owncontainers убраны DOWN без-v; образы/volumes сохранены. См.docs/testing/CLEAN_START.md.
- PyPI+OSV: отдельный image4cea47c…/pip26.2.2, runtime29/global1/lock36:0knownadvisories/0skips. Не full source/OSsecurity. Старый liveimage e3… с6pipadvisories не перезапускался.
- CLOCK-OBS-001: PG snapshots06.882…936 и последующие preview06.478/.493 показали clock inconsistency; механизм UNKNOWN. Только price-changeтест получил fixedawareclock; runtimeguards не ослаблялись. Старый FAIL сохранён.

## Последний завершённый этап HARNESS-001

`docs/verification/actual-group-flow-20261026.json`: **PASS_MIXED_ACTUAL_TWO_STAY_FLOW**, source5f14db4, отдельный C-context и новый PG-only Dockerproject. Две семьи, два проживания, четыре номера, три перелёта и два трансфера.9внешних HTTP:6LiteAPI/3Duffel,0SerpApi/0LLM. Старые8081/8083/8084 и их процессы не изменились; собственные контейнеры убраны без удаления volume/image.

В выбранном плане оба проживания — ответы настоящего **SANDBOX**, не рыночные предложения: распределение/вместимость/обязательные сборы подтверждены полями response; возраст ребёнка6→7 учтён между проживаниями. Preview200 повторил rates для тех же native offer ID; это НЕ prebook. Confirm200 и точный повтор200 создали один BOOKED_DEMO,0supplier orders. TTL ограничен источником. Duffel ответил EUR при бюджете RUB: предложения отклонены, без выдуманного FX; перелёты и трансферы остались DEMO. Сумма313331.66RUB — simulated estimate, не подтверждённая цена реальной покупки.

Не закрыты actual flight selection, сравнение реальных поставщиков, полный live/bookable trip и NLU этого маршрута. Request-echo пассажиров Duffel не становится доказательством ответа API.

## Текущий этап HARNESS-004

H004-SAVED-FACTS-001 закрыт только в canonical4topics scope: `trip_answers.py` отвечает budget/dates/route/why-plan без LLM/search, latest own/current планы, точное сохранение status/pending/quote и CAS. Date-pending не перехватывает чистый вопрос о бюджете; короткие slot-ответы и mixed edits сохраняют прежний путь. Известные масштабы валют, реальные saved departure dates и unknown arrival/return не подменяются догадками.21ASGI/PG и107pure входят в полный **1972PASS**,0skip. Отчёт docs/verification/saved-facts-answer-20261026.json. Initial1965PASS/1FAIL сохранён: context-isolation fixture переведён с теперь canonical explanation на explicit edit при неизменных assertions. 8084 не обновляли, identity PID421782/start3488716 совпал. Свободный NLG режим остаётся DESIGN/NOT_IMPLEMENTED; текущие80 целиком ждут передачи владельцем.

## Осталось

- HARNESS-001: mixed SANDBOX hotels→refresh→simconfirm подтверждён; actual flights/полный live trip/сопоставимость реальных поставщиков остаются открыты.
- Полный106NLU не закрыт:71частично typed/domain/adapter/PG,35неисполнены, fullactual106PASS0. D250:50диалогов/300реплик импортированы,12staticissues, не исполнен. LegacyD100/130реплик не равен текущим100полным разговорам.
- QA прочитаны60вопросов/180реплик плюс MITsecurityцитата. HTML — ASRпересказ, не окончательные письменные правила. Видео/доступная ссылка нужны; ключи и мощности жюри ждут ответа26.10; сдача29.10 до10:00МСК в repoорганизаторов. Source-evaluation patterncheck219файлов/0strongmatches — лишь частичный чек.
- SETUP004: ключи вставлялись в чат; перевыпустить после100/перед передачей, сейчас freeze не нарушать. Gemini ключа нет, Mistral требует Upgrade, Cohere onboarding зависает, NVIDIAключа нет. Новые аккаунты для нынешнего ZAI/CF не нужны.

AEGYX INDEX81 PAUSE передSOURCE3: не трогать AEGYX/Ubuntu24.04/WindowsPG/PEM/8080. Не wsl--shutdown/dockerprune/удалениеvolumes. SSD E отключался; активные стенды на C. Lifecycle только с identity/PIDguards.
