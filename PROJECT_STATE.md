# Текущий checkpoint перед actual OpenRouter50

Core E frozen,4403PASS/0skip/147.56s, docs/verification/initial-layout-router50-core-20261027.json. Новое: complete literal initialroommapping (newstays/multicity only), explicit changes-answer coverage, optionalOpenRouter50/default40 без reset.227initialrelated/81answer-related/158budget-related/230controllerpure проходят; не складывать сfull.

Runtime C ещёaefa56c/pkg1f0ac0cfae63cfe17510ae930b2953308c5c1ccd8ef8c63376b0af6069504c50, currently legacy GLM4.5 via after80_backend.py, actualactor94273 завершён. Следующее: commit/push→ownlegacyZAIstop→installnewHEAD(previous1f0,addedinitial_room_layout.py)→ownRouterstart --daily-bound50→PF-GROUP04freshactual,profileopenrouter-dots/max15/spacing60. Dailycounter39сохраняется; GET/key39used/50limit/11remaining/$0 подтверждён04:33UTC. Не трогать creditlimit0.01/неbuycredit/неpaidfallback/неlocalQwen.

GLM47actual /home/aegml/evaluation-runs-prefrontend/prefrontend-20261027-041224-411ea843: T1PASS/T4HTTP429 business1305; after>300s fresh /.../prefrontend-20261027-042119-6be92a5f firstmessage1305. Official docs classifytemporaryoverload, dailyquotaunconfirmed. GLM45 /.../prefrontend-20261027-042827-a828a6e1 firstmessage200 butroomsomitted→FAIL/complexNOT_REACHED. Exactraw11ops causednewinitial-layoutfix. No change to originalFAIL.

OwnR4obsoletepausedqueue cancelled ONCE viaAPI, rowsdeleted0/semantictrip/order unchanged/model0. HelperwronglyexpectedHTTP200 whilecancelreturns202; outcome RECONCILED GET-only, no resubmission. docs/verification/own-paused-test-cleanup-20261027.json. Pending0 then allowedlegacy4.5startup, no guard bypass. OriginalR4report untouched. Existingreview595471db…usedearlierforZai47startnowhistorical,notreuseforsnapshot.

Client newzai47-flash/legacyzai remain distinct. Currentprefrontenddriver65353d9d4f16a13d59d9603bf80bfb99dbef12faf513593a136603dee6dc50c2,recipeR4 79acc092… . Providerchange explicit, notautomaticfallback.

Original80oraclesv2 prepared byacceptance:14newcaseIDs/129contentchecks +old65=20partialcases/172turns/194rules; fullybound0. fixtures/evaluation/after80-oracles-v2.json +manifest. Original80 have NO actualpause/resume sequence; don'tfakepausecoverage. Full80HYBRIDdryplan verified80/697,notstarted. FollowsaftercriticalactualPASS+sourceavailability; no metadataPASS. Source80SHA81a7e59c…unchanged.

Historical context below; currentheader overrides runtime/task details.

# O!Travel Agent — состояние на 27 октября 2026

## Цель и границы

Закрыть backend/harness до интеграции frontend по docs/research/harness-plan-review.md. Работа разрешена, продолжать. Frontend делает сокомандник. Сильные подтверждённые free API прежде локальной Qwen; локальных inference в этой работе0. Нет настоящих броней/оплат. Исходные корпуса/FAIL/квоты не переписывать.

E:/work/aegml; Native /home/aegml/projects/otravel-after80-20261026, WSL AegML-Hackathon, DBotravel_zai_eval,8084. Тесты толькоotravel_test. Master private /home/aegml/projects/otravel/.env. SOURCE.env содержит только старый ZAIkey, равенство Master проверено без вывода значения. AEGYX R16 INDEX81 PAUSED; чужие WSL/WindowsPG/httpd8080 не трогать; no shutdown/prune/volume delete.

## Последний опубликованный runtime и текущий кандидат

