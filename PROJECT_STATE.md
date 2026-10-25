# O!Travel Agent — состояние на 26.10.2026

## Сейчас

Четыре направления: HARNESS-004, сохранение кода в GitHub, дополнительные бесплатные LLM API, требования из Q&A. Frontend делает сокомандник. Полные 100 диалогов из другого чата разбирать позже, после передачи владельцем.

Работающие 8081/8083 заморожены на время этого прогона. Разработка — E:/work/aegml; тесты — только otravel_test. Не синхронизировать /home/aegml/projects/otravel и не перезапускать app/Ollama. Изменения исходников не означают обновление стенда.

## Проверено в новом коде

- 1550 pytest PASS: docs/verification/full-suite-20261026-zai-final.xml. Реальные модели и туристические API в этом прогоне не вызывались.
- HARNESS-004-CONTEXT-MERGE-001: явное удаление отклонённого предложения и адресность подтверждения. Контролируемый API/PG repro на старом C-коде падает; новый код проходит. RS и остальные факты сохраняются, позднее «да» не принимает отклонённый город. Actual NLU replay ещё нужен.
- HARNESS-004-DATE-001: календарный slot понимает явные «выезд завтра», использует clock/TZ и проверяет порядок дат; при нескольких остановках не выбирает первую молча. Полный actual диалог открыт.
- Деньги: нормализация масштаба и относительных изменений; блокировка тарифов за единицу как общего бюджета. Дополнительно113 money-тестов проверяют «номер на ночь/сутки/день/неделю» и сохранение общего бюджета на длительность поездки. Сложные составные scope ещё не полностью квалифицированы.
- Журнал извлечения до/после guards, usage и rate-limit metadata. Usage сохраняется и после невалидного HTTP200 content/schema, включая отдельные попытки repair; API/PG regression PASS. При сетевой ошибке без счётчиков расход UNKNOWN; секреты и hidden reasoning не сохраняются. JSON schema уменьшена на 26–28% в байтах; это не измерение экономии токенов и не actual grammar PASS.
- Новый отдельный image sha256:4cea47c19b5f3714cfced757641d8242e1e992045056b57212b21a72a2270ed0: pip26.2.2 в обоих Python. PyPI/OSV: runtime29/global1/lock36 — 0 известных advisories, 0 пропусков. Он НЕ развёрнут. Старые 6 advisories pip25.0.1 и исходные отчёты сохранены. Это не аудит всей безопасности приложения/ОС.

## Работающий стенд и API

- 8081: Groq/qwen3.8-27b. Владелец подтвердил Free без карты. Один actual compound запрос — PASS ограниченных утверждений; затем пользователь получил 429. Причина и остаток квоты не установлены. Новые генерации Groq пока не делать.
- 8083: Ollama qwen3.5:4b, GPU, отдельная otravel_local_eval; внешние API-ключи исключены. Модель3.32GB Q4_K_M, runtime+archive около7.32GB на C. Это стенд конкретной 4B-модели, не оценка качества всех LLM.
- LiteAPI SANDBOX, Duffel TEST, SerpApi LIVE_INDICATIVE и Open-Meteo ранее дали actual responses. Полная живая многогородская групповая корзина и сравнение реальных поставщиков ещё не доказаны.
- OpenRouter: только бесплатные маршруты. Gemini ключа нет. Mistral в аккаунте требует Upgrade; его ключ не использован/не сохранён. Cloudflare: tokenverify200active, владелец подтвердил WorkersFree/no-card; ключ и AccountID сохранены. Новый адаптер разрешает одну Free-модель Llama3.3-70B,40/day и1/run,безfallback. Actual adapter проба: JSON валиден,6из7проверок,Стамбул пропущен; NOT_ALL_BOUND_CHECKS_PASS, не полный harness. Z.ai Standard key сохранён; одна tinyFreeGLM4.7Flash проба:200/ожидаемыйJSON/85tokens, только connectivity. Z.ai runtime-адаптер и отдельный persistent eval-пул реализованы: default40, явно до2000/day,1/run,безfallback. Отдельный8084/C-source/DBmanager подготовлен, actual запуск и пилот ещё впереди. Cohere onboarding зависает; ключа нет. NVIDIA пока без ключа. 8081/8083 не переключены.
- Ключи только в ignored native .env0600; не Git/ответы/фикстуры. SETUP-004: ранее присланные в чат ключи перевыпустить перед передачей. Не включать billing, реальные оплаты или бронирования.

## Приёмка и новые материалы

