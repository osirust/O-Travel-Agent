# O!Travel Agent — 28 октября, 06:56 МСК

Цель: передать backend для утреннего frontend и видео. Frontend делает сокомандник; здесь — backend/harness и проверки.

## Код и рабочий чат

R19 прошёл **7384 PASS / 0 fail / 0 skip**, 447,54 с, source `f36166b989706e35c111dded5c79f1e0e0a42bf7b0d97cf137c389b683b21cc3`, 86 package files / 297 test files. Source/tests не менялись. [Квитанция](docs/verification/prefrontend-r19-checkpoint-20261028.json). R19 сохраняется как проверенный checkpoint; rollout удержан до R20 из-за нового readonly079 finding; новых source-изменений после full нет.

Последний опубликованный core — R18 `4645c24`, обе private-ветки `backend/harness-foundation`. Live http://localhost:8084/debug уже R18/Git-package `4e383ff6…`; 7314 controlled PASS, exact Docker19 PASS, native31 checks/3 reminders PASS с 0 model/tool calls. Профиль пока Groq120B/v3low/HYBRID/global; `/health` не доказывает наличие API-квоты. SQL/сессии/журнал и metadata .env сохранены.

R19 исправляет три доказанных случая: (1) точная правка возраста ребёнка по уникальному номеру без LLM, с проверкой старого возраста/DOB/границ; (2) неверный batch с одной группой вместо явно названных двух семей не применяется; (3) сохранённые требования к тарифу подписаны конкретным проживанием/номером/городом. Первоначальные RED сохранены. Это узкие исправления, не общий parser произвольных семей/возрастов.

## Реальные туристические сервисы

R18, отдельный freshDB и typed-input: **22 фактических supplier HTTP (все200/201)**, 2 отеля/4 номера/3 рейса/4 пассажира, 2 фотографии именно выбранных отелей, ЦБFX, 2preview → старыйquote409 → новыйconfirm200/retry200 → **ровно один BOOKED_DEMO**. Итог376165,80RUB при бюджете1180000RUB. Отели/рейсы SANDBOX, трансферы DEMO; LLM0, настоящие supplierbook/pay0. [Доказательство](docs/verification/r18-typed-real-travel-checkout-20261028.json). Первоначальные FAIL проверочного script (версия при confirm и имя статуса старойquote) сохранены; read-only addendum сверил намеренные правила Store, без новыхHTTP/DBwrite. Это функциональная квалификация, не NLU.

LiteAPI/Duffel/SerpApi/Open-Meteo/ЦБ адаптеры настроены. SerpApi даёт ориентировочные цены, не booking; его месячную квоту в текущем typed-прогоне не тратили. Mixed не называется полностью реально бронируемым. Свежий OSV lookup: 29 установленных runtimePython-версий, 0 известных advisories; не полный OS/source audit.

## Остаток языкового прогона

Исходные697 реплик/80 диалогов неизменны (sourceSHA81a7e59c…). Historical a36/Z.ai45:619 accepted=584completed+30failed+5queued. Отдельный R12 повтор072/T1 не удваивает uniquecoverage.

R17, один другой DB: Nemo8089 — 41input/39model/79supplier; Groq8090 — 23input/23model/34supplier (21completed, schemaFAIL и TPD429); Nemo8091 — 8input/8model/0supplier (6completed/2INVALID_LLM_OPERATION), стоп по выделенному hard8, не ответ vendor429. Z.ai47 ASGI — 080/T3 FAILED/RATE_LIMITED, 1model/0supplier. Все фазы известным образом закрыты, исходные ledger не сброшены, pending/quotes/orders=0. 697 uniqueinputs приняты:657completed/35failed/5oldqueued; missing/duplicates/textSHA mismatch=0. Последние5 CF-ASGI completed (только1modelcall), всефазы закрыты. Совместная линия сохраняет разные модели и не является uniformPASS. CF-ASGI исполнялся на том же immutable R17, не на текущем E/R19. CF выбран по свежим официальным docs: @cf/nvidia/nemotron-3-120b-a12b, WorkersFree подтверждён ранее; cap6newHTTP, native71+6<=operator80, perrun1, nofallback/local/paid/checkout.

Все уже имеющиеся ответы прочитаны; новые последние будут разобраны после terminal. Полного uniform80/106 language PASS нет: remain semantic misses/unknowns и недостаточные oracles; COMPLETED не равно правильному ответу. Общий pytest-count этого не закрывает. Пять старых queued не объявляются executed.

## Сдача и внешние блокеры

Официальная сверка: docs/testing/SUBMISSION_REQUIREMENTS_MATRIX_20261028.md. age1.3.2/официальный recipient/проверенный helper/пустой example готовы. **Jury-only LLM key и secrets.env.age ещё отсутствуют**: автоматическая Chrome URL-проверка остановила создание отдельного ключа. Не обходить UI-stop, не экспортировать Master. Старый отдельный ключ не даёт новую общую квоту OpenRouter/Groq.

GigaChat OAuth200, generation0: API TLS timeout. Проверка CA включена; VPN/Happ/маршруты/MTU/globaltrust не менялись. Direct-исключение api.giga.chat только предложено, ответа владельца нет. Не повторять одинаковые пробы и не объявлять Giga рабочей.

Далее: опубликовать R19, проверить точный архив, обновить8084; закончить/разобрать CF terminal и uniquecoverage; выбрать реально отвечающий профиль для frontend. После frontend — exactfinalDocker, общедоступное видео, выделенный juryenv+binaryage, итоговый officialmain и submissiontag. Последние четыре deliverygate ещё открыты.

## Изоляция

E:/work/aegml; native/Docker/PG в C-backed WSL AegML-Hackathon. AEGYX R16/INDEX81 owner-paused; A524/Ubuntu24.04/WindowsPG/PEM/httpd8080 не трогать. Никаких wsl --shutdown/prune/чужих volumes. Ключи только ignored0600 env; локальная Qwen/оплата/настоящий booking=0.

**Новый критический HOLD — HARNESS-004-READONLY-NOW-PREFIX-079-001.** Вопрос «Теперь сколько взрослых и детей осталось? Первый номер сохранился прежним?» обошёл readonly из-за префикса и secondary room-topic gate; raw изменил комнаты, checked не снял эти setters. Исходный actualR17 сохранён. R20 готовится отдельно; до проверки исправления R19 не разворачивать. [Причина](docs/verification/readonly-question-now-prefix-079-cause-20261028.json).
