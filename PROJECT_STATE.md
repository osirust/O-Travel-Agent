# O!Travel Agent — состояние на 27 октября

Цель владельца: завершить backend/harness по docs/research/harness-plan-review.md до интеграции frontend. Frontend делает сокомандник. Продолжать работу, не выдавать отдельный PASS за закрытие всего80/106.

## Подтверждено

- Оба private remotes: backend/harness-foundation, commit3208f0383d7be522f5d6690d5f34809c3a790063. PR3 draft; main автоматически не сливается.
- Native: WSL AegML-Hackathon, /home/aegml/projects/otravel-after80-20261026, DBotravel_zai_eval,8084. Пакет5e9f6860cbefabcc3518f6930db4dd8cbbe13af9677e3a92bdf2c40e2c9cd3c8, собственный OpenRouter Dots :free, max_price0, fallbackfalse. App-bound50/day, счётчики не сброшены; после последних48 резервирований вероятно2 free вызова, внешний остаток заново не прочитан.
- Последний опубликованный полный core4438PASS/0skip. Новые hotel-preservation + optional-clarification helpers сейчас проверяются на E:, ещё не deployed.
- Новый targeted90PASS. Предыдущий новый full4528PASS/2FAIL сохранён: старые assertions требовали hotel_idNone после явного KEEP выбранного отеля. Исправлены на actual selected hotelID с проверкой trip ownership; остальные room/budget/date/constraint/quote assertions сохранены. Новый frozen full4530PASS/0skip/171.22s, source/tests FREEZE: .local/evaluation/after80-20261027/full-hotel-clarification-final.xml.
- Actual whole WOW12steps на93f91fd: все исходные state/control oraclesPASS, но дополнительная проверка нашла WAITING_USER после compoundT4. Географический guard3208 устранил это в отдельном actual single-transition; дополнительно обнаружен непиннутый first-hotel KEEP. Новый helper пинит только явно сохраняемый отель из свежего own preview, не цену/consent; actual повтор pending.
- Actual natural HYBRID group: модель сохранила все12операций/семьи/номера/даты/бюджет/RU. Два optional вопроса заблокировали поиск. Новый gate51controlledPASS, actual повтор pending.
- Explicit typed search этой естественно извлечённой группы дал5SANDBOX+2DEMO компонента. Preview503/upstreamLiteAPI409,causeUNKNOWN. Один прямой diagnostic prebook первого отеля200 с прежней ценой; второй не повторялся. Это не autonomous natural-agent PASS.
- Earlier actual typed group ab378: LiteAPIprebook2+DuffelEURrefresh3+CBR→RUB,2DEMOtransfers→один BOOKED_DEMO, exact retry sameorder.0supplierbook/pay. Serp property-details:12seller names/6matching oneadult rates; exact-equivalence/bookability не доказаны. Фото10exacthotelIDs вне economic hash. ColdDockerarchive/0keys/search/preview/confirm/restart проверен.
- Все80/697исходных реплик прочитаны; полный новый actual80 НЕ начат. Oraclesv2:20partialcases/172turns/194rules,fullybound0.106:71partial/35notexecuted; full-languagePASSне заявлен. D25050dialogs/300turns импортирован.

## Следующий шаг

Дождаться current full → приPASS explicit stage/secret-scan/commit/push → own Routerstop → exact committed Native install (previous5e9f..., added hotel_preservation.py+clarification_policy.py) → startRouter50. Два оставшихся free slots: actual oneWOWtransition и selectedplan3. Далее отдельный fresh HYBRID search→reprice→simconfirm и GLM4.5/4.7 availability перед all80 fixed-transcript replay. Никаких слепых retries неизвестного POST. Старые FAIL сохранять.

Параллельно acceptance_audit сверяет основную матрицу, travel_checkout готовит actual source recipe, geo_dialogues проверяет all80 replay. Ни один не меняет текущий Native/source/tests во время full.

## Границы

Ключи только private Master0600 /home/aegml/projects/otravel/.env. Не публиковать .local/raw token/credential URLs. Сильные бесплатные API прежде localQwen; local inference0. ZAI47 actual1305 capacity после300s повторился, это не dailyquota; legacy45 отдельный профиль. Никаких оплат/реальных броней/платного fallback.

AEGYX R16 INDEX81 PAUSED; другие AEGYX работы другого чата не трогать. Не менять Ubuntu24.04/WindowsPG/PEM/httpd8080; no WSLshutdown/dockerprune/volume delete/globalkill. Только owned PID/controller. Тестовая БД исключительноotravel_test.

Контракты: docs/FRONTEND_HANDOFF.md, FRONTEND_CONTRACT.md, HOTEL_MEDIA.md, SUPPLIER_COMPARISON.md, docs/frontend/openapi.json. API IDs/источники/валюты/unknownfacts не выдумывать. DEMO/SANDBOX/LIVE_INDICATIVE отдельно, LIVE_BOOKABLE не обещать.
