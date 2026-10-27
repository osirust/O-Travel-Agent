# O!Travel Agent — состояние на 28 октября, 02:45 МСК

Цель: закончить известные критичные ошибки backend до интеграции frontend и видео. Владелец разрешил API-прогоны, исправления и обновление8084. Frontend делает сокомандник.

## Проверенный checkpoint

R14: **7098 PASS / 0 fail / 0 skip**,410.55с, frozen source3eca7197628416b4614650ba473121af5aa695298fec28b5e349053799b77564,277testfiles. [Доказательство](docs/verification/prefrontend-r14-checkpoint-20261028.json). R13:7062PASS/5FAIL сохранены; все пять регрессий исправлены без изменения исходных assertions, повтор5PASS.

Проверены: неверные роли городов040/052/056/059, календарь/бюджет/опечатки, сохранение выбора места, незапрошенные удаления и pause, исходные hard nights, same-batch номера, durable Z.ai cap2; R14 добавляет budget omission/readonly/quotes/advice/component scope, final-roster025, сохранение даты возвращения, проверку2номеров в каждом городе. Controlled/cached PG не равен fresh NLU.

Git: последний ранее опубликованный3f369a9 в origin+cv/backend/harness-foundation; сейчас публикуется точный R14. Live8084 пока3f/R12/bfb5. Ключи только ignored secure native env; ни в Git, ни в stdout.

## Actual проверки и ограничения

- Fixed a36/GLM4.5:619/697inputs,71наблюдавшийся чат,584COMPLETED/30FAILED/5QUEUED.515model/771source calls. Полного80PASS нет. Все619 ответов вручную разобраны.
- Отдельный3f/R12/GLM4.7: только072/T1, timeout45s,1model/0source;77inputsNOTRUN. Источники/модели разные, это не общая приёмка. Исходные106 не пройдены целиком; D250 только импортирован.
- Actual complex R5:5старыхmodelattempts(4ответа+1timeout)/32source,0orders. Последняя6-я попытка на immutableR14 дала JSONschema error(чужойroom target и запрещённые поля update_traveler), состояние/version5 сохранены. Далее разрешён отдельно отмеченный recorded-final-JSON adapter с настоящими LiteAPI/Duffel/FX/фото/refresh/local BOOKED_DEMO. Это проверка harness+источников, не новый успешный NLU.
- Предыдущие single-city Аргентина/Белград дошли до фото/refresh/одногоBOOKED_DEMO+retry. Hotel/flight SANDBOX, transfer DEMO; MIXED не полностью live. Реальных booking/payments0.
- GigaChat OAuth200 с проверенными Host/SNI/CA, api.giga.chat TLS timeout, generation0. VPN/Happ и TLS verification не менялись. Отдельный Direct exception только предложен, ответа владельца нет.
- Официальный age recipient проверен, helper и пустой .env.example готовы. Отдельный jury-only ключ/ciphertext не созданы: создание в Chrome остановлено автоматической проверкой неопределённого URL. Master ключ не передавать. Финальный main/submission tag и публичное видео ещё не готовы.

## Следующий этап R15

Private candidates:035/T7 server путает конец маршрута с городом отеля;025/T1 модель придумывает4AD при явно неизвестных категориях;038/T6 вопрос об алиасах удаляет единственный stay. После сохранения R14 разрешить только эти bounded source fixes, related PG и общий full. Не подменять их старымPASS. Не запускать новые массовые внешние фазы на медленном Z.ai без отдельного плана; нет подтверждённой причины timeout или времени восстановления.

## Стенды / изоляция

8084 http://localhost:8084/debug, Groq120B/v3/HYBRID/global, DBotravel_zai_eval.8086 immutable a36/свояDB с сохранённой историей; массовыеactor завершены.8088 остановлен поPIDguard, егоDB сохранена.8085 Giga external inference не работает.8080 стандарт Docker-сдачи; локальныйDocker8081 может отставать.

Исходники E:/work/aegml; WSL AegML-Hackathon наC. AEGYX R16INDEX81 paused; A524/Ubuntu24.04/WindowsPG/PEM/httpd8080 не трогать. NoWSLshutdown/prune/global kill/чужих volume. В текущих проверках localQwen0/paid0; настоящие бронирования и оплаты запрещены.
