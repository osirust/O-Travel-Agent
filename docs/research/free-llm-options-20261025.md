# Бесплатные LLM API: кандидаты для сравнительного прогона

Проверено по официальной документации 25.10.2026. Ни один новый аккаунт/ключ или inference этим обзором не создан. Бесплатность в документации не доказывает доступность для конкретного аккаунта, региона и запроса. Вывод о качестве — пока гипотеза до одинакового теста.

Локальная Qwen3.5:4b — небольшой dev-стенд. Ошибки семантики возможны, но наблюдавшиеся потери контекста, неверные вопросы и schema failures частично вызваны harness. Development7 после исправления age-slot прошёл на этой же модели; это не доказывает качество всего106. Сильная модель нужна для сравнения, но не заменяет исправления хранения и применения требований.

| Кандидат | Подтверждено | Что проверить до выбора |
| --- | --- | --- |
| Gemini3.8Flash напрямую | Есть free input/output в Standard API; adapter уже существует. [Pricing](https://ai.google.dev/gemini-api/docs/pricing). | Нужен ключ проекта без billing и фактическая доступная квота; лимиты per-project видны в AI Studio. [Limits](https://ai.google.dev/gemini-api/docs/rate-limits). Ключа сейчас нет, actual quality не проверено. |
| Groq, Qwen3.8-27B | Официально listed free30RPM/1000RPD/8000TPM/200000TPD; exact account limits могут отличаться. [Limits](https://console.groq.com/docs/rate-limits). Structured Outputs поддерживаются; strict гарантирует форму, не смысл. [Schema](https://console.groq.com/docs/structured-outputs). | Независимый от OpenRouter аккаунт/ключ и отдельный adapter. Наша schema с optional fields/oneOf требует проверки совместимости; длинный TripState может упереться в token quota раньше1000RPD. Сначала10целевых диалогов, не обещание всего106 за сутки. |
| Mistral Studio Free | API включён без карты, included usage ограничен. [Официальный старт](https://docs.mistral.ai/getting-started/quickstarts/studio/activate-and-generate-api-key). | Конкретная модель и лимиты — по аккаунту; качество русского compound extraction/стоимость контекста не проверены. Дополнительный вариант после двух основных кандидатов. |

Первым проверить Gemini: backend adapter уже готов. Groq — следующий независимый поставщик для сравнения. Не регистрировать десяток сервисов вместо диалоговых проверок. Локальную модель оставить для массовых доступных dev-прогонов; API-модель выбрать по actual результатам. Gemma429/remote timeout измеряют доступность, а не точность географии.

Сравнение: одни inputs/initial states/clocks/oracles, отдельно raw model output и итог harness. Метрики: сохранённые явные факты и поля, правильный адрес правки, лишние вопросы, schema errors, неопрошенные изменения, latency и provider outages. Прогнать compound Белград/Москва/1adult/0children, FAQ, опечатку при заданной стране, relative date conflict, составные изменения и паузу. Результаты хранить без токенов; не менять oracle после FAIL.

## Уточнение по аккаунтам владельца

25.10 владелец сообщил о недоступности Google через свой текущий доступ. Geminikeyнеполучен. По фактическому screenshot Mistral организации на Free требуетUpgradeдляактивацииAPIkeys; предыдущая рекомендация бесплатногоMistral не подтвердилась для этогоаккаунта, платныйпланне включён. КлючMistral не использован и не сохранён.

Groq: владелец подтвердилFree/no-card. Ключсохранён только native.env0600, signup/billing не менялись. АутентифицированныйGET /models200, qwen/qwen3.8-27b active/context131072. Adapter+keyentry+PGbudget/cooldown реализованы; общий1070pytestPASS. Это пока негенерация и не actual качество. ДляGroq отдельныйконсервативный40/daypool; ранееисчерпанныйGemini/OpenRouter40/day не сбрасывается, суммарныйremote2/run сохраняется; самGroqgatewayделает1attempt/run. При429trustedRetry-After сохраняется и соблюдается безавторетрая. Первойреальнойгенерацией будет тот же compoundB1, затем NLU03/06 при доступнойtokenquota.
