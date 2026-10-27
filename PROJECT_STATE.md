# O!Travel Agent — состояние 27 октября 2026

Текущая цель: исправить полный разговор владельца про Сербию, расширить поиск за пределы ручного списка городов, проверить Harness с сильной LLM и туристическими API. Frontend делает сокомандник. Последний вопрос владельца — поиск бизнес-класса; Duffel поддерживает cabin_class=business, существующий адаптер его отправляет. Канонический класс поездки, передача из чата и доказательство класса каждого сегмента ещё не реализованы (CABIN-CLASS-E2E-001). Цена 2,8 млн рублей на скриншоте не связана с исходным JSON, ошибка единиц не подтверждена.

## Runtime и Git

8084: source /home/aegml/projects/otravel-after80-20261026, DB otravel_zai_eval, WSL AegML-Hackathon на C:. Установлен commit 0bf71be3dc3b1b5eb000f71bd96fea8d2ab80a53, package SHA256 92bb70e62dbb7756c77e9815cd5fc67400ad54c677a58d774dfaab5476b37046. Groq openai/gpt-oss-120b, strict v2, low, 1 attempt; HYBRID LiteAPI + Duffel + SerpApi + CBR. Ветка backend/harness-foundation; оба private remote (origin/cv) указывают на 0bf71be. Новые изменения E после этого коммита ещё не установлены на 8084.

GigaChat 8085: локальный server/health доступен; OAuth/model inference не подтверждены из-за TLS-зависания api.giga.chat. VPN/Happ только предложения: настройки, маршруты, профили и системную сеть НЕ МЕНЯТЬ. Не отключать TLS verify. Новых проб подключения сейчас нет.

Ключи только /home/aegml/projects/otravel/.env (0600), не выводить. Реальные booking/pay запрещены. Local Qwen inference=0 в этом этапе; оплачиваемые модели/billing=0.

## Что проверено

- 0bf71be: общий контролируемый прогон 5625 PASS / 0 skip, JUnit .local/evaluation/after80-20261027/full-owner12-live8-final.xml. Исходный 5601 PASS / 8 FAIL сохранён. Квитанция docs/verification/owner12-harness-repair-20261027.json. Это pure/Mock/PG проверки, не 5625 реальных диалогов.
- Исходный owner12 immutable: .local/evaluation/owner-serbia-20261027/dialogue-projection.json, SHA256 3f6d58ef911cc6f45353714e08da05d7e3b8dc739d77913164eb87fe8f4747fa; 12 реплик / 7 моделей / 0 tourist tools. Fixture SHA256 1f48645bad6ee87b9cae14843ca36784fb28805c62ed474b871b050e72c441e2. Старые FAIL не заменять.
- После 0bf добавлены сохранение дат при неподтверждённом городе, literal_intake для пропущенных явных фактов, защита ASK-уточнений и коротких неответов. Targeted pure/PG проходили; общий frozen-source прогон нового объединённого состояния: 6185 PASS / 0 skip, 239.99 с. Source package 6d12c99bc72f7269ea68fd12b1d4f5e9600558a774535b63cab4952a842390e6; source/tests неизменны. docs/verification/dynamic-geography-integration-20261027.json. Это controlled suite, не actual world NLU.
- Actual R2: /home/aegml/checks/ownerfix-isolated-r2-20261027-141421, отдельная DB otravel_ownerfix_eval_20261027. Все 12 исходных + 3 адаптивных хода; 4 успешных ответа сильной Groq LLM, 17 внешних tourist HTTP, 27 предметных assertions PASS. LiteAPI/Duffel дали отель и перелёты, CBR — FX. Точная численная HTTP status для модели не захватывалась; не называть их записанными 200. Trip остаётся PARTIAL: нет подтверждённого transfer:new-stay-1. Orders/prebook/pay=0.
  Safe proof .local/evaluation/owner-serbia-20261027/ownerfix-isolated-r2-actual-safe-proof-status-corrected.json SHA256 f2a7b3fa1175c2a18d96d203807dc1a47625b42b679f41c8fa76adfba4cf51a1. Literal fallback восстановил факты, когда модель снова вернула пустые операции; это не доказательство улучшения самой модели.
