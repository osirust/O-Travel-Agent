# O!Travel Agent —27октября13:45

Цель: довести backend/Harness перед frontend сокомандника. Основная матрица docs/research/harness-plan-review.md. Настройка, Mock, typed и actual различаются; full80/106 не закрыты.

## Текущий стенд и пауза для проверки владельца

На http://localhost:8084/debug работает Groq/openai/gpt-oss-120b, strictv2, low,1attempt/run, HYBRID/LiteAPI sandbox/Duffel test/SerpApi/CBRFX. Код701da371fff15535ce2a2449e7b8a461f974482b, Nativepackage d7d715c1ec628e34f0fcb863cec71d49dfbcdca80346c66b89ec0093a21d5d1a. Source /home/aegml/projects/otravel-after80-20261026, DBotravel_zai_eval, C-backedWSL AegML-Hackathon.

Владелец получил ссылку13:43 и проверяет новый одиночный диалог. ОБЕЩАНО: не менять8084/source/профиль/егоDB и не отправлять параллельные rootGroqвызовы до завершения его теста. E-source и controlledtests можно продолжать. Последний actualactor завершён, фонового modelrun нет.

## Проверки текущего этапа

- На5f4d0d8 общий набор5385PASS/0fail/0skip,210.578с JUnit. .local/evaluation/after80-20261027/full-groqv2-giga-greeting-final.xml; docs/verification/groq-v2-gigachat-core-20261027.json. После этого узкий countrywish-hook701 и изоляция одного Gigaтеста проверены155relatedPASS13.28с, не называются новым полным5385. До этого216 имел4987PASS; результаты не суммируются.
- Actual701 fresh2turns: «привет»→приветствие0models; «хочу в Сербию»→вопрос Белград/Нови-Сад0models, tripunchanged. Отдельный naturalcity запрос→1Groqcall200 и уточнение гражданства перед поиском;0tools/0plans. Это не весь hotel/flightflow. docs/verification/groq-v2-retest-20261027.json, private /home/aegml/checks/groq-v2-retest-20261027-104303/report.json.
- Originalactual5f4 FAIL сохранён: тот же countrywish модель отправила в informationalASK, pendingnull,3373input+149output/1215ms. Исправлен узким exactfullmatchfreshcountrywish, не заявлен рост качества самоймодели. Compoundfacts/hypotheses/questions/savedtrip остаются обычному пути. Finding HARNESS-004-LITERAL-COUNTRY-WISH-001, docs/verification/literal-country-wish-20261027.json.0автовыборгорода.
- OwnerGroq42913:09 был именно TPM8000/used3944/request6132/Retry16, не daily. docs/verification/owner-groq-429-20261027.json. Publicfailure теперь показывает только boundednumericwait. Actualv2 ответ без429 на перечисленных попытках не гарантирует отсутствие будущих лимитов.
- Giga Personal: AuthKey+Freemium сохранены, модель GigaChat-3-Ultra(UIUltra3.5). Отдельный8085 localbackend запущен5f4/source /home/aegml/projects/otravel-gigachat-20261027, DBotravel_gigachat_eval, package eed0ad2dd123c4619f0a4c2681f0c501a5e6a0d8c733825958f5db356dc1bef7. health/debug200. Install/start0OAuth/models/generation/travel; всеDBcounts0. NGWverifiedTLSда; api.giga.chat443 TCPconnected/TLSstall Win+WSL. Владелец пока не может выключитьVPN. Key/models/balance/реальные ответы НЕ проверены;8085не выдаватьза рабочуюSberinference. CA936a43fea6e8e525bcc0f81acd9c3d21b4fc4b9b68acea7906d698005afc6504 perclient0444. OnlyMasterCApath добавлен атомарно, остальныеopaqueполя/uid0600сохранены. docs/verification/gigachat-install-20261027.json.
- GigaMock/PG подтверждают OAuthcache/RAM/serialflow/strictcanonical/budget/idempotency/no-secret-echo; этонеactualAPI. Publictokenredaction отделена отlog-onlyregistry, standaloneisolatedtoken-clean тестPASS. Stagedscan9configuredsecrets+encodings0matches.

## Сохранённые открытые границы

ActualGPT396 GEO01 15/15PASS; Groupinitial/даты/бюджетPASS, compoundT4HTTP413. R6на216 подтвердилTPM8000/request11053,2models/0tools/STOPPED,tripunchanged; /home/aegml/evaluation-runs-prefrontend/prefrontend-20261027-091740-90f75c77/report.json. Новый полный Group/R6/v2 ещё нужен. Без modelконкуренции с владельцем.

ТурAPI: LiteAPI/DuffelSANDBOX, SerpApiLIVE_INDICATIVE, CBRсправочныйFX, Open-Meteo. Ранееactualtypedgroup создала1BOOKED_DEMO+samekeyretry,0supplierbook/pay. Новыйfreshgroup5SANDBOX+2DEMO/9coverage: второйprebookотказ2001→OFFER_NOT_CONFIRMED409/refresh_required;0автозамен/догадок. Originalolder409causeUNKNOWN сохранён.10exacthotelIDphotos проверены. Полныйnaturalliveflow и эквивалентность разныхреальныхsupplierтарифов открыты.

Original80/697turns SHA81a7e59c65d41da8dfc6101fc0429c455c259561e44f6a9a79f0dc99323058ca. V3oracles781=194old+587new,80cases/657partialboundturns/fullybound0; full80actualнезавершён.106:71partial/35notexecuted, full-languagePASS0. D250импорт. OldFAIL/UNKNOWN/input/oracle hashes не переписывать. ColdDocker396newproject18089 DEMO/restart/idemPASS,0keys/models/vendor; новыеV2/GigaегоPASS не наследуют.

## Следующий шаг

1. Дождаться результата одиночного диалога владельца;8084заморожен. Можно доделывать controlled/backendproofs безегоmodelквоты.
2. После проверки владельца: целевой actualR6group сv2+travelAPI, затем80V3 в oneactor слимитами и case-specificoracles. Сначала гражданство/обязательные факты, не додумывать вместо пользователя.
3. После разрешённого восстановления сети — GigaOAuth/models/balance и реальные comparablecases; TLSverificationобязательна. Никакогоverifyfalse/paidfallback/VPNобхода.
4. Согласовать primaryHARNESSmatrix/PR3draft и опубликованные checkpoint/evidence в обоих private remotes. Не gitadd-A: чужойdirtyzai-eval-backend и olduntrackedистории не включать.

## Изоляция

AEGYXR16INDEX81 owner-paused; чужойA524/Ubuntu24.04/WindowsPG/PEM/httpd8080 не трогать. No WSLshutdown/dockerprune/volumedelete/globalkill. LocalQweninference0;paid0;supplierbook/pay0. Master /home/aegml/projects/otravel/.env0600, значения не выводить. Только ownedcontrollers/PIDs. Source E:/work/aegml, runtime C-backedWSL. Длинная старая история сохранена .local/state-snapshots и Git.
