# O!Travel Agent — состояние на26.10.2026

## Текущая цель

Улучшать backend/harness по плану и Q&A. Frontend делает сокомандник. Владелец начал100полныхадаптивныхдиалоговвдругомчате; результаты разбирать после его передачи.

## Замороженные стенды

- **8084 — zai/glm-4.5-flash**: отдельная C-копия `/home/aegml/projects/otravel-zai-eval`, DB `otravel_zai_eval`. API source commit85160effde07ff891295ba437754b328ebdf258a; evalprofile330a2b7. Source manifest `a25ec3607131e26be4ea879c0166e4a0dc4010fef96c6c7281c4c0969852537d`, startup/currentmatch подтверждён. Владелец начал100: не менять код/модель/ключи/БД/порт, не restart/sync, не делать своиZAIгенерации.
- 8084 actual3turnpilot:2LLM+1deterministicpause PASS ограниченныхassertions, маршрут/люди/бюджет100k→80k/пауза/повторныйGET сохранены. LLM13.44s/33.66s.4.7fullrequest45sTIMEOUT сохранён;4.5directdiagnostic10.25s. Это не полнаяприёмка.
- Лимит8084:2000/day — engineeringbound, не vendorquota;1/run,no fallback. Ключ уже на сервере, тестовому чату не передавать. Промпт: docs/testing/ZAI_FULL100_HANDOFF.md.
- Старые8081(Groq) и8083(Ollamaqwen3.5:4b) также не трогать. СтарыйnativeC-source `/home/aegml/projects/otravel`, manifest3837e5849bc9d71ace31b359dad2e89fc200605959fdf100a23503d9fc9f26bb. Разработка только E:/work/aegml, проверки толькоotravel_test.

## Что проверено в E

- **1844pytestPASS**, docs/verification/full-suite-20261026-release.xml. HTTPмоделей/поставщиков в нём mock; это не1601NLUдиалог.
- H004-CONTEXT-MERGE-001: отклонённыйгород очищается, подтверждение адресовано своемуslot; controlledAPI/PGPASS, actualполныйreplayоткрыт.
- **H004-DATE-TARGET-001 CLOSED_CONTROLLED_HELPER_API_PG_SCOPE**: absolute/version/trip/clock/candidateIDsdateproposal, точныйcity/ordinal/ID, stale/ambiguous/malformed/overlapASK, cleanup вsamefinishCAS.304pure+35relatedPG/ASGI(8новых), независимыйreview5counterexamplesPASS. Сменаorigin/datefield/target не перехватываетсястарымproposal; невернаякалендарнаядатасохраняетtarget. Compoundtarget+другаяправка покаASK/noop. ActualNLUнепроверен; в8084НЕдеплоить.
- Money scope:113тестов, стоимостьномера «на ночь/сутки/день/неделю» не становитсяwholetripbudget; общийбюджетна длительность сохраняется. Сложныеcompoundscopes ещёнеполнаяприёмка.
- Usage записывается и при невалидномHTTP200content/schema; ContextVar/reset/PGevent guards проверены. Сетевыеошибкибезvendorcounts=UNKNOWN. TEST-LEDGER-001: fakeusage-тестыизолированывtestadapterbucketсhighfixturelimit, productioncapsнеослаблены, rowsнесбрасывались.
- Compose теперьпередаёт7новыхZAI/CFconfigfields. Syntheticcomposeconfig7PASS/loopback. Настоящийcleanstartбезличныхключей ещёпредстоит.

## API, ключи и безопасность

- Z.aiStandard: FreeGLM4.7/4.5; actualключработает, runtimeна8084выше. Cloudflaretokenactive/WorkersFreeбезкартыподтверждён, адаптерLlama3.3-70Bготов; actual6/7checks, Стамбулпропущен(H004-CF-EXTRACTION-001OPEN). Схемаразрешаетгородбездат; этонеconfig/keyошибка.
- Groq:1actualcompoundsubsetPASS, затем429unknown; OpenRouterGemmaтоже429. Ихисходныеошибкисохранены. Geminiключанет,MistralаккаунттребуетUpgrade(ключнеиспользован). Cohereonboardingзависает/ключанет; NVIDIAключанет.
- LiteAPISANDBOX,DuffelTEST,SerpApiLIVE_INDICATIVE,Open-Meteo ранееactualответили. Полнаяactualmulticitygroupкорзина/сравнениереальныхпоставщиков(HARNESS001)открыты; платежи/настоящиебронированияне выполняются.
- Ключи толькоignorednative.env0600; новыйhiddenstdinввод,API_TOKENredaction иcleanlocalenvпроверены. SETUP004: присланныевчатключиперевыпуститьпосле100/передпередачей; сейчасfreezeне нарушать. Передpushstagedsecret-scan.
- Dependencyaudit: separateimage4cea47c…/pip26.2.2,PyPI+OSVruntime29/global1/lock36=0knownadvisories/0skips. Неfullsecurity/OS/sourceаудит. Старыйliveimagee3…/6pipadvisoriesсохранёнбезrestart.

## Корпуса и требования

106:71частичноtyped/domain/adapter/PG,35неисполнены; полныйactual106NLUPASS0. D250импортирован50диалогов/300реплик; авторскиеBOUNDнеравнынашимPASS,12staticissues,неисполнялся. СтарыйD100/130репликнеравен100полнымразговорам. Чужиеadaptive100неправить/нестадить/неанализироватьдоownerhandoff.