- PRICE/MEDIA QA: 195 относящихся controlled PASS. Фото выдаётся по точному supplier hotel ID, partial plan не разрешает checkout. Суммы остаются в исходных minor units, FX отдельно; групповая цена не умножается повторно. Звёзды/review score отдельным DTO пока не экспортируются. docs/testing/PRICE_MEDIA_BACKEND_QA_20261027.md.

## Глобальная география — реализуется, ещё не принята целиком

В E добавлены immutable CityRef + полный граф place ID в TripState (legacy empty JSON сохранён), durable GeoCatalog с TTL и атомарными RU/EN source witnesses, bounded Open-Meteo PlaceDirectory, resolve_place schema/strict Groq v3, coordinator с подтверждением страны/города/омонима, supplier binding для LiteAPI/Duffel, API resolve/select и worker с CAS/бюджетом. Feature flag DYNAMIC_GEOGRAPHY_ENABLED по умолчанию false.

Модули имеют отдельные pure/Mock/PG квитанции docs/verification/dynamic-geography-*.json, place-directory-phase1-20261027.json, place-resolution-phase2-20261027.json. Это отдельные проверки, их количества не складывать в новый общий PASS. Open-Meteo: две реальные keyless GET подтвердили AR/GeoNames identity и омонимы, docs/verification/openmeteo-geocoding-ar-20261027.json; мировой actual LLM→directory→suppliers прогон ещё не выполнен.

Root связал API/worker: country → city question → homonyms → explicit choice → persistent EN CityRef. Отдельный объединённый API/PG batch 52 PASS, следующий 99 PASS; последний control-focused batch 50 PASS. LLM/геокодер в этих наборах контролируемые. CLDR RU/EN 249 стран закреплён SHA256 74dd233f299cb6f5f3a620dd53f62d1a778aff4bb4799872725b384109eb5fa4; complete literal country wish задаёт вопрос, не выбирает столицу. ASK/PROPOSE place action без согласия не меняет поездку; explicit pause/cancel обрабатываются отдельно.

## Текущие открытые проверки и ближайший шаг

1. Durable pause/additions/resume для place_deferred_action: Store/API/helper и Harness hook внесены, 68 related PASS (включая 19 новых). Старый plaintext control crash между set_control и finish_run остаётся отдельным OPEN; docs/verification/PLACE_RESUME_RUNTIME_20261027.md. Предмодельный короткий city shortcut теперь исключает explicit pause и информационные фразы; 3 PG PASS, включая coordinator/directory call spies (cache hit не скрывает поиск).
2. Existing trusted CityRef identity не должна теряться в legacy intent_grounding при echo/поправке даты. Исправлено, 265 related PASS (20 новых pure cases). Перед supplier search добавлен exact fresh/current Catalog admission используемых refs; stale/changed/missing catalogue блокируют источники и сохраняют состояние.
3. Model clarification после typo/NO_MATCH не должно заменяться старым вопросом maintenance. Нерешённая информационная география остаётся HOLD; не выбирать город по информационному вопросу.
4. Общий controlled прогон завершён 6185 PASS. Далее отдельный bounded actual world conversation + travel APIs на отдельной DB. Один actual actor, фиксировать модель/source/wires/ошибки и частичное покрытие. Не выдавать availability/airport match за готовую корзину.
5. Согласовать main matrix docs/research/harness-plan-review.md по новым доказательствам, secret scan, commit/push только относящегося кода; не git add -A. После зелёных проверок установить точный snapshot на 8084.

## Ограничения

Полные actual 80/106/D250 серии не приняты; прежние результаты сохраняются в матрице. Dynamic cities без подтверждённого transfer дают partial, а не вымышленный ready-план. Новая глобальная география не означает наличие тарифа во всех городах. AEGYX R16 INDEX81 owner-paused; чужие A524, Ubuntu-24.04, Windows PostgreSQL/PEM/httpd8080 не трогать. Никаких WSL shutdown, prune, удаления volumes и глобальных kill.
