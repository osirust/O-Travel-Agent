# O!Travel Agent — короткое состояние,25.10.2026

## Цель и рабочий режим

HARNESS001…008 продолжаются. Владелец уточнил: в другом чате он проверяет **100 отдельных полноценных многошаговых разговоров**, адаптируя следующий ход к фактическому ответу; один разговор может иметь несколько ошибок. Старый D100 из100сценариев/130реплик этому требованию НЕ соответствует. Его55rawPASS/45FAIL — только историческое исследование двух runtime-фаз, не приёмка100полных диалогов и не оценкаGroq. Исходники/ошибки/UNKNOWN058 сохранены.

**Работающие8081/8083 сохранять стабильными на время нового прогона.** Разработка только E:/work/aegml, offline/controlled проверки в otravel_test; не sync_to_wsl и не restart app/Ollama до согласованной новой границы. Native runtimeC:/WSL/AegML-Hackathon,/home/aegml/projects/otravel. Source changes наE не означают изменение загруженногоC-процесса.

Ближайший этап: воспроизвести и исправить адресный pending-context (подтверждённыйD100-036/3 с0LLM), даты/отрицания и деньги, добавить безопасные extraction/usage-traces и уменьшить стоимость prompt. Далее тот же вход/initial/pending/history/clock: контролируемый правильныйModelResult против реальнойAPIмодели; source/raw/guards/commit/reply учитывать отдельно. Не списывать все45FAIL на4B. Код/typed PASS не заменяет actual NLU.

## Подтверждено и ограничения

- **1070pytestPASS**,docs/verification/full-suite-20261025-groq-final.xml. Это проверки кода, не1070NLUдиалогов.
- 8081: **Groq/qwen3.8-27b**,Free/no-card подтверждён владельцем; ключ толькоnative.env0600, backup не выбирается. MetadataGET200. Одна actualcompoundB1→PASS_BOUND_SUBSET_ONLY/1call/1775ms (Москва,Белград,adult1,nochildren,earlymorning сохранены). Это не полныйDoD. Следующий пользовательский«привет»→429unknown,безсохранённогоRetry-After; причина/остатокvendorquotaне установлены.19:06UTCвнашемruntimeledger2Groqattempts,1response+1RATE_LIMITED. Новые генерацииGroq пока не делать.
- 8083: **Ollama/qwen3.5:4b**,GPU,отдельнаяotravel_local_eval,APIkeys unset. Модель наC,3.32GBQ4_K_M, runtime+archive~7.32GB;127.0.0.1:11434,cloud off.500calls/day/1run engineeringcap,неAPIтокены.
- Groq40/day отдельныйпул; Gemini/OpenRouter прежнийобщий40/day,он25.10исчерпан. Remote≤2/run,Groqgateway≤1/run;trustedGroq429Retry-After1..86400сек сохраняется,безавторетрая. Повышениеплатныхтарифов/платежей/заказов0.
- LiteAPI SANDBOX,Duffel TEST,SerpApi LIVE_INDICATIVE,Open-Meteo имелиactualresponses; это неготоваяполнаяреальнаякорзина. Hotelbeds/RateHawk/Travelpayouts безключей/actualqualification. HARNESS001actualgroupflow иOTR007suppliercomparisonOPEN.
- Mistral: владелецприслалключ,ноегоFreeкабинеттребуетUpgradeдляAPI. Ключнеиспользован/несохранён,платныйпланневключён. Geminiключа нет,доступвладельцанедоступен.
- DEMOсложная9person/2family/3rooms/2stays, revisions/CAS/queue/idempotentquote/order/undo/cancel/pause и16mixedPG/APIchecksпроверены. Single-nodePGSIGKILL→503JSON→WALrecoveryпроверенотдельно,неallfaults.
- DebugUI desktop/mobile иactualAPIdate-slot26.10→«завтра»PASS0LLM/0consoleerrors,карточкиисточникиXSSliteral. Swaggerdisableflag/globalexternalauthTODO. Финальныйfrontend делаетсокомандник.

## Корпуса и диалоговые findings

Оригинал106/129messagesнеизменён:71executedpartialtyped/domain/adapter/PG,35NOT_EXECUTED;полныхactual106NLUPASS0. Pilot4actualQwen:NLU01/09PASS_BOUND,NLU03/06FAIL_BOUND(Сочиошибочноorigin/100к→10к;Стамбул/120тыс потеряны),все4fullcaseBLOCKED_ORACLE.106bindings/185checksUNBOUND. Original/newsourceSHA —docs/research/sources/manifest.json.

Owner3localраньше3/3диалога,8turns(3LLM+5contextslots)PASS;remoteNOT_ALL_PASS(Dots timeout/Xing error). Age-slotHARNESS004-SLOT-AGE001relatedPASS:child1=4,child2=7,exactfixture;oldFAILсохранены. Development7послеagefixPASSисторический;последнийпослеинтеграции6PASS/1FAIL(придуманadult). Новыйowner11/derivedcompound на4B NOT_ALL_BOUND_CHECKS_PASS,sourceevidence E:/work/tmp/otravel-dialogue-regressions/owner11-compound-v3-20261025-a.json; исходныеv1/v2иv3erratumнепереписывать.

HARNESS004-CONTEXT-MERGE001 OPEN: D100036/3contextual/0LLM,«нет»показываетRSнооставляетTRproposal/confirmation;актуальныйmergeкодподозреваем,нужентотжеконтролируемыйPGrepro. HARNESS004-DATE001 OPENFULLDIALOGUE: bare-targetdatehelper47pure+PG/browserPASS,ноD100051«Выездзавтра»безpendingнепокрыт. HARNESS004-COMPOUND001 OPENFULL:singleGroqB1PASSнеполнаяцепочка. COUNTRY-TYPO001policyv2:strongRS+bareБелгород→visibleБелград,явнаяRUнеalias;каталог7нерасширен.

## Материалы/следующий обмен

По прямому указанию владельца отправлен запрос в существующийChatGPTчат6adc7653-72c4-83eb-9550-c61a36268e73:официальныйAPIresearch+≥250userturns/50связныхдиалоговсоракуламивJSONL/MD. Доставка usermessagee9e4ac27…подтверждена. **Владелец сампришлётответ;чатдальшенеопрашивать.** D250отдельноот106,старогоD100и нового100adaptive. ResearchfreeLLM —docs/research/free-llm-options-20261025.md; ZAI/CF/NVIDIA/Gigaусловиядокументальноисследованы,новыхключей/actualprobesнет.

SETUP004OPEN: ранее3travelkeyи2новыхключавставленывчат;неповторять/неGit. ИспользованныйGroqтолькоnative.env,неиспользованныйMistralнесохранён;перевыпускпередпередачей. Передpush secret-scan. Repo privateostrovok-hackathon-2026/aegml,branchcodex/backend-harness-foundation,DraftPR1;текущийcheckpointпосле1070готовится. АртефактыдругогочатаD100не изменять/нестадитьвслепую.

AEGYX INDEX81PAUSEпередSOURCE3. Не трогатьAEGYX/Ubuntu24.04/WindowsPG/PEM/8080,не wsl--shutdown/dockerprune. ENV003SSD Edisconnect/reconnectподтверждён,drvfsвосстановленoverlayбезforce;C-runtimeотдельно. LifecyclePIDтолькочерезscripts/local_backend.py сidentityguards.
