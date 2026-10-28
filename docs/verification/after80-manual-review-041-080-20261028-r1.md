# Ручной разбор 041–080: срез r1, 28 октября 2026

Все доступные ответы 041–071 прочитаны полностью: 31 наблюдавшийся чат, 267 исходных реплик. 041–070 имеют полные сохранённые последовательности;071 остановлен послеT2. 072–080 не запускались. Все85 неотправленных исходных текстов также прочитаны, но только как план: результатов у них нет.

В этом срезе253COMPLETED,12FAILED,2queued(060/T8–9);ошибки8INVALID_LLM_OPERATION,2INVALID_LLM_RESPONSE,2REQUEST_TIMEOUT.070/T1—историческийfailedprefix,скопированныйодинразбезповторногоPOST. Ниоставшиеся85реплик,никритерии781oracles не превращаютсявactualPASS.

Сумма с [первой половиной](after80-manual-review-001-040-20261028.md):71наблюдавшийсячат/612реплик,578completed+29failed+5queued. Это НЕзавершённые80диалогов/697реплик. НовыйsuffixRootразрешилотдельнопослесреза;егоещёнетвэтомотчёте.

Старыйruntimea36d76/cd021acb,Z.ai45. Currentb170/R12этотручнойаудитнеперепроверял. Новых API/LLM/DB-вызовов, тестов и изменений core:0. Читалисьтолькоwhitelistпользовательских/видимыхответов/error/pending/minimalstate;нетsourceevents/headers/providerraw/reasoning/ключей/vault. Prefs/constraints/groups/legs/offersисключены:неназванныеданныевпроекциинеявляютсядоказательствомпотери.

## Новые точные наблюдения

| Постоянный ID | Исходный случай | Факт | Условие закрытия |
| --- | --- | --- | --- |
| HARNESS-004-THOUSAND-DELTA-SCALE-001 | ZAI100-044/T7 | Масштаб тысяч в денежной дельте уменьшается в10раз | На exact 044:98000−8000=90000;на 064:120000−10000=110000. Сохранитьparty/date;неприниматьmodelarithmetic приоднозначномliteral. Проверитьсловами/цифрамиимасштабкопеек,непереносить 002 typoPASS. |
| HARNESS-004-BUDGET-QUESTION-MUTATION-001 | ZAI100-044/T8 | Проверочный вопрос о бюджете изменяет каноническую сумму | Наrecordedbefore 97200 вопрос«budget 90000?ничегонеменяй»долженответить 97200/объяснитьошибку,неизменяяrevision/quote/source. Явное«установи 90000»позитивныйконтроль. |
| HARNESS-004-HOTEL-CHECKOUT-ORIGIN-CONFLATION-001 | ZAI100-052/T5 | Город в фразе о выезде из гостиницы заменяет origin | Для 4 exactтекстовпривязатьдатукhotelstay;originостаетсяMoscow/Kazan/Kazan/Sochi.0 ложныхorigin=destinationflightsearch. Явнаясменаoriginдолжнаработать. |
| HARNESS-004-EXPLICIT-ORIGIN-CORRECTION-FALSE-AMBIGUITY-001 | ZAI100-061/T2 | Однозначное неA,аB превращается в неоднозначность origin | ExactT 2 сохраняетoriginSPb,hotelMoscow/party/dates/budgetбезизменений. ОтрицательныйKazanнестановитсявторымкандидатом;реальныедваальтернативныхoriginASK. |
| HARNESS-003-SCOPED-ORIGIN-RESTORE-NONAPPLY-001 | ZAI100-062/T8 | Историческая ссылка вернуть originкаквначале не применяется | ПослеSochi→KazanвернутьтолькоoriginSochi,сохранить+5000/budget 135000/checkout 18 Dec/ages 65,67. Неundoвсейверсии,небратьlatestнеправильныйoriginDubai. |
| HARNESS-004-INITIAL-SINGLE-ROOM-OMISSION-001 | ZAI100-041/T1 | Явный один семейный номер в полной заявке потерян | Exactinitialтекстсоздаёт 1 roomнасуществующих 2 adult+child 12 либоадресноуточняетдействительнонеизвестнуюallocation,нонепереспрашиваетужеявныйroomcount. Unknownroomcountнельзяdefault 1. |

Причины модель/guard/Harness остаютсяUNKNOWN без finalcontracttrace. Это OPEN старогорелиза, не утверждение что новые controlled-исправления не работают. Требуется exactrelatedповторнаcurrentrelease.

