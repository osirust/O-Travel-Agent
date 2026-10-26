# HARNESS-001: малая проверка фактического группового поиска

**ACTUAL NOT_EXECUTED.** Прочитаны текущие `orchestrator.py`, `providers.py`, `provider_facts.py`, `planning.py`, `api.py`, `store.py`, `domain.py` и `inventory.py`. Ключи/`.env` не читались; HTTP, ASGI, БД и работающий8084 не вызывались. Ниже план для отдельной БД и реального TravelGateway, без LLM и настоящих бронирований.

Pure preflight самой fixture выполнен: `validate_trip=[]`,9обязательных компонентов, возраст детей6/12 на первой остановке и7/12 на второй. DemoInventory дал3плана без violations; первый147600RUB — **вымышленный контроль**, не оценка рынка.

## Что реально допускает текущий код

- HYBRID делает по одному гостиничному поиску на stay и авиационному на leg. У LiteAPI сначала lookup до3отелей, если hotel_id не задан. Выбор — минимальная пригодная цена среди первых30ответов компонента, не глобальный оптимум и не доказанное сравнение независимых поставщиков.
- Для двух stays нужны4комнатных покрытия,3плеча и2трансфера. Живого transfergateway нет: для полного текущего плана трансферы будут **DEMO**. Поэтому даже при всех внешних отелях/перелётах итогMIXED, не fullylive/LIVE_BOOKABLE.
- Серпавиавыдача сейчас не содержит `mandatory_fees_known`/`fees_included`; `choose()` её отклонит. SerpHotel не поддерживает2номера. Не использовать SerpApi как будто он уже даёт пригодную групповую корзину.
- Duffel не передаёт `request.currency` в `offer_requests`: берёт фактический `total_currency` ответа. В исторической квитанции `travel-api-probes-20261025.json` были LiteAPIUSD, DuffelEUR и SerpUSD. Это не обещание валюты/наличия сегодня; GBP не установлен этой квитанцией.
- BudgetGBP/EUR/другая валюта может разрешить отдельные внешние компоненты, но DemoInventory считает толькоRUB. Тогда нечем честно заполнить обязательные DEMOтрансферы; принудительныйFX/переклейка currency недопустимы.

**Основной выбор для boundedrun:** `hotel_provider=liteapi`, `flight_provider=duffel`, budgetRUB, `demo_fallback=true`. Если Duffel вернёт нерублёвые тарифы, сохранить отказ `unpriced_or_fx_required` и явно помеченный DEMOавиатранспорт. Это позволит проверить actualhotel→refresh→simconfirm при наличии достаточных гостиничных фактов, но не закроет actualflightчасть. Для доказательства внешнего группового транспорта потребуется отдельный совместимый источник/валюта и решение transfercoverage.

## Фиксированная fixture

Синтетические путешественники; RU — тестовая национальность для sandbox, не установленный факт о владельце. Никаких паспортов или платёжных данных. Две семьи не смешиваются, один взрослый с одним ребёнком в каждом номере на обеих остановках.

