# Настройка API

Редакция: 26 октября 2026. Это настройки реализованных адаптеров и переносимого backend, без личных ключей и обещаний vendor-квоты. HTTP-контракт — [FRONTEND_CONTRACT.md](FRONTEND_CONTRACT.md), карта для frontend — [FRONTEND_HANDOFF.md](FRONTEND_HANDOFF.md).

## Запуск на другом компьютере

Нужны доступ к репозиторию и работающий Docker с Compose. Из корня своего checkout:

```bash
docker compose up --build -d
```

Backend: `http://localhost:8080`; `/health` проверяет PostgreSQL, `/docs` и `/openapi.json` описывают API, `/debug` — временный тестовый чат. Compose поднимает свою PostgreSQL 17; отдельный сервер БД и персональные WSL-скрипты владельца не требуются. Если порт занят, задайте `APP_PORT` в своём `.env`.

Для настроек скопируйте [.env.example](../.env.example) в собственный `.env`. Задайте локальный `POSTGRES_PASSWORD` до первого запуска БД и добавьте нужные ключи. `.env` игнорируется Git. Изменение этой переменной позже само по себе не ротирует пароль уже созданной БД. Ключи остаются на backend: не переносите их во frontend, Vite-переменные, URL или сообщения.

После изменения env-конфигурации приложения:

```bash
docker compose up -d app
```

Если изменился код или зависимости, нужна пересборка `docker compose up --build -d app`. Не удаляйте volume ради применения настроек. Для другого frontend-origin задайте `CORS_ORIGINS`; обычные localhost 5173/3000 уже предусмотрены.

Переносимые defaults: `LLM_PROVIDER=openrouter`, `OPENROUTER_MODEL=dots-studio/dots-3-note-preview:free`, `DEFAULT_SEARCH_MODE=DEMO`, hotel LiteAPI / flight SerpApi. Пустой ключ не имитирует NLU: без него работают формы, control, DEMO и ограниченные ответы по сохранённым фактам. Произвольный свободный диалог требует доступной модели.

`configure_keys.py` и lifecycle-скрипты `after80_*` относятся к личному стенду владельца; запускать их другу для настройки своего Docker-приложения не требуется.

## Модель

Основной провайдер выбирается через `LLM_PROVIDER`. В таблице — поддержанные кодом ID и поля конфигурации; это не гарантия доступности модели у конкретного аккаунта.

| Провайдер | Настройки |
|---|---|
| OpenRouter | `LLM_PROVIDER=openrouter`, `OPENROUTER_API_KEY`, `OPENROUTER_MODEL=dots-studio/dots-3-note-preview:free` или другой явно выбранный `:free` ID. |
| Groq | `LLM_PROVIDER=groq`, `GROQ_API_KEY`, `GROQ_MODEL=qwen/qwen3.8-27b`, `GROQ_FREE_TIER_CONFIRMED=true`. Строгий транспорт включается отдельно, как описано ниже. |
| Gemini | `LLM_PROVIDER=gemini`, `GEMINI_API_KEY`, `GEMINI_MODEL=gemini-3.8-flash`, `GEMINI_FREE_TIER_CONFIRMED=true`. |
| Cloudflare | `LLM_PROVIDER=cloudflare`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_MODEL`, `CLOUDFLARE_FREE_TIER_CONFIRMED=true`. |
| Z.ai | `LLM_PROVIDER=zai`, `ZAI_API_KEY`, `ZAI_MODEL=glm-4.7-flash` или `glm-4.5-flash`, `ZAI_FREE_TIER_CONFIRMED=true`. |

Free-флаги ставятся человеком после проверки тарифа своего аккаунта. Приложение не включает billing. OpenRouter допускает только `:free`, отправляет ограничения `max_price=0` и отключает неявный paid fallback. `OPENROUTER_BACKUP_MODEL` по умолчанию пуст: резерв должен быть выбран явно и тоже иметь `:free` ID. Account/key/credit и неизвестные 429 переключением модели не обходятся.

Разрешённые профили Cloudflare: default `@cf/meta/llama-3.3-70b-instruct-fp8-fast`, а также `@cf/google/gemma-4-26b-a4b-it` и `@cf/nvidia/nemotron-3-120b-a12b`. Для двух последних thinking отключён. Их наличие в конфигурации не означает, что фактическая бесплатная квота ещё доступна.

Z.ai работает через Standard API с отдельным ключом. Наблюдавшийся 1113 означает отсутствие баланса/ресурса у текущего аккаунта. Это не доказательство постоянной недоступности Z.ai или другой учётной записи. Cohere/NVIDIA пока не являются подключёнными runtime-провайдерами; запись ключа сама по себе адаптер не добавляет.

[Ключи OpenRouter](https://openrouter.ai/settings/keys) · [Groq](https://console.groq.com/keys) · [Gemini](https://aistudio.google.com/api-keys) · [дополнительные аккаунты и условия доступа](LLM_ACCOUNTS.md)

Для явного включения строгой структуры ответа Groq:

```dotenv
GROQ_REASONING_EFFORT=none
GROQ_STRUCTURED_TRANSPORT_ENABLED=true
```

Флаг по умолчанию выключен. Он меняет только обмен backend с моделью: сервер задаёт допустимые ID, декодирует значения и повторно проверяет каноническую схему. HTTP-контракт frontend остаётся прежним. Для вопросов модель выбирает разделы, а фактические суммы, состав и даты выводит сервер. Это не разрешение на платный fallback и не увеличение квоты. Параметр `reasoning_effort=none` документирован для [instruct-режима Groq Qwen](https://console.groq.com/docs/model/qwen/qwen3.8-27b). Реальную языковую приёмку учитывать отдельно от проверки JSON.

## Лимиты и ошибки модели

| Настройка | Реализованный предел приложения |
|---|---|
| `MAX_LLM_CALLS_PER_DAY` | Default 40; для Z.ai до 2000, для OpenRouter допускается явный профиль50. Это предел приложения, не остаток аккаунта. |
| `MAX_CLOUDFLARE_LLM_CALLS_PER_DAY` | Отдельный Cloudflare-пул: default 40, максимум 2000; переменная передаётся и через Compose. |
| `MAX_GROQ_LLM_CALLS_PER_DAY` | Отдельный Groq-пул: default40, максимум1000; vendor TPM/TPD всё равно ограничивают доступ. |
| `MAX_LLM_CALLS_PER_RUN` | Default 2 для Gemini/OpenRouter; Groq/Cloudflare/Z.ai ограничены одной попыткой без автоматического fallback. Readonly-ответ — не более одного вызова. |

Эти числа не увеличивают vendor-квоту. Cloudflare учитывает neurons, а не количество сообщений. Для подтверждённой 3036 известен суточный сброс 00:00 UTC; для неизвестного 429 инженерное ожидание не является обещанием vendor-reset. Z.ai billing/usage без подтверждённого reset не получает выдуманное время восстановления. Не повторяйте автоматически сообщения с новыми UUID после 429: сначала прочитайте результат run и его журнал.

`GET /api/v1/providers/status` показывает конфигурацию, а не авторизацию, баланс или доступность модели. Фактический провайдер/модель и известный расход находятся в metadata конкретного run. Неизвестный расход не равен нулю. При отказе LLM остаются сохранённое состояние, формы и DEMO.

## Текущий стенд владельца

`http://localhost:8084` — отдельный стенд владельца; модельный профиль меняется между зафиксированными прогонами. Актуальные provider/model берутся из `/api/v1/providers/status` и PROJECT_STATE.md. HYBRID использует LiteAPI Sandbox, Duffel test и SerpApi Free. Это не переносимый default; на другом компьютере фронтендер запускает свой backend по инструкции выше.

