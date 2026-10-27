# O!Travel Agent — backend перед frontend, 27 октября 2026

## Текущий checkpoint перед новой инструкцией сдачи

Последний полный frozen-source прогон r10: **6680 PASS / 0 fail / 0 skip**, 381.76 с; package `ddc54528e15b02f1d20119120948bfc2fabbef6bcc95e39401b962e83a7707c3`, tests `8ff2f9aaacaec38c75ad32f0c859fa56a6c7ed813beb38deb103ab109d889cad`, source/tests unchanged. r9:6671 PASS. Квитанции `.local/evaluation/after80-20261027/full-completed-return-r10-*` и `full-prefrontend-fixes-r9-*`.

Новый запрос владельца: сверить два PDF, выполнить требования передачи секретов через age, проверить GigaChat; продолжать все прогоны даже при его отказе. Утром интеграция frontend и подготовка видео. VPN/Happ остаются без изменений. Официальная инструкция требует ОТДЕЛЬНЫЙ ключ жюри, основной не передавать; вопрос доступа к вошедшему Chrome отправлен владельцу асинхронно. Public recipient подтверждён страницей организаторов; helper пока шифрует только synthetic/ignored jury env, Master не экспортируется.

Исправлено после a63: DEMO-трансфер для зарегистрированного Белграда; потеря начальных уточнений NLU01; сохранение отвергнутого final JSON без reasoning/ключей; явное расселение «в первом — …, во втором — …» в обоих городах; удаление выдуманного возраста новых взрослых; снятие единственного model-флага `return_flight_date_change` только после доказанного переноса последнего проживания и стабильного обратного плеча. Raw FAIL/oracles сохранены. Старый тест запрета любого Belgrade fallback имеет byte-exact historical copy SHA `b3b298a715b49f2c87bf95765373fb563db22b2aaa42ae3acb1f9c369072d59e`; новая пара проверяет ОБЕ политики fallback=False/True и неизменные external-факты.

Actual complex: R1 и R2 выявили реальные guard-дефекты, R3 остановился на Groq400 для новой room-reference; один разрешённый known-failed retry получил200, но Domain отверг stale derived duration4 при checkout19. Итог новая версия4pax/2rooms ещё НЕ дошла до поиска/оформления. Нужно: same-batch room-target schema, не превращать выведенные моделью ночи в новое жёсткое условие; отдельный новый actual-прогон. Не возобновлять исчерпанные actor-эпохи скрытым resend.

Giga: поздние Windows+WSL public DNS/TLS-пробы подтверждают OAuth TLS1.3 с pinned CA; `api.giga.chat` TCP есть, TLS timeout. Ключи/OAuth/model этими пробами не вызывались. Одна некорректная WSL curl-проба с wildcard остановлена по exact own curl/UID/argv; чужие процессы не затронуты. Повторить только прямыми argv/SSL, без shell wildcard. Не отключать TLS и не менять VPN. Подготовленный Z.ai repair2/8086 сохраняет историю БД,2/modelrun/day1000, no local/paid; ждёт нового committed source pin и Root GO.

Команда владельца: выполнить реальные прогоны нового backend и туристических API, довести разговор/пересборку/refresh до одного имитированного заказа, устранить ошибки диалога и control recovery. Frontend делает сокомандник. Продолжать без новых согласований в этом scope.

## Текущий этап: повтор диалога про декабрь

Владелец передал 8084 для наших проверок и обновлений. Исходный разговор из 14 реплик сохранён без изменения: `/home/aegml/checks/owner-december-20261027-182929/projection.json`, SHA256 `ad7f3c61321347d2ed458836f47918f5b0d1817da459b8decccf78fd3f793fed`; 5 вызовов модели, 0 инструментов. Вопрос `party.children` был адресован правильно: отсутствовало распознавание фразы «дети не едут» в коротком ответе.

Исправления кандидата: общий literal-parser нулевого состава; адресные даты с «на»; объяснение обратного порядка дат; отдельное подтверждение январского выезда следующего года; вопросы о стоимости из сохранённых сумм либо новый поиск; «так что?» возвращает к текущему вопросу. Управляемые проверки: 236 по датам, 203 pure и 2 PG по детям, 4 PG по стоимости, 19 независимых pure по маршрутизации стоимости. Это не actual-повтор исходного разговора.

Полные прогоны: r6 **6581 PASS**, r7 после null-field исправления **6582 PASS / 0 skip**, 419.44 с, source unchanged `6b8de209267a53a1bf4a757d990c42b1948ca489ea75a160f16e032e0694a63f`. На8084 установлен a63c2fc / Git package9706a5a7; health/debug200, Groq120B/v3. [Квитанция](docs/verification/owner-december-release-20261027.json).

