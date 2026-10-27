# Ручной разбор ZAI100-001–040, 28 октября 2026

Полностью прочитаны 40 диалогов и 345 исходных пользовательских реплик старого релиза a36d76/cd021acb, Z.ai glm-4.5-flash. Это разбор сохранённого actual-прогона, а не новый запуск или квалификация текущего b170. Все сообщения и доступные ответы читались последовательно; 003 перечитан отдельно после обрезанного первого вывода.

325 ходов COMPLETED, 17 FAILED, 3 неисполненных queued в012/T7–9. Ошибки:14 INVALID_LLM_OPERATION,2 INVALID_LLM_RESPONSE,1 REQUEST_TIMEOUT в017/T9. Ни одного показанного429. COMPLETED не означает правильный ответ/переход состояния. Новых model/API/DB-вызовов, тестов и изменений core:0.

Читались user/reply/error/pending и минимальные origin/party/dates/rooms/budget до→после. Не читались sourceevents, headers, ключи, capability/resume-vault, providerrawbody или reasoning. Preferences/constraints/groups/legs/offers исключены: их отсутствие в проекции не доказывает потерю. Source/projectionSHA, подробные наблюдения и ограничения — в [JSON](after80-manual-review-001-040-20261028.json).

## Новые подтверждённые наблюдения старого релиза

Причина модель/Harness UNKNOWN без finalcontract/guardtrace. IDs фиксируют exactповедение и остаются OPEN для targetedпроверки текущего релиза; исторические FAIL не переписываются.

| Постоянный ID | Случай | Факт | Требуемое действие и условие закрытия |
| --- | --- | --- | --- |
| HARNESS-004-REMOVED-CITY-AS-DESTINATION-001 | ZAI100-023/T7 | Удаляемый город создан как единственное проживание | Единственный stay должен стать Стамбулом 10–14 Nov либо нужен адресный безопасныйASK;Анталью создавать нельзя. |
| HARNESS-004-RELATIVE-CHECKOUT-CURRENT-BASE-001 | ZAI100-024/T8 | Относительный выезд использует неверную текущую базу/дельту | Exactцепочка 20→18→+1 должна дать 19 Nov с прежним заездом 15; проверить currentversionbase, старуюкорзину и отсутствие неназванных hard 5 ночей. |
| HARNESS-004-UNSPECIFIED-TRAVELER-TYPES-001 | ZAI100-025/T1 | Число людей без категорий превращено во взрослых | Не создавать 4 adult при явном запрете угадывать; сохранить известное количество/уточнить категории, затем принять явный 2 adult+children 4/12 без лишнихлюдей. |
| HARNESS-004-ROUTE-END-HOTEL-CONFLATION-001 | ZAI100-035/T7 | Конечный город маршрута заменяет город гостиницы | EndpointMoscow меняет только transport/endsemantics;Dubaihotel/dates/rooms сохраняются. Если setterнеподдержан, конкретныйASK, но неhotelretarget. Отдельно воспроизвести originT 5. |
| HARNESS-004-ALIAS-QUESTION-STAY-REMOVAL-001 | ZAI100-038/T6 | Вопрос об алиасах удаляет единственное проживание | Exactinput сединственнымSPbstay сохраняет ID/даты/номер/party и отвечаетaliases+budget. Настоящее explicitdelete должно оставаться возможным отдельнымконтролем. |
| HARNESS-004-NEGATED-DESTINATION-001 | ZAI100-040/T6 | Явно исключённый город становится назначением | Применить только budget 75000;сохранитьKazan/geoID/dates/party, не искатьSochi. PositiveexplicitSochi edit остаётся возможным. |
| HARNESS-004-ENGLISH-PLACE-QUERY-ROUTING-001 | ZAI100-039/T5 | Englishroom/origin инструкции принимаются за cityquery | Не посылать room/fullorigin инструкции как cityquery; сохранять независимыефакты иfreshplacechoice. Реальные новыеcitylabels/номера кандидатов должны работать. |
| HARNESS-004-EXPLICIT-DESTINATION-NONAPPLY-001 | ZAI100-028/T3 | Явная смена города/страны не применяется без понятного уточнения | Exact 028/T 3 иrelated 021/T 6,029/T 6,015/T 7 квалифицировать раздельно:commitподтверждённого направления либо конкретныйchoice/consent,не silentoldcity/genericwait. |
| HARNESS-004-GENERIC-STAY-KEEP-FALSE-PIN-001 | ZAI100-033/T6 | Сохранить гостиничное проживание ошибочно значит выбрать конкретныйhotel | Genericstaykeep сохраняетstayиобрабатываетnonstopбезfakehotelpin/previewrequirement. Явный keepконкретноговыбранногоhotel всё ещё требует freshselectionproof. |
| HARNESS-004-DATE-CONFLICT-QUESTION-BINDING-001 | ZAI100-016/T4 | Отклонённый неверныйcheckout объясняется вопросом про заезд | Exactinvalidorder:объяснитьcheckoutдолженбытьпозже 10 Nov и спросить выезд именноэтогоstay. Related 018/T 6 долженобъяснятьsame-dayorder,неroomcount. |