106: 71 сценарий имеют частичные typed/domain/adapter/PG проверки,35 не исполнены; полный actual106 NLU PASS=0. Старые NOT_ALL_PASS и UNKNOWN сохранены. HARNESS-004-SLOT-AGE-001 закрыт только относящимся actual replay; COMPOUND-001 и COUNTRY-TYPO-001 остаются открыты на полный диалог.

Старый D100/130 реплик — исследовательский набор, не 100 полных разговоров. Новый adaptive100 принадлежит другому чату: не редактировать, не стадить и не интерпретировать результаты сейчас. Runtime freeze: docs/verification/runtime-freeze-full-dialogues-20261025.json; source aggregate3837e5849bc9d71ace31b359dad2e89fc200605959fdf100a23503d9fc9f26bb.

D250 импортирован побайтно:50 диалогов/300 реплик; manifest249BOUND/48UNBOUND/3BLOCKED — статусы автора, не наши PASS. Выявлены12 статических проблем; оригиналы не исправлены и корпус не исполнялся.

Q&A прочитан полностью:60 вопросов/180 реплик; HTML — пересказ ASR, не окончательные письменные правила. QA25-OSS-01: проверка зависимостей выполнена в указанном scope, полная безопасность не закрыта. QA25-VIDEO-01 OPEN: нужен ролик/доступная ссылка. QA25-JUDGE-ACCESS-01: ожидается письменное уточнение ключей/ресурсов26.10. QA25-SUBMISSION-01 OPEN: репозиторий организаторов,29.10 до10:00МСК. QA25-EVAL-INTEGRITY-01 OPEN. Live/multicity/group требования владельца сохраняются.

## Следующий шаг

Checkpoints c767203/e3486e8 отправлены; c767203 уже merged владельцем в main/PR1. Ветка переименована по просьбе владельца в backend/harness-foundation без переписывания истории. Cloudflare/key-entry f679b1d отправлен, создан DraftPR3. Z.ai+отдельныйmanager прошли1550тестов; готовится отдельныйC-стенд8084 для100разговоров. Инструкция docs/LLM_ACCOUNTS.md опубликована. Скрытый ввод5новых credentialfields, API_TOKEN redaction и исключение из local Ollama env реализованы; ключи не попадают в Git. Следующие отдельные шаги: H004-DATE-TARGET-001 — сохранить предложенную дату до выбора остановки; CF-EXTRACTION-001 — разобраться с потерянным направлением, затем Z.ai runtime-adapter и одинаковые modelcases. После этого actual сравнение моделей и HARNESS-004, не объявляя controlled PASS полной NLU-приёмкой.

AEGYX INDEX81 PAUSE перед SOURCE3. AEGYX/Ubuntu24.04/Windows PostgreSQL/PEM/8080 не трогать; не wsl --shutdown/docker prune. Native C независим от SSD E; lifecycle только с identity guards.

CLOCK-OBS-001: первоначальный ZAI fullsuite1523PASS/1FAIL сохранён. PG показал snapshots21:22:06.882–.936 и последующие preview21:22:06.478/.493: clock inconsistency, механизмUNKNOWN. Только price-changeтест переведён на общийfixedawareclock, перваяpreviewтеперьтожеassert200; runtimeexpiryguardsнеослаблены. Module16PASS, новыйfull1550PASS. Diagnostic docs/verification/mixed-checkout-clock-diagnostic-20261026.json.

ZAI100 HANDOFF READY: localhost8084/debug, zai/glm-4.5-flash, отдельнаяotravel_zai_eval, C-source85160ef manifest a25ec3607131e26be4ea879c0166e4a0dc4010fef96c6c7281c4c0969852537d. Actual3turnpilot PASS_BOUND_CONVERSATION_ONLY:2LLM+1deterministicpause, origin/destination/adult/children/budget100k→80k/pause/readback сохранены. 4.7fullrequest45sTIMEOUT сохранён;4.5diagnostic10.25s/fullcalls13.44s+33.66s. Modelquality100NOT_RUN;2000/day engineering bound≠vendorquota,1/run,no fallback. Handoff docs/testing/ZAI_FULL100_HANDOFF.md; receipt docs/verification/zai-eval-ready-20261026.json. ТолькоэтотC-runtimeзаморожен; HARNESS004-DATE-TARGET-001 далееисправляетсянаEбезsync/deploy. Не анализироватьчужие100доownerhandoff. PR3 https://github.com/ostrovok-hackathon-2026/aegml/pull/3.
