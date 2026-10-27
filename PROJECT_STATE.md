# O!Travel Agent — состояние на 27 октября 2026

## Цель и границы

Закрыть backend/harness по docs/research/harness-plan-review.md до интеграции frontend. Работа разрешена, продолжать. Дизайн делает сокомандник. Только подтверждённые free/test API; локальную Qwen не использовать, пока доступны сильные API. Реальных броней/оплат нет. Исходные FAIL/корпуса сохранять.

E:/work/aegml; Native /home/aegml/projects/otravel-after80-20261026, WSL AegML-Hackathon, DB otravel_zai_eval,8084. Тесты только otravel_test. Master private /home/aegml/projects/otravel/.env. AEGYX R16 INDEX81 PAUSED, чужие WSL/WindowsPG/httpd8080 не трогать; no shutdown/prune/volume delete.

## Публикация и runtime

- В обоих private remotes ветка backend/harness-foundation:43050b8a9e0ac344723bd7f942a15205aabba196. Draft PR3 открыт, main не сливается автоматически.
- Native43050b8/package0d452223f0f25967b532f0f3a51d4eca17779142edc83979b0c5569eaad6a0d0, OpenRouter dots-studio/dots-3-note-preview:free. Own manager scripts/after80_router_backend.py. CF и Groq остановлены своими managers; смена профиля явная, результаты не смешиваются. HYBRID LiteAPI sandbox/Duffel test/Serp Free, reference FX включён; paid/local fallback запрещён.
- Последний полный baseline3914 PASS/0skip. Новые CF-r2 repairs опубликованы и deployed; integrated103 PASS. Общий full новых repairs:4026PASS/0skip/123.78s; docs/verification/cf-r2-repairs-20261027.json.

## Настоящие внешние проверки

- Actual group:2stays/4rooms/3flights,4people/2families; child6→7, LiteAPI prebook2 + Duffel EUR refresh3 + CBR RUB, DEMO transfers. Полное покрытие9компонентов,346697.38RUB, preview/confirm/retry200→1BOOKED_DEMO,0supplierbooking/payment. docs/verification/actual-group-checkout-20261027.json. Первый503 сохранён, exact cause UNKNOWN; отдельные диагностические чтения не воспроизвели его.
- Фото: Native поиск→10фото exact hotel IDs без изменения цены. В отдельном positive checkout media после confirmation получил stale409 по версии — это ожидаемая защита, не новый photo PASS.
- Fresh committed archive Docker:0ключей, one build/up31.866s,38local HTTP,0model/tool/vendor. DEMO search/preview/confirm/retry/restart сохранили trip/token/order/idempotency; собственный Compose down без-v. docs/verification/prefrontend-coldstart-20261027.json.
- Serp Free Hotels20 indicative; Flights MOW→DXB exact noresults, IST→DXB12. Последний подтверждённый остаток232/250 был ДО последующих запросов. OpenMeteo текущий прогноз, CBR reference с датой/TTL; не банковская цена. Реальные разные сравнимые suppliers/LIVE_BOOKABLE OPEN.

## Последняя actual LLM серия и ремонт

- ТЕКУЩАЯ РАБОЧАЯ ВЕРСИЯ E после43050b8 ещё НЕ deployed: r3 нашла замену правильного geo-ответа и false conflict составной правки. Все6остальныхcasesPASS, включаяmode/duration/lateorigin/undo/checkout/orderreply/pause. Добавлены contextual CITY-в-COUNTRY/FAQ, scoped quoted-span return alias, representation-only room_identity + hook. Дополнительно Serp property details/supplier_comparison и безопасные optional diagnostics ошибок API. Новый общий full4202PASS/0skip/130.40s завершён, r4client113purePASS отдельно.4026 относятся к предыдущему repair checkpoint. Новые исходники готовы к commit/deploy, пока C остаётся43050b8.
- CF r2 report ec5c7e7dc701a2228f1f0de3e58bec60774f28c9eedc94dabc3f016fc749d3d9:71language messages,24calls(23responses+1HTTP429),7durabletoolstarts,72PASS/9FAIL,2NOT_REACHED. Полностью прошёл PF-TRANSPORT05 12/12. PF-SOURCE06 достиг mixed preview/confirm/retry и1demoorder. docs/verification/prefrontend-cf-20261027.json.
- HARNESS-004-DEMO-COMMAND-IGNORED-001: модель вернула DEMO, старый guard удалял из-за слова «деморежим». Literal guard+root intake интегрированы;303related + exact2PG проходят, providers/fallback не подменяются.
- Поздняя «отправление Санкт-Петербрг» теперь создаёт confirmation, Белград не стирается неподтверждённым Стамбулом и отказом. Geo424related,3PGchains26turns/11injected outputs; actual retest PENDING.
- HARNESS-004-ORDER-REPLY-SUPPRESSED-001: точный вопрос и captured validated contract сохранены; ответ только из Store orders. Mixed declarative edits не поглощаются status shortcut. Новый integrated103PASS, including0extraorders/tools/quotes.
- r3 корректирует только неверные oracles unsupportedcity pending и pause control-only CAS+1; r2 исходные результаты не переписываются. Actual retest после freeze/deploy, vendor429 не обходим.

## Корпуса и ближайшее

-80 исходных/697turns прочитаны, SHA81a7e59c65d41da8dfc6101fc0429c455c259561e44f6a9a79f0dc99323058ca. Ранний fixed replay43messages/39calls/4целых+часть5го остановлен локальнымcap, не80PASS.
-106:71partial/35notexecuted, полного language PASS нет. D25050dialogs/300turns только импорт. docs/verification/acceptance-audit-20261027.md.
- Сейчас: full4026/secret-scan0matches/commit43050b8/pushboth/deploy завершены. Actual r3 завершена через OpenRouter:89actions/79messages,30models/7toolstarts,6целыхcasesPASS/2FAIL,80PASS/9FAIL+1NOT_REACHED.35calls/160tools cap,60s pacing, один actor. Private folder /home/aegml/evaluation-runs-prefrontend/prefrontend-20261027-020041-d217d861. Driver1c565a33599a549c0532211ed274ea036ef7fbdf0adafea13ecf39941db16eb1, recipe c24251b6900c8d3650181bd0c7f038c801f537e67b3aab5c0005feea5f54e2b5. Новая модель не является повтором той же CF. Read-only OpenRouter quota до серии200:50/50free remaining,usage0,credit cap0.01 неизменён. Actor завершён, можно exactstop/install/start. РезультатнеALLPASS; исходныеFAILсохранены. Новаяr4 меняеттолькоещёодинpause-versionoracle(GROUP04T09); targetedGEO01+GROUP04/max10models готов сdriver52e131c864ff5f03bcd02f61a9da7f774930e4d1ec38348c7266a91ccac71d4f.
- Frontend: docs/FRONTEND_HANDOFF.md, FRONTEND_CONTRACT.md,HOTEL_MEDIA.md,docs/frontend/openapi.json. Portable APP_PORT по умолчанию8080 конфигурируется; локальный8080 владельца не трогать. Без ключа формы/DEMO, не имитация NLU.
