"""Pure runner/recipe checks. No native, API, model, SQL or credential access."""
import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("prefrontend_under_test", SCRIPTS / "evaluate_prefrontend_dialogues.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def frozen_r2():
    raw = (runner.ROOT / 'fixtures/evaluation/prefrontend-dialogues-20261027-r2.json').read_bytes()
    assert runner.sha(raw) == 'fc40e7aab1083633fb09fd3702772c6af50618b0dde04a38481ec73d7a9bb64e'
    return json.loads(raw)


def frozen_r3():
    raw = (runner.ROOT / 'fixtures/evaluation/prefrontend-dialogues-20261027-r3.json').read_bytes()
    assert runner.sha(raw) == 'c24251b6900c8d3650181bd0c7f038c801f537e67b3aab5c0005feea5f54e2b5'
    return json.loads(raw)


def obs():
    snapshot = {"session_id": "s", "state_version": 0, "trip": {"origin": "Москва",
        "travelers": [{"id": "a", "type": "adult"}, {"id": "c", "type": "child", "age": 7}],
        "groups": [{"id": "family", "traveler_ids": ["a", "c"], "keep_separate": True}],
        "stays": [{"id": "stay", "city": "Казань", "hotel_id": "h", "check_in": "2026-11-10", "rooms": [{"id": "r", "traveler_ids": ["a", "c"]}]}],
        "legs": [{"id": "leg", "departure_date": "2026-11-15", "mode": "train"}],
        "budget": {"amount_minor": 7500000}, "preferences": [{"key": "price_priority", "value": "lowest_total"}],
        "constraints": [{"key": "breakfast", "value": True, "hard": True, "stay_id": "stay"}]}}
    plan = {"plan_id": "p", "state_version": 0, "source_type": "DEMO", "total_minor": 4000000}
    return {"before": deepcopy(snapshot), "after": deepcopy(snapshot), "http_status": 200,
        "run": {"status": "completed", "error": {"code": "INVALID_REQUEST"}}, "reply": "Москва → Казань, ребёнок 7 лет.",
        "orders": [{"order_id": "o", "status": "BOOKED_DEMO", "is_demo": True, "booking_created_at_supplier": False}],
        "plans": [plan], "before_plans": [deepcopy(plan)], "captures": {"h": "h"}, "same_order": True}


@pytest.mark.parametrize("check", [
    {"op": "eq", "path": "/trip/origin", "value": "Москва"},
    {"op": "unchanged", "path": "/trip/travelers"},
    {"op": "party", "adults": 1, "ages": [7]},
    {"op": "stay", "city": "Казань", "rooms": 1, "check_in": "2026-11-10"},
    {"op": "reply_contains", "parts": ["Москва", "Казань", "7"]},
    {"op": "absent", "path": "/pending_question/context/proposed_destination"},
    {"op": "run_status", "value": "completed"}, {"op": "http_status", "value": 200},
    {"op": "last_leg_date", "value": "2026-11-15"},
    {"op": "preference", "key": "price_priority", "value": "lowest_total"},
    {"op": "group_count", "value": 1}, {"op": "allocation_complete", "rooms": 1},
    {"op": "constraint", "key": "breakfast", "value": True, "hard": True, "city": "Казань"},
    {"op": "hotel_capture", "city": "Казань", "capture": "h"},
    {"op": "plans_min", "value": 1}, {"op": "ready_plan"}, {"op": "plans_unchanged"},
    {"op": "order_count", "value": 1}, {"op": "only_simulated_orders"},
    {"op": "same_order"}, {"op": "leg_mode", "index": 0, "value": "train"},
    {"op": "run_error", "codes": ["INVALID_REQUEST"]},
])
def test_each_declared_case_predicate_has_an_actual_content_or_effect_oracle(check):
    assert runner.grade([check], obs())[0]["status"] == "PASS"


@pytest.mark.parametrize("check", [
    {"op": "eq", "path": "/trip/budget/amount_minor", "value": True},
    {"op": "eq", "path": "/trip/missing", "value": None},
    {"op": "unchanged", "path": "/trip/missing"},
    {"op": "party", "adults": 2, "ages": [7]},
    {"op": "reply_contains", "parts": ["Стамбул"]},
    {"op": "hotel_capture", "city": "Казань", "capture": "unbound"},
    {"op": "unknown_operator"},
])
def test_wrong_or_missing_content_does_not_pass_from_generic_metadata(check):
    assert runner.grade([check], obs())[0]["status"] != "PASS"


def test_no_vacuous_allocation_or_simulation_or_ready_false_pass():
    row = obs()
    row["after"]["trip"]["travelers"] = []
    row["after"]["trip"]["stays"][0]["rooms"] = [{"id": "empty", "traveler_ids": []}]
    row["orders"] = []
    row["plans"][0]["ready_for_simulated_checkout"] = False
    assert not runner.check_one({"op": "allocation_complete", "rooms": 1}, row)
    assert not runner.check_one({"op": "only_simulated_orders"}, row)
    assert not runner.check_one({"op": "ready_plan"}, row)


def test_recipe_is_eight_whole_dialogues_with_explicit_natural_and_typed_scope():
    recipe = runner.load_recipe()
    cases = recipe["cases"]
    assert len(cases) == 8 and sum(len(case["steps"]) for case in cases) == 98
    assert sum(step["kind"] in {"message", "queued_message", "resume"} for case in cases for step in case["steps"]) == 88
    assert [case["id"] for case in cases if case["initial_trip"]] == ["PF-TRANSPORT-05"]
    assert len(next(case for case in cases if case["id"] == "PF-DURATION-03")["steps"]) == 19
    assert all(step["checks"] for case in cases for step in case["steps"])
    assert all(any(check["op"] not in {"run_status", "http_status"} for check in step["checks"]) for case in cases for step in case["steps"])
    boundary = next(case for case in cases if case["id"] == "PF-SOURCE-07")
    assert "invalid-request boundary" in boundary["source_failure_policy"].casefold()
    assert "not a claim that vendor outage was injected" in boundary["source_failure_policy"].casefold()
    assert all(not part.isdecimal() for case in cases for step in case["steps"] for check in step["checks"]
               if check["op"] == "reply_contains" for part in check["parts"])


def test_r2_keeps_r1_immutable_and_corrects_only_unsupported_city_gold_and_model_reservations():
    from otravel.trip_answers import classify_saved_question
    source = runner.ROOT / "fixtures/evaluation/prefrontend-dialogues-20261027.json"
    raw = source.read_bytes()
    assert runner.sha(raw) == "cbaf7b168e0574aca26a9f30b043a7dd86d91dbca5d273f26b786ca413ffdad6"
    old, new = json.loads(raw), frozen_r2()
    assert new["revision"] == 2 and new["supersedes_recipe"]["sha256"] == runner.sha(raw)
    assert new["revision_rationale"]
    for old_case, new_case in zip(old["cases"], new["cases"], strict=True):
        assert old_case["id"] == new_case["id"]
        for before, after in zip(old_case["steps"], new_case["steps"], strict=True):
            assert before["id"] == after["id"] and before.get("text") == after.get("text")
            if (old_case["id"], before["id"]) == ("PF-GEO-01", "05"):
                assert after["scope"] == "unsupported_city_retained_as_request_not_bookable"
                assert {"op": "eq", "path": "/pending_question/context/requested_destination", "value": {
                    "kind": "city", "name": "Белград", "country_code": "RS"}} in after["checks"]
                assert {"op": "eq", "path": "/trip/stays", "value": []} in after["checks"]
                assert {"op": "stay", "city": "Белград"} in before["checks"]
                assert all(check in after["checks"] for check in before["checks"] if check["op"] != "stay")
            else:
                assert before["checks"] == after["checks"]
            assert before.get("requires") == after.get("requires")
            if after["kind"] == "message":
                expected_zero = (classify_saved_question(after["text"]) is not None
                    or after["text"].strip().casefold() == "подожди")
                assert (after["model_policy"] == "zero") is expected_zero
            else:
                assert before == after
    assert new["cases"][0]["steps"][5]["model_policy"] == "possible"


def test_r2_unsupported_city_gold_requires_exact_pending_and_no_fabricated_stay():
    case = frozen_r2()["cases"][0]["steps"][4]
    row = obs()
    row["after"]["trip"].update(origin="Москва", budget={"amount_minor": 8000000},
        travelers=[{"id": "a", "type": "adult"}], stays=[])
    row["after"]["pending_question"] = {"context": {"requested_destination": {
        "kind": "city", "name": "Белград", "country_code": "RS"}}}
    row["reply"] = "Понял как Белград, Сербия. Поиск пока недоступен."
    assert all(item["status"] == "PASS" for item in runner.grade(case["checks"], row))
    row["after"]["pending_question"]["context"]["requested_destination"]["country_code"] = "RU"
    assert any(item["status"] == "FAIL" for item in runner.grade(case["checks"], row))
    row["after"]["pending_question"]["context"]["requested_destination"]["country_code"] = "RS"
    row["after"]["trip"]["stays"] = [{"city": "Белград", "hotel_id": "fabricated"}]
    assert any(item["status"] == "FAIL" for item in runner.grade(case["checks"], row))


def test_r3_preserves_r2_and_changes_only_three_confirmed_oracles():
    old, new = frozen_r2(), frozen_r3()
    assert new['revision'] == 3 and new['supersedes_recipe']['sha256'] == runner.sha(
        (runner.ROOT / 'fixtures/evaluation/prefrontend-dialogues-20261027-r2.json').read_bytes())
    assert new['revision_rationale']
    modified = []
    for old_case, new_case in zip(old['cases'], new['cases'], strict=True):
        assert {key: value for key, value in old_case.items() if key != 'steps'} == {
            key: value for key, value in new_case.items() if key != 'steps'}
        for before, after in zip(old_case['steps'], new_case['steps'], strict=True):
            assert {key: value for key, value in before.items() if key != 'checks'} == {
                key: value for key, value in after.items() if key != 'checks'}
            if before['checks'] == after['checks']:
                continue
            identity = old_case['id'], before['id']
            modified.append(identity)
            expected = []
            for check in before['checks']:
                if identity == ('PF-GEO-01', '07') and check == {'op': 'stay', 'city': 'Белград'}:
                    expected.extend([
                        {'op': 'eq', 'path': '/pending_question/context/requested_destination',
                         'value': {'kind': 'city', 'name': 'Белград', 'country_code': 'RS'}},
                        {'op': 'eq', 'path': '/trip/stays', 'value': []},
                    ])
                elif identity in {('PF-GEO-01', '12'), ('PF-PAUSE-08', '02')} and check == {'op': 'unchanged', 'path': '/trip'}:
                    expected.extend([{'op': 'trip_unchanged_except_version'}, {'op': 'state_version_delta', 'value': 1}])
                else:
                    expected.append(check)
            assert after['checks'] == expected
    assert modified == [('PF-GEO-01', '07'), ('PF-GEO-01', '12'), ('PF-PAUSE-08', '02')]


def test_r3_rejected_alternative_requires_exact_selected_city_and_no_fabricated_stay():
    case = frozen_r3()['cases'][0]['steps'][6]
    row = obs()
    row['after']['trip'].update(budget={'amount_minor': 8000000}, stays=[])
    row['after']['pending_question'] = {'context': {'requested_destination': {
        'kind': 'city', 'name': 'Белград', 'country_code': 'RS'}}}
    assert all(item['status'] == 'PASS' for item in runner.grade(case['checks'], row))
    row['after']['pending_question']['context']['requested_destination'] = {
        'kind': 'country', 'name': 'Сербия', 'country_code': 'RS'}
    assert any(item['status'] == 'FAIL' for item in runner.grade(case['checks'], row))
    row['after']['pending_question']['context']['requested_destination'] = {
        'kind': 'city', 'name': 'Белград', 'country_code': 'RS'}
    row['after']['trip']['stays'] = [{'city': 'Белград', 'hotel_id': 'fabricated'}]
    assert any(item['status'] == 'FAIL' for item in runner.grade(case['checks'], row))


def control_revision_obs():
    row = obs()
    row['before']['trip']['state_version'] = row['before']['state_version']
    row['after']['state_version'] += 1
    row['after']['trip']['state_version'] = row['after']['state_version']
    return row


def group_pause_recipe(recipe):
    return next(step for case in recipe['cases'] if case['id'] == 'PF-GROUP-04'
        for step in case['steps'] if step['id'] == '09')


def test_r4_preserves_frozen_r3_and_changes_only_one_additional_control_revision_oracle():
    old, new = frozen_r3(), runner.load_recipe()
    assert new['revision'] == 4
    assert new['supersedes_recipe']['sha256'] == 'c24251b6900c8d3650181bd0c7f038c801f537e67b3aab5c0005feea5f54e2b5'
    modified = []
    for old_case, new_case in zip(old['cases'], new['cases'], strict=True):
        assert {k: v for k, v in old_case.items() if k != 'steps'} == {
            k: v for k, v in new_case.items() if k != 'steps'}
        for before, after in zip(old_case['steps'], new_case['steps'], strict=True):
            assert {k: v for k, v in before.items() if k != 'checks'} == {
                k: v for k, v in after.items() if k != 'checks'}
            if before['checks'] != after['checks']:
                modified.append((old_case['id'], before['id']))
                expected = []
                for check in before['checks']:
                    expected.extend([{'op': 'trip_unchanged_except_version'}, {'op': 'state_version_delta', 'value': 1}]
                        if check == {'op': 'unchanged', 'path': '/trip'} else [check])
                assert after['checks'] == expected
    assert modified == [('PF-GROUP-04', '09')]
    # The compound edit and exact unsupported-city consent remain strict.
    assert old['cases'][3]['steps'][3] == new['cases'][3]['steps'][3]
    assert old['cases'][0]['steps'][6] == new['cases'][0]['steps'][6]


def test_r4_group_pause_accepts_control_revision_but_does_not_rewrite_historical_gold():
    row = control_revision_obs()
    row['after']['waiting_reason'] = 'user_pause'
    old = group_pause_recipe(frozen_r3())['checks']
    new = group_pause_recipe(runner.load_recipe())['checks']
    assert any(item['status'] == 'FAIL' for item in runner.grade(old, row))
    assert all(item['status'] == 'PASS' for item in runner.grade(new, row))


@pytest.mark.parametrize('tamper', ['semantic', 'wrapper_version', 'nested_version', 'unpaused'])
def test_r4_group_pause_requires_unchanged_data_exact_version_and_explicit_pause(tamper):
    row = control_revision_obs()
    row['after']['waiting_reason'] = 'user_pause'
    if tamper == 'semantic':
        row['after']['trip']['budget']['amount_minor'] -= 2000000
    elif tamper == 'wrapper_version':
        row['after']['state_version'] += 1
    elif tamper == 'nested_version':
        row['after']['trip']['state_version'] += 1
    else:
        row['after']['waiting_reason'] = None
    checks = group_pause_recipe(runner.load_recipe())['checks']
    assert any(item['status'] == 'FAIL' for item in runner.grade(checks, row))


def test_r4_two_case_ten_call_dry_plan_never_touches_http_or_native(monkeypatch, capsys):
    def forbidden(*_args, **_kwargs):
        raise AssertionError('R4 dry plan touched a runtime surface')
    monkeypatch.setattr(runner, 'LocalHTTP', forbidden)
    monkeypatch.setattr(runner.NativeFence, 'check', forbidden)
    monkeypatch.setattr(runner, 'private_json', forbidden)
    assert runner.main(['--llm-profile', 'openrouter-dots', '--case-id', 'PF-GEO-01',
        '--case-id', 'PF-GROUP-04', '--max-model-calls', '10']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['selected_cases'] == ['PF-GEO-01', 'PF-GROUP-04']
    assert result['steps'] == 27 and result['max_model_calls'] == 10
    assert result['recipe_sha256'] == '79acc0928d5163f12d782128485a7976192ea48708cf58bf85ce724cdb4fa4ea'
    assert result['default_dry_run_http_calls'] == 0


def test_control_revision_ignores_only_trip_version_and_requires_exact_consistent_delta():
    row = control_revision_obs()
    assert runner.check_one({'op': 'trip_unchanged_except_version'}, row)
    assert runner.check_one({'op': 'state_version_delta', 'value': 1}, row)
    assert not runner.check_one({'op': 'unchanged', 'path': '/trip'}, row)


@pytest.mark.parametrize('field', ['trip_id', 'origin', 'travelers', 'groups', 'stays', 'legs',
    'budget', 'preferences', 'constraints', 'route_policy', 'search_policy', 'new_unreviewed_field'])
def test_control_revision_still_rejects_every_other_trip_change(field):
    row = control_revision_obs()
    row['after']['trip'][field] = 'unauthorized mutation'
    assert not runner.check_one({'op': 'trip_unchanged_except_version'}, row)


@pytest.mark.parametrize('side,scope,value', [
    ('before', 'wrapper', True), ('after', 'wrapper', True), ('after', 'wrapper', 0), ('after', 'wrapper', 2),
    ('before', 'nested', 1), ('after', 'nested', 0), ('after', 'nested', True), ('after', 'nested', None),
])
def test_control_revision_rejects_boolean_missing_inconsistent_or_wrong_versions(side, scope, value):
    row = control_revision_obs()
    (row[side] if scope == 'wrapper' else row[side]['trip'])['state_version'] = value
    assert not runner.check_one({'op': 'state_version_delta', 'value': 1}, row)


@pytest.mark.parametrize('expected', [True, -1, 0, 2, '1'])
def test_control_revision_rejects_invalid_or_wrong_expected_delta(expected):
    assert not runner.check_one({'op': 'state_version_delta', 'value': expected}, control_revision_obs())


@pytest.mark.parametrize('side', ['before', 'after'])
def test_control_revision_missing_trip_is_not_evidence_of_nonmutation(side):
    row = control_revision_obs()
    del row[side]['trip']
    assert not runner.check_one({'op': 'trip_unchanged_except_version'}, row)
    assert not runner.check_one({'op': 'state_version_delta', 'value': 1}, row)


def test_default_dry_run_cannot_construct_http_or_native_fence_or_output(monkeypatch, capsys):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Dry run touched runtime")
    monkeypatch.setattr(runner, "LocalHTTP", forbidden)
    monkeypatch.setattr(runner.NativeFence, "check", forbidden)
    monkeypatch.setattr(runner, "private_json", forbidden)
    assert runner.main([]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["default_dry_run_http_calls"] == 0 and result["parallel"] == 1
    assert result["max_model_calls"] == 50 and result["max_tool_calls"] == 160
    assert len(result["recipe_sha256"]) == len(result["driver_sha256"]) == 64


def test_unreviewed_recipe_and_unreviewed_actual_driver_cannot_write(monkeypatch):
    monkeypatch.setattr(runner, "RECIPE_SHA256", "0" * 64)
    with pytest.raises(ValueError, match="reviewed_recipe_sha_mismatch"):
        runner.load_recipe()


@pytest.mark.parametrize("models,tools", [(True, 160), (51, 160), (50, 161), (-1, 0), (1, -1), (1.5, 0)])
def test_evaluation_budget_is_bounded_and_strict(models, tools):
    with pytest.raises(ValueError):
        runner.Budget(models, tools)


class FakeClock:
    def __init__(self):
        self.now, self.waits = 100.0, []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


def test_model_pacing_waits_only_for_actual_generation_observation_and_not_zero_slots():
    clock = FakeClock()
    pacer = runner.ModelPacer(clock=clock, sleep=clock.sleep)
    pacer.before(True)
    pacer.observe([{"event_type": "SAVED_FACTS_ANSWER_PROPOSED"}])
    assert pacer.last_model_seen is None and clock.waits == []
    pacer.observe([{"event_type": "MODEL_CALL_STARTED", "created_at": "2999-01-01"}])
    pacer.before(False)
    assert clock.waits == []
    pacer.before(True)
    assert sum(clock.waits) == 60 and max(clock.waits) <= 10


@pytest.mark.parametrize("seconds", [0, 59, 121, True, float("nan"), float("inf")])
def test_pacing_cannot_be_disabled_or_unbounded(seconds):
    with pytest.raises(ValueError):
        runner.ModelPacer(seconds)


class FakeFence:
    def __init__(self):
        self.calls = 0

    def check(self):
        self.calls += 1
        return {}


class FakeAPI:
    def __init__(self):
        self.snapshot = {"session_id": "s", "state_version": 0, "waiting_reason": "user_pause", "trip": {
            "origin": "Москва", "travelers": [], "stays": [], "budget": {"amount_minor": 8000000}}}
        self.runs, self.events, self.posts, self.reads, self.messages = {}, [], [], [], []

    def request(self, method, path, token=None, body=None):
        if method == "POST":
            self.posts.append((path, deepcopy(body)))
            if path.endswith("/messages") and body["text"] == "продолжай":
                self.snapshot["trip"]["budget"]["amount_minor"] = 7000000
                self.snapshot.update(state_version=1, waiting_reason=None)
                self.runs["queued"]["status"] = "completed"
                self.runs["resume"] = {"run_id": "resume", "status": "completed"}
                self.events.append({"event_id": "model1", "event_type": "MODEL_CALL_STARTED"})
                self.messages.append({"role": "assistant", "run_id": "queued", "text": "Бюджет 70000 сохранён."})
                return {"status": 202, "body": {"run_id": "resume"}}
            if path.endswith("/messages"):
                self.runs["queued"] = {"run_id": "queued", "status": "pending"}
                return {"status": 202, "body": {"run_id": "queued"}}
        self.reads.append(path)
        if path == "/api/v1/sessions/s": data = self.snapshot
        elif path.endswith("/events"): data = {"events": self.events}
        elif path.endswith("/messages"): data = {"messages": self.messages}
        elif path.endswith("/orders"): data = {"orders": []}
        elif path.startswith("/api/v1/runs/"): data = self.runs[path.rsplit("/", 1)[1]]
        else: raise AssertionError("Unexpected fake API route")
        return {"status": 200, "body": deepcopy(data)}


def driver(api=None, budget=None):
    api = api or FakeAPI()
    clock, writes = FakeClock(), {}
    instance = runner.Driver([], Path("/not-created"), http=api, fence=FakeFence(), budget=budget,
        pacer=runner.ModelPacer(clock=clock, sleep=clock.sleep), writer=lambda path, data: writes.update({str(path): deepcopy(data)}),
        clock=clock, sleep=clock.sleep)
    context = {"route": "/api/v1/sessions/s", "token": "synthetic-private-session-token", "plans": [], "captures": {}, "queued": []}
    instance.cleaner.add(context["token"])
    return instance, context, api, writes


def test_paused_message_is_accepted_but_not_waited_until_explicit_resume():
    instance, ctx, api, _ = driver()
    waited = []
    wait = instance.wait
    instance.wait = lambda run_id, token: (waited.append(run_id), wait(run_id, token))[1]
    queued = {"id": "q", "kind": "queued_message", "model_policy": "zero", "text": "Бюджет 70000.",
              "checks": [{"op": "run_status", "value": "pending"}, {"op": "unchanged", "path": "/trip"}]}
    row = instance.execute(ctx, queued)
    assert row["status"] == "PASS" and waited == [] and ctx["queued"] == ["queued"]
    assert instance.budget.models == 0 and api.snapshot["trip"]["budget"]["amount_minor"] == 8000000
    resumed = {"id": "r", "kind": "resume", "model_policy": "possible", "text": "продолжай",
               "checks": [{"op": "eq", "path": "/trip/budget/amount_minor", "value": 7000000}]}
    row = instance.execute(ctx, resumed)
    assert row["status"] == "PASS" and waited == ["resume", "queued"] and ctx["queued"] == []
    assert row["observation"]["drained_runs"][0]["status"] == "completed"
    assert instance.budget.models == 1 and len(api.posts) == 2


def test_budget_fence_halts_before_a_message_even_when_recipe_expects_zero_model():
    instance, ctx, api, _ = driver(budget=runner.Budget(models=0, tools=160))
    with pytest.raises(runner.StopRun, match="ENGINEERING_BUDGET_NO_WRITE"):
        instance.execute(ctx, {"kind": "message", "model_policy": "zero", "text": "Напомни бюджет.", "checks": []})
    assert api.posts == []


def test_unknown_post_is_recorded_once_and_never_resent():
    instance, ctx, api, writes = driver()
    def unknown(method, path, token=None, body=None):
        api.posts.append((path, deepcopy(body)))
        raise runner.StopRun("UNKNOWN_WRITE_NO_RESEND")
    api.request = unknown
    with pytest.raises(runner.StopRun, match="UNKNOWN_WRITE_NO_RESEND"):
        instance.post(ctx["route"] + "/messages", ctx["token"], {"client_message_id": "synthetic-message", "text": "привет"})
    assert len(api.posts) == 1
    assert writes["/not-created/vault.json"]["last_intent"]["status"] == "ABOUT_TO_SEND"


def test_clean_report_never_contains_session_token_or_idempotency_key():
    instance, _, _, writes = driver()
    instance.report["example"] = {"session_token": "synthetic-private-session-token", "idempotency_key": "synthetic-private-confirmation"}
    instance.save()
    saved = json.dumps(writes["/not-created/report.json"])
    assert "synthetic-private-session-token" not in saved and "synthetic-private-confirmation" not in saved


def test_case_hotel_lookup_never_chooses_another_property_or_missing_offer():
    instance, _, _, _ = driver()
    plans = [{"offers": [{"kind": "hotel", "city": "Казань", "name": "Actual shown", "hotel_id": "h"}]}]
    assert instance.hotel(plans, {"plan_index": 0, "city": "Казань", "field": "hotel_id"}) == "h"
    with pytest.raises(runner.NotReached):
        instance.hotel(plans, {"plan_index": 0, "city": "Сочи", "field": "hotel_id"})


def test_only_runner_script_uses_public_api_and_never_store_or_provider_keys():
    import ast
    tree = ast.parse((SCRIPTS / "evaluate_prefrontend_dialogues.py").read_text(encoding="utf-8"))
    imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not any(name.startswith(("otravel", "sqlalchemy", "asyncpg")) for name in imports)
    source = (SCRIPTS / "evaluate_prefrontend_dialogues.py").read_text(encoding="utf-8")
    assert "read_values" not in source and "GROQ_API_KEY" not in source and "seed_pending(" not in source


@pytest.mark.parametrize("check", [
    {"op": "reply_money", "amount_minor": 38000000, "currency": "RUB", "topic": "budget"},
    {"op": "reply_money", "amount_minor": 40000000, "currency": "RUB", "topic": "budget"},
    {"op": "reply_age", "value": 8}, {"op": "reply_nights", "value": 7},
    {"op": "reply_dates", "values": ["2026-11-10", "2026-11-17"]}, {"op": "reply_change_marker"},
])
def test_reply_numeric_facts_are_bound_to_ages_units_currency_or_full_dates(check):
    row = obs()
    row["reply"] = "Добавлен ребёнок 8 лет. Бюджет: 400 000 RUB → 380 000 RUB. 7 ночей, заезд 10.11.2026, выезд 17.11.2026."
    assert runner.grade([check], row)[0]["status"] == "PASS"


@pytest.mark.parametrize("check,text", [
    ({"op": "reply_age", "value": 8}, "Вариант 8, бюджет 380000 RUB."),
    ({"op": "reply_age", "value": 8}, "Ребёнок 18 лет."),
    ({"op": "reply_nights", "value": 5}, "75 ночей, бюджет 75000 RUB."),
    ({"op": "reply_money", "amount_minor": 38000000, "currency": "RUB", "topic": "budget"}, "Бюджет 380000."),
    ({"op": "reply_money", "amount_minor": 38000000, "currency": "RUB", "topic": "budget"}, "Бюджет 38000000 RUB."),
    ({"op": "reply_money", "amount_minor": 38000000, "currency": "RUB", "topic": "budget"}, "Цена 380000 RUB."),
    ({"op": "reply_dates", "values": ["2026-11-10"]}, "Вариант 10, 17 ночей."),
])
def test_unrelated_or_embedded_digits_do_not_pass_reply_semantics(check, text):
    row = obs()
    row["reply"] = text
    assert runner.grade([check], row)[0]["status"] == "FAIL"


def test_native_manifest_refusal_or_io_error_is_a_code_only_stop_before_writes(tmp_path, monkeypatch):
    from types import SimpleNamespace
    profile = "groq-qwen"
    expected = "a" * 64
    receipt_path = tmp_path / runner.LLM_PROFILES[profile]["evidence"]
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text(json.dumps({"status": "RUNNING", "manager": runner.LLM_PROFILES[profile]["manager"],
        "source": str(runner.NATIVE_SOURCE), "current_source_manifest": {"sha256": expected},
        "loaded_manifest": {"matches_current": True}}))
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    class ForeignRefusal(Exception):
        pass
    def refusal(_):
        raise ForeignRefusal("synthetic-private-path-and-error")
    monkeypatch.setitem(sys.modules, "local_backend", SimpleNamespace(source_manifest=refusal, Refusal=ForeignRefusal))
    fence = runner.NativeFence(profile, expected, http=None)
    with pytest.raises(runner.StopRun, match="^NATIVE_FENCE_REFUSED_NO_WRITE$"):
        fence.check()
    receipt_path.unlink()
    with pytest.raises(runner.StopRun, match="^NATIVE_FENCE_REFUSED_NO_WRITE$"):
        fence.check()


def test_fence_refusal_during_driver_keeps_a_terminal_report_and_never_posts():
    instance, _, api, writes = driver()
    def refused():
        raise runner.StopRun("NATIVE_FENCE_REFUSED_NO_WRITE")
    instance.fence.check = refused
    instance.cases = [{"id": "one", "title": "known", "bootstrap_scope": "fresh_empty_session", "initial_trip": {}, "steps": []}]
    result = instance.run()
    assert result["status"] == "STOPPED_NO_RETRY" and api.posts == []
    assert writes["/not-created/report.json"]["status"] == "STOPPED_NO_RETRY"


@pytest.mark.parametrize("profile", ["groq-qwen", "zai", "openrouter-dots"])
def test_cloudflare_short_interval_cannot_be_used_for_other_fixed_profiles(profile):
    with pytest.raises(ValueError, match="profile_bound"):
        runner.ModelPacer(15, llm_profile=profile)
    assert runner.ModelPacer(60, llm_profile=profile).seconds == 60


def test_only_exact_cloudflare_profile_can_use_fifteen_second_pacing():
    clock = FakeClock()
    pacer = runner.ModelPacer(15, llm_profile="cloudflare-nemotron", clock=clock, sleep=clock.sleep)
    pacer.observe([{"event_type": "MODEL_CALL_STARTED"}])
    pacer.before(False)
    assert clock.waits == []
    pacer.before(True)
    assert sum(clock.waits) == 15 and max(clock.waits) <= 10
    with pytest.raises(ValueError, match="unknown_fixed_pacing_profile"):
        runner.ModelPacer(15, llm_profile="pretend-cloudflare")


def test_cloudflare_dry_plan_does_not_touch_http_or_relax_other_model_defaults(monkeypatch, capsys):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Dry plan touched HTTP")
    monkeypatch.setattr(runner, "LocalHTTP", forbidden)
    assert runner.main(["--llm-profile", "cloudflare-nemotron", "--model-interval-seconds", "15", "--max-model-calls", "40"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["profile"] == "cloudflare-nemotron" and result["model_interval_seconds"] == 15
    assert result["max_model_calls"] == 40 and result["default_dry_run_http_calls"] == 0


@pytest.mark.parametrize("runtime_provider,accepted", [("groq", False), ("cloudflare", True)])
def test_native_fence_checks_actual_profile_and_payzero_before_allowing_post(tmp_path, monkeypatch, runtime_provider, accepted):
    from types import SimpleNamespace
    profile, expected, pid, ticks = "cloudflare-nemotron", "a" * 64, 123456, 777
    receipt_path = tmp_path / runner.LLM_PROFILES[profile]["evidence"]
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text(json.dumps({"status": "RUNNING", "manager": runner.LLM_PROFILES[profile]["manager"],
        "source": str(runner.NATIVE_SOURCE), "database": "otravel_zai_eval", "url": "http://127.0.0.1:8084",
        "current_source_manifest": {"sha256": expected}, "loaded_manifest": {"matches_current": True},
        "process": {"pid": pid, "start_ticks": ticks}, "limits": {"calls_per_run": 1, "local_calls": 0,
            "fallback": False, "cloudflare_daily_engineering_bound": 2000}}))
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    class Refusal(Exception):
        pass
    monkeypatch.setitem(sys.modules, "local_backend", SimpleNamespace(source_manifest=lambda _: {"sha256": expected}, Refusal=Refusal))
    original_read = Path.read_text
    def read(path, *args, **kwargs):
        if str(path) == f"/proc/{pid}/stat":
            return f"{pid} (python) " + " ".join(["S", *(["0"] * 18), str(ticks)])
        return original_read(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", read)
    status = {key: {"configured": False, "free_tier_confirmed": False} for key in
        ["gemini", "openrouter", "groq", "zai", "cloudflare", "ollama", "hotelbeds", "ratehawk", "travelpayouts"]}
    status.update({"_runtime": {"llm_provider": runtime_provider,
        "model": runner.LLM_PROFILES[profile]["model"] if runtime_provider == "cloudflare" else "qwen/qwen3.8-27b"},
        "cloudflare": {"configured": True, "free_tier_confirmed": True, "paid_fallback": False},
        "liteapi": {"configured": True, "mode": "sandbox"}, "duffel": {"configured": True, "mode": "sandbox"},
        "serpapi": {"configured": True, "free_tier_confirmed": True, "source_type": "LIVE_INDICATIVE"},
        "open_meteo": {"configured": True}})
    reads = []
    class ReadHTTP:
        def get(self, path):
            reads.append(path)
            return {"database": "ready", "supplier_booking": False} if path == "/health" else status
    fence = runner.NativeFence(profile, expected, ReadHTTP())
    if accepted:
        assert fence.check()["manager"] == "after80_cf_backend.py"
    else:
        with pytest.raises(runner.StopRun, match="NATIVE_FENCE_REFUSED_NO_WRITE"):
            fence.check()
    assert reads == ["/health", "/api/v1/providers/status"]