Actual R3: **17 реплик, 19/19 предметных checks**, 10 source HTTP, 0 LLM; даты/опечатка/дети → LiteAPI hotel + Duffel flights SANDBOX → DEMO transfer → фото → reprice → один BOOKED_DEMO/retry. Итог12952552minor RUB=129525.52 RUB, справочный FX. Это адаптированный повтор с явным годом, не exact14 и не языковой benchmark. [Доказательство](docs/verification/owner-december-actual-r3-20261027.json). R1 setup отказ Git safe.directory и R2 transfer-only gap сохранены отдельно. Исправление зарегистрированного DEMO-трансфера не расширяет DEMO-отели/перелёты:191relatedPASS, package `4b8eedd251365427a19fa2b2113708d6991ec06360947ba745f6ca2a03f32699`. Полный r8 выполняется, код/тесты до terminal не менять.

Сильные API проверяются отдельно: на одном a63c2fc и одинаковом HYBRID/RU seed Groq120B прошёл три исходные реплики001, сохранив состав/утро/отправление и сменив город; Z.ai45 на T3 дал INVALID_LLM_OPERATION. Точный badfield неизвестен: gateway не сохранил схему отказа в аудит. [Сравнение](docs/verification/three-turn-provider-comparison-20261027.json). Полный80 не завершён: первый3-case pilot остановился на T3; новый executor продолжает семантические FAIL без повторной отправки, но останавливает ресурсные/неизвестные исходы. Оригинальный106 pilot4 запущен отдельно на8086, результатов пока нет.

## Проверенный checkpoint

- Последний общий frozen-source прогон: **6448 PASS / 0 fail / 0 skip**, 362.934 с. Package SHA256 7e8b4d1b28b12a365a1251e604993d47575f4b9bc3ab10ca176c90c5473acb9b. Source/tests не менялись. Квитанция docs/verification/delivery-backend-release-20261027.json.
- Actual R5: /home/aegml/checks/world-actual-r5-20261027-174732, DB otravel_world_eval_20261027_r5, snapshot f423a80f455269434d540e50da7b9a2d23d405649914b983fcefe69e54f78fd7. 4 сообщения с пустой поездки, Groq openai/gpt-oss-120b1 HTTP200,13 source HTTP,11 предметных checks PASS. СтранаAR→выбор GeoNames3435910→Москва/1adult/7nights/room1/300k/RU, без повторного запроса дат и без phantom preferences. Photo exacthotelID, real sandbox prebook200, Duffel refresh200, CBR FX. Preview/confirm/retry200, ровно1 BOOKED_DEMO, source booking/payment0. docs/verification/world-single-city-actual-r5-20261027.json.
- Источники: LiteAPI+Duffel **SANDBOX**, local synthetic transfer **DEMO**, итог **MIXED**; не объявлять это полностью live. У реального R5 total26420016minor RUB=264200.16 RUB, цельбюджета300000 RUB соблюдена. Нативные валюты и FX отдельно; included taxes не прибавляются повторно.
- Первый country lookup R1 зависел от LiteAPI /countries4s и упал доML; R2 model правильный batch был отвергнут legacy preflight; R3 hotelтерялся доproof-фильтра из-заcap30; R4 model далNULLdates+unaskedpreferences, testdriverwrongconfirmfield422. Все исходныеFAIL/source/oracles сохранены отдельно, R4confirmationaddendum200+одинorder тоже отдельно. Новыеpass не переписываютих.

## Исправлено и проверено по своим границам

- Страны по pinnedCLDR249без зависимостиотsupplierinventory; globalcitydirectory/омонимы/explicitchoice/immutableCityRef и ownership/country/TTL/CAS. Source revision confirmation отдельна; выбор города не доказываетналичиеотеля.
- Conflict preflight сохраняетtrustedname+placeID; дваодноимённыхместасразнымиIDразличаются. NO_MATCH сохраняетсвойquery/country/scope; spellingProviderError не даётбесконечностарыйвопрос.
- Явныедаты/гражданство и2поддержанныхsoftpreferences не теряются/не выдумываются. Datehelperработаеттолькопослеownership/targetchecks иприЗаезд/Выезд, не добавляетдиагностикувчужиеобычныереплики. КопииactualR4 истарыеoracles сохранены.
- ASK/PROPOSE сuserpauseнеисполняетпредложения; обычныйModelwait-clarification не уничтожаетявныебезопасныефакты. Старыеpositivepricephrasesвосстановлены. Все14FULL3regressions прошлиrelated77; FULL4singlemetadata regression46relatedpass. ВсеисторическиеXMLPRIVATE в.local/evaluation/after80-20261027.
- Control accepted-beforecommit/aftercommit, supersession, multiworker/idempotence, oldpausevsnewresume:90relatedPG/ASGI PASS. Ordinarypausedmessagesнеclaim; данныепослепаузы ждутявногопродолжения. Exactly-once arbitraryexternalHTTP/потерядисканезаявляются.
- Source rates: до600 rows ДОproof-фильтров, minсредипригодных. Диагностикаcomparison_scopeвнеeconomichash. Двапригодныхтарифа82/84раньшене доходилидофильтра; налоги/вместимость/TTL/группы неослаблены.

