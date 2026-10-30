"""Owner-dialogue source/partial-oracle controls, not backend or new NLU PASS.

No app imports, Store, API or provider calls. Original failures are asserted as
failures; declared counterfactuals only check that narrow predicates distinguish
safe context retention from the recorded mismatch. Other response meaning stays
under the fixture's human per-turn SPEC and adaptive conditions.
"""

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path

import pytest

PATH = Path(__file__).parents[1] / "fixtures/regression/owner-serbia-full-20261027.json"
SHA = "1f48645bad6ee87b9cae14843ca36784fb28805c62ed474b871b050e72c441e2"
INPUTS = ["Привет", "хочу в сербию наверное", "давай", "ты поможешь или нет?",
    "из москвы, я один взрослый и хочу в белгород в отель на неделю с 28 октября",
    "в белград хочу", "да", "да", "сохряняем", "1", "россии", "сам разбери"]


def fixture():
    raw = PATH.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == SHA
    return json.loads(raw)


def turn(number):
    return deepcopy(fixture()["turns"][number - 1])


def snapshot(number):
    data = fixture()
    row = data["turns"][number - 1]
    state = next(r["trip"] for r in data["observed_revisions"] if r["state_version"] == row["observed"]["state_version"])
    return {**deepcopy(row["observed"]), "trip": deepcopy(state), "input": row["input"]}


def requested(row):
    question = row.get("pending_question") or {}
    direct = question.get("requested_destination")
    nested = (question.get("context") or {}).get("requested_destination")
    if direct is not None and nested is not None and direct != nested:
        return None
    return direct or nested


def country_city_question(row):
    request = requested(row) or {}
    question = row.get("pending_question") or {}
    return (request.get("country_code") == "RS" and question.get("field") == "destination.city"
        and bool(re.search(r"город|Белград|Нови.Сад", question.get("text", ""), re.IGNORECASE)))


def consistent_selected_destination(row):
    """A retained unsupported request is allowed; an active contradictory stay is not."""
    request = requested(row) or {}
    if request.get("kind") != "city" or request.get("name") != "Белград" or request.get("country_code") != "RS":
        return False
    trip = row["trip"]
    if any(s.get("city") == "Белгород" for s in trip.get("stays", [])):
        return False
    return not any("Белгород" in (leg.get("origin"), leg.get("destination")) for leg in trip.get("legs", []))


def help_affirmed(reply):
    return (bool(re.search(r"\b(?:да|помогу|поможем|готов|могу помочь)\b", reply, re.IGNORECASE))
        and not re.search(r"по сохран[её]нным данным эта часть ответа не подтверждена", reply, re.IGNORECASE))


def nationality_question(row):
    q = row.get("pending_question") or {}
    return bool(re.search(r"гражданств|nationality", q.get("text", ""), re.IGNORECASE))


def nationality_field_consistent(row):
    if not nationality_question(row):
        return None  # Another genuinely missing question is not a nationality failure.
    q = row["pending_question"]
    return q.get("field") == "search.guest_nationality" and q.get("target_id") is None


def nationality_answer(before, after):
    q = before.get("pending_question") or {}
    if (not nationality_question(before) or q.get("state_version") != before["state_version"]
            or not q.get("question_id")):
        return None  # NEEDS_ADAPTIVE: do not assign citizenship from an unbound Russia.
    return (after["trip"]["search_policy"].get("guest_nationality") == "RU"
        and after["trip"].get("origin") == before["trip"].get("origin"))


def confirmed_city_survives_model_guard(row):
    raw = row["original_model_contracts"].get("validated")
    checked = row["original_model_contracts"].get("checked")
    if raw is None or checked is None:
        return None  # A controller-owned no-model path is not required to invent a raw contract.
    owned = [op for op in raw["operations"] if op["type"] == "upsert_stay" and op["value"].get("city") == "Белград"]
    return bool(owned) and all(any(op["type"] == "upsert_stay" and op.get("target_id") == original["target_id"]
        and op["value"].get("city") == "Белград" for op in checked["operations"]) for original in owned)


def no_inventory_claim(row):
    return not row.get("plans") and all(s.get("hotel_id") is None for s in row["trip"].get("stays", []))


def retarget_legacy(row):
    """Declared counterfactual, never presented as a current implementation run."""
    result = deepcopy(row)
    for stay in result["trip"]["stays"]:
        if stay["city"] == "Белгород":
            stay["city"] = "Белград"
    for leg in result["trip"]["legs"]:
        for field in ("origin", "destination"):
            if leg[field] == "Белгород":
                leg[field] = "Белград"
    return result