Не дублируются уже существующие UNASKED-ADULT-AGE(058/T12,059/T7,068/T2),falsepause(060/T7),savedfacts/replyloss и derivedduration. Неприменённые explicitcheckout в056/066/067/068/069 могутбытьсвязанысненазваннымиhardночами,ноточныйrejectedfield невыводитсяизодногоответа.

## Все случаи диапазона

| Диалог | Наблюдавшихся ходов | Ручной вывод |
| --- | ---: | --- |
| ZAI100-041 | 8 | Полная заявка сохраняет party, даты и 155000 RUB, но пропускает явно указанный семейный номер. Подтверждённая смена наDubaiT 3 получает contracterror. Поздние room/child 12→13/budget−20000 работают; актуальнаяТурция названа честно. |
| ZAI100-042 | 8 | Antalya сохраняется при гипотезеSochi,130000 RUB/2 adult/roompair целы, checkout 30 Nov применяется. Сам вопрос о запасномSochi не объяснён по существу. |
| ZAI100-043 | 10 | TbilisiGE/ограничениепоиска объяснены; повторный capability-вопросT 5 проигнорирован. Явная замена наDubai работает;15–20 Nov/room 1/115000 RUB и текущиймаршрут верны. |
| ZAI100-044 | 8 | Countryconflict Istanbul/Serbia не автоприменён, physicalchoice сохраняется. Budget 98000−«восемьтысяч» ошибочно 97200 вместо 90000; readonly«бюджет 90000?» затем сам исправляетегона 90000. T 4 baredatecontracterror. |
| ZAI100-045 | 8 | Цитата рекламыDubai не меняетSochi. Child 5→6 безновогодобавления корректен. Первыйslotcheckout 7 Nov: check-in 13 Nov правильно отклонён. Явныйcheckout 14 ноябраT 8 не применяется. |
| ZAI100-046 | 8 | Serbia остаётся nationality, destinationKazan не подменён; originSPbT 7 верен. CityechoT 2 contracterror, nationality-purposequestionT 6 неотвечен, readonlyrouteT 8 responseerror. Canonicalnationality исключена. |
| ZAI100-047 | 8 | Мосвкы→consent→Москва с независимой датой работает;26 Nov–2 Dec/room 2/135000;addadult 3 включёнвтотженомер. Страна назначения и причина pricechange не объясненыполностью. |
| ZAI100-048 | 8 | Дуба→consentDubai сохраняет party, но явное подтверждениеT 2 contracterror. Даты не отвечаютconsent, packet становитсяgenericfieldnull; room/costprefs не разрешаютего.145000 цел, нетfakeplan. |
| ZAI100-049 | 8 | SochiилиKazan не делаются двумяостановками; при этом initialoriginMoscow/party 2 не сохраняютсявfreshstate. ЯвныйSochiпозже принят,partyвосстановлен,room/80000 верны. Скрипт игнорируетactualoriginquestion. |
| ZAI100-050 | 8 | Неуверенный выдуманныйгород даёт contracterror, неfakeoffers. ЯвныйSochiпозже принят; origin/party/datesпослеinitialfailureнеизвестны.82000−две тысячи=80000 верно, actualfactrecall честен. |
| ZAI100-051 | 8 | Moscow→Istanbul/1 adult/checkin 2 Nov/100000 верны. Fresh«черездвадня»непризнано. Checkoutникогда незадан: относительный−2 дняправильнонеугадываетбазу. Hotelcomparisonбезпоказанныхпредложенийнеобъяснён. |
| ZAI100-052 | 11 | T 5 «датавыездаизАнтальи» меняетoriginMoscow→Antalya беззапроса;T 6 возвращаетMoscow. Явные 8 ночей,checkout+3→19 Nov/11 ночей/140000/2 adult и morningpref вT 11 верны. T 8 nightcheckнеотвечен. |
| ZAI100-053 | 8 | Состав 2 adult/children 4,9 иroom 4 не меняетсярадицены; отрицательный−1 возрастигнорирован.90000−десятьтысяч=80000 правильно, repeated 80000 неповторяетдельту. Полныйsourcepriceне квалифицирован. |
| ZAI100-054 | 9 | Infant 0 не превращаетсявдетейнет.110000 руб 50 коп точно 11000050 minor,десятичныйповторбезокругления. Checkout 26 Nov/age 0 иT 9 всеfacts верны. ВопроссмыслануливT 5 неотвечен. |
| ZAI100-055 | 8 | 2 сотрудника/2 раздельныхномера,3–7 Nov/70000→75000 корректны; pernight 5000 неподменяетwholetrip. T 7 roommapверен. T 8 одинhotelилидванепроясняет, хотяsourcecoverageцифрынесамипосебеошибка. |
| ZAI100-056 | 10 | TranslitKazan→Istanbulработает;историческийbudget 40000 неприменён. Checkout 5 DecT 7 contracterror;T 8«выездизСтамбула»меняетoriginKazan→Istanbul. Явные 6 ночейпозженормальны,нонеисправляютorigin. |
| ZAI100-057 | 8 | 3 adult/SPb→Sochi/14–17 Nov/120000/room 3;гипотетическийдругнеудалён,пересчётобъяснён. Typo«восемнацатое»checkout 18 неприменён,вместоэтогоunknownadultagequestion,обязательностькотороговпроекцииневидна. |
| ZAI100-058 | 13 | Serbia→Istanbulconflict→Нет иlateДапослебюджетанеавтоподтверждаютотвергнутыйгород. Antalyaвыбрана,sourcegeoнеподтверждён. T 12 checkout 30 Novприсваиваетadultage 30;31 Novнеизменилдату,нонеобъяснён. |
| ZAI100-059 | 11 | 2 adult/Kazan→Dubai/5–10 Nov/150000;hypotheticalFebнепереноситпоездку; +2 ночи→12 иexplicit 5 ночей→10 корректны. ReadonlyT 7 выдумываетadultage 18 обоим;T 9 hotelcheckoutменяетoriginнаDubai;T 11 recallпотерян. |
| ZAI100-060 | 9 | 29 Dec 2026→3 Jan 2027 иcheckout 2 Janкорректны. YearcrosscheckT 5 неотвечен. Вопрос«бюджетгарантируетбронь?поканичегонепокупаю»даётложнуюпаузу;T 8–9 queued,неисполнены. |
| ZAI100-061 | 8 | 50000−20%=40000 верно,checkout+1→20 Nov/4 ночи. Явное«неизKazan,аизSPb»ложноспрашиваетодинorigin,оставляетKazan;поздниеканоническиеответыправдивыпоstate,нонеисправляютпотерянноеизменение. |
| ZAI100-062 | 10 | Явныеadult 65/67 сначалаопущены,позжеисторическивосстановлены(невыдуманы). HotelcheckoutT 4 меняетoriginSochi→Dubai. Budget+5000→135000 верен;scoped«верниoriginкаквначале»T 8 неработает,explicitSochiT 9 работает. |
| ZAI100-063 | 8 | 1 adult/Moscow→Kazan/6–9 Nov/30 k;cheaperненуляетbudget; +5000→35 k,new 40 k,typo«выезт»+1→10 Novверны. MixedDEMO/SANDBOX/нетброниобъяснены. Constraint/prefsполностьюнепроверены. |
| ZAI100-064 | 8 | Agehypothesisнеприменена;explicit 7→8 верен.120000−10 тысячошибочно 119000 вместо 110000;T 4 подтверждаетневерныйstate. Checkout 26 Novкорректен;T 8 всеитоговыефактыпотерянывoverbudget/sourceboilerplate. |
| ZAI100-065 | 10 | 3 adult→4 adultпослеявногоподтверждения,room 4/135000,checkin 22 Nov/checkout 26 верны. Hypothesisнеприменена. Budgetscope/recall/отель 1 vs 2 вопросыполучаютnochange/genericвместоответа;T 10 savedfactsверны. |
| ZAI100-066 | 9 | Явнаяноваясумма 2000 USD=200000 minor,затем 115000 RUBкакноваясумманеFXправильны. T 6 currency/sumrecallпропущен,checkout 8 DecT 7 silentnonapply(остаётся 7). SourcecurrencyRUBвT 9 показана. |
| ZAI100-067 | 10 | Checkin 11 ноябяряопечаткапонята;checkout 17 T 4 неприменён,потомexplicit 6 ночейT 5 работает.−10%=72000,явноеудалениевторогоadultсchild 5 сохранённымверно. T 10 итогзаменёнsourceboilerplate. |
| ZAI100-068 | 9 | 06/11 срасшифровкой 6 Novприменено,новыдуманadultage 30. ReadonlydatesT 4 responseerror;explicitcheckout 10/11 T 5 неприменён,диапазон 6–10 T 6 работает.55000/originKazanверны,T 9 recallпотерян. |
| ZAI100-069 | 8 | Неопределённые 14/15 Novнеприменены. Вопросразницыночейнеотвечен;explicit 14 NovT 4 contracterror. ПятьночейT 5 создают 14 Nov,T 7 completeверен. НеобъявлятьповторT 8 новойошибкой. |
| ZAI100-070 | 8 | T 1 historicalREQUEST_TIMEOUTскопированодинразбезповторногоPOST. T 2 originKazanсохранён;бюджета/party/stayнетпослеfailedT 1. Процентбезbaselineправильноуточнён;T 4/T 8 savedfactsнеотвечены. Скрипт требуетнесуществующий 99000. |
| ZAI100-071 | 2 | ТолькоT 1–2 наблюдались:полнаяSPb→Istanbul 10–15 Dec/room 1/75000 принята;readonlyfive-nightquestionT 2 REQUEST_TIMEOUT 45 s. T 3–9 ещёненаправлялисьвэтомr 1 срезе. |
| ZAI100-072 | 0 | NOT_RUN: Проценты 100000→90000,неназванныеprefs,checkout 30→28 Nov/5 ночей,child 10→11 ссохранениеммладшего 3. |
| ZAI100-073 | 0 | NOT_RUN: Нулевыедети≠нулевойbudget/adults;45000→50000;checkout 7 Dec,неhotelcityasorigin;child 0 и+adultкоманды. |
| ZAI100-074 | 0 | NOT_RUN: Invalidcheckout 16<checkin 17 Novнеприменять,исправить 25 Nov;budget 130000−20000=110000,child 6→7. |
| ZAI100-075 | 0 | NOT_RUN: Scope-selectivebudgetundo 135000→125000 безоткатадругихfacts;checkout 19 Dec/7 ночей;originKazan/costpref. |
| ZAI100-076 | 0 | NOT_RUN: 2 families/9 pax/3 roomsсдельнойordinalmembership;firstfamilychild 6→7 другуюсемьюнеизменяет. |
| ZAI100-077 | 0 | NOT_RUN: 2 семьи/2 roomsсразнымиhardrefund/breakfastscopes;childfirst 5→6;explanation-or-explicitunsupportedнеunifycriteria. |
| ZAI100-078 | 0 | NOT_RUN: 9→10 pax/newduplicateage 9 сустойчивымиIDs;3→4 rooms;reallocatefirstfamilyonly;newchild 9→10 нетронутьстарого 9 secondfamily. |
| ZAI100-079 | 0 | NOT_RUN: Удалениеодногоиздвухadultво 2 roomможеттребоватьуточненияконкретногоID,но 3 adult+2 childитогдолженбытьпоистинномуconsent;budget−20000,child 9→10 во 2 room. |
| ZAI100-080 | 0 | NOT_RUN: 9 pax/3 rooms/2 families;secondfamilyseniorchild 12→13→14→13 неfirstfamily;budget 470000+costpref/noPurchaseвопроснетfalsepause. |