## Уже известные IDs

Повторно не заведены002budget,003suffix,004morning,005choice,008removal,012falsepause,017calendar и unaskedderivedduration. Их ранее сохранённые causalreceipts перечислены в JSON; наличие controlledисправления не превращает этот старый actualпрогон вPASS.

Повторные пропуски ответов о сохранённых фактах связаны с H004-SAVED-FACTS-001 / HARNESS-004-REPLY-LOSS-001. Примеры:007/T7,011/T9,014/T2,T8,022/T8,024/T6,026/T8,030/T9,031/T9,032/T8,036/T8,037/T9. Это не доказательство общего rootcause или универсального покрытия старым four-topics/morningисправлением.

## Все40диалогов

| Диалог | Полностью прочитано ходов | Предметный вывод и граница |
| --- | ---: | --- |
| ZAI100-001 | 11 | Контракт начальной полной заявки отвергнут; точный badfield UNKNOWN. После явных уточнений Москва→SPb, даты и бюджет сохранены. Ответ SPb на actual origin-question не считать ошибкой роли. |
| ZAI100-002 | 11 | Возрастная гипотеза не применена, явное 4→5 корректно. Бюджет 150000−20000 ошибочно 148000 вместо 130000 RUB; поздние план/корзина ссылки без ответа о номерах требуют adaptive actor. |
| ZAI100-003 | 10 | Явный checkout 10 NovT 3 пропущен; T 4 бюджетная реплика поздно применяет дату и неназванные hard 4 ночи. Whole-trip/per-night суммы не смешаны. Номер ещё не указан. |
| ZAI100-004 | 9 | Казань→Москва/12–15 Nov сохранены. T 9 рано утрам+вопрос о checkout получает genericno-change. МоскваT 3 не отвечала check-in, повтор вопроса оправдан. |
| ZAI100-005 | 9 | ГеовыборDubai теряется при независимой дате; infant 0, добавление взрослого и checkout+1 позже корректны. BareDubai не выбирает физического кандидата 1/2/3. |
| ZAI100-006 | 8 | Свежая family-заявка получает сообщение о сохранённых группах без сохранения маршрута. Позднее party/child 10→11/150000 сохранены. Rawgroup UNKNOWN; даты не отвечают actual group/origin-уточнениям. |
| ZAI100-007 | 8 | Даты 8–16→8–14 Nov изменены правильно. T 7 напомнить даты заменено roomquestion. Номера ни разу не заданы: отсутствие финального плана законно. |
| ZAI100-008 | 8 | Явное удаление ребёнка корректно. T 8 бюджет/recall без запроса удаляет ещё взрослого: осталось 1 вместо 2. Это canonical mutation, не только ошибочный текст. |
| ZAI100-009 | 9 | Начальная 1 adult поездка, гипотеза без мутации, +child 7 и correction 8 корректны; ответы source/cart по существу. Неназванный hardduration 5 наблюдается, hotel sourceerror честно обозначен. |
| ZAI100-010 | 9 | Whole-trip 120000, добавление друга и 135000 верны. Новый взрослый не вошёл в явный номер; T 7 общий estimate, T 9 актуального плана нет. Эффективный coverage требует отдельной проверки. |
| ZAI100-011 | 9 | 2 adult/Москва→Стамбул/23–29 Nov/110000 сохранены. T 9 даты/остаток утреннего требования не отвечены. Снятие prefs нельзя доказать без исключённых preference fields. |
| ZAI100-012 | 9 | Пока ничего не меняйT 6 ошибочно становится user_pause; T 7–9 queued, не исполнялись. Ранняя дата также заменяет неподтверждённый геовопрос. |
| ZAI100-013 | 8 | Сербия/Белгород приводят к БелградуRS; T 3 сменаSPbRU contracterror. Поздний RU/MS выбор может терять явную страну, exactcauseUNKNOWN. Bareдаты не ответ на candidate 1/2. |
| ZAI100-014 | 8 | RU/EN маршрут и budget 95000→105000 распознаны; savedfactsT 2/T 8 не отвечены, T 3 contracterror. ПослеfailedT 3 T 4 дата 20 Nov относится к check-in, не к предполагаемому checkout. |
| ZAI100-015 | 8 | TypoT 1 contracterror; явные SPb иSPbRU позднее не становятся city. Даты/2 adult/90000 сохраняются. Повтор origin после failedT 1 не означает потерю committedKazan. |
| ZAI100-016 | 8 | Даты словами 10–15 Nov приняты; checkout 9 Nov безопасно блокирован, но спрашивают известный check-in безtarget. Исправление 16 Nov работает; поздняя жалоба на неприменение расходится соstate. |
| ZAI100-017 | 9 | 31 Nov молча становится 1 Dec с реальной сменой state. Следующий REQUEST_TIMEOUT отдельный transport/resource outcome. Жалоба о повторном вопросе Москвы не подтверждается actual pending. |
| ZAI100-018 | 8 | Party/child 6/19–23 Nov/100000 сохранены. Zero-night безопасно не применён, но ошибка объясняется roomquestion; следующее relative+1 даёт contracterror. Номер не задан. |
| ZAI100-019 | 8 | Гипотетические 80 k/100 k не меняют 110 k; явные 95 k и before→after корректны, overbudget честно указан. Полная цена/coverage вне проекции. |
| ZAI100-020 | 8 | 2 rooms→1 familyroom назначает всех 4 существующих людей; гипотеза не возвращает 2 rooms. T 5 не называет число номеров, T 7 не объясняет отсутствие цены гипотезы. T 8 premise подходящего плана не доказан. |
| ZAI100-021 | 9 | Геовопрос не отвечен; явный Antalya→DubaiT 6 не применён, повторныйconsentT 7 затем заменён roomquestion. T 8 не отвечает да/нет, поэтому autoDubai недопустим; pending всё же должен сохраняться. |
| ZAI100-022 | 9 | Dubai не становится hotelstay. T 9 mandatorynonstop объяснён; T 8 вопрос сохранено ли условие проигнорирован. Соответствие каждого flightsegment исключено из проверки. |
| ZAI100-023 | 8 | Явный Istanbul первоначально не сохранён. T 7 убериAntalya/толькоIstanbul создаёт наоборот Antalya 10–14 Nov. T 8 предполагает вторую остановку при фактически одной, dependentcontracterror. |
| ZAI100-024 | 8 | T 7 checkout 20→18 Nov верен. T 8 +1 оттекущего даёт 20 вместо 19. T 6 явный saveddate вопрос не отвечен. Точный model/guardcause неверной базы UNKNOWN. |
| ZAI100-025 | 8 | T 1 нас 4/составещёненазван/неугадывай становится 4 adult; T 2 проблема не объяснена. Явный итоговый 2 adult+children 4/12 и correction 13 позже правильны. |
| ZAI100-026 | 8 | Belgrade без автозамены сохранён; гипотетический budget не меняется, checkout 9 Nov верен. Гео/capability и origin/datesrecall не отвечены. Номер не указан: supplieravailability не проверена. |
| ZAI100-027 | 8 | БелгородRU не подменёнБелградом; явныеKazan и originMoscow позже верны. T 2 геоконтраст не отвечен. Coverageотказ старогорелиза не является вечным запретом worldcities. |
| ZAI100-028 | 8 | NoviSad/party/child 9→10 сохранены, отсутствие sourceoffers честно. Явный IstanbulTRT 3 не применён; genericfieldnull. Поздние даты не доказывают состоявшуюся замену города. |
| ZAI100-029 | 10 | GeorgiaT 1 responseerror. Явный DubaiUAET 6 не применён; T 10 geographyDubai верна, но сохранённыйMoscow не назван. T 3 Moscow отвечал actualdestinationpending, не origin. |
| ZAI100-030 | 9 | Турция без autoIstanbul, party сохранён; выборAntalyaT 3 contracterror. Age 6→7 верен, T 9 recall не отвечен. Bareдаты после неуспешного выбора не доказывают ожидаемый dateflow. |
| ZAI100-031 | 9 | Dubai 1/2/3 pending сохраняется на датах, но заменяется room/origin фактами. Hypotheticalorigin не меняетKazan, явныйMoscow применяется; T 9 generic. Кандидат не выбран ни разу. |
| ZAI100-032 | 8 | Sochi выбран явно; child 4→5,85000,roompair верны. T 2 список городов иT 8 five-night вопрос не отвечены. HotelDEMOsourceerror не означает доказанный current providerfailure. |
| ZAI100-033 | 8 | ТолькоIstanbulhotel; invalid 25 Novcheckin при 20 Novcheckout блокирован. T 6 generic гостиницусохраняем превращается в specifichotel-previewASK. Первый slot былcheckout, неcheckin. |
| ZAI100-034 | 8 | Известный checkout 13 Nov спрашивается заново сtargetnull. ДобавитьAntalya/no-return contracterrors/неприменение; traincapability не отвечена. Phantomreturn по жалобе не доказан: legs исключены. |
| ZAI100-035 | 10 | ЯвныйoriginKazanT 5 не применён. Route-endMoscowT 7 заменяет hotelDubai→Moscow; T 10 удаляет единственныйstay. Доказана потеря hotel-role, точные returnlegs для этого не нужны. |
| ZAI100-036 | 8 | Tomorrow 29 Octcheckout и неверныеcheckin 29/30 блокированы; roomT 5 не сохранён. Invalidsame-daycompound contracterror, recalldateT 8 пропущен. Скрипт требует другой порядок/clock:29→29 не 2 ночи. |
| ZAI100-037 | 9 | АнтлаьюT 1 contracterror, явнаяAntalyaT 2 не записана; genericfieldnull вместо нужныхorigin/city/check-in. T 9 recall не отвечен.145 тыщ сразу сохранено 145000: жалоба про 1.45 млн неверна. |
| ZAI100-038 | 8 | AliasвопросПитер/SPb и 2 остановкиненужны удаляет единственныйstay сдатами/номером. T 7 зависимый contracterror после удаления, а не отдельное доказательство неверной dateарифметики. |
| ZAI100-039 | 8 | English One room,please и полнаяorigin-editреплика становятсяcityquery. Budget 100000/originMoscow сохранены, routequestionsgeneric. Даты не отвечали physicalplace 1/2/3; displayname неselectedref. |
| ZAI100-040 | 9 | Не вSochi/выбранаKazan/Sochiтолькоисключить меняетKazan→Sochi.75 k+10 k=85 k верно. Первым былcheckout:2 Nov→6 Nov не утверждённый check-in/out, повтор 85 k не выявляет moneybug. |

