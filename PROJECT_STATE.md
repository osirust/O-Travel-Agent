# O!Travel Agent — текущий статус, 25.10.2026

## Активная цель и ближайший шаг

Выполнять HARNESS001…008 из docs/research/harness-plan-review.md. Новый аудит/106oracle-proposals подтверждают приоритет: **HARNESS004, три диалога владельца → actual NLU106 → actual mixed flow001**. Не добавлять новые API/модели/таблицы/фреймворки вместо диалоговых исправлений. Финальный frontend делает сокомандник; минимальный debug-chat был отдельно запрошен владельцем и уже доступен.

Сейчас строгий operation-schema и contextual clarifications ВСТРОЕНЫ в рабочее дерево. Последний общий прогон **713 PASS** (`docs/verification/full-suite-20261025.xml`), включая controlled owner3 и slot-continuation11. Это НЕ713пользовательскихNLUслучаев. Ближайший шаг: syncC → безопасноrestartmainapp/localbackend → actualТОТЖЕ frozenowner3 наQwen и доступнойAPIмодели; затемповторитьlocal7case/11turn и развивать106oracles. Не менять freeze послеFAIL.

## Код и развёртывание — различать

- Canonical source E:/work/aegml, private repo ostrovok-hackathon-2026/aegml, ветка codex/backend-harness-foundation, Draft PR1. Последний ранее запушенный cb4db37; текущий большой checkpoint готовится после713PASS/secret-scan0matches. Личный CVmirror не настроен.
- Native build/runtime copy /home/aegml/projects/otravel в C:/WSL/AegML-Hackathon. Последние713изменения ещё нужно синхронизировать и перезапустить процессы: нельзя считать старые app8081/local8083 автоматически обновлёнными.
- Main Docker backend http://localhost:8081/debug, /health; standalone local backend http://localhost:8083/debug. Local process managed scripts/local_backend.py, useraegml, separateDBotravel_local_eval, noAPIkeys; PID175377 — исторический runtime snapshot, перед сигналомобязательнаidentityпроверка.

## Что проверено

- CanonicalTripState/whitelist operations, ID/group/room/date/budget checks, source policy versioned, compound edits/before→after.
- PostgreSQL: sessions/messages/runs/events/revisions/quotes/demo_orders, bearer tokenHASH, per-session queue/CAS/idempotency/oneactive-run. Remote40/day+2/run; localOllama500/day+1/run; durable cooldown, knownupstream10min, unknown/accountlimitsfailclosed. Никаких networkcallsподSQLlock.
- DEMOcomplex9people/2families/3roomsperstay/Kazan→Istanbul→Antalya→Kazan, demoquote/order/idempotency/staleconsent.
- Undo monotonicnewrevision, cancel отдельно, historicalfirstplan notbookable, partialhotelrestore typedfixture. Publiclegmodesetterне добавлен.
- MIXEDpureplanning/orchestrator/checkout16PGtests: exactbinding, fees/allocation/evidence, currencybasis, providerrefresh, sourceTTL, previousquotesrevoked, simulatedonlyorders.
- RealPGSIGKILL в НОВОМ временномComposeproject:503JSON(DBDNSfailurefixed),WAL recovery, trip/version/pause/token/order/idempotencyсохранились,partialintake0. OldFAILattempts сохранены. ОсновнойPGне убивался. Это не все распределённыеfaultoutcomes.
- DebugUIdesktop/mobile,DEMO/pause/resume/literalXSStext,0consoleerrors/0modelcalls. APIкнопка убрана. Swaggerdisableflagдлясдаваемогорежима ещё TODO; сессионнаяавторизациянеравнаглобальнойавторизациисайта.

## HARNESS004 — реальные дефекты и новые исправления

Владелец выявил country/city confusion, 'Стамбул?'→wrongorigin, rawdestination.city, 'я взрослый'loop/'1'invalidoperation. Initial frozenowner3=3FAIL, затем controlledowner3 PASS. Slot-continuationbaseline6FAIL/5PASS, послефиксов11PASS (нольдетей/ageID/post-searchrawdatefields).

- llm_contract.py:47purepayloadtests,5-fieldenvelope,typedoneOf/valuefields,existingtargetIDenumrequired,newIDsbounded. ProviderпередаётschemaвnativeOllamaformat иsystemprompt; serverjsonschema4.23валидируетответ; schemaне sharedmutable acrosssessions. Countryнеcityenum.
- clarifications.py:90purecases, pendingfield/target/context→shortanswerops, independentorigin/destinationcountries, hypothetical/countrychangeconfirmation, uniquefuzzyBelгрод→confirmБелград. unknown/placeackнеforcedhandled.
- Geography28countries/9knowncities vs7searchcities; странаSerbiaRSраспознана, невыдумываемсербскийinventory.
- Humanquestions sanitize model+typed/search paths, filledslotнеповторяется, выбраныйnextchild/стейимеетточныйtargetID. context-drivenanswerне называетсяrealLLMcall.
- money.py54tests: scalar/relativeRUB groundingдляужеимеющегосяsetbudget, ambiguous/historical/pernightнеугадываются. updateageнепереставляетgroupmembers.

