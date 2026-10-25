# O!Travel Agent — состояние на 25.10.2026

## Цель и ближайший шаг

Настроить воспроизводимый backend/harness и разрешённые бесплатные/test API. Реализация разрешена владельцем; прежний запрет начинать отменён. Frontend делает сокомандник. Ближайший продуктовый этап: прогон незнакомых многошаговых запросов на доступной LLM, связка реального поиска с канонической поездкой, затем интеграция frontend. Настройка API не означает завершённый агент.

Код отправлен в ветку `codex/backend-harness-foundation`; [Draft PR#1](https://github.com/ostrovok-hackathon-2026/aegml/pull/1), не merged. Кодовый commit `1d69467`; позднейшие изменения — отчёты/документация.

## Подтверждено

- Канонический checkout `E:/work/aegml`, private repo `ostrovok-hackathon-2026/aegml`; базовый commit до реализации `0f528b6`. Старый checkout `E:/work/AegML О!дин запрос` и личный mirror не менялись.
- WSL2 `AegML-Hackathon` перемещена на `C:/WSL/AegML-Hackathon`, readback registry/VHD выполнен. Docker29.9.1, Compose5.6.1. Native build tree `/home/aegml/projects/otravel`; venv `/home/aegml/venvs/otravel-harness-20261025`.
- FastAPI backend и PostgreSQL17 работают в Compose. `http://localhost:8081/health` отвечает database=ready; `/docs` доступен. Порт8080 занят важным PEMHTTPD-x64, не освобождался. Windows PostgreSQL5432, Ubuntu-24.04 и AEGYX не менялись.
- **152 автоматические проверки PASS** (`py scripts/run_tests.py -q`, отдельная БД `otravel_test`). Прежняя тестовая БД сохранена переименованием, рабочая `otravel` отделена. Полный106-корпус против приложения не запускался.
- HTTP DEMO smoke: сложный маршрут,9гостей/2группы/3комнаты на остановку; preview, один BOOKED_DEMO, идемпотентность, stale quote и auth. Model/live вызовов в нём0.
- После restart только app поездка, версия, токен и явная пауза сохранились. Отдельный временный Compose project стартовал с чистым volume и паролем со спецсимволами; собственные временные ресурсы удалены после проверки. Restart Windows и in-flight fault matrix не квалифицированы.
- Сохранены sanitized evidence в `docs/verification/`; frontend-контракт — `docs/FRONTEND_CONTRACT.md`. `/events` сейчас JSON polling, **не SSE**.

## Ключи и реальные пробы

Ключи только в нативной `.env`0600, не в Git/ответах. Скрытый ввод — `py scripts/configure_keys.py`; запись под lock. OpenRouter через официальный PKCE, ключ активен, денежный cap$0.01 разрешён владельцем; при readback usage$0.00. Не покупались кредиты, не включались платные модели.

- Gemma `google/gemma-4-31b-it:free`: HTTP429 с `upstream_provider_shared_pool`/Google AI Studio даже после cap$0.01. **Успешного ответа Gemma нет**.
- NVIDIA `nvidia/nemotron-3.5-super-vl:free`: одна синтетическая extraction-проба прошла JSON+domain и извлекла Казань/140000руб. Также один реальный HTTP harness-turn прошёл: Москва→Казань, бюджет160000→140000руб., состав3гостей сохранён, версия увеличена, PostgreSQL/audit записаны. Gemma429→NVIDIA ответ24342мс; persisted2attempts. Evidence `docs/verification/llm-runtime-20261025.json`. Это не оценка NLU на сложных диалогах. В native env включён явным backup; primary Gemma сохранён, actual model виден в audit/run.
- Gemini: ключа нет, вызовы не проверены. Нужен разрешённый регион и явно подтверждённый бесплатный проект; billing не включать.
- LiteAPI sandbox: каталог+rates, **586 SANDBOX тарифов** на синтетический однономерный запрос.
- Duffel test: **9 SANDBOX предложений** KZN→IST для2взрослых+ребёнка7лет. Supplier orders0.
- SerpApi: metadata подтвердили Free и250остатка до проб; два поиска вернули20hotel/4flight вариантов `LIVE_INDICATIVE`. Не подтверждают inventory/prebook.
- Open-Meteo: 7дней прогноза Istanbul, attribution сохранена.
- Hotelbeds/RateHawk/Travelpayouts: только guarded адаптеры и fixtures, ключей/реальных проверок нет; регистрация сейчас не требуется.

## Принятые ограничения

Семь направлений (Москва, СПб, Сочи, Казань, Стамбул, Анталья, Дубай); сразу multi-city/rooms/groups. Главное — массовые правки и «Было → Стало». Live в приоритете, смешанные источники допускаются с маркировкой компонентов. При отказе LLM — сохранённая поездка, формы/typed commands/demo. После явной паузы ждать явного «продолжай». Один агрегатор с различимыми поставщиками допустим. Все заказы имитированные. Не сокращать согласованный объём незаметно; численные guards10travelers/4stays/5rooms остаются implementation bounds, не согласованным финальным продуктовым периметром. Команда3: Глеб fullstack/frontend, роль/доступность Жени не определены.

## Findings и остающиеся работы

- **OTR-001 / PARTIAL_OPEN_GEMMA_NLU:** OpenRouter подключён, direct extraction и один относительный HTTP harness-case прошли; Gemma429 и полноценная проверка новых многошаговых запросов остаются открытыми.
- **OTR-002 / PRIMARY_SELECTED:** официальный repo выбран; личный mirror не настроен.
- **OTR-003 / FOUNDATION_VERIFIED_PRODUCT_OPEN:** backend/Docker/DB/DEMO проверены; законченный интерфейс, живой многогородской план и конкурсная готовность не доказаны.
- **OTR-004 / OPEN_PUBLICATION_RIGHTS:** лицензия/публичное CV-копирование/редакция правил требуют уточнения; private сохранён, submission-тег не создан.
- **OTR-005 / PARTIAL_SANDBOX_INDICATIVE:** реальные HTTP-пробы четырёхисточников успешны; `LIVE_BOOKABLE`, price/inventory validation и целая реальная групповая корзина ещё не реализованы.
- **OTR-006 / OPEN_PRODUCT_LIMITS:** семь городов определены; численные пределы, API покрытие групп и задержки ещё не согласованы/не проверены.
- **OTR-007 / OPEN_COMPARABLE_SUPPLIERS:** сведения SerpApi о ценах не доказывают сопоставимость одинакового room/rate/питания/отмены разных поставщиков. LiteAPI ToS требуют отдельной проверки выбранного сравнения.
- **HARNESS-001…008:** основной учёт в `docs/research/harness-plan-review.md`; реализованные части проверены тестами, undo/SSE/UI/смешанная корзина/полная fault matrix/реальное NLU остаются открытыми.
- **CORPUS-001…005 / OPEN_AUTOMATION**, **CORPUS-006 / DOCUMENTED**, **REVIEW-001…006 / OPEN:** исходные106случаев/129реплик/12категорий сохранены с прежними SHA; фикстуры, clocks и assertions ещё требуют адаптации. Mock-suite не закрывает весь корпус.
- **ENV-001 / RESOLVED_LOCAL_PORT:** важный8080сохранён, app проверен на8081; default Compose8080 для чистого окружения.
- **ENV-002 / VERIFIED_ISOLATED_DOCKER:** daemon/Compose/DB/app/fresh boot/restart проверены в собственной WSL. Автозапуск после Windows reboot не проверен.
- **SETUP-001…006:** точные факты/исправления/условия закрытия в `docs/verification/setup-findings.md`. **SETUP-004 OPEN_ROTATE_CHAT_KEYS:** три travel-key владелец прислал в чат; после подключения перевыпустить и заменить через скрытый локальный ввод.

AEGYX INDEX81 остаётся paused передSOURCE3; ни AEGYX, ни следующий блок его плана не запускались. Не делать `wsl --shutdown`, `docker prune`, удаление существующих volume или остановку чужих сервисов. Метаданные собственного скрытого WSL keeper: `%LOCALAPPDATA%/OTravel/setup/wsl-keeper.json`; остановка допустима только после сверки PID/start/точной команды. Дополнительные вопросы о доступах теперь не нужны для уже подключённых четырёхисточников; реальное бронирование/платёж не разрешены.