Прочитаны60QA/180реплик ицитатаMITsecurity. HTML—пересказASR; QA25-OSS-01частичныйdependencycheck,VIDEO-01нуженролик/ссылка,JUDGE-ACCESS-01ждётписьменныхключей/ресурсов26.10,SUBMISSION-01сдача29.10до10:00МСКвrepoорганизаторов,EVAL-INTEGRITY-01проверкапередсдачей. Владелецсохраняетlive/multicity/groupтребования, конкурсныйминимумодингородихнеотменяет.

CLOCK-OBS-001: initialZAI1523PASS/1FAILсохранён; PGsources21:22:06.882–.936, последующиеpreview06.478/.493. Clockinconsistency,механизмUNKNOWN. Толькоpricechangeтестпереведённаfixedawareclock; runtimeexpiryguardsнеослаблены. Новыйfull1601PASSнеобъявляетчасыустойчивыми.

## Ближайший шаг

СохранитьDate/Composecheckpointвbackend/harness-foundation(PR3), затем новыйизолированныйDockerproject/image/PGvolume8085 иDEMO-проверкачистогозапуска. ИсследованырезервныеCFGemma/Nemotron, контрактнуждаетсявмалыхactualпробах;8084непереключать. ДальшеHARNESS001actualtripflowиоставшиесяH004сложныеправки;100разобратьпослезавершения.

AEGYX INDEX81 PAUSE передSOURCE3. AEGYX/Ubuntu24.04/WindowsPostgreSQL/PEM/8080не трогать. Не wsl--shutdown/dockerprune/удалениеvolumes. SSD Eотключался; всеактивныестендынаC. LifecycleтолькосPIDidentityguards.

OWNER009 RESPONSE FIX: только прямо присланныйпримерпрочитанread-only,непрочие100. RawLLMпонялаответыпосоставу/корзине,ноharnessзаменялтекстplanssummary;первыйrawпотерялКазаньвops. НаE route_intakeдополняеттолькоодинявныйначальныймаршрутбездат/номеров,replyformatterсохраняетnon-opplans/quotes/pending,каноническипоказываетparty/source/cart,hypotheticalнепересчитывает,actualedits+questionобеимлогикамработают. suggested_actionтолькоактуальныйread-onlypreview,неquote/order. Точныеcachedturn6–9среальнойrevision5иотдельныйturn1прошливотрavel_test,новыхLLM0. Full1844PASS после5выявленныхregressions(1821/5FAILсохранён);последняяliteral'детейпоканебудет'поправка23targetPASS. Поддержанныеклассынеравныобщейгенерациивсехответов/fullNLU.

CFBACKUP: Gemma/NemotronwireprofilesрасширенывE;6actualHTTP/3fixedcasesкаждой. Gemma2/3boundedPASS,Нemotron3/3;budgetguardисправилGemmaraw98k→80k,ноонаupdatechild-2вместоновогоребёнка(H004-CF-ADD-CHILD-001OPEN). ZAIcalls0/8084неизменён. scripts/evaluate_cf_models.pyпоумолчаниюневызываетмодель,explicit--run≤6calls,newoutputнепереписываетold.

CLEANSTART:cb0eadcархив→freshDockerPGбез18личныхkeys/9flagsfalse. Attempt1FAILискусственнаяinternalсеть; freshordinarybridgeattempt2PASS(13checks,DEMOorder+app/dbrestart), freshattempt3PASSточныйup--build--wait(11checks,DEMO→quote→1order/idempotency).0model/external/toolcalls,old808xunchanged;owncontainersdownбез-v,volumes/imagespreserved. READMEссылаетсяdocs/testing/CLEAN_START.md. CVрепоosirust/O-Travel-Agentпроверенprivate/writable,remote cv; cb0eadcветкатудаскопированабезmain/force, последующийreleaseнужнопушитьтакжеcv.

CURRENTCHECKPOINT: полный1844PASS послеprovided009responsefix(1821/5FAILoldсохранён); дополнительно23party/slotchecksпослеliteralnegativeвариантов. Новыеroute_intake/conversationalhelpers,exactcached009fixtures+PGreplay;неполный100анализ. СценарийфикситобщийпотокNOOP/интегрированногоответа:данные+вопрос,релевантныеunansweredbindings,safeCartAction,NoOldPlanResurrection. Genericunsupportedquestions/erroneousGemmaaddchildremainOPEN. SourceC85160efmodel4.5НЕизменялся.

NEXT:commit/pushcurrentverifiedpacketвoriginИcvprivatebackend/harness-foundation,PR3обновить. ЗатемHARNESS001actualmixedgroupqualificationпоdocs/research/actual-group-flow-plan-20261026.md:fixture2семьи/2stays/4rooms/3legs/2transfers,≤12HTTP,0LLM; transferDEMO/RUBиactualcurrency/fees/capacityfactsмогутдатьPARTIAL/FAIL. DuffelhistoricalEUR(неGBP), request.currencyнепередаётся;LiteAPIrefreshнеprebook/ищетстарыйnativeofferid. Негарантироватьsuccess,непереклеиватьвалюты,неослаблятьevidenceguards. Всеactualзадачи вдругомизолированномPGбезизмененияcluster/БД8084.
