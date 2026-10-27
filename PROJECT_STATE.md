# O!Travel Agent — backend перед frontend, 27 октября 2026

Команда владельца: выполнить реальные прогоны нового backend и туристических API, довести разговор/пересборку/refresh до одного имитированного заказа, устранить ошибки диалога и control recovery. Frontend делает сокомандник. Продолжать без новых согласований в этом scope.

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

Всеисторическиепроверки/SourceR1–R5/FAILimmutable. Неотключатьnative8084дляbulk80, userможетпроверять. BulkZAI45/8086долженбытьотдельным: RootдалsetupGOexact6ea3d11/c07b46/newC/newDB, generationпокаНЕразрешалась(ждёмREADY+RootpilotGO). НиLocalQwen,ниpaidfallbackнеподключать.

## Осталось после стенда

Полные80/106actuallanguage/rebuildсерии не завершены. PrivateZAI45/8086pilotdraft .local/drafts/after80-zai45-8086-20261027: толькоDRY_PLAN001–003/32turns, source81a7/v3 explicit; native/keys/HTTP0, --runзапрещёнпокаreleasebinding/controllerнепроверены. ДляbulkуZ.aiGLM4.5Flash/4.7FlashофициальноFree; GroqFree200kTPD/8kTPM не хватитна697полныхвызововбезquota/cacheпроверки. Не называтьinventory/pilotplan исполнявшимися80.

ActualR5solo/singlecityнеподменяетвсюmulticity/groupNLU. Новаяcomplexactualсерия иstress106driver/caseoracles нужныотдельно. GeneralworldQA, exactsupplier-equivalence, excluded-fee ledger/business-class end-to-end, finalfrontend/cleanarchiveещёOPEN. Giga8085localhealthесть, externalTLSapi.giga.chatblocked/OAuth0; oldURLне поддержанnewJulyaccounts, не обходить. Фото иименаисточникаотдельноотpricefacts.

КлючитолькоC-WSLMASTER /home/aegml/projects/otravel/.env0600; неввывод/Git. UserVPN/HappтолькоПРЕДЛОЖЕНИЯ: настройки/маршруты/профили/сервисы/прокси/системнуюсетьНЕменять,TLSverifyнеотключать. AEGYXR16INDEX81paused, чужиеA524/Ubuntu24.04/WindowsPG/PEM/httpd8080не трогать. NoWSLshutdown/prune/volumedelete/globalkill. LocalQwen inference0/paid0/supplierbook/pay0. Frontendнепроектировать.