## Runtime / rollout

8084 ОБНОВЛЁН И ПРОВЕРЕН: http://localhost:8084/debug, «Новая поездка». Code6ea3d110560a0d4c95ce0021687c7f5505147db6 вorigin+cv/backend/harness-foundation; functionalcommitcced386 иfull6448. НовыйCpackagec07b46abbf1599eac299078d497965a98dfab95ad4f7ba80d2e7244a566ea11f. Readinessstatusверсия3 исправлена отдельнойоднострочнойметадатнойправкой+58targetedPASS, весьfullпосленеёнепереобъявлен.

Controller .local/after80_world_backend.py (CREATORafter80_world_backend.py), SOURCE/home/aegml/projects/otravel-after80-20261026, DBotravel_zai_eval, Runtimeidentity .local/after80-world-backend-8084. ТолькоownPID/uid/argv/source/loopback guards, strictv3/globalTrue/Groq120B/free/local0. Полный start manifestMATCH, /health и/debug200, actualnativeAR→cityquestion0LLM/0implicitcapital. docs/verification/native-delivery-ready-20261027.json. Master.envinode/size/mtime/uid/modeнеизменны;DBданныесохранены. Oldcontrollerstopиобаarchiveinstallreceiptsв/home/aegml/checks/after80-delivery-cced386 иafter80-delivery-6ea3d11. ПерваяstartuphealthREFUSEDиз-занеправильногоstatus.transportversionсохранена; процессбылоостановленоowncontroller,metadatafixпроверен,повторныйstartRUNNING.

Исторические проверки/Source R1–R5/FAIL неизменны. Владелец теперь разрешил использовать 8084 для наших прогонов и обновлений. Для bulk80 подготовлен отдельный Z.ai 8086 с собственной БД и копией исходников; setup проверен на 6ea3d11/c07b46, generation ещё не запускалась. Перед pilot установить исправленный кандидат и зафиксировать новый manifest. LocalQwen и платный fallback не подключать.

## Осталось после стенда

Полные80/106actuallanguage/rebuildсерии не завершены. PrivateZAI45/8086pilotdraft .local/drafts/after80-zai45-8086-20261027: толькоDRY_PLAN001–003/32turns, source81a7/v3 explicit; native/keys/HTTP0, --runзапрещёнпокаreleasebinding/controllerнепроверены. ДляbulkуZ.aiGLM4.5Flash/4.7FlashофициальноFree; GroqFree200kTPD/8kTPM не хватитна697полныхвызововбезquota/cacheпроверки. Не называтьinventory/pilotplan исполнявшимися80.

ActualR5solo/singlecityнеподменяетвсюmulticity/groupNLU. Новаяcomplexactualсерия иstress106driver/caseoracles нужныотдельно. GeneralworldQA, exactsupplier-equivalence, excluded-fee ledger/business-class end-to-end, finalfrontend/cleanarchiveещёOPEN. Giga8085localhealthесть, externalTLSapi.giga.chatblocked/OAuth0; oldURLне поддержанnewJulyaccounts, не обходить. Фото иименаисточникаотдельноотpricefacts.

КлючитолькоC-WSLMASTER /home/aegml/projects/otravel/.env0600; неввывод/Git. UserVPN/HappтолькоПРЕДЛОЖЕНИЯ: настройки/маршруты/профили/сервисы/прокси/системнуюсетьНЕменять,TLSverifyнеотключать. AEGYXR16INDEX81paused, чужиеA524/Ubuntu24.04/WindowsPG/PEM/httpd8080не трогать. NoWSLshutdown/prune/volumedelete/globalkill. LocalQwen inference0/paid0/supplierbook/pay0. Frontendнепроектировать.
