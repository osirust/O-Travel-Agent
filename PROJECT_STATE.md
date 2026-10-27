# O!Travel Agent — состояние на 27 октября 2026

## Цель и границы

Закрыть backend/harness по docs/research/harness-plan-review.md до интеграции frontend. Работа разрешена, продолжать. Дизайн делает сокомандник. Только подтверждённые free/test API; локальную Qwen не использовать, пока доступны сильные API. Реальных броней/оплат нет. Исходные FAIL/корпуса сохранять.

E:/work/aegml; Native /home/aegml/projects/otravel-after80-20261026, WSL AegML-Hackathon, DB otravel_zai_eval,8084. Тесты только otravel_test. Master private /home/aegml/projects/otravel/.env. AEGYX R16 INDEX81 PAUSED, чужие WSL/WindowsPG/httpd8080 не трогать; no shutdown/prune/volume delete.

## Публикация и runtime

- В обоих private remotes ветка backend/harness-foundation: ab378d299639b484774c2697130adf71c56ecacb. Draft PR3 открыт, main не сливается автоматически.
- Native ab378d2/package2d9f2d88d0b673236ec860518e7c783b61f8cb2837b31eaac36eadf95a08b714, Cloudflare @cf/nvidia/nemotron-3-120b-a12b. Own manager scripts/after80_cf_backend.py. Groq остановлен своим manager. HYBRID LiteAPI sandbox/Duffel test/Serp Free, reference FX включён; paid/local fallback запрещён.
- Последний полный baseline3914 PASS/0skip. Новые CF-r2 repairs находятся в E, пока не deployed; integrated103 PASS. Общий full новых repairs:4026PASS/0skip/123.78s; docs/verification/cf-r2-repairs-20261027.json.

## Настоящие внешние проверки

- Actual group:2stays/4rooms/3flights,4people/2families; child6→7, LiteAPI prebook2 + Duffel EUR refresh3 + CBR RUB, DEMO transfers. Полное покрытие9компонентов,346697.38RUB, preview/confirm/retry200→1BOOKED_DEMO,0supplierbooking/payment. docs/verification/actual-group-checkout-20261027.json. Первый503 сохранён, exact cause UNKNOWN; отдельные диагностические чтения не воспроизвели его.
- Фото: Native поиск→10фото exact hotel IDs без изменения цены. В отдельном positive checkout media после confirmation получил stale409 по версии — это ожидаемая защита, не новый photo PASS.
- Fresh committed archive Docker:0ключей, one build/up31.866s,38local HTTP,0model/tool/vendor. DEMO search/preview/confirm/retry/restart сохранили trip/token/order/idempotency; собственный Compose down без-v. docs/verification/prefrontend-coldstart-20261027.json.
- Serp Free Hotels20 indicative; Flights MOW→DXB exact noresults, IST→DXB12. Последний подтверждённый остаток232/250 был ДО последующих запросов. OpenMeteo текущий прогноз, CBR reference с датой/TTL; не банковская цена. Реальные разные сравнимые suppliers/LIVE_BOOKABLE OPEN.

## Последняя actual LLM серия и ремонт

- CF r2 report ec5c7e7dc701a2228f1f0de3e58bec60774f28c9eedc94dabc3f016fc749d3d9:71language messages,24calls(23responses+1HTTP429),7durabletoolstarts,72PASS/9FAIL,2NOT_REACHED. Полностью прошёл PF-TRANSPORT05 12/12. PF-SOURCE06 достиг mixed preview/confirm/retry и1demoorder. docs/verification/prefrontend-cf-20261027.json.
- HARNESS-004-DEMO-COMMAND-IGNORED-001: модель вернула DEMO, старый guard удалял из-за слова «деморежим». Literal guard+root intake интегрированы;303related + exact2PG проходят, providers/fallback не подменяются.
- Поздняя «отправление Санкт-Петербрг» теперь создаёт confirmation, Белград не стирается неподтверждённым Стамбулом и отказом. Geo424related,3PGchains26turns/11injected outputs; actual retest PENDING.
- HARNESS-004-ORDER-REPLY-SUPPRESSED-001: точный вопрос и captured validated contract сохранены; ответ только из Store orders. Mixed declarative edits не поглощаются status shortcut. Новый integrated103PASS, including0extraorders/tools/quotes.
- r3 корректирует только неверные oracles unsupportedcity pending и pause control-only CAS+1; r2 исходные результаты не переписываются. Actual retest после freeze/deploy, vendor429 не обходим.

## Корпуса и ближайшее

-80 исходных/697turns прочитаны, SHA81a7e59c65d41da8dfc6101fc0429c455c259561e44f6a9a79f0dc99323058ca. Ранний fixed replay43messages/39calls/4целых+часть5го остановлен локальнымcap, не80PASS.
-106:71partial/35notexecuted, полного language PASS нет. D25050dialogs/300turns только импорт. docs/verification/acceptance-audit-20261027.md.
- Сейчас: full новых repairs завершён, secret-scan, commit/push обоих remotes, exact CF Native deploy. Затем r3 actual targeted retest и расширение языковой приёмки с case-specific checks. OpenRouter reserve можно проверить read-only account quota; никакого платного switch или обхода429.
- Frontend: docs/FRONTEND_HANDOFF.md, FRONTEND_CONTRACT.md,HOTEL_MEDIA.md,docs/frontend/openapi.json. Portable APP_PORT по умолчанию8080 конфигурируется; локальный8080 владельца не трогать. Без ключа формы/DEMO, не имитация NLU.
