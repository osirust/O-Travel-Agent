# Cloudflare: контракты запасных моделей

Проверено 26.10.2026 по публичной документации. Генерации, аккаунты, ключи, allowlist и работающий стенд8084 не менялись. Это выбор кандидатов для малого сравнения, не результат оценки качества.

Для первой одинаковой серии предлагаются **Gemma4** и **Nemotron3**: у обеих официально описано отключение reasoning. CFGLM4.7 оставить третьим кандидатом до проверки его точного serving-контракта.

## Бесплатный доступ

Все три ID прямо перечислены как доступные Workers Free в [официальном changelog](https://developers.cloudflare.com/changelog/post/2026-07-28-models-require-workers-paid/). [Прайс от20октября](https://developers.cloudflare.com/workers-ai/platform/pricing/) сохраняет10000neurons/day, сброс00:00UTC; после исчерпания Free возвращает ошибку. Для этих трёх не требуется Workers Paid/prepaid credits. Доступ и свободная ёмкость именно этого аккаунта ещё не проверены для новых моделей.

| Модель | Документированный контекст | Отключение reasoning | Формат/лимиты: подтверждено и неизвестно |
| --- | --- | --- | --- |
| [Gemma4](https://developers.cloudflare.com/workers-ai/models/gemma-4-26b-a4b-it/) `@cf/google/gemma-4-26b-a4b-it` | 256000токенов | `chat_template_kwargs: {"enable_thinking": false}` — явный [официальный tutorial](https://developers.cloudflare.com/workers-ai/get-started/workers-wrangler/). | Model schema содержит `response_format`, `max_completion_tokens`; `max_tokens` отмечен deprecated. Численный максимальный output и отдельный reasoning-budget в просмотренной CFдокументации не установлены. |
| [Nemotron3](https://developers.cloudflare.com/workers-ai/models/nemotron-3-120b-a12b/) `@cf/nvidia/nemotron-3-120b-a12b` | 256000токенов | `chat_template_kwargs: {"enable_thinking": false}`. Top-level `reasoning_effort` **не выбирает режим** этой модели. Low mode — `enable_thinking:true, low_effort:true`; это не выключение reasoning. | Schema содержит `response_format`, `max_tokens`, `max_completion_tokens`. Пример16000токенов не является заявленным maximum. Численный maxoutput/отдельный reasoning-budget не установлен. |
| [GLM4.7Flash](https://developers.cloudflare.com/workers-ai/models/glm-4.7-flash/) `@cf/zai-org/glm-4.7-flash` | 131072токена | Reasoning заявлен, `chat_template_kwargs` есть, но точное поле выключения в просмотренных публичных CFстраницах не найдено. **Не переносить** `thinking:{type:"disabled"}` с прямого Z.ai API как подтверждённый CFконтракт. | Schema содержит `response_format`, `max_completion_tokens`; `max_tokens` deprecated. Численный maximumoutput и отдельный reasoning-budget не установлены. |

На [странице прайса](https://developers.cloudflare.com/workers-ai/platform/pricing/) приведены5500/36400neurons за1Minput/output дляGLM,9091/27273 дляGemma,45455/136364 дляNemotron. Это помогает сравнить расход, но не обещает фиксированного числа диалогов: длина контекста, ответа и фактический usage различаются.

## OpenAI-compatible и JSON

[Официальный endpoint](https://developers.cloudflare.com/workers-ai/configuration/open-ai-compatibility/): `https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1/chat/completions`. Страницы моделей описывают совместимость и поле `response_format`; они не подтверждают отдельным работающим примером сочетание **конкретный новый ID + json_object + выключенный reasoning**.

[JSON Mode](https://developers.cloudflare.com/workers-ai/features/json-mode/) описывает `json_object`/`json_schema`, но его текущий список Supported Models **не содержит эти три ID**. Наличие общего поля в schema не доказывает, что сервер применяет нужный режим к каждой модели. Это документальное расхождение/неполнота, а не доказанный отказ. Приём `response_format:{"type":"json_object"}`, корректный `choices[].message.content`, finishreason и соблюдение нашей schema нужно проверить отдельным фактическим вызовом. Даже JSONMode не гарантирует соответствия сложной schema; server-side validation остаётся обязательной. Streaming для этого сравнения выключить.

## Предложенная малая серия — ещё не выполнена

Сначала зафиксировать разрешённый profile каждой модели с тестами fakeHTTP; runtime8084 и основной выбранный provider сохранить. Для Gemma/Nemotron кандидатный body: одинаковые messages и наша динамическая schema в systemprompt, `response_format:{"type":"json_object"}`, `max_completion_tokens:2048`, `chat_template_kwargs:{"enable_thinking":false}`, без tools/stream/fallback/gatewaybilling. Значение2048 — **наш engineering cap**, не максимум модели. Не посылать одновременно два конкурирующих token-limit поля. Формат параметров сначала проходит одну wire-пробу; при unsupportedparam/режиме не убирать защиту молча и не запускать всю серию.

После wire-проверки — до6одинаковых extraction-входов на модель, по одному запросу без автоматического repair/retry:

1. Составной первый запрос: Белград/Сербия изМосквы, одинвзрослый, бездетей, раноутром.
2. Справочный «Сербию знаешь?» — без изменений поездки и придуманного intake.
3. Ответ возрастом при текущем уточнении конкретного ребёнка; сохранить других участников.
4. Общий бюджет100к и отдельный лимит цены заночь; различать области ограничения.
5. Несколько правок маршрута/группы/бюджета вместе с явной паузой.
6. Противоречивое/гипотетическое изменение, требующее уточнения вместо молчаливого изменения фактов.

До вызовов сохранить одни canonicalTripState, recentmessages, pendingquestion, clock, schema и предметные ожидаемые переходы. Меняются только modelID и документированный modelprofile. Отдельно учитывать raw extraction, schema-validity и проверку смысла/harness; правильный JSON не равен правильному плану. Записывать latency, vendorusage, finishreason, число попыток и источник отказа без ключей/HTTPдампов/скрытого reasoning. При429/403/3040 сохранить результат и остановить модель; не менять тариф и не заменять её незаметно. На основании серии можно выбрать следующий стенд, но нельзя объявить100диалогов или весь106корпус пройденными.

## Выполненная малая проверка после документального обзора

Gemma и Nemotron добавлены как явно выбираемые Free-профили; default Llama не менялся. Две модели получили3одинаковых фиксированных extractioncases, по1HTTP наcase. Все6запросоввернули200/валиднуюschema, promptSHAпоодномуcaseсовпадают. Gemma2/3boundedPASS, Nemotron3/3; это не полные диалоги и не весь106корпус. GemmaCFP03обновиласуществующегоchild-2вместодобавления ивыдалаrawbudget9800000вместо8000000; денежныйguardисправилтолькобюджет,ошибкадобавленияостаётсяOPEN. Report cf-backup-pilot-20261026.json/fixturescf-backup-pilot-20261026.json. НовыхZAIвызовов/изменений8084небыло. Temperatureнепередавалась(модельныеdefaults),чтоограничиваетстрогостьсравнения.