## Не подменять ошибочный oracle дефектом приложения

-012/T7–9 не исполнялись: это очередь после ложной паузыT6, а не три новых модельных отказа.
-033/034/036/040 сначала получили вопросcheckout. Первая bareдата относится кнему. Последующий check-in позже/втотжедень правильно отклонён; нельзя переименовать даты по порядку исходногоскрипта.
-В036 tomorrow разрешён в29Oct: checkout29Oct/checkinTomorrow не2ночи. Clockreference нужно фиксировать отдельно, не подгонять.
-Неотвеченный вопрос о номерах или physicalcandidatecity не позволяет предположить готовый план/корзину. Фразы скрипта «первыйплан/корзина» не доказывают их наличие.
-Некоторые жалобы скрипта расходятся с state:002возраст уже назван,016выезд уже исправлен,017Москва не спрашивалась заново,037бюджет145000 и040прибавка10000 правильны.
-Честный unknownfees/sourceerror и маркировкаDEMO/SANDBOX не равны ложной реальной брони. Сравнимостьsupplier, photos, полнаяoccupancy и checkout данным аудитом не квалифицированы.

Следующий шаг — exacttargetedповторы stateошибок на текущем релизе и содержательные oracles для неполных ответов, садаптацией креальным уточнениям. Основной план/PROJECT_STATE, core, тесты и исходные стендовые отчёты не изменялись.