- Оба private remotes/backend/harness-foundation:aefa56ca2f34dfae674299e7cd7b9ac7d8aaad7d. Draft PR3 открыт, main не сливается автоматически.
- Native source ужеaefa56c/package1f0ac0cfae63cfe17510ae930b2953308c5c1ccd8ef8c63376b0af6069504c50. Router/CF/Groq остановлены своими managers,8084 пока НЕ запущен. Legacy after80_backend.py оказался glm-4.5-flash (историю не переписывать) и отказал из-за pending queue. Новая отдельная after80_zai47_backend.py для glm-4.7-flash готовится geo_dialogues; контроллер SourceC НЕ меняет. HYBRID LiteAPI sandbox/Duffel test/SerpFree/CBR, local/paid fallback запрещены.
- Core ужеcommitted/deployed (процесс ещё не стартован): scoped set_room_members + addition-only sparse compatibility + count/conflict guards + честная domain-error attribution + current UI selection в extraction/readonly/cart facts. Source/tests FREEZE, итоговый4292PASS/0skip/145.23s; docs/verification/room-selection-core-20261027.json. Промежуточный full с неверным ожиданием числа adaptercalls и редактированием теста во время исполнения исключён из qualification, исходныйFAIL сохранён. Перед этим отдельные4263/4202; не складывать overlapping suites.
- Новые поля/primitive описаны в docs/FRONTEND_CONTRACT.md. RoomID локален к stay, поэтому value.stay_id обязателен. Полный upsert_stay.rooms по-прежнему FULL replacement. Выбор preview не равен quote consent; selected identity projection без денег/quoteID.

## Actual LLM и причины исправлений

- R3 OpenRouter/43050b8:8cases,89actions/79messages,30models/7toolstarts;6wholePASS/2FAIL,80PASS9FAIL+1NOT_REACHED. Geo02lateorigin12/12,Duration19/19,Transport12/12,Source06mixedcheckout/orderreply10/10,Source07boundary8/8,Pause08control10/10. docs/verification/prefrontend-router-r3-20261027.json. Не полный80/106.
- R4 OpenRouter/73e7437: GEO01 15/15actualPASS. GROUP04 validJSON6ops содержал только второй номер, что трактовалось полной заменой и удаляло первый с breakfast; schema0errors, Domainunknown_constraint_room. Поэтому новый typed primitive/strict compatibility. Затем CLIENT ENGINEERING_BUDGET_NO_WRITE остановил run после9models/0tools ПЕРЕД resume: это не provider429. Старая группа осталась paused с очередью; не возобновлять её на новом коде, чтобы переписать результат. docs/verification/prefrontend-router-r4-20261027.json.
- Router app использовал30+9=39/day из40, vendor остаток ещё не перечитан. Квоты не сбрасывать. Z.ai glm-4.7-flash отдельный1call smoke200/2333ms/1227tokens/2correctops;0Native/DB/ledger изменений. Source и Master key совпадают. docs/verification/zai-availability-smoke-20261027.json. Новый actual следует запускать явным ZAI-профилем (2000app-bound, не квота вендора).
- Candidate controlled:366domain/schema/typed/sparse checks;2PGexactraw/typed +oldquote409/0orders. Count-only новыйpatch не меняет расселение (191related). Room-only refresh заново запрашивает цены/occupancies; selective cache нет. Quote/version/pause/owned guards общие. Semantic failure больше не называется schemaerror.
- Selected plan3:22Store/context +root7checks(6pure/1PG), current fresh own preview identity передаётся модели и readonly sections без перенумерации; explicitordinal сильнееdefault. Latest revoked/expired/stale/foreign не возрождает старую выборку. Фактическое NLU этого случая ещё нужно.

## Туристические API, фото и запуск

