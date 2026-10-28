# O!Travel Agent — состояние на 28 октября,03:25 МСК

Цель: подготовить backend к frontend и видео. Известные критичные ошибки исправляются небольшими проверяемыми этапами; новый PASS не закрывает старые actual FAIL другой ветви.

## Проверено

Последний полный frozen-source R15: **7154 PASS / 0 fail / 0 skip**,414.59с; sourcec81257906ca0a907609aaabf2f9e1691f6aa2c4c0add2a38dfc1de4ff5115f48,281testfiles. [Квитанция](docs/verification/prefrontend-r15-checkpoint-20261028.json). R14:7098PASS; R13:7062PASS/5FAIL сохранены, все5 исходныхregressiontests после ремонта прошли без ослабления assertions.

Исправлены controlled/cached: бюджет/цитаты/гипотезы/component scope, страна/город и роли origin/hotel/route-end, неверный календарь и derived nights, same-batch номера, конечный состав2AD+2CH вместо4AD+2CH, unknown initial categories, сохранение единственного проживания при alias-вопросе, пауза/resume/сохранение места, повторный shift_return и два номера в каждом городе.

Actual R14: Groq120B —2 входа из исторического состояния,10/10checks,2 modelcalls,5toolstarts(4completed,1LiteAPI PROVIDER_RESPONSE_ERROR). Это не два полных диалога. Отдельный recorded-JSON+REAL-TRAVEL прогон:27/27checks,19supplierHTTPsuccess,2отеля/по2номера/3перелёта/4человека/фото/refresh/одинBOOKED_DEMO+retry,372523.72RUB. Hotel/flight SANDBOX, transfer DEMO,0supplier booking/payments. Fresh Z.ai correction отклонена за невалидные операции; replay не превращается в NLU PASS.

## Открыто / следующий этап R16

- Recorded Nemotron после полного R15 добавляет лишнего ребёнка вопреки«Повторно никого не добавляй»: новый private RED, нужен current-person-NoAdd guard+bounded repair. Не remap личности по одному возрасту.
- Простые закрытые напоминания состава/дат/бюджета: private49pure-кандидат canonical_saved_fact_answer, ещё не интегрирован/не проверен на сохранение pending/quote.
- Fixed a36/Zai45:619/697inputs,584completed/30failed/5queued,515model/771tools; все619 ответов вручную прочитаны. ОтдельныйR12/Zai47: только072T1/timeout,77NOTRUN. Full80/106 не пройдены; D250 только импорт.
- OpenRouter новый день: metadata50free; Gemma429 при неистраченнойaccountquota; Nemotron3HTTP200/валидныйJSON, rawsemanticошибки подтверждены. Model-only probes не заменяют wholeHarness/actualNLU. Подготовка ограниченного отдельного072–080phase, generation только после нового GO.
- Giga OAuth200, inference0: api.giga.chat TLS timeout, VPN/Happ/TLSverify не менялись. Точечный Direct exception только предложен.
- Официальный age recipient/helper/пустойenv готовы. Separatejury-key/ciphertext не созданы: автоматическая проверка Chrome остановила доступ из-за неопределённогоURL. Masterключ не передавать. Финальный frontend/video/main+submission ещё впереди.

## Git и стенды

Ветка backend/harness-foundation в origin+cv; последний опубликованныйR14=4daa166, сейчас сохраняется R15. Live8084 http://localhost:8084/debug —R14/e37ccb72, Groq120B/v3/HYBRID/global, DBotravel_zai_eval. Новые WIP изменения на него не установлены.8086 immutablea36/сохранённаяDB, массовыеactor завершены;8088 остановлен, егоDB сохранена.8080 стандарт Docker-сдачи, Docker8081 может отставать. ИсточникиE:/work/aegml, nativeкопии/БД вC-backed WSL AegML-Hackathon.

AEGYX R16INDEX81 owner-paused; A524/Ubuntu24.04/WindowsPG/PEM/httpd8080 не трогать. NoWSLshutdown/prune/globalkill/чужихvolume. Секреты только private ignorednativeenv; localQwen/paid/currentphase0. Никаких настоящих бронирований и оплат. Frontend делает сокомандник; дизайн в этом чате не делаем.
