# Проверка после 80 диалогов

Исходный набор содержит 80 разговоров, 697 пользовательских реплик и 597 наблюдавшихся резервирований вызовов модели Z.ai GLM-4.5-Flash. Диалоги 81–82 исключены. Все разговоры прочитаны с ответами, изменениями состояния и действиями корзины; подробные разборы находятся в [отчётах](../verification/after80-20261026/checkpoint.json).

Экспорт не содержит исходных операций модели для каждого хода. Поэтому ошибку состояния можно подтвердить, но её точную причину нельзя всегда приписать модели или серверному guard без нового воспроизведения.

## Текущий checkpoint

Core **b9343c0c6459c6d34e8605371c955de95da2f73d** опубликован в обоих private remotes и установлен в Native, package **12666a05b41c22bd27965fe1399344e0e2be9ff906c03eb2107608f6f4bb2d11**. [Полный controlled/domain/API/PG/MockTransport набор](../verification/hotel-clarification-core-20261027.json): **4530PASS/0fail/0skip**. Это не4530языковых сценариев и не сумма targeted-наборов.

[Два actual OpenRouter перехода](../verification/actual-selected-hotels-wow-20261027.json) прошли21предметную проверку: WOW-правка с сохранением выбранного первого отеля и ссылка на выбранный UI plan3, отличный от первой карточки. Оба имеют явную типизированную подготовку; это не два новых полных natural диалога. Реальных броней/оплат, paid fallback и local inference нет.

После этих переходов собственный Router-процесс остановлен. Попытки fixed80 на Z.ai4.5 и CF дали по1сообщению/1model/0tools и остановились: HTTP429/business1113account/billing и unknown429 соответственно. Новый Groq-run запущен с60sPOST gap; результата ещё нет.300s для неизвестного429 — engineering wait, не доказанный vendor reset. Ни одна из этих стартовавших попыток не считается80завершёнными диалогами.

## Изменения и подтверждённые границы

| Наблюдение | Изменение |
| --- | --- |
| Ответ заменялся общим сообщением о планах | Модель выбирает разделы ответа; суммы, даты, состав, тарифы и заказы выводятся из сохранённых фактов кодом. |
| Ошибки масштаба денег, процентов и копеек | Точная арифметика младших единиц, проверка текущего сообщения, отдельные уточнения для неоднозначной суммы. |
| Перепутанные роли городов и потерянное назначение | Привязка к явно названным ролям, защита отправления и контекста страны. |
| Лишний обратный перелёт или отель в конечном городе | Явная политика маршрута: туда-обратно, в одну сторону, другой конечный город или ещё не выбранный конец. |
| Не снималось утреннее пожелание | Отдельная операция удаления пожелания по ключу. |
| Неверные семьи и расселение | Атомарное изменение состава группы, запрет угадывать семьи, обязательное уточнение неизвестного состава. |
| Разные требования номеров становились общими | Условия тарифа имеют адрес конкретного номера; у DEMO есть раздельные тарифы и цены номеров. |
| Человек исключался только из номера | Перед новым поиском требуется уточнить удаление из всей поездки. Старая корзина при этом не оформляется. |
| Прямой рейс не выражался контрактом | Добавлено требование `nonstop`; факт проверяется по сегментам и остановкам ответа Duffel. |
| Повторные запросы расходовали квоту | Кэшируются только справочные ID отелей; тарифы и reprice запрашиваются заново. Travel-429 включает временный cooldown. |

Новые проверки дополнительно подтверждают scoped `set_room_members`, защиту count-only расселения, fresh room requests после перестановки гостей и сохранение актуально выбранного отеля. First-shown plan не становится выбором автоматически: keep требует canonical pin или fresh own preview; иначе ожидается ASK. Selection identity не переносит цену или согласие.

В исходном natural3208 HYBRID вводе модель сохранила полные12ops/гражданствоRU, но два необязательных вопроса остановили поиск. [Новый gate](../verification/natural-hybrid-optional-questions-20261027.json) прошёл51controlled проверку, включая9PG: optional темы не блокируют complete search, обязательные/user-hard/server вопросы и явная пауза сохраняются. Нового remote natural PASS этого fix пока нет. Последующий typed search по уже извлечённой поездке — отдельная source integration проверка.

