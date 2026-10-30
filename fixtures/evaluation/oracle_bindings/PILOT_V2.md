# Пилот revision 2: NLU-01/03/06/09

[pilot-v2.json](pilot-v2.json) сохраняет точные исходные реплики, `initial_trip={}`, reference date `2026-10-25`, `Europe/Moscow`. [Schema](pilot-v2.schema.json) описывает wrapper; внутри `cases[].binding` — прежний формат `oracle_bindings.v1` с `binding_revision=2`. Revision 1 и исходные annotations не изменены.

Это **34 BOUND typed checks и 8 UNBOUND обязательств**, а не четыре завершённых case. У каждого остаётся `original_behavior=UNBOUND`. У NLU-01 проверяется отсутствие структурированных offers/plans, а обещанные в свободном тексте цены не считаются автоматически проверенными. NLU-03: 7 nights; NLU-06: 4 nights; NLU-09: 4 days — отдельные `requested_duration_not_lost=UNBOUND / NOT_REPRESENTED`. Пока нет принятого duration-only пути, нельзя объявлять полную приёмку или выводить даты/число ночей из дней.

| Case | Конкретные typed ожидания |
| --- | --- |
| NLU-01 | Уточнение есть; нет созданных предложений/заказа/search tools; неизвестные origin и destination не становятся выбранными городами. Семантика предложенных направлений остаётся открыта. |
| NLU-03 | 0 выдуманных людей; budget `10000000` minor RUB, `scope=whole_trip`; origin неизвестен, явное назначение Сочи; уточнение есть. Состав семьи и duration дальше требуют полного oracle. |
| NLU-06 | origin Санкт-Петербург; ровно одно выбранное направление Стамбул; budget `12000000` minor RUB, whole trip; неизвестный party не выдуман; уточнение есть. |
| NLU-09 | origin Москва; ровно 1 traveler, adult; destination и budget неизвестны; уточнение есть. Минимальность/тема вопроса и 4 days не объявлены доказанными. |

Для всех: actual run должен завершиться, source arrays `plans/offers/live_offers` пусты, нет `TOOL_CALL_STARTED` и orders. Это проверяемые состояния, не оценка всей прозы модели. Reference date зафиксирована как policy; bound predicates не нормализуют относительные даты и не требуют подмены runtime clock. В реальном отчёте всё равно сохраняется фактический backend clock.

## Чистый адаптер

[`scripts/check_acceptance_pilot.py`](../../../scripts/check_acceptance_pilot.py) ничего не вызывает по сети и не читает environment/БД. Экспорт:

```python
evaluate_observation(case: dict, observation: dict) -> dict
```

`observation` совместим с detail-файлом Owner Evaluation:

```json
{
  "case_id": "NLU-06",
  "request_text": "<точная реплика из fixture>",
  "state_before": {"trip": "<канонический снимок fresh session>"},
  "state_after": {"trip": "<канонический снимок после run>", "status": "WAITING_USER", "waiting_reason": "clarification", "pending_question": {"questions": ["<фактический вопрос>"]}},
  "actual_run": {"status": "completed", "result": {"result": {}}},
  "current_run_events": [],
  "orders": []
}
```

Здесь placeholders — иллюстрация shape, не запускаемый положительный пример. `state_before.trip` должен содержать канонические пустые origin/people/groups/stays/legs/budget/constraints/preferences. Неполное observation, несовпадение case/text, nonempty initial или неизвестный оператор → `BLOCKED_OBSERVATION_OR_BINDING`, а не PASS. Незавершённый run → `BLOCKED_RUN`.

Поддержаны `eq`, `unchanged(before_turn)`, `count_where`, `within(nonempty_strings)`, непустые рекурсивные `all_of/any_of`. `where={}` считает все элементы; `where={"type":"adult"}` — точное совпадение полей; `where={"$any_nonempty":["city"]}` — элементы с выбранным city. Неизвестный `$filter` отклоняется и на пустом массиве.

Без `--observations` CLI только показывает замороженный план, **0 HTTP**:

```text
py scripts/check_acceptance_pilot.py
```

Для уже сохранённых очищенных observations (проверка также без HTTP):

```text
py scripts/check_acceptance_pilot.py --observations <saved.json> --output <new-report.json>
```

Input file содержит `fixture_sha256` и `observations: {"NLU-01": {...}, ...}`. Непредоставленные ID — `NOT_RUN`; неизвестный ID или другой fixture SHA отклоняется. Existing output не перезаписывается. Формат результатов: `PASS_BOUND_CHECKS / FAIL_BOUND_CHECKS / BLOCKED_* / NOT_RUN` отдельно от **всегда `full_case_status=BLOCKED_ORACLE`** в этом прототипе. Provenance реальной модели/HTTP, handler и budgets проверяет вызывающий actual runner; синтетический observation не доказывает работу LLM.