## Ограничения fixed-replay

- Послеfailedначальногохода070 нет ранее сохранённых90000/party/stay. Проситьbaseline для+10%и число взрослых правильно;скриптоваяфраза«лимит99000сохраняется»неподтверждаетуспешнуюпрошлуюоперацию.
- В045первымвопросомбылcheckout,поэтому7Nov—выезд,а13Nov—недопустимыйзаезд. Этотпорядокнельзяперевернутьрадиожиданийскрипта.
- В052претензия«Kazanяне называл»неподтверждена:originдоэтогобылnull. В062возраста65/67позжевосстановленыизявногоT1;этоотличаетсяотвыдуманных18/30.
- В051checkoutникогданезадан,поэтомуневозможновычестьизнеизвестнойдаты2дня. Нельзязасчитатьэтоошибкойарифметики.
- Реальныеhotel/flightstock,комиссии,полнаяallocationиpricecomparisonнеоцениваютсяпосредствомодноготекста. HonestDEMO/SANDBOXlabelsнеравныLIVEBOOKABLE.

Полные заметки по каждому чату, source/projectionSHA, exactnewfindinguser/before/afterи неприсланныеисходныетексты — в [JSON](after80-manual-review-041-080-20261028-r1.json). Основной план иPROJECT_STATEнеизменены.
