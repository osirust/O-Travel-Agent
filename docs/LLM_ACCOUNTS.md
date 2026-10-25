# Дополнительные аккаунты LLM

Условия проверены по официальной документации 25.10.2026. На26.10 у команды работают ключи Z.ai и Cloudflare; качество диалогов ещё проверяется. Эта инструкция не переключает работающую модель. Ключи храните только в локальной конфигурации, не в Git или чате.

## Z.ai — Standard API

1. Зарегистрироваться на [API Platform](https://z.ai/model-api), открыть [API Keys](https://z.ai/manage-apikey/apikey-list).
2. Создать **Standard API Key**, например `OTravel-Evaluation`.
3. Для первого сравнения выбрать `glm-4.7-flash` или `glm-4.5-flash`: input/output бесплатны по [прайсу](https://docs.z.ai/guides/overview/pricing). Численные лимиты запросов и токенов нужно проверить в кабинете.

Обычный API endpoint: `https://api.z.ai/api/paas/v4`. Coding Plan, bundles и пополнение баланса не нужны для этого этапа. `glm-5.3-flash` — платная модель. Требования телефона/карты для конкретного нового аккаунта пока неизвестны; при запросе оплаты не продолжать. [Официальный порядок получения ключа](https://docs.z.ai/guides/overview/quick-start).

Наша первая проверка `glm-4.7-flash`: HTTP200, ожидаемый короткий JSON,85 vendor tokens. Это проверка доступа. Runtime-адаптер добавлен отдельно; многошаговое качество проверяется на зафиксированной версии.

## Cohere — evaluation key

1. Открыть [регистрацию](https://dashboard.cohere.com/welcome/register), подтвердить аккаунт.
2. В [API keys](https://dashboard.cohere.com/api-keys) выбрать **trial/evaluation**. Не включать production или платёжный метод.
3. Кандидат: `command-a-plus-05-2026`. Для trial действуют **1000 API-вызовов в месяц**, для Command A+ — **20 запросов в минуту**. [Лимиты](https://docs.cohere.com/docs/rate-limits), [модель](https://docs.cohere.com/docs/command-a-plus).

Карта отсутствует в начальной форме регистрации; дальнейшие проверки аккаунта и региональная доступность ещё не подтверждены. 1000 вызовов не равны 1000 полным диалогам. При GitHub-входе доступ к приватным репозиториям для этой задачи не требуется.

## Cloudflare — Workers AI Free

1. Создать [Free account](https://dash.cloudflare.com/sign-up/workers-and-pages), открыть [Workers AI](https://dash.cloudflare.com/?to=/:account/ai/workers-ai).
2. Выбрать `Use REST API` → `Create a Workers AI API Token`. Сохранить token и Account ID. Для ручного токена нужны только `Workers AI - Read` и `Workers AI - Edit` на свой аккаунт. [Инструкция](https://developers.cloudflare.com/workers-ai/get-started/rest-api/).
3. Оставаться на Workers Free: **10000 neurons в сутки**, сброс **03:00 МСК**. После исчерпания Free возвращает ошибку; это не фиксированное число сообщений. [Тариф](https://developers.cloudflare.com/workers-ai/platform/pricing/).

Кандидаты на Free: `@cf/zai-org/glm-4.7-flash`, `@cf/google/gemma-4-26b-a4b-it`, `@cf/nvidia/nemotron-3-120b-a12b`. [Разделение Free/Paid](https://developers.cloudflare.com/changelog/post/2026-07-28-models-require-workers-paid/). Бесплатный аккаунт [не требует карту](https://developers.cloudflare.com/labs/workers). Не выбирать Workers Paid, prepaid AI Gateway credits, платные GLM-5.x/DeepSeek V4/Kimi K2.6 и не создавать Global API Key.

В текущем адаптере разрешена только `@cf/meta/llama-3.3-70b-instruct-fp8-fast`, для которой официально указан JSON Mode. Первая actual проба прошла схему, но потеряла направление поездки; это открытая проблема. Остальные кандидаты пока не включены в allowlist и не проверены.

## Дополнительно: NVIDIA hosted NIM

[API Catalog](https://build.nvidia.com/) → Login/регистрация → модель → `Get API Key`. При необходимости выбрать бесплатное членство Developer Program, не Enterprise trial или платную GPU. Если доступны service scopes — только AI Foundation Models and Endpoints. Телефон и сведения о стране указывать фактические; страну VPN нельзя считать страной проживания.

[NVIDIA подтверждает](https://developer.nvidia.com/nim) бесплатное hosted-прототипирование. Конкретные rate limits нужно записать из профиля: они зависят от модели/нагрузки согласно [FAQ](https://forums.developer.nvidia.com/t/nvidia-nim-faq/300317). Текущая численная квота нового аккаунта неизвестна. Старые обещания «1000 credits» и отдельный 90-day Enterprise trial не подтверждают её.

После получения доступа сначала проверяем тариф и модель, затем один короткий запрос с учётом usage/лимитов. Массовое сравнение запускается отдельно на одинаковых входах и состояниях поездки. Работающий стенд во время текущего прогона диалогов сохраняем без переключений.
