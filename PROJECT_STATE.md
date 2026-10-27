# O!Travel Agent — состояние на 28 октября, 01:20 МСК

Цель: подготовить backend/harness к утренней интеграции frontend и видео. Владелец разрешил API-прогоны, исправления и обновление8084. Frontend делает сокомандник.

## Проверено

- Последний committed checkpoint: a36d76e, ветка backend/harness-foundation в origin и cv. Новый кандидат b170deda прошёл полный frozen-source R12: **6884 PASS / 0 fail / 0 skip**,400.98с; source/tests неизменны. [Квитанция](docs/verification/prefrontend-r12-checkpoint-20261028.json).
- Исправлены и проверены related/cached/PG: даты/невозможный календарь, typo бюджета и относительный масштаб, утреннее пожелание и ответ о дате, сохранение выбора места при независимых фактах, незапрошенное удаление участника, ложная hard pause, выдуманные hard nights, новые номера в одном batch, durable Z.ai cap2. Это не fresh actual-приёмка каждого исправления.
- Single-city actual: Аргентина и декабрьский Белград ранее дошли до фото/refresh/одного BOOKED_DEMO+retry. Hotel/flight — SANDBOX, transfer — DEMO; MIXED не полностью live. Реальных booking/payments0.
- Официальные PDF прочитаны; правило отдельного ключа для жюри и binary secrets.env.age подтверждено. Helper и пустой .env.example готовы. Изолированный Docker smoke16checks относится к a36/cd021+packaging, не автоматически к b170.
- GigaChat: OAuth200 с проверенными Host/SNI/CA; api.giga.chat зависает на TLS до ответа моделей. Generation0. VPN/Happ не менялись, TLS verification не отключалась.

## Выполняется / открыто

- Fixed80/697 replay на отдельном immutable a36/cd021, Z.ai45/8086. Первые001–040 вручную разобраны:345inputs=325COMPLETED+17FAILED+3queued. COMPLETED не означает semanticPASS. После второго timeout на070/T1 разрешён отдельный suffix с070/T2 без resendT1; финальная сводка ещё не готова.
- Новые state-дефекты023/024/025/035/038/040 и English039 требуют exact cached reproduction/исправления. Rootcause пока UNKNOWN, не переносить PASS другого пути. Полные80/106 languagePASS не объявлять.
- Complex R5: Z.ai47, та же b170 core; Istanbul/Antalya verified; Казань уточняется как Татарстан по исходномуKZN. Goal: compound4people/2rooms each→photos→refresh→oneBOOKED_DEMO. R1–R4 и лишние уточнения сохранены, будущий успех будет WITH_DEFECTS.
- Новые jury-only ключи и secrets.env.age НЕ созданы. Основной Master ключ передавать запрещено официальной инструкцией. Chrome остановлен автоматической проверкой из-за неопределённого URL; не обходить другим UI.
- Финальный frontend, его интеграция, видео, main/submission tag ещё не готовы. Tag submission не создавать преждевременно.

## Стенды и границы

-8084: http://localhost:8084/debug, Groq openai/gpt-oss-120b strictv3 low/HYBRID/global, сейчас a36/cd021; ближайшее действие — commit нового R12 и guarded rollout. DB otravel_zai_eval сохраняется.
-8086: fixed corpus Z.ai45, immutable source/своя БД otravel_zai45_80_eval_20261027. Не обновлять/перезапускать до terminal, не сбрасывать ledger. Там effective model attempts1, новый cap2 ещё не installed.
-8085: отдельный Giga candidate, external inference не работает.8080 — стандартный порт Docker-сдачи, локальный Docker8081 может отставать от native8084.
- Источники E:/work/aegml; WSL AegML-Hackathon, C-backed. Ключи только ignored secure native env. LocalQwen0/paid0; скрытого fallback нет.
- AEGYX R16 INDEX81 owner-paused; чужие A524/Ubuntu24.04/WindowsPG/PEM/httpd8080 не трогать. No wsl shutdown/prune/global kill/volume deletion.

## Ближайший шаг

Сохранить новый R12 в обеих приватных ветках, обновить только owned8084 с identity/env/DB guards; закончить fixed80 и ручной разбор041–080, complexR5 и fresh targeted проверки. Затем исправлять подтверждённые новые state-defects с отдельными доказательствами; jury keys — только после доступного кабинета/ответа владельца. История/исходные FAIL и input/oracle SHA остаются неизменны.