- Actual ab378 group:2stays/4rooms/3flights,4people/2families,child6→7; LiteAPI prebook2/DuffelEUR refresh3/CBR→RUB+DEMOtransfers.9components complete,346697.38RUB,preview/confirm/retry200→1BOOKED_DEMO,0supplierbooking/payment. Первый503 сохранён,causeUNKNOWN; diagnostics сейчас имеют безопасный provider/upstream status. docs/verification/actual-group-checkout-20261027.json.
- Native73 Serp property-details:1actualtypedquery→12seller names/6roomrates на1adult,3980600RUB; sourceasof02:03, retrieval02:44 (cache),0model/statechange/orders. LIVE_INDICATIVE, equivalence/bookability false. docs/verification/native-serp-comparison-20261027.json; пример docs/frontend/supplier-comparison-example.json сplaceholderIDs/тестовымиссылками. Multiroom/children details ещё не заявлены.
- Serp earlier Hotels20; FlightsMOW→DXB exactnoresults,controlIST→DXB12. Последний account232/250 был ДО следующих запросов, текущий остаток неизвестен. OpenMeteo текущий прогноз, не погода ноябрьской поездки; CBRreference с TTL/датой,неbankrate. Реальные эквивалентныеbookable suppliers OPEN.
- Фото exactLiteAPIhotelID, separate owner-auth media GET, вне economic hash. Native10photos без изменения цены; stale media409 после сменыrevision ожидаем. DEMO фото не притворяются реальным отелем.
- Fresh Docker committed archive/no keys:onebuild/up31.866s,38localHTTP/0model/vendor;DEMOsearch/preview/confirm/retry/restart сохраняют trip/token/одинorder. Собственныйcompose down без-v. Это не весьOS/offline/NLUбезключа. docs/verification/prefrontend-coldstart-20261027.json.

## Ближайший шаг

Startup admission: read-only SELECT подтвердил РОВНО1pending, это нашR4GROUP04/user_pause/active_run_idnull/based_version=currentversion.0DBmutations/inference. Private0600review /home/aegml/checks/paused-r4-admission-20261027.json SHA595471dbf08c84601b23c0c35acb7d802855fd2cdc33cafbceaf5501fc4b2ff2, packagebound1f0ac… . НовомуZai47контроллеру передать CLI --pending-review <path> --expected-review-sha256 <sha>; legacy4.5guard не обходить, старуюочередьне резюмировать. Новыйclientprofilezai47-flash добавлен Root в replay/prefrontend/pacer15,158purePASS; productionCoreнеизменён.

Commit/push explicit кандидат+secret-scan, exact Native ownrouterstop→install pinnedHEAD(previous625..., addedroom_additions.py+selected_plan.py)→start ZAI manager scripts/after80_backend.py. Старый installer ещё нужно дополнить625allowlist/added2. Все actualactors завершены; pausedR4queue НЕ резюмировать. Затем новый GROUP04 natural actual на freshsession, driver текущийSHA7108bde01331ef50e080d8052b3e3202941609b1d863537b1be9167f1dfd9a70, r4recipe79acc092...,profilezai,min15spacing,max15models/160tools. Это новый модельный epoch. После PASS — selectedplan3 actual и дальнейший80HYBRID replay с честными case-oracles/лимитами.

80 исходных/697turns прочитаны (SHA81a7e59c65d41da8dfc6101fc0429c455c259561e44f6a9a79f0dc99323058ca); полного нового actual80 нет.106:71partial/35notexecuted, full-languagePASS нет; D25050dialogs/300turns только импорт. Official docs URLs27Oct черезweb tool недоступны, новые письменные ответы26Oct по ключам/среде UNKNOWN; ранее Q&A разрешал маркированныйDEMO/имитацию. Не объявлять это подтверждением новых правил.

Frontend: docs/FRONTEND_HANDOFF.md,FRONTEND_CONTRACT.md,HOTEL_MEDIA.md,SUPPLIER_COMPARISON.md,docs/frontend/openapi.json. Portable Docker APP_PORT8080 по умолчанию, личныхключей нет, формы/DEMO работают. Фронтенд здесь не разрабатывается.
