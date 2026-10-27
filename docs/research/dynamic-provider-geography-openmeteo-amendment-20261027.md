# Уточнение foundation: физическая identity через Open-Meteo

Решение заменяет LiteAPI-name-only основу из [предыдущего предложения](dynamic-provider-geography-proposal-20261027.md). Справочник LiteAPI остаётся provider-association, а не глобальной физической identity. Runtime ещё не реализован; текущий исправляемый core не менялся.

## Проверенный контракт

Open-Meteo документирует глобальный `/v1/search`: `name`, `count` до100, `language`, явный `countryCode`; результат содержит location ID, country/admin IDs, координаты и timezone. Язык может откатиться к английскому/локальному имени. Поиск от трёх символов — normalized **prefix**, не произвольное исправление опечаток. Одноимённые места/аэропорты требуют различения по типу, стране и admin. [Официальная документация](https://open-meteo.com/en/docs/geocoding-api).

`/v1/get?id=…&language=…` поддержан официальным исходником: `GetQuery` принимает ID, optional language/format; язык по умолчанию и неизвестный язык откатываются к `en`, затем извлекается запись по ID с выбранным language ID. Это подтверждает возможность получить RU-display и EN-название **одного** места без нового name-search. [Pinned source b404e4d5](https://github.com/open-meteo/geocoding-api/blob/b404e4d5c75eb32a4cec68a665d1c33f523b0dab/Sources/App/GeocodingapiController.swift). Исходник изучен как контракт, не копируется и не устанавливается как зависимость.

В [двух разрешённых keyless чтениях](../verification/openmeteo-geocoding-ar-20261027.json) synthetic `Buenos Aires/countryCode=AR/language=ru` вернул4 кандидата. Для выбранного **только в проверочном fixture** ID3435910 search-RU дал «Буэнос-Айрес», get-EN — «Buenos Aires». Совпали country/admin IDs, координаты и `America/Argentina/Buenos_Aires`. Другой одноимённый населённый пункт в AR имел другой ID и timezone: страна сама по себе не определяет город/часовой пояс. Столица в пользовательской поездке не выбиралась. HTTP status/timing не сохранились из-за Windows-ошибки записи отчёта; receipt восстановлен из двух raw JSON **без повторных запросов**.

## Изменение интерфейсов и cache

- `PlaceDirectory.search(query, country_iso2?, language, count<=5, budget, deadline)` возвращает candidate snapshots; несколько подходящих мест → AMBIGUOUS, не первый результат.
- `PlaceDirectory.get(source_id, language, budget, deadline)` возвращает тот же verified ID либо отказ. Для двух языков сверяются ID/country/admin IDs/координаты; одних похожих имён недостаточно.
- `CityRef` использует `source=open_meteo_geonames`, исходный numeric ID, immutable identity revision, localized names, country/admin, lat/lon, nullable timezone и provenance. Snapshot кодом прикрепляется к версии TripState; модель не может прислать готовый доверенный объект.
- LiteAPI external city ID не выдумывается. Для гостиничного запроса используется отдельно проверенный country/provider label, например EN-имя из `get` плюс сверка каталога. Несовпадение label — не отсутствие физического города. Возможный location-based поиск по координатам требует отдельной проверки и явного сохранения ограниченного search-scope; он не объявляется поиском всех отелей города.
- Duffel city/airport ID и IATA связываются отдельно по подтверждённому месту/стране. Нельзя вывести IATA из GeoNames ID или автоматически выбрать первый аэропорт.

Cache разделяет search-key `(query,country,language)` и identity-key `(source_id,language)`. Обновление label не переписывает выбранные Trip refs/quotes. HTTP cache miss оплачивается общим tool budget; при cold пути search1 + EN-get1 добавляются к provider discovery/rates/flights/FX, не исполняются скрыто. Только запрос нужного места, без мирового dump или новых тяжёлых библиотек. Поиск страны «Аргентина» остаётся country-resolution с вопросом о городе; гражданство используется отдельно.

Следующий этап после полного core-прогона и отдельного GO: новый изолированный `place_directory.py` с MockTransport-контрактами search/get, bounds, errors/ambiguity, ID/language consistency и budget/deadline. Затем domain/schema/harness интеграция. Ни эти два чтения, ни стабильная география не доказывают наличие отелей/рейсов, бронируемость или завершённый глобальный Travel Agent.
