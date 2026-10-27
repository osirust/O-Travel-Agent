# O!Travel Agent — состояние на 27 октября 2026

## Цель и ограничения

27 октября довести backend/harness до передачи frontend и тестировщику. Рабочий список — `docs/research/harness-plan-review.md`. Дизайн здесь не делаем. Только бесплатные подтверждённые API; локальную Qwen не использовать, пока доступны сильные API. Настоящих оплат/бронирований нет. Исходные корпуса и FAIL не переписывать.

Исходники: `E:/work/aegml`. Native: `/home/aegml/projects/otravel-after80-20261026`, WSL `AegML-Hackathon`, PostgreSQL `otravel_zai_eval`, порт8084. Тесты используют только `otravel_test`. Master-секреты — private `/home/aegml/projects/otravel/.env`, не Git/отчёты. AEGYX INDEX81 остаётся PAUSE перед SOURCE3; Ubuntu24.04, WindowsPG/PEM/8080 не трогать. Никаких WSL shutdown, docker prune или удаления volumes.

## Проверенный исходный checkpoint

- В обеих приватных репозиториях ветка `backend/harness-foundation` получила `9ddfcda6dde46604e01c132f02b9a54a12cef628`. Следующий локальный commit `3c9f3c06bf78e2342b66172971268a56c7f77781` исправляет ошибочную суточную блокировку при неизвестном Groq429 (143PG checks); ещё не опубликован.
- Native уже3c9f3c0/package `bb08b98b166432a969d15acbfa91c00efa3d50409e61e835e36de5307dba4214`, controller `scripts/after80_groq_backend.py`, Groq Free Qwen3.8-27B +HYBRID LiteAPI sandbox/Duffel test/SerpApi Free, FX включён. Новые правки ниже пока в E и не развёрнуты.
- Последний завершённый общий прогон:3723PASS/0skip (`.local/evaluation/after80-20261026/full-20261027-initial.xml`). Он предшествует последнему leg-intent/semantic-pause пакету; новый полный прогон идёт отдельно.
- Actual Groq: один связный custom диалог,3 model calls,3 пользовательских сообщения +1 явный typed resume. Сохранены Москва→Казань,1adult, child7→8, бюджет75k→55k, пауза/очередь;0tools/orders. `docs/verification/groq-dialogue-20261027.json`. Первый driver неправильно ждал queuedrun и timeout сохранён; это не80/106PASS.

## Новые исправления до общей приёмки

- `TRAVEL-LITEAPI-SELECTED-REPRICE-001`: выбранный offerId перепроверяется через sandbox prebook, а не повтор rates. Настоящие single-room Dubai и multi-room Istanbul проходят (7HTTP/0booking/payment);253relatedPASS. Цена/состав/fees перепроверяются, источник остаётсяSANDBOX. Native search→prebook→simconfirm ещё требуется. `docs/verification/liteapi-prebook-20261027.json`.
- География: qualified city/country conflict, scoped Белгород→Белград при Сербии, адресные предложения опечаток Москвы/Петербурга/Стамбула, compound yes/no+child, позднее yes после отказа. 402relatedPASS,5 PG диалогов/38ходов; actual NLU этих новых случаев ещё нужен. `docs/verification/geo-dialogues-20261027.json`.
- FAQ: содержательный ответ о неподдержанном nightly hotel cap без замены whole-trip бюджета; вопросы о стране и aliases не меняют поездку. 8PG связанных проверок старой activequote и17последних pause/FAQ/geo/duration checks проходят.
- Длительность: `Stay.requested_nights` сохраняется без дат, после заезда выводится checkout+N и обратное плечо. Дни не превращаются в ночи; противоречие требует fresh confirmation. None не меняет legacy JSON/ревизию/quote. 343relatedpure+5PG. Pydantic>=2.14.1, lock не изменён.
- Публичный `set_leg_mode` меняет только mode существующего leg. Новый intent guard и адресный pendingpacket защищают от незапрошенных/неверно адресованных правок;114rootrelatedPASS. Oldquote не воскресает после undo.
- `HARNESS-004-PAUSE-CLARIFICATION-LOSS-001`: initial1controlPASS/3reproFAIL подтвердили потерю нового и уже показанного country-consent вопроса при model/UI pause. Root сохраняет fresh semantic pending через control-only версии, не оживляет stale packet;17targetPASS. Новый preview/confirm блокируются на user_pause, включая no-op модельную паузу.

## Туристические источники

- LiteAPI/Duffel даютSANDBOX. Смешанный native9dd поиск действительно вернул hotel+2flights EUR+referenceCBR+DEMOtransfer и5фото, цена после media неизменна. Но старый preview503OFFER_UNAVAILABLE не объявленPASS; его закрывает только новый prebook end-to-end.
- Фото: отдельный owner-auth `/api/v1/sessions/{id}/media`, exact hotel ID, кэш статического контента, gallery<=5, вне economic hash. Actual browser JPEG200 подтверждён; никакого дизайна или image-generation.
- SerpApi: Account200 Free, перед проверками232/250 поисков; после запросов остаток не перечитывался. HotelsDubai20 ориентировочных вариантов; FlightsMOW→DXB — точный no-results, контрольIST→DXB дал12 вариантов. Только точная форма no-results нормализуется вNO_MATCHES; это не доказательство отсутствия московских рейсов.
- Open-Meteo200 — прогноз27окт–2ноя, не погода ноябрьской поездки. CBR бываетtimeout: при отказе нет выдуманного общего итога; успешные reference snapshots ранее получены.
- Сравнимость нескольких подтверждённых реальных suppliers и LIVE_BOOKABLE остаются отдельным OPEN. Поля source-name сами по себе её не доказывают.

## Корпуса и следующий шаг

- Все80 исходных диалогов/697turns прочитаны, SHA `81a7e59c65d41da8dfc6101fc0429c455c259561e44f6a9a79f0dc99323058ca`. Новый fixed replay был43messages/39modelcalls/4полных+часть5го; остановка локального бюджета, не80PASS.
- После новых исправлений нужен сильный API actual-run: подготовлен отдельный runner8связных диалогов/98steps сcase-specific assertions; defaultdry0HTTP. Полный80 и исходный106 NLU ещё не квалифицированы.
-106:71частично проверены/35не исполнены в исходном учёте; часть сценариев вне согласованного MVP. D250=50диалогов/300turns только импорт. Аудит доказательств: `docs/verification/acceptance-audit-20261027.md`.
- Ближайшее: общий full/секрет-scan→commit/push обоих remotes→exact Native deploy. Затем actual2stays/4rooms/3flights EUR+FX→prebook/refresh→simconfirm+idempotency, cold-start freshDocker/0keys и8actualдиалогов. Все failure/неохваченные условия сохранять отдельно; фронтенд интегрируется после27октября.
- Для frontend: `docs/FRONTEND_HANDOFF.md`, `docs/FRONTEND_CONTRACT.md`, `docs/HOTEL_MEDIA.md`, `docs/frontend/openapi.json`. Перед передачей обновить экспорт схемы/receipt на новый commit; ключи не включать.