[Actual mixed group](../verification/actual-group-checkout-20261027.json) ранее подтвердил LiteAPI prebook2hotels/4rooms, Duffel3EURflights, CBR→RUB и DEMOtransfers: preview/confirm/retry→один BOOKED_DEMO. [Native Serp](../verification/native-serp-comparison-20261027.json) отдельно подтвердил12sellers/6ставок на1гостя. Новый [LiteAPI409](../verification/natural-hybrid-prebook409-diagnostic-20261027.json) не имеет доказанной причины/failed hotel; последующий200первого отеля её не устанавливает. Состояние и безопасные provider/status diagnostics сохранены.

## Content-oracles и fixed replay

[Oraclesv2](../../fixtures/evaluation/after80-oracles-v2.json) сохраняют прежние65правил/53turns/6partialcases. Добавлены14других whole-dialogue IDs/124реплики и129предметных инвариантов. Совместный учёт: **20partialcases /172bound turns /194rules /fullybound0**. [Manifest](../../fixtures/evaluation/after80-oracles-v2.manifest.json) содержит источник, IDs и ограничения; old-source offline113PASS/16FAIL — диагностика прежнего85160ef, не новый LLM результат.

Правила проверяют значения и отношения: origin/destination, состав с младенцем age0, даты, деньги/дельты, nonmutation на гипотезе, добавление и возраст того же ребёнка. Identity/status metadata не дают content coverage. В исходных80нет связкиpause→дополнения→explicitresume; её проверяет отдельная PF-серия.

Bare city/date при другом pending field получает **NEEDS_ADAPTIVE**. Относительная дата требует настоящей before-базы; volatile цены, supplierIDs и quote/orderIDs не подменяются историческими константами. Там, где grader не выражает роли, категорию/точную identity участника, preferences/constraints или содержательный ответ, остаётся **UNBOUND**. Данные о source-предложении не доказывают весь диалог.

Команды ниже строят только dry plan, без HTTP:

```powershell
py scripts/replay_after80.py
py scripts/replay_after80.py --llm-profile cloudflare-nemotron
py scripts/replay_after80.py --llm-profile openrouter-dots
py scripts/replay_after80.py --llm-profile groq-qwen
```

Реальный повтор имеет отдельный frozen план и private WSL-каталог. Это fixed transcript, а не новый адаптивный разговор: часть реплик зависит от старых вопросов. Счётчик сообщений не равен приёмке разговоров. Offline grading использует `scripts/grade_after80.py` с `--manifest fixtures/evaluation/after80-oracles-v2.json` и `--results` фактического каталога прогона; source/runtime binding и FAIL сохраняются.

`--llm-profile` выбирает ожидаемый профиль/receipt, не переключает сервер. ZAI legacy4.5 и ZAI4.7, CF, Groq и Router имеют отдельные bindings. Чужой vault не продолжается заменой флага. Текущий runtime фиксируется в PROJECT_STATE/NOW; старый f52 или stopped профиль не объявляется работающим.

Default application cap40; Router50 требует явного согласованного opt-in во всех слоях, а не произвольного высокого значения. Daily counters сохраняются при restart. App caps не измеряют vendor остаток. Нет скрытого retry/paid fallback/local модели; billing/кредитный потолок replay не меняет.

Открытые границы: native EUR из нашего аккаунта Duffel нельзя молча переименовать в RUB; справочный FX требует отдельной политики и снимка. Обобщённый факт тарифа не доказывает условие конкретного номера. SANDBOX и поисковая цена не становятся LIVE_BOOKABLE. Реальные брони и платежи не создаются.

## Исторические checkpoint и передача

2477,2538,2919,4202,4292,4403и4438 — отдельные более ранние snapshots, не слагаемые4530. R3OpenRouter6/8wholecases, R4GEO01 и GROUP04 state-only12/12 имеют свои frozen source. Для последнего отдельно найден лишний city ASK; old scores сохранены, R5добавил READY/no pending/current plans. Типизированная подготовка/preview в новом selected-hotel actual обозначены явно.

Первоначальные FAIL, budget-stops и source/model отказы не переписываются новым результатом. Полный106не имеет language PASS; D250=50диалогов/300ходов импортирован отдельно. Это acceptance pending, а не отмена доказанных узких scope.

Frontend делает сокомандник. В handoff нужны scoped rooms, optional темы как необязательные, текущий выбранный preview, раздельные источники/валюты/quote lifecycle. [Fresh Docker](../verification/prefrontend-coldstart-20261027.json) без ключей проверен на прежнем committed archive; сдаваемый новый архив требует относящегося smoke. Интеграция интерфейса проверяется отдельно; без LLM-ключа работают формы/DEMO, не свободный NLU.
