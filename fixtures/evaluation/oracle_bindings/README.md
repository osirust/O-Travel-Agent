# oracle_bindings v1 — подготовка машинной приёмки

Этот пакет содержит **106 неисполненных bindings**, а не результаты. Исходные JSONL/MD и модельные freeze не изменяются. [План](../../../docs/testing/ACCEPTANCE_PLAN.md) задаёт приоритет Owner-3 → actual local 106 → H1.

- `bindings.jsonl`: один record на исходный case ID; исходный и proposed record SHA, revision, все реплики и setup, proposed scope/layers, ссылка на прежнее частичное покрытие, обязательные `expectedChecks`.
- `schema.json`: JSON Schema записи binding, включая запрет PASS в самой спецификации и обязательный `original_behavior` check.
- `flag-adapters.json`: **проект** поверхностей/операторов и кандидатов для 79 флагов. `implementation_ref=null`: исполняемые evaluator-функции здесь не реализованы.
- `manifest.json`: bytes/SHA файлов пакета и защищённых источников/freeze; факт структурной сверки, не поведенческая приёмка.

## Привязка одного check

Каждому обязательному check нужны `binding_status=BOUND`, конкретный `expression`, принятая revision evaluator и ссылка `implementation_ref`. Expression указывает `surface` (`trip`, `session`, `messages`, `events`, `quote`, `orders`, `source`, `ui`), оператор, JSON Pointer и ожидаемое значение. `unchanged` требует именованный baseline. Именованные IDs могут использовать aliases, разрешаемые в конкретный объект этой сессии; они не выбираются из произвольной прошлой выдачи.

`setup_binding` связывает fresh session/initial state, clock/TZ, seed либо явную неподдержку seed, source snapshots, history, UI и fault. Неприменимость ресурса отмечается отдельной принятой policy; отсутствие файла или `null` не означает готовность. Для fault фиксируются момент/условие отказа, число попыток, recovery и проверяемые effects. Текст `test_setup` из источника сам не инъецирует fault.

Для `readiness=BOUND` **каждый** setup-ref должен быть непустой строкой. Неприменимый ресурс указывает `not_applicable:<accepted-policy-ref>`, неподдерживаемый seed — `not_supported:<verified-provider-policy-ref>`; runner обязан разрешить ссылку и проверить, что неприменимость допустима для этого case. `target_slot` и `required_confirmation` также задаются явно. Для информационного ответа без изменения slot используется принятая policy `none:<policy-ref>`, а не `null`.

`intent_contract` различает `PROPOSE`, `ASK`, `CHANGE` и `NONE`, задаёт slot и требование подтверждения. Кандидат с однозначным названием не разрешает мутацию. Unknown passport/citizenship не становится визовым выводом. `scope.source_proposal` сохраняет мнение автора, `scope.accepted` до решения остаётся `null`.

Ниже **пример структуры**, не добавленный исполняемый oracle для конкретного case:

```json
{
  "check_id": "preserve_origin",
  "origin": "derived_reviewed",
  "statement": "Вопрос о городе назначения не меняет origin.",
  "binding_status": "BOUND",
  "candidate_adapters": ["state.assertions"],
  "implementation_ref": "<reviewed evaluator path and revision>",
  "expression": {
    "surface": "trip",
    "operator": "unchanged",
    "path": "/origin",
    "baseline_ref": "before_turn",
    "expected": null
  },
  "critical": true,
  "result_default": "NOT_RUN"
}
```

Полный case не становится BOUND от одного такого predicate. Должны быть связаны все исходные обязательства, setup, semantic response checks и необходимые source/fault/UI части. `original_behavior` присутствует даже при `oracle_flags_proposed=[]`: пустой список не может дать успешный `all([])`.

## Правила адаптера/runner

1. Проверить SHA источников, физических строк **с line ending**, binding revision и schema. Несовпадение останавливает этот case; это preflight, не PASS поведения.
2. Разрешить только известные evaluator/операторы и принадлежащие этой сессии aliases. `UNBOUND`, неизвестный flag/evaluator, пропущенный assertion или setup-action → `BLOCKED`, с явной причиной.
3. Создать отдельное состояние case, применить setup/clock/source/fault fixtures, исполнить **все** исходные turns; после каждого сохранить наблюдения и проверить речевой акт, slot, diff, версии и побочные действия.
4. `PASS` разрешён только в отдельном execution report и только если все обязательные checks реально выполнены в требуемом слое. Частичный typed/domain PASS не переносится в `actual_local_llm`.
5. `FAIL` — исполненный predicate нарушен; `BLOCKED` — не хватает oracle/setup/data/модели; `UNSUPPORTED` — принята граница возможности; `NOT_RUN` — запуск не выполнялся. Неподдерживаемую возможность нельзя скрыть успешным guard и назвать полной.

Execution report связывает case/turn/check IDs с source SHA, binding SHA/revision, code/model/digest/параметрами, clock/seed, input/state-before/state-after, вопросами и очищенными events. Отдельно указываются attempts/latency и полное/частичное покрытие. Токены сессии, ключи, DSN и сырые credentials в артефакты не попадают.

Сейчас у всех records `readiness=UNBOUND`, `case_execution_default=NOT_RUN`, у всех checks `binding_status=UNBOUND`. Файлы валидны как формат, но **ни один полный case этим пакетом не объявлен готовым или прошедшим**. Наполнение оракулов и runner — следующий явно проверяемый этап.

`all_of`/`any_of` требуют непустые `children`, каждый дочерний predicate проходит ту же рекурсивную schema. `eq` и счётчики требуют явное `expected`; `unchanged` — явный baseline. Schema проверяет форму, а принятую семантику и содержимое ссылок должен проверять будущий runner.
