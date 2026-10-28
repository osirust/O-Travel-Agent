# O!Travel Agent — 28 октября, 07:32 МСК

Цель: передать backend для frontend/видео. Frontend делает сокомандник; здесь код backend и доказательства проверок.

## Последний код

R20: **7463 PASS / 0 fail / 0 skip**,451,01с; source `9bd2f7d752c96d95849c433031b9481076294150464e6c5a12e371c2f4d89589`,87package/301testfiles. Исходники/тесты не менялись. [Fullproof](docs/verification/prefrontend-r20-checkpoint-20261028.json). Сейчас R20 сохраняется в Git; точный Docker и native CF проверки ещё впереди. До их завершения source/test не менять.

R19 `3f4f871`/7384PASS опубликован в обеих private-ветках backend/harness-foundation с явным rollout HOLD. В последнем actualR17 обнаружен readonly079, поэтому R19 не разворачивался. R20 устранил обе границы readonly (префикс «теперь» и второй вопрос о номере), а также перехват всех сообщений незавершённым вопросом о числе семей080. Exact malicious modelbatch теперь rejected/state unchanged; valid selector отвечает по сохранённым данным. Независимые470k/lowest_total сохраняются при pendinggroup без поиска и выдумывания групп. RED/targetedPG/full сохранены отдельно.

CanonicalID readonlyfinding: HARNESS-004-READONLY-DISCOURSE-PREFIX-MUTATION-079-001. Прежняя временная метка HARNESS-004-READONLY-NOW-PREFIX-079-001 — alias того же finding, не новая проблема. GroupgateID: HARNESS-004-GROUP-PENDING-SHORTCUT-SWALLOW-080-001.

## Текущий стенд и ближайший шаг

http://localhost:8084/debug покаR18/4645c24/Gitpackage4e383ff6, Groq120B/v3low/HYBRID/global. ExactR18Docker19 и native31checks/0model прошли. CF-контроллер и клиент подготовлены `.local/drafts/cf-live8084-r20-20261028/`,9fakePASS/RuffPASS, pinsPENDING/неактивирован. Approved40/day/perrun1/local0, CF120 и sandboxtravelkeys only, Master не меняется. Перед switch:0active→guardedSTOPownworldGroq→installточныйGitR20→CFstartup0inference→неболее3actualCF на2OWNsynthetic session cases079/080 с контролируемыми exactpending fixtures. Sessionseed сверяетownerhash/version0/пустыеmessages-runs-quotes-orders/CAS; чужие сессии не править.0supplier — ожидаемый результат с аудитом событий, не установленный networkpacketfence.

## Реальные API и языковой корпус

R18 freshDB/typed:22supplierHTTP(all200/201),2отеля/4номера/3рейса/4pax/2exactphotos/ЦБFX/2preview→old409→newconfirm/retry→ONEBOOKED_DEMO.376165,80RUB<1180000. Отели/рейсыSANDBOX,transfersDEMO,LLM0,realbooking/payment0. [Proof](docs/verification/r18-typed-real-travel-checkout-20261028.json). Ошибки тестовых oracles(version/statusquote) и read-only corrections сохранены; не NLU PASS.

80исходныхдиалогов/697inputs:697uniqueaccepted,692terminal=657completed+35failed,5oldqueued(012T7–9/060T8–9)неexecuted.0missing/duplicate/textSHAmismatch. Новые78terminal наR17:73completed/5failed,72modelHTTP/113supplierHTTP. Все ответы прочитаны; состояния/версии/модели и исходныеFAIL сохранены. [Census](docs/verification/actual80-unique-input-coverage-20261028.json), [lastmanual](docs/verification/after80-final-manual-079-080-20261028.json). Это не uniform semantic80/106PASS: остаются старые языковые пропуски/уточнения и недостаточные oracles.

CF120 дал1actualуспешный endpoint/schema/roomextraction;4другихfinalхода были0model(contextual/canonical), это не5modelPASS. GroqTPD429, Zai47RATE_LIMITED, Routerвыделенный8cap исчерпан; accountquota не сбрасывалась. НоваяпроверкаCFпослеR20ещёнезапускалась. ЛокальнаяQwen0/paid0. APIадаптеры LiteAPI/Duffel/SerpApi/Open-Meteo/ЦБ готовы; разныеsourcekindне смешиваютсявLIVE_BOOKABLE. FreshOSV29runtimePythonверсий:0knownadvisories,неполныйOS/sourceaudit.

## Внешние gates сдачи

Jury-onlyLLMkey и secrets.env.age отсутствуют (fresh status-only check безчтенияenv). Официальныеrecipient/age1.3.2/helper/emptyexample готовы; Chrome URL-проверка остановила создание отдельного ключа. Не обходитьUIstop,неэкспортироватьMaster. GigaOAuth200/inference0, API-TLStimeout; VPN/Happ/routes/MTU/globaltrustнеизменны, Directapi.giga.chatтолькопредложен.

После R20runtime: frontend+exactfinalarchive, publicincognito-video+READMElink, выделенныйjuryenv/binaryage, finalofficialmain/submissiontag. [Матрица](docs/testing/SUBMISSION_REQUIREMENTS_MATRIX_20261028.md). Ещё OPEN: произвольные family/census/date-language сценарии; controlled7463 не закрывает все неизвестные запросы жюри.

## Изоляция

E:/work/aegml; native/Docker/PG в C-backed WSL AegML-Hackathon. AEGYX R16/INDEX81 owner-paused; A524/Ubuntu24.04/WindowsPG/PEM/httpd8080 не трогать. NoWSLshutdown/prune/чужихvolumes. Ключи ignored0600env,неGit/ответы. Не создавать настоящуюбронь/оплату.
