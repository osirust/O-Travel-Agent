# O!Travel Agent — 28 октября, 20:17 МСК

## Текущая цель

До 19:30 владелец разрешил только проверки; они завершены. До 22:00 — исправления подтверждённых ошибок, относящиеся проверки, итоговая публикация и передача для видео. Новый 40/80 не запускать. R7 закончен с 7900 PASS / 26 FAIL; все26 исправлены и прошли связанные проверки. Кандидат R27 заморожен, общий R8 начат20:15 (exec68812), terminal ещё нет. Код/тесты не менять. Проверки реальных API и чистого Docker разрешены параллельно на том же Git/package, runtime переключать только после результатов.

## Что проверено

- Финальные 40: все 437 реплик прочитаны; 106 исходных ID / 129 оригинальных реплик плюс 308 продолжений. 415 completed / 22 failed, 352 настоящих вызова Z.ai и 313 прочих HTTP. Это НЕ семантический PASS всех диалогов; 16 fault-setup остались BLOCKED. Источник: `/home/aegml/.local/evaluation/final40-adaptive-20261028-142306-fbeb5460/`. Сводка: `docs/verification/final40-diagnostic-census-20261028.json`.
- R26 код `91e6ee4`: общий R6 — 7664 PASS; чистый Docker — 30 проверок, внешний неизвестный model ID / HTTP — 25 проверок. Реальные Serp-фотографии отелей, LiteAPI sandbox, Duffel test, FX и один имитированный заказ отдельно проверялись. Последний UI-preview безопасно отказал по LiteAPI 2001: quote/order не созданы.
- Новые R27 исправления: бюджет и непроверенные расходы (205 связанных, 31 новых целевых, включая PG); состав группы / единственный номер (111 контролируемых, отдельный пересекающийся related 188); даты / страна / booking restore (612 связанных, включая 3 PG); отмена и противоречия (403 связанных, затем wrong-stay fix 415 связанных / 73 own-PG+pure). Наборы пересекаются, не складывать.
- Независимый peer подтвердил блокировку гипотетического снятия расходов и снятия условия не у того отеля. Исходные RED, подготовительные ошибки и ограничения сохранены. Отчёты: `docs/verification/final40-*-20261028.json`.

## Что выполняется сейчас

Общий R8: `.local/verify_integrated_release_20261028.py r8`, exec session68812. Before `.local/evaluation/integrated-release-full-20261028-r8.before.json`, E package `1287953a3d8d09c06e58cf0f03b4b36dc0a714a9dab091792dce90538fe4f26e`, 96файлов/327testfiles. APP/tests frozen. R7terminal/XML сохранены:24schema+2partyFAIL не переименовываются вPASS. Новые related:767Groq/financial,123party,155ownedreask (пересекаются). Actualcanary/cleanDocker — отдельные privateконтроллеры, runtimeR26 пока не менять.

## Живой стенд и Git

- Пользовательский frontend: http://localhost:18082/#studio → native8084. Сейчас установлен именно R26, не R27. Frontend-контейнер ранее вручную запустил владелец; запрет автоматического запуска не обходился.
- R26 C source: `/home/aegml/projects/otravel-recipient-release-r26-20261028`; DB `otravel_zai_eval`. Не перезаписывать live source. Подготовлен private `.local/recipient_release_r27_8084_20261028.py` для НОВОГО C source и точного guarded SIGTERM только старого R26, без смены БД и startup inference. Install/start только после общего PASS и точного Git/package pin.
- Ветка `backend/harness-foundation`, оба private remote origin/cv на `5e2575e`. PR3 draft: https://github.com/ostrovok-hackathon-2026/aegml/pull/3. Основная main ещё старая `545e692`; финальная merge/tag не выполнена. Не force-push.
- GitHub connector даёт 404 private repo, gh нет. Допустимый fallback: existing GCM credential только RAM, `.local/github_release_pr_20261028.py`; токен не печатать.

## Передача

README уже содержит обложку, настоящий скриншот, одну команду `docker compose up --build`, localhost8080 и LLM config. Видео пока placeholder. `LLM_PROVIDER=external`, форматы `openai|gemini|anthropic`, свои URL/model/key/confirmation; нестандартный протокол по одному ключу не угадывается. Gemini/Anthropic live не проверялись, wire/API/PG проверялись.

`secrets.env.age`: 78 имён, три существующих проектных LiteAPI-sandbox / Duffel-test / SerpApi ключа по разрешению владельца, LLM-ключей 0. Official recipient `age1932fu7f70rwyfhaxflyfszz4gf6avxwc95wc0yjwm6zns9tspycqpgdnc0`; ciphertext SHA `0a6f6b336548bee747aba1ee5fe2b05da5050317f7a3d15ead06844c3f5649c9`. Private конфиг записывающему: `/home/aegml/delivery/otravel-recording-20261028/recording-final.env`, 0600, 78 имён/3 непустых tourkeys/LLM пустые. Не публиковать plaintext. Старый архив ae7d2b4 устарел — нужен новый после commit.

## Открытые границы

Нет полного semantic PASS 40/80/106. Весь первый набор разнородных hard пожеланий, rail-only, день рождения ребёнка и все свободные вопросы не закрыты. Until-checkin сохраняется как обязательное непроверенное условие и блокирует оформление; положительная квалификация произвольного срока отмены не реализована. Неадресный clear иногда консервативно уточняется. GigaChat OAuth200, inference TLS не подтверждён, VPN не менять.

## Ближайший шаг

Получить R7 terminal и source/test equality → целевой actual retake с сильной API LLM / туристическими API → stage только известные файлы, scan секретов, commit/push обоим → R27 immutable install / проверка frontend → свежий архив, PR/main и передача для видео. Не менять закрытые блоки без новой причины.

AEGYX R16 INDEX81 owner-paused; A524, Ubuntu-24.04, Windows PostgreSQL/PEM, httpd8080 не трогать. Нет WSL shutdown/prune/global trust/VPN действий. Local Qwen / платные вызовы / настоящая бронь и оплата = 0.