def test_exact_twelve_inputs_source_versions_and_immutable_failure_scope():
    data = fixture()
    assert [t["input"] for t in data["turns"]] == INPUTS
    assert [t["turn"] for t in data["turns"]] == list(range(1, 13))
    assert all(t["input_sha256"] == hashlib.sha256(t["input"].encode()).hexdigest() for t in data["turns"])
    assert data["source"]["sha256"] == "3f6d58ef911cc6f45353714e08da05d7e3b8dc739d77913164eb87fe8f4747fa"
    assert data["source"]["source_commit"] == "701da371fff15535ce2a2449e7b8a461f974482b"
    assert data["source"]["native_package_sha256"] == "d7d715c1ec628e34f0fcb863cec71d49dfbcdca80346c66b89ec0093a21d5d1a"
    assert sum(t["observed"]["model_calls"] for t in data["turns"]) == 7
    assert sum(t["observed"]["tool_calls"] for t in data["turns"]) == 0
    assert len(data["turns"]) == 12 and data["total_oracle_obligations"] == 42
    assert all(t["acceptance"]["oracles"] and t["acceptance"]["requirements"] for t in data["turns"])


def test_greeting_succeeds_without_inventing_travel_facts_or_a_model_call():
    row = snapshot(1)
    assert row["state_version"] == 0 and row["pending_question"] is None
    assert row["trip"]["origin"] is None and row["trip"]["travelers"] == row["trip"]["stays"] == []
    assert row["model_calls"] == row["tool_calls"] == 0


@pytest.mark.parametrize("number", [2, 3])
def test_original_city_question_was_present_in_model_reply_but_missing_from_bound_public_context(number):
    row, observed = turn(number), snapshot(number)
    assert "город" in row["original_model_contracts"]["validated"]["reply"]
    assert row["original_model_contracts"]["validated"]["information_subject"] == "Сербия"
    assert country_city_question(observed) is False
    candidate = deepcopy(observed)
    candidate["pending_question"] = {"field": "destination.city", "text": "Какой город в Сербии выберем?",
        "context": {"requested_destination": {"kind": "country", "name": "Сербия", "country_code": "RS"}}}
    assert country_city_question(candidate) is True
    assert candidate["trip"] == observed["trip"]  # No invented city, party, date or budget.


def test_help_answer_failure_is_not_the_correct_questions_about_actually_missing_data():
    old = "\n".join(snapshot(4)["reply"])
    assert help_affirmed(old) is False
    assert help_affirmed("Да, помогу. Из какого города вы отправляетесь и кто едет?") is True
    assert help_affirmed("Могу помочь. Какое гражданство использовать для поиска отеля?") is True
    assert not help_affirmed("Да. По сохранённым данным эта часть ответа не подтверждена.")


def test_original_compound_facts_were_partially_saved_and_unstated_room_budget_nationality_stay_unknown():
    row = snapshot(5)
    assert row["trip"]["origin"] == "Москва"
    assert [p["type"] for p in row["trip"]["travelers"]] == ["adult"]
    stay, = row["trip"]["stays"]
    assert stay["check_in"] == "2026-10-28" and stay["requested_nights"] == 7
    assert stay["check_out"] == "2026-11-04" and stay["rooms"] == []
    assert row["trip"]["budget"] is None and row["trip"]["search_policy"]["guest_nationality"] is None
    assert "may ask" in turn(5)["acceptance"]["duration_policy"]
    assert no_inventory_claim(row)


def test_legacy_cross_country_confirmation_is_not_falsely_marked_as_a_new_failure():
    row = snapshot(6)
    context = row["pending_question"]["context"]
    assert context["confirmation_required"] is True
    assert context["proposed_destination"] == {"kind": "city", "name": "Белград", "country_code": "RS"}
    assert row["state_version"] == snapshot(5)["state_version"]
    assert turn(6)["acceptance"]["observed_status"].startswith("PASS_NARROW")


@pytest.mark.parametrize("number", [7, 8, 9, 10, 11])
def test_pending_belgrade_cannot_hide_the_contradictory_active_belgorod_stay_and_route(number):
    observed = snapshot(number)
    assert requested(observed) == {"kind": "city", "name": "Белград", "country_code": "RS"}
    assert consistent_selected_destination(observed) is False
    candidate = retarget_legacy(observed)
    assert consistent_selected_destination(candidate) is True
    before_stay, = observed["trip"]["stays"]
    after_stay, = candidate["trip"]["stays"]
    assert {k: v for k, v in before_stay.items() if k != "city"} == {k: v for k, v in after_stay.items() if k != "city"}
    assert observed["trip"]["travelers"] == candidate["trip"]["travelers"]
    assert no_inventory_claim(candidate)
    candidate["trip"]["legs"][0]["destination"] = "Белгород"
    assert consistent_selected_destination(candidate) is False


