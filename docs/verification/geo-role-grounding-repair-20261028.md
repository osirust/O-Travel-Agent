# Географические роли: отрицание города и выезд из гостиницы

По разрешённому causal export четырёх старых actual-ходов установлено: в040/T6 модель не меняла город, а в052/T5,056/T8,059/T9 не меняла отправление. Неверные upsert_stay.city=Сочи и set_origin=hotelcity добавлял сам Harness через intent_grounding._facts. Это подтверждённые ошибки server grounding, а не предположение о слабой модели.

Изменён только intent_grounding.py: отрицание относится к конкретному предлогу, а подтверждённый raw hotelcheckout с owned stay и той же буквальной датой не становится новой точкой отправления. Даты, target IDs, остальные операции и независимые положительные origin/destination сохраняются. Публичный typed-контракт не изменён.

Private RED:4из4 FAIL на прежнем модуле. Кандидат:25 новых pure-контролей PASS; общий related pure141 PASS; реальный API/PG4 PASS. Всего145 разных related checks; private25 повторно не суммируются. Никаких новых удалённых вызовов LLM/туристических API или заказов.

Постоянные IDs: HARNESS-004-NEGATED-DESTINATION-001 и HARNESS-004-HOTEL-CHECKOUT-ORIGIN-CONFLATION-001 — CONTROLLED_PASS / ACTUAL_REMOTE_RETEST_PENDING. Дополнительно controlled-проверка HARNESS-004-EXPLICIT-ORIGIN-CORRECTION-FALSE-AMBIGUITY-001. В038/T6 модель уже сама прислала ошибочный remove_stay; этот отдельный finding здесь не исправлен.

Новые сильные actual-диалоги, весь80/106корпус и произвольные мировые города этим узким ремонтом не квалифицируются. Детали bootstrap, неизменные rawоперации, source/XMLSHA и условия закрытия — в [JSON](geo-role-grounding-repair-20261028.json).