Для каждого прогона отдельно фиксируются версия, профиль и состояние квот. Текущий provider проверяется через `/api/v1/providers/status` и metadata run; исторический отчёт Z.ai/DEMO не описывает более позднее развёртывание. Менять параметры чужого активного прогона не следует.

## Туристические данные

| Источник | Настройки | Что реально означает ответ |
|---|---|---|
| LiteAPI | `LITEAPI_API_KEY` с префиксом `sand_`/`sandbox_`, `LITEAPI_MODE=sandbox` | Тестовые гостиницы и тарифы SANDBOX. |
| Duffel | `DUFFEL_ACCESS_TOKEN` с префиксом `duffel_test_` | Тестовые авиационные предложения. Валюта ответа может отличаться от запрошенной. |
| SerpApi | `SERPAPI_API_KEY`, `SERPAPI_FREE_TIER_CONFIRMED=true` | Поисковые цены LIVE_INDICATIVE; multiroom allocation этим источником не подтверждается. |
| Open-Meteo | Ключ не нужен | Прогноз на ближайшие дни с attribution; не гарантия погоды на даты будущей поездки. |

Hotelbeds, RateHawk и Travelpayouts имеют ограниченные read-only адаптеры; доступ к их аккаунтам и production-покрытие не подтверждены. Не считайте пустое поле в `.env.example` подключённым источником.

Ключ сам по себе не меняет существующую поездку. Для общего поиска задаётся `trip.search_policy.mode=HYBRID`, выбранные providers, `guest_nationality` для LiteAPI и явный `demo_fallback`; затем используется `POST /api/v1/sessions/{id}/search`. `/live/search` возвращает отдельные компоненты, а не целую автоматически проверенную корзину. Default-параметры сервера применяются только к новым сессиям без явной `initial_trip.search_policy`.

Проверен ограниченный смешанный путь: два проживания SANDBOX с отдельными семьями/четырьмя номерами, перепроверка и один идемпотентный `BOOKED_DEMO`; часть транспорта остаётся DEMO. Это не полностью live/bookable-поездка и не приёмка всех языковых диалогов.

Предложения различают `DEMO`, `SANDBOX`, `LIVE_INDICATIVE` и нормализованный source type; название API не подтверждает наличие мест. Preview обновляет доступные внешние компоненты, но sandbox rate-refresh и indicative search-refresh не равны supplier prebook. Сборы, распределение, вместимость и доступность читаются по фактическим флагам ответа. Подтверждение создаёт только локальный тестовый заказ; реальной оплаты и брони нет.

Backend сохраняет исходную валюту поставщика и использует отдельный справочный снимок CBR для поддерживаемого пересчёта RUB/USD/EUR. Курс имеет источник, дату и срок действия; банковские комиссии не угадываются. При отсутствии курса общий сопоставимый итог не подтверждается. Рублёвый DEMO и sandbox не становятся настоящей рыночной ценой.

Отдельный авторизованный [photo API](HOTEL_MEDIA.md) возвращает изображения по ID выбранного отеля; media не меняют экономическое содержимое корзины. Выбор валюты и отображение фото во frontend интегрирует сокомандник. SSE отсутствует: результаты и события читаются JSON-polling.