```json
{
  "trip_id": "actual-group-20261026",
  "state_version": 0,
  "origin": "Казань",
  "travelers": [
    {"id":"a1","type":"adult","age":34,"group_id":"f1"},
    {"id":"c1","type":"child","birth_date":"2019-11-12","group_id":"f1"},
    {"id":"a2","type":"adult","age":36,"group_id":"f2"},
    {"id":"c2","type":"child","age":12,"group_id":"f2"}
  ],
  "groups": [
    {"id":"f1","traveler_ids":["a1","c1"],"keep_separate":true},
    {"id":"f2","traveler_ids":["a2","c2"],"keep_separate":true}
  ],
  "stays": [
    {"id":"s-ist","city":"Стамбул","check_in":"2026-11-10","check_out":"2026-11-13",
      "rooms":[{"id":"r1","traveler_ids":["a1","c1"]},{"id":"r2","traveler_ids":["a2","c2"]}]},
    {"id":"s-ayt","city":"Анталья","check_in":"2026-11-13","check_out":"2026-11-17",
      "rooms":[{"id":"r1","traveler_ids":["a1","c1"]},{"id":"r2","traveler_ids":["a2","c2"]}]}
  ],
  "legs": [
    {"id":"l1","origin":"Казань","destination":"Стамбул","departure_date":"2026-11-10","mode":"flight"},
    {"id":"l2","origin":"Стамбул","destination":"Анталья","departure_date":"2026-11-13","mode":"flight"},
    {"id":"l3","origin":"Анталья","destination":"Казань","departure_date":"2026-11-17","mode":"flight"}
  ],
  "budget":{"amount_minor":50000000,"currency":"RUB","scope":"whole_trip"},
  "constraints":[],
  "search_policy":{"mode":"HYBRID","hotel_provider":"liteapi","flight_provider":"duffel",
    "demo_fallback":true,"guest_nationality":"RU"}
}
```

500000RUB — входной тестовый бюджет, не прогноз стоимости. Не поднимать его после ответа радиPASS. HotelIDs не выдумывать: брать из реального lookup. Начальный основной вариант без hardbreakfast/refundable/pets: цель первого прогона — факты группы/комнат/стоимости; отсутствие этих условий в fixture не закрывает их приёмку.

Ожидаемые occupancies первого отеля: `[{"adults":1,"children":[6]},{"adults":1,"children":[12]}]`; второго — тот же порядок с7вместо6. Пассажиров4 на каждом leg; возраст c1 считается на дату отправления6/7/7, c2=12. В коде IATA: KZN→IST,IST→AYT,AYT→KZN. Не менять даты/состав на лету, если поставщик не вернул подходящих предложений.

## Обязательные факты ответа

| Объект | Что проверять в actualответе/нормализации | Чего нельзя достраивать |
| --- | --- | --- |
| LiteAPI2roombundle | Один `offerId`, hotelId из запрошенного lookup, **общий** `offerRetailRate.amount/currency` для всех комнат. Ровно2rates с уникальными `occupancyNumber=1,2`; `adultCount`, `childCount`, `childrenAges` совпадают с соответствующим запросом. | Сумму не складывать из произвольных альтернатив; не заполнять недостающие гостевые числа запросом. `childCount=0` с явно пустым request допускает специальную нормализацию пустых ages; в этой fixture дети есть. |
| Вместимость | Если есть `maxOccupancy`, он целый положительный и≥2 для каждой комнаты. Сохранить `room_facts` и `capacity_verified`. | В текущем коде отсутствие maxOccupancy оставляет `capacity_verified=null`, хотя allocation может бытьtrue. Это **не подтверждённая вместимость** и не основание закрыть capacityscope. Не вписывать2/4 от себя. |
| Налоги/сборы | `retailRate.taxesAndFees` присутствует в каждом rate. По принятому контракту явныйnull означает included; у массива каждый элемент имеет booleanincluded, корректныеamount/currency; все included=true. Пустой явныймассив принят текущим parser. | Missingполе — unknown; included=false/некорректные данные не превращать в `mandatory_fees_known=true`. Не добавлять неустановленные суммы к bundle. |
| Условия | `boardType=BI/RO` даёт факт breakfast. Refundability отдельно: RFNбез точного schedule/deadline не доказывает бесплатную отмену. | Отсутствие hardcriteria в fixture не доказывает проверку всех возможных условий. Suppliername/ID сохранять только из настоящих полей bundle, не по разным offerIDs. |
| Duffeltest | Реальные returnedcurrency/totalamount/expiry и offerID. Сравнить actualslices с requestedroute/date, actualpassengers с4запрошенными. | `passengers_count` сейчас берётся из request; `received_passengers_count` отдельно. Совпадение первого само по себе не доказывает ответ поставщика. Если факты гостей/маршрута не представлены, записать evidencegap, не объявлять его закрытым. |
| Любой компонент | Точные trip/version/stay/room/legbindings, каждый traveler ровноодинраз, все цены целыеminorunits одной валюты, происхождение каждого snapshot. | Запросный binding не является доказательством вместимости/сборов/инвентаря. SANDBOX остаётся SANDBOX; DEMOтрансферы/перелёты остаются DEMO. |

