# Настройка API

Ключи храните в локальном `.env`. Для демонстрационного поиска они не нужны. После изменения настроек нужно пересоздать контейнер приложения:

```bash
docker compose up -d app
```

## Модель

Основной провайдер выбирается через `LLM_PROVIDER`.

| Провайдер | Настройки |
| --- | --- |
| Groq | `LLM_PROVIDER=groq`, `GROQ_API_KEY`, `GROQ_MODEL=qwen/qwen3.8-27b`, `GROQ_FREE_TIER_CONFIRMED=true` |
| OpenRouter | `LLM_PROVIDER=openrouter`, `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` с окончанием `:free` |
| Gemini | `LLM_PROVIDER=gemini`, `GEMINI_API_KEY`, `GEMINI_MODEL=gemini-3.8-flash`, `GEMINI_FREE_TIER_CONFIRMED=true` |
| Cloudflare | `LLM_PROVIDER=cloudflare`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_MODEL=@cf/meta/llama-3.3-70b-instruct-fp8-fast`, `CLOUDFLARE_FREE_TIER_CONFIRMED=true` |

Флаги Free ставятся после проверки тарифа в кабинете провайдера. Приложение не подключает оплату. У OpenRouter дополнительно запрещены ненулевые цены и неявный выбор платной модели.

[Ключи Groq](https://console.groq.com/keys) · [Ключи OpenRouter](https://openrouter.ai/settings/keys) · [Ключи Gemini](https://aistudio.google.com/api-keys)

Для дополнительных провайдеров: [регистрация и бесплатные квоты Z.ai, Cohere, Cloudflare и NVIDIA](LLM_ACCOUNTS.md). Их доступ и подключение к проекту проверяются отдельно.

В настроенной WSL-среде ключи можно ввести без отображения в терминале:

```powershell
py scripts/configure_keys.py
```

Enter пропускает отсутствующий ключ. Эта команда сохраняет ключи, но не меняет основной провайдер.

Для Cloudflare разрешена указанная модель Workers Free, одна попытка на сообщение и отдельный предел40вызовов в сутки. Это ограничение приложения; фактическая квота Cloudflare измеряется в neurons. Автоматического переключения с Cloudflare на другой источник нет.

Ключи Z.ai, Cohere и NVIDIA пока можно сохранить для следующих сравнений. Сохранённый ключ не означает готового runtime-адаптера. Первая короткая проверка доступа Z.ai прошла; качество его диалогов ещё не измерено. У Cloudflare единичная проба извлечения выявила пропущенное направление — полная приёмка открыта.

Бесплатные квоты ограничивают и число запросов, и объём токенов. При429 не отправляйте сообщение многократно. Поездка сохраняется; причина отказа и доступное время повтора записываются в журнал, если их сообщил поставщик.

При недоступной модели остаются демосценарий и структурированные команды. Свободный диалог требует работающей LLM.

## Туристические данные

| Источник | Настройки | Данные |
| --- | --- | --- |
| LiteAPI | `LITEAPI_API_KEY`, `LITEAPI_MODE=sandbox` | Тестовые отели и тарифы |
| Duffel | `DUFFEL_ACCESS_TOKEN` с префиксом `duffel_test_` | Тестовые перелёты |
| SerpApi | `SERPAPI_API_KEY`, `SERPAPI_FREE_TIER_CONFIRMED=true` | Цены из поиска, без гарантии покупки |
| Open-Meteo | Ключ не нужен | Прогноз погоды |

Для Hotelbeds, RateHawk и Travelpayouts есть адаптеры, но доступ к аккаунтам пока не проверен. Настройки для них перечислены в `.env.example`.

Каждое предложение хранит источник, время получения и тип данных: `DEMO`, `SANDBOX`, `LIVE_INDICATIVE` или `LIVE_BOOKABLE`. Смешанная поездка не становится подтверждённой рыночной корзиной только из-за успешного поиска.

Перед тестовым оформлением цена и наличие перепроверяются. Настоящие заказы у поставщиков и платежи приложение не создаёт.
