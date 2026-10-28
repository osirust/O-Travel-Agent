# Динамическая география: минимальный provider-контур

**Статус: предложение, не реализация и не обещание всемирной доступности.** Текущий код восьми направлений зафиксирован на время проверки. Сохранены actual-доказательства: [LiteAPI: RS/Belgrade, 3 отеля/596 SANDBOX rates](../verification/belgrade-liteapi-coverage-20261027.json) и [Duffel: RS/BEG place identity](../verification/belgrade-duffel-place-20261027.json). Они не подтверждают другие города, production-цены или наличие перелётов. Новых запросов для этого документа нет.

## Где менять границу

Ручной список допустимых городов нельзя увеличивать по одному. Нужны подтверждённые справочные identity, затем независимый поиск предложений. При этом adapter-only замены недостаточно: выше стоят `canonical_city`, статический city enum в extraction schema и coverage-вопросы. Domain/schema/диалог должны принимать согласованный `CityRef`; provider-контур не расширяет их сам и не добавляет города в DEMO-каталог.

Для «хочу в Аргентину» сначала определяется страна и спрашивается город. Ни Буэнос-Айрес, ни столица другой страны не выбираются автоматически. Название страны назначения, страна отправления и `guest_nationality` — разные поля; гражданство не служит геокодером.

## Справочники и единый контракт

| Операция | Документированный вход | Проверяемый результат |
|---|---|---|
| LiteAPI country list | `GET /v3.0/data/countries`, optional `timeout` | ISO-2 и название страны; небольшой reference-list, не каталог отелей. [Источник](https://docs.liteapi.travel/reference/get_data-countries). |
| LiteAPI city list | `GET /v3.0/data/cities`, required `countryCode`, optional `timeout` | Точные provider city names для одной выбранной страны. City ID/часовой пояс этим контрактом не гарантированы. [Источник](https://docs.liteapi.travel/reference/get_data-cities). |
| LiteAPI hotel IDs | `GET /v3.0/data/hotels`, `countryCode` + `cityName`, bounded `limit` | IDs и country/city гостиниц. Значения должны соответствовать справочнику; пустой список не доказывает отсутствия города. [Параметры](https://docs.liteapi.travel/reference/get_data-hotels), [точное совпадение имён](https://docs.liteapi.travel/docs/preparing-a-list-of-hotel-ids). |
| Duffel place suggestions | `GET /places/suggestions`, `query`; optional `lat/lng/rad` | Provider ID, `type=city/airport`, IATA и country; timezone может отсутствовать. У этого endpoint нет country-filter: сверяем `iata_country_code` в ответе. [Источник](https://duffel.com/docs/api/places). |
| LiteAPI rates | Отдельный `POST /hotels/rates`: даты, валюта, citizenship, occupancies и IDs либо другой поддержанный location selector | Цены/условия конкретного поиска, отдельно от identity/cache. Первой реализацией сохраняем существующий путь через hotel IDs. [Источник](https://docs.liteapi.travel/reference/post_hotels-rates). |

Предлагаемый API адаптеров:

```text
resolve_country(query, budget, deadline) -> Resolution
resolve_city(query, country_iso2, budget, deadline) -> Resolution
resolve_air_place(query, country_iso2, kind, budget, deadline) -> Resolution
resolve_hotels(verified_city_ref, limit<=3, budget, deadline) -> HotelIdentity[]
```

`Resolution.status`: `RESOLVED`, `AMBIGUOUS`, `NO_MATCH`, `UNAVAILABLE`, `BUDGET_EXHAUSTED`; `candidates[]`, проверенные `source_facts`, время и использованные вызовы. Пустые/ошибочные ответы не превращаются в вымышленный `RESOLVED`.

Согласованный с domain/geo DTO: `city_id` (внутренний), `canonical_name`, `country_iso2`, nullable `timezone`, `identity_version`, `provider_refs[]`, provenance/дата проверки. Для LiteAPI ref содержит **точное provider_city_name**; если источник не дал external city ID, поле остаётся null. Внутренний hash-ключ страны/имени не называется внешним ID. Для Duffel сохраняются реальный external ID, city/airport kind, IATA/country и подтверждённые аэропорты. IATA не выводится из написания города; timezone не выводится из страны.

Domain получает code-owned immutable snapshot, а не выполняет HTTP/DB lookup при валидации. Модель может предложить `resolve_place`; последующий выбор адресует только server-bound ref/version. Переданный моделью полный `CityRef` не считается доказательством; расширение operation/schema/wire-контрактов требует отдельной проверки совместимости.

Имена сопоставляются Unicode-normalization/casefold и точными подтверждёнными aliases. Перевод/опечатка от модели — кандидат, который проверяется по справочнику; неоднозначное соответствие требует выбора пользователя. При одноимённых городах country обязателен. Для города с несколькими аэропортами можно использовать подтверждённый city-IATA; первый аэропорт из списка автоматически не выбирается. Никакого свободного city/ID/URL из текста модели в supplier request.

## Cache, бюджет и ошибки

Минимальное долговременное хранилище справочных фактов: provider + kind + country + нормализованный query/type; отдельная immutable revision с source ID/name/country/IATA, response SHA, retrieved/expiry и status. Рекомендованные **инженерные** TTL: country/city 7 дней, place suggestions 24 часа, отрицательный результат 15 минут; transport errors не записываются как «города нет». Числа не являются vendor-гарантией актуальности. Объём/кардинальность cache и размер ответа ограничены; запрашиваются только нужные страны, без обхода всех стран и без загрузки мирового hotel-каталога. На одновременный miss одного ключа — single-flight.

Trip хранит свою версию выбранного `CityRef`. Обновление общего cache не переименовывает поездку, не переносит выбранные гостиницы и не оживляет quote/consent. Старый positive snapshot может объяснять ранее выбранную identity; актуальная цена всё равно требует свежего поиска и перепроверки. Секреты, заголовки, raw ошибки и PII в cache не нужны.

Все directory HTTP считаются в **том же** run tool budget до вызова. Cold путь для нового назначения с известным origin: countries miss — 1, cities страны miss — 1, Duffel suggestions miss — 1, hotel IDs miss — 1; rates/перелёты/FX оплачиваются оставшимся бюджетом отдельно. Cache hit — 0 внешних вызовов. Для многих городов дедуплицируем страны/запросы; при недостатке бюджета возвращаем незавершённую resolution, а не скрытый fan-out/новый UUID/retry. Directory timeout — до 4 с, общий deadline остаётся общим: endpoint timeout не добавляется поверх него. Никаких автоматических повторов после 429/ошибки или снятия явной user pause.

Покрытие — результат конкретного component request, не вечный флаг «весь мир поддержан». Нет отелей/рейсов/подтверждённого трансфера — сохраняется маршрут и partial plan с причиной; API identity не создаёт DEMO-отель/трансфер и не разрешает оформление неполной корзины.

## Минимальная последовательность внедрения

1. Domain/geo/schema согласуют versioned `CityRef` и отдельный unresolved candidate; прежние городские строки совместимы с существующими поездками. Не перечислять мировой справочник в каждом LLM prompt.
2. Provider resolver/cache внедряются за этим контрактом; сначала controlled tests: country/city homonyms, переводы/опечатки, multi-airport, пустой/error ответ, cache miss/single-flight/TTL, budget-stop и отсутствие любой подстановки citizenship/origin.
3. После общего прогона — отдельно ограниченный actual путь новой страны/города через тот же Harness. Только его результат квалифицирует source coverage; прежние 3 LiteAPI + 1 Duffel остаются отдельными доказательствами. DEMO7 и исторические FAIL не переписываются.