Allocation/feesfalseилиunknown приводят к отказу выбора внешнего отеля; DEMOзамена не скрывает этого результата. Еслиcapacityunknownприпригоднойallocation, весьsourcecapacityscope отметитьPARTIAL даже при успешном simulatedorder. Не менять parser/guards в рамках прогона.

## Бюджет внешних HTTP

Учитывать фактические обращения транспортным счётчиком **до** отправки; безавторетраев и repair. LocalASGI/poll/SQLне входят в upstreamcap. Разрешены только нужные LiteAPI/Duffel и, отдельно при остатке квоты, SerpApi; Open-Meteo в основном пути не нужен.

| Этап | LiteAPI | Duffel | SerpApi | Итого |
| --- | ---: | ---: | ---: | ---: |
| Два lookup+два multiroomrates | 4 | 0 | 0 | 4 |
| Три одноплечевых поиска для4пассажиров | 0 | 3 | 0 | 3 |
| Один preview: повтор rates для2выбранных отелей | до2 | 0 | 0 | до2 |
| Preview только пригодных и включённых Duffelкомпонентов | 0 | до3 | 0 | до3 |
| Confirm+идемпотентныйповтор | 0 | 0 | 0 | 0 |
| **Основной максимум** | **6** | **6** | **0** | **12** |

Если все Duffelcurrency≠RUB и они заменены DEMO, previewне вызывает их refresh: основной путь≤9HTTP. Опциональный Serpmarketprobe — **не более1** обращения, только при реальном остатке totalcap≤12 (общийhardSerpcap3). Он не входит в корзину и не закрывает transportproof. Не выполнять отдельно Serpaccountstatus или дополнительные повторы без учётаcap.

Для опциональной поисковой цены вместо неподдержанногоGoogleRUмаршрута можно один Turkishmarketprobe IST→AYT→IST с датами13/17ноября и2adult+2child, currencyRUB. Это отдельный вспомогательный маршрут, не незаметная замена основной fixture. Сам по себе IST→AYT→IST имеет одну остановку; нельзя объявить его2stays/3legsприёмкой. В текущем коде Serpchildagesвaviapayload не передаются и групповыеfeesне доказаны — явно сохранить ограничение. Полностью сменить основную географию, например DXB→IST→AYT→DXB, можно только отдельной версией fixture/новым boundedrun, не дополнительными скрытыми запросами текущего бюджета.

## ASGI/PG последовательность без LLM

1. Зафиксировать codeSHA иfixtureSHA. Отдельный Store в `otravel_test` с уникальными session/clientmessageIDs; не очищать общие таблицы и не использовать liveDB. Inject реальный TravelGateway с уже разрешёнными sandbox/testcredentials из защищённого локального конфига; читает их только исполняющий корень. LLMdouble, который аварийно отказывает при любом вызове, доказывает отсутствие inference. Не применять fakeHTTP к travel.
2. `POST /api/v1/sessions` с `initial_trip=fixture`. Проверить сохранённыеIDs/даты/группы/комнаты, взять фактическую stateversion. Не использовать `/demo`: он перезаписывает поездку sample_trip.
3. `POST .../search` с новым `client_message_id,expected_version`; текущий harness запускает TripSearchпо сохранённой HYBRIDpolicy без LLM. Поле Search.mode в HTTPсхеме сейчасDEMO, однако этот structuredsearch не перезаписываетpolicy. Сохранить safeTOOLevents, количествоactualHTTP иresult/partial_plan; boundedpoll.
4. Проверить coverageровно9безoverlap, sourceкомпонентов, roomassignments иtotalRUB≤budget. Для meaningfultwo-stayactualflow **оба hotelbundle должны действительно прийти из LiteAPI**, а не DEMO. Еслиplan целикомDEMO/одинactualhotel — это PARTIAL, даже если `plans` непустой. Не вписывать sourcefacts вручную для готовности.
5. Только если план пригоден и не истёк: один `POST .../checkout/preview` с действительнымиplanID/stateversion. API сначала отзывает прежние activequotes; repriceисполняется для каждого внешнего компонента, DEMOпересчитывается локально. Сохранить before→after и всеpricechanges. При отказе/измененииidentity зафиксировать исход; не менятьofferIDдлясовпадения.
6. После успешного новогоquote выполнить `confirm` с егоquoteID/version, текущейstateversion, `confirmed:true`, новымidempotencykey. Повторить точнототжеpayload/key; одинorderID/одназапись, `BOOKED_DEMO`, `is_demo:true`, `booking_created_at_supplier:false`. Последующий GETпроверяетсохранение. Внешних callsнаconfirm0. Подтверждение поднимает tripstateversion; читать его заново, не оживлять старыйconsent.

