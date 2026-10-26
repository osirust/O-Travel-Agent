# Фотографии отелей

Для карточек предложений используется отдельный запрос. Поиск, цена и предварительная корзина работают независимо от загрузки фотографии.

```http
GET /api/v1/sessions/{session_id}/media?plan_id={plan_id}&state_version={version}
Authorization: Bearer {session_token}
```

Backend берёт ID отеля из уже сохранённого плана этой сессии. Произвольный URL, название гостиницы или новый hotel ID клиент не передаёт. Устаревшая версия/план возвращает 409, чужая сессия — 403.

```json
{
  "session_id": "session-id",
  "state_version": 3,
  "plan_id": "plan-id",
  "items": [{
    "offer_id": "hotel-offer-id",
    "component_id": "stay-id",
    "media": {
      "url": "https://static.cupid.travel/hotels/210194244.jpg",
      "thumbnailUrl": null,
      "alt": "Фотография отеля",
      "kind": "HOTEL",
      "source": "LITEAPI",
      "hotelId": "lp1b30d1",
      "sourceHotelId": "lp1b30d1",
      "isActualPropertyPhoto": true,
      "caption": "Фото отеля",
      "gallery": [{"url": "https://static.cupid.travel/hotels/210194244.jpg", "alt": "Фотография отеля", "caption": null}]
    }
  }],
  "warnings": []
}
```

Это пример формы ответа и реального content URL, не предложение с подтверждённой ценой. `component_id` может быть null. Галерея ограничена пятью снимками; один запрос охватывает до пяти разных отелей. Статический контент кэшируется на шесть часов, неуспешный lookup — на минуту. Цены этим кэшем не покрываются.

Поддержан контент LiteAPI по совпавшему hotel ID. DEMO и неподтверждённый mapping возвращают `kind="PLACEHOLDER"`, `url=null`, `isActualPropertyPhoto=false`. Фото конкретного номера этим контрактом не подтверждается. SerpApi-картинки и фото направлений пока не подключены.

Frontend после результата поиска загружает медиа асинхронно. Перед показом проверяет `session_id`, `state_version`, `plan_id` и соответствие `offer_id` текущей карточке. На смене поездки/версии отменяет запрос. При `url=null`, ошибке API или `img.onerror` показывает нейтральную заглушку. Цена, кнопки выбора и чат остаются доступны.

URL выдаются только для проверенных HTTPS CDN из backend allowlist. В `/debug` те же домены разрешены через CSP; у отдельного frontend нужно согласовать `img-src`. Не передавать ключ LiteAPI браузеру и не проксировать произвольные изображения. Сведения об источнике фотографии сохраняются; дополнительные права на использование/перепубликацию контента не выдумываются.

Источники схемы: [LiteAPI hotel details](https://docs.liteapi.travel/reference/get_data-hotel), [displaying hotel details](https://docs.liteapi.travel/docs/displaying-hotel-details). Фактический CDN `static.cupid.travel` также подтверждён ответом подключённого LiteAPI для `lp1b30d1` 26 октября 2026 года.