def test_ambiguous_yes_loop_is_recorded_without_granting_an_arbitrary_keep_change_or_purchase_action():
    before, after = snapshot(7), snapshot(8)
    assert after["input"] == "да" and "или" in before["pending_question"]["text"]
    assert before["pending_question"]["text"] == after["pending_question"]["text"]
    assert before["trip"] == after["trip"] and before["state_version"] == after["state_version"]
    assert "ambiguous" in turn(8)["acceptance"]["requirements"][0]
    assert "NEEDS_ADAPTIVE" not in turn(8)["acceptance"]["observed_status"]


def test_exact_model_city_was_stripped_by_guard_and_declared_counterfactual_keeps_the_owned_target():
    row = turn(9)
    assert confirmed_city_survives_model_guard(row) is False
    assert row["original_normalizations"] == [{"code": "unanchored_destination_suppressed",
        "proposed": "Белград", "target_id": "new-stay-1"}]
    corrected = deepcopy(row)
    corrected["original_model_contracts"]["checked"]["operations"][0]["value"]["city"] = "Белград"
    assert confirmed_city_survives_model_guard(corrected) is True
    corrected["original_model_contracts"]["checked"]["operations"][0]["target_id"] = "foreign-stay"
    assert confirmed_city_survives_model_guard(corrected) is False
    corrected["original_model_contracts"] = {}
    assert confirmed_city_survives_model_guard(corrected) is None


def test_correct_nationality_question_is_allowed_but_stale_room_field_target_are_not():
    observed = snapshot(10)
    assert nationality_question(observed) is True
    assert nationality_field_consistent(observed) is False
    assert observed["pending_question"]["field"] == "stay.rooms"
    candidate = deepcopy(observed)
    candidate["pending_question"].update(field="search.guest_nationality", target_id=None)
    assert nationality_field_consistent(candidate) is True
    candidate["pending_question"]["text"] = "На какую дату планируете выезд?"
    assert nationality_field_consistent(candidate) is None  # Correct missing dates not failed as nationality.


def test_russia_answer_depends_on_the_fresh_owned_nationality_question_and_never_becomes_destination():
    before, after = snapshot(10), snapshot(11)
    assert nationality_answer(before, after) is False
    candidate = retarget_legacy(after)
    candidate["trip"]["search_policy"]["guest_nationality"] = "RU"
    assert nationality_answer(before, candidate) is True and consistent_selected_destination(candidate)
    candidate["trip"]["search_policy"]["guest_nationality"] = "RS"
    assert nationality_answer(before, candidate) is False
    candidate["trip"]["search_policy"]["guest_nationality"] = "RU"
    candidate["trip"]["origin"] = "Россия"
    assert nationality_answer(before, candidate) is False
    stale = deepcopy(before)
    stale["pending_question"]["state_version"] -= 1
    assert nationality_answer(stale, after) is None
    other = deepcopy(before)
    other["pending_question"]["text"] = "Какой город хотите выбрать?"
    assert nationality_answer(other, after) is None


def test_question_identity_reuse_and_resource_failure_preserve_original_trip_and_pending():
    before, after = snapshot(11), snapshot(12)
    assert snapshot(10)["pending_question"]["question_id"] == before["pending_question"]["question_id"] == after["pending_question"]["question_id"]
    assert before["trip"] == after["trip"] and before["pending_question"] == after["pending_question"]
    assert before["state_version"] == after["state_version"] == 2
    assert after["run_status"] == "failed" and turn(12)["original_model_contracts"] == {}
    rates = after["failure"]["rate_limits"]
    assert rates["token_window"] == "TPM" and rates["token_limit_from_error"] == 8000
    assert rates["tokens_used_from_error"] + rates["tokens_requested_from_error"] > rates["token_limit_from_error"]
    assert rates["retry_after_seconds"] == 15
    assert fixture()["resource_boundary"]["daily_quota_exhausted"] == "NOT_ESTABLISHED"
    assert all(snapshot(n)["tool_calls"] == 0 for n in range(1, 13))


def test_unknown_hotel_coverage_cannot_be_replaced_by_a_fabricated_property_or_plan():
    candidate = retarget_legacy(snapshot(11))
    assert no_inventory_claim(candidate)
    candidate["trip"]["stays"][0]["hotel_id"] = "invented-covered-hotel"
    assert not no_inventory_claim(candidate)
    candidate["trip"]["stays"][0]["hotel_id"] = None
    candidate["plans"] = [{"source_type": "LIVE_BOOKABLE", "total_minor": 1}]
    assert not no_inventory_claim(candidate)