## Что именно означает refresh/TTL

LiteAPIreprice сейчас — повторный `hotels/rates` и поиск того же `native_offer_id`, **не prebook**. Если supplierвыдал новыйofferId, код вернёт `OFFER_UNAVAILABLE`; это допустимый честныйblockedисход, а не поводсравниватьтарифыпоимени. Duffelrefresh — GETконкретногоtestoffer; это не создание авиазаказа. API дополнительно проверяет provider/offerID/kind/nativeID/hotelID/searchrequest/binding и заново собирает economicfacts.

`TravelGateway._offer()` назначает LiteAPIлокальный5minTTL; это engineeringfreshness, не supplierhold. Duffelиспользует returnedexpires_at, а при его отсутствии код тоже fallback5min: отсутствиеисходногоsourceexpiry отдельноотметить, не выдавать локальныйTTLзафакт поставщика. Оба timestampсtimezone; `retrieved_at≤now<expires_at` наsearch/preview. StorequoteTTL≤600сек и≤минимальногоexpires_atвнешнихsnapshot. Не замораживать фактический HTTPclock в прошлом и не продлеватьистёкшийsnapshot безrefresh.

## Критерии отчёта

- `PASS_MIXED_ACTUAL_TWO_STAY_FLOW`: два actualLiteAPIbundle с2комнатами каждый, verifiedallocation/fees, идентичные успешноrefreshIDs, все9покрытий иRUBtotal, одинpersisted simulatedorder. В отчёте отдельно число actualDuffellegs;0не считается транспортнымPASS. CapacityunknownоставляетcapacityscopePARTIAL.
- `PARTIAL_SOURCE_FLOW`: толькоодинactualstay, толькоотдельныевалидныепредложения, полностьюDEMOfallback или sourcecapacity/passengers evidenceнеполны. Запуск/работающийendpointне закрываютHARNESS001.
- `GUARD_BLOCKED`: fx/неизвестныеfees/allocation/истёкшийилиисчезнувшийoffer честноблокируютquote. Записать exactcode+sourcefacts; неизменятьfixtureпослеответа и не ослаблятьguard.
- `FAIL_ADAPTER_OR_FLOW`: сохранённыеactualfacts достаточны, но код теряетroom/group/identity/TTL или неправильноприменяет их. Нужен отдельный finding с воспроизводимым evidence, а не автоматическое обвинение upstream.

Артефакткорня: code/fixtureSHA, безопасныевыбранныеsourcefacts иpayloadscope, actualHTTPledgerhost/method/statusбезquerykeys, fieldassertions, snapshot/time/currency/coverage, preview/confirmитоги и незакрытыеIDs. Bearertokens/APIkeys/Authorization/скрытыеcookieиHTTPдампы не сохранять. Это не livebooking, не certificationпоставщика, не сравнениеполных рынков и не actualNLU106/100диалогов.