ActualmodelПРОВЕРКИОТДЕЛЬНО: initialAPI10attempts NOT_ALL_PASS; repairedAPI12attemptsblockedNvidia5xx+Gemma429; alternateAPI11attemptsXing429/Dotsresponses+budget/groupfindings. OldlocalQwen7case/11actualcalls=5casePASS/2FAIL(targetNonehotel/age) ДОновойсхемы. CountryregressionactualDots2.7s иQwen20.5s PASS. Actualowner3после713integrationЕЩЁНЕВЫПОЛНЕНЫ.

## 106 исходных случаев и новые оракулы

Все106/129messages прочитаны;71sourceIDимеетчастичныйexecutedassertion,35ещёнеисполнены,fulloriginal106NLUPASS0. corpus-maprev3 и separateevidence — fixtures/evaluation,docs/verification/corpus*. Не суммироватьpartialкакfullE2E.

Новое25октября: aegml_acceptance_oracles_106.md/jsonl + auditTXTскопированыbyteexactвdocs/research/sources/manifest. Original106fields106/106совпали0diff;flagsестьу52/106(79предложений),setupnull90/106. Этоproposal,нетполныхexpectedstates. oracle_bindings106records/185checks покаUNBOUND/NOT_RUN, форматfailclosed. План docs/testing/ACCEPTANCE_PLAN.md; конкретныеclock/seed/init/fault/slot/consentорaкулыещёнужны. Originalsource и2LLMfreezeSHA сохранять.

## LLM/поставщики

- OpenRouterconfigured, только:free иmax_price0; cap$0.01разрешёнвладельцем,кредиты/платныемодели/платежине покупались. NativecurrentprofileDotsprimary/Xingbackup. Dotsотвечал;Xing/Gemmaupstreampool429,Nvidiaпозже5xx. Geminiнеподключён.
- LiteAPIsandbox catalog/rates actualHTTP200,586initialoffers;9people/3roomresponseфактыoccupancyNumber/counts/ages/BI/taxesполучены. provider_facts83puretests+adapterintegration; sourceSANDBOX,availabilityFalse. Полныйactualmixedsearch/refresh/checkoutTODO. Prebookещёне вызывался,обычныйrepriceповторяетratesearch.
- Duffeltestactual9offers,neverlive; SerpFreeactual20hotel/4flightindicative; OpenMeteo7dayforecast. Hotelbeds/RateHawk/Travelpayoutsтолькоguardedfixtures,ключей/actualprobesнет.
- LocalOllama0.40.11/qwen3.5:4b(4.2B,Q4_K_M),Cnative,officialSHA,12711434,noCloud,parallel1,ctx16k,GPU5070Ti16GB. Modelmetadata3.324GB,totalruntime+archive+models≈7.3GB. Coldmicro48.69s,warmlocal7case1.1–2s;неSLA. LocalHTTP105s/1runвlease120. ВесаНЕвSQL/НЕвGit. MainAPIиlocalbenchнезависимы.

## Сохранённые ограничения/IDs

OTR001actualNLUOPEN;002primaryselected/CVmirrorOPEN;003foundationchecked/productOPEN;004publicationrightsOPEN;005sandboxindicativepartial/realbookableOPEN;006numericalboundsOPEN;007comparableactualsuppliersOPEN. HARNESSprimaryblocksобновляютсявharness-plan-review.md. CORPUS/REVIEWoriginalIDs не закрывать несвязаннымPASS.

SETUP004: триtravelkeyвладелецприслалвчат,перевыпуск/заменавсёещёOPEN;ключи не повторять/не коммитить. JSON/env/логипроверятьsecret-scanпередпушем. Keyvaluesonlynativeignored.env0600. SQLданные/LLMevidenceразличатьотсамоймодели.

ENV003: владелецподтвердилSSD Edisconnect/reconnect; drvfsErrno19accessrestoredoverlaymountсSHAWindows/WSL,безforceumount/shutdown. Extraalias/stalelayerбылиbusy;не делатьслучайныйkill. SourceEcanonical, runtimeCstable. AEGYX INDEX81pausedпередSOURCE3; source не запускать, Ubuntu24.04/WindowsPG/PEM не трогать. НашWSLkeepermetadata %LOCALAPPDATA%/OTravel/setup/wsl-keeper.json,сигналытолькопослепроверкиPID/start/argv.
