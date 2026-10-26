# O!Travel Agent — состояние, 26.10.2026

## Цель владельца

Сегодня исправить и проверить harness по всем80диалогам, использовать сильные LLM API и туристические API. Завтра интегрируем frontend/фотографии от сокомандника; сегодня frontend не переделываем. Локальную Qwen не использовать, пока доступны лимиты сильных API. Не обещать отсутствие любых будущих ошибок и не выдавать controlledPASS за actualNLU.

## Исходный материал и квалификация

- Полностью прочитаны80разговоров/697userturns/597наблюдаемых modelreservations.081–082 не включены. SHA исходного JSONL:81a7e59c65d41da8dfc6101fc0429c455c259561e44f6a9a79f0dc99323058ca. Оригиналы в ignored .local/evaluation/zai-full100-20261026/, не менять.
- Четыре полных аудита и текущий checkpoint: docs/verification/after80-20261026/. Rawоперации/reply модели не экспортированы во всех ходах; причинную атрибуцию не выдумывать.
- **2477controlled/API/PostgreSQL/MockTransportPASS,0skip**: full-integrated-final1.xml. Initial2459PASS/5FAIL сохранён: поправлены raw-conflict domain error path, короткая curatedgeoинформация и conditionalunknowncityASK/старое подтверждение. Никакой oracle не ослаблялся ради этих5PASS.
- RootQA после стилистики52relatedPASS; actor money144pure+6PG; route/group/room/removal13PG; intent6PG+oldunknowncity1PG; nonstop466related/29new; travelcache182related. Отдельные наборы пересекаются — не складывать их как независимую приёмку.
- Actual-пилот f52b622 выполнен в двух новых сессиях:22сообщения/20Z.ai calls. Состояние группы, география и money-delta выдержали проверенные ходы; два исходных сценария не объявлены PASS. Найдены AFTER80-MONEY-QUOTE-001 (стоимость оформления ошибочно становилась бюджетом), пропущенный ответ после edit+question и несовместимость старых followups с новым вопросом о номерах. Отчёты C-backed private, редактированные копии ignored .local/evaluation/after80-20261026/pilot-f52b622/.
- Полный106NLU ранее не квалифицирован:71частичноtyped,35неисполнены; текущие80 не превращаются в106PASS. D250 импортирован, отдельной actualприёмки нет.

## Внесено в текущий проверенный пакет

Точные деньги/проценты/копейки и no-op удержанных прибавок; boundorigin/destination/country; oneway/openjaw/undecided routepolicy; clear_preference; atomic upsert_group/раздельные семьи; адресные условия тарифов room_id; guard пропущенного удаления человека; nonstop по actual response facts, а не requestecho. HYBRID policy для новых сессий, явное гражданство без RUguess. Кэшируются только справочные hotelIDs, тарифы/reprice всегда HTTP; travel429cooldown ограничен одним процессом.

Модель для справочных вопросов выбирает разделы, а суммы/даты/возраст/тарифы/заказы формирует код из canonicalfacts. Строгая7field/0ops схема; один durablecall/run независимо отprovider; status/pending/quote сохраняются, CAS и pausefences проверены. Это не универсальное свободное NLG; полнота выбранных тем требует реального теста. Обычные изменения по-прежнему извлекаются моделью.

Новые quote/confirm блокируются при незавершённой обработке, уточнении или recoverableerror. Точный повтор уже созданного заказа остаётся идемпотентным. Источник каждого компонента и DEMO/SANDBOX/LIVE_INDICATIVE/LIVE_BOOKABLE не смешиваются в обещание реальной покупки.

## Runtime8084 — обновлён, HYBRID включён

Старый процесс421782/start3488716 остановлен штатным exactidentity guard; исходные80 не изменялись. Исходный код и БД сохранены в C-backed /home/aegml/checks/after80-original-20261026-attempt2 (private600); originalbackupreceipt вотчётах. Перед обновлением86sessions/706runs/4775events/605modelreservations/0pending/1order. Firstfailedsocketdump сохранён отдельно, не удалять.

Новый controller scripts/after80_backend.py: fixed Csource /home/aegml/projects/otravel-after80-20261026, тотже8084/DBotravel_zai_eval; PID1019537/start8793863, source f52b622, package manifest2155ab3c8e00c3ccfe9f037057cb42ac7e95736d34644df01ee3762894bd8fdc. Child толькоZAI45+LiteAPIsandbox+Duffeltest+SerpFree, HYBRID,0localLLM,2000/dayengineering/1run. Masterkeys толькоignorednative.env, SOURCE.envтолькоZAI. VendorremainingUNKNOWN,2000/day не квота поставщика. Запуск0inference. Nativef52 не меняется во время actualcanaries; phase2workspace отдельно, развёртывание после тестов/commit.

Шесть actual travel HTTP200: подтверждена ошибка provider-label СПб (Saint Petersburg0hotels, официальный St. Petersburg3IDs); SerpApi20СПб+18Стамбул в RUB/LIVE_INDICATIVE. docs/verification/travel-lookup-marketrefs-20261026.json. Это не факты вместимости/fees/брони. Групповая actual проверка после labelfix остаётся. Partycanary5Z.ai calls:029/043 initial взрослый+origin сохранены; country-slot «страна Грузия» потерял GE-context, отдельный45s timeout сохранил state. CF Nemotron canary выполняется отдельно, native8084 не переключён.

Runner scripts/replay_after80.py: без --run0HTTP; privateWSLvault, immutableoriginal80/697, noUNKNOWNresend, 429/timeoutstop, optionalsimcheckout. Исходные followups связаны с прежними вопросами — fixedreplay не называется новым adaptiveразговором, счётчик сообщений не80PASS. Добавить SOURCE_COOLDOWN в halt taxonomy и подтвердитьtarget перед actual.

## Ближайший шаг

Опубликован f52b622 в origin+cv backend/harness-foundation,2477controlledPASS, PR3 draft (описание ещё требует фактов нового пакета). Phase2: moneyquote-guard163pure+2PGPASS, mixed-edit canonical receipt/ordertopic и СПбprovider label+GE-slot подключены; общий2538PASS/0skip. Затем новый immutablecommit/nativeобновление, actualзакрытие этихfindings, адаптивный многокомпонентный travel-поиск/refresh/simulatedconfirm и полный80сcase-specificoracles. Ожидания runnerнеравны фактической приёмке.

Открыто: actualповтор; произвольные пропуски NLU (напримерadultintake043/029 не имеют literalcoverageguard); объяснение причин пустой выдачи; actualperroomrateevidence; EURbilling нашегоDuffel vsRUBбюджет требует отдельного явногоreferenceFXконтракта, не переименовыватьcurrency; сравнение реальных поставщиков; финальная интеграция frontend. Realbooking/payment запрещены. Ключи, ранее вставленные вчат, перевыпустить перед передачей, не ломая текущую квалификацию.

AEGYX INDEX81 PAUSE передSOURCE3; не трогать AEGYX/Ubuntu24.04/WindowsPG/PEM/8080, не wsl--shutdown/dockerprune/удалениеvolumes. Все lifecycle действия только exactidentity/PIDguards.
