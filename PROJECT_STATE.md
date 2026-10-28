# O!Travel Agent — состояние на28октября,04:45МСК

Цель: передать устойчивый backend для frontend/видео и закончить оставшуюся языковую проверку. Код не объявляется безошибочным по числу pytest.

## Последний проверенный код

R17: **7301 PASS / 0 fail / 0 skip**,430.79с; source451adca3ceb724c65a2b27ceda938f4413d58f59f79ec5df3aaefb832c49cf4a,289testfiles. [Квитанция](docs/verification/prefrontend-r17-checkpoint-20261028.json). Source/tests во время full не менялись. R16:7236PASS/2FAIL сохранены; первоначальные два regressiontests после исправления прошли без изменения assertions. R15:7154PASS; R14:7098PASS.

Закрытые controlled области: budgetdelta/scope/quotes/advice, origin/hotel/endpoint roles, даты/календарь/derivednights, начальный и конечный состав, alias single-stay removal, explicitNoAdd+boundedrepair, незапрошенный existingADage, canonical closedread-only напоминания(0model), pause/continuation/ownedquestions/CAS/idempotency/quote/order guards. Same-value/typed/explicitpositive правила проверены отдельно. DOB-изобретения и универсальная person-age attribution этим не закрываются.

## Что проверено через внешние сервисы

- R14 Groq120B:2 exactinputs с явным историческимsetup,10/10checks,2modelcalls.5toolstarts:4completed,1LiteAPI PROVIDER_RESPONSE_ERROR. Это не два полных диалога.
- R14 recordedfinalJSON+настоящиеLiteAPI/Duffel/CBR:27/27checks,19HTTPsuccess,2hotels/по2rooms/3flights/4pax/photos/refresh/oldquote409/одинBOOKED_DEMO+retry.372523.72RUB при1180000budget. LLM replay явноcontrolled, freshZai6attempt отвергнут за невалидные операции. Hotel/flightSANDBOX, transferDEMO; realbooking/payments0.
- Fixeda36/Zai45:619/697inputs,584completed/30failed/5queued,515model/771tools. Все619ответов прочитаны. ОтдельныйR12/Zai47:072T1timeout;77inputsNOTRUN. Full80/106PASS нет; D250 импортирован.
- OpenRouter: Gemma429 приaccountfree50/50; Nemotron3HTTP200/validJSON, rawsemanticошибки. Whole-Harness recordedduplicate RED сохранён; NoAddguard теперьcontrolledtested. Микрофазы4actualHTTP отдельно. Качество моделей не ранжировали по метаданным.

## Git / стенды

Ветка backend/harness-foundation в origin+cv; последнийpublishedR15=6418de3, сейчас сохраняетсяR17. Live8084 http://localhost:8084/debug покаR14/e37ccb72, Groq120B/v3/HYBRID/global, DBotravel_zai_eval. Следующий guardedrollout — новыйкоммитR17.8086 immutablea36/DBсохранена;8088остановлен/DBсохранена.8089Nemotron072–080 толькоPREPARED:40modelHTTP/500supplierHTTP/perrun2, actualrun ещё0. RootдастGOпоточномуGitarchiveSHAпослепубликации; start изimmutableC, оригинальныеinputs/oraclebyteexact, nofallback/paid/local/checkout/resend. SourceE не используетсяruntime.

## Что осталось

1. Запустить prepared9casephase, сохранитьполныеответы/ошибки/remaininginputledger; app40cap илиvendoroutage не объявлять semanticPASS. При необходимости отдельнаяявнаяGroqepoch, безпересылкиunknownPOST/сбросаквоты.
2. ТочныйфинальныйGitarchive Docker smoke, frontend интеграцияпо существующемуконтракту, видео, затемmain+submissiontag. Source17неподменятьновымнепровереннымкодомвпроцессе.
3. Separatejury-onlykey+secrets.env.age ещё НЕ созданы. Официальныйrecipient/helper/пустой.env.example готовы. АвтопроверкаChromeостановиласозданиеключаиз-за неопределённогоURL; Masterключ не экспортировать. Финальноеvideo/publiclink ещёнет.
4. Giga OAuth200/inference0: TLSapi.giga.chattimeout. VPN/Happ/systemroutes/TLSverify не менялись; точечныйDirectexception только предложен, ответа владельца нет.

## Границы

ИсходникиE:/work/aegml; native/Docker/PG — C-backed WSL AegML-Hackathon. AEGYXR16INDEX81 owner-paused, A524/Ubuntu24.04/WindowsPG/PEM/httpd8080 не трогать. NoWSLshutdown/prune/globalkill/чужихvolume. Ключи только ignored0600nativeenv, не вGit/ответах. Текущиепроверки localQwen0/paid0; настоящиеbooking/payment запрещены. Frontend делает сокомандник, дизайнздесьне меняем.
