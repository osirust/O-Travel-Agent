"""Owned place worker with controlled coordinator/catalog/store. No HTTP or PG."""
from copy import deepcopy
from datetime import timedelta

import pytest
from test_dynamic_place_suppliers import NOW, ref

from otravel.domain import DomainError, Operation, SearchPolicy, TripState, sample_trip
from otravel.place_refs import CityRefSnapshot
from otravel.place_resolution import OWNER, PlaceResolutionCoordinator, _binding, _choice_id, _read_pending
from otravel.place_run import finish_place_run
from otravel.store import Conflict


def ask(state, *, role="destination", stay_id="stay-istanbul", queue=None, source_id=3435910):
    packet = {"owner": OWNER, "trip_id": state.trip_id, "state_version": state.state_version,
        "resolution_id": "pr-" + "a" * 32, "role": role, "stay_id": stay_id,
        "query": "Buenos Aires", "country_ref": None, "stage": "city_choice",
        "expires_at": (NOW + timedelta(minutes=15)).isoformat(),
        "candidates": [{"kind": "city", "source_id": source_id, "label": "Буэнос-Айрес", "country_iso2": "AR",
            "fact_sha256": "a" * 64, "response_sha256": "b" * 64, "key": None}]}
    for candidate in packet["candidates"]:
        candidate["candidate_id"] = _choice_id(packet, candidate)
    context = _binding(packet)
    if queue:
        context["place_remaining_requests"] = deepcopy(queue)
    return {"question_id": "original-question", "state_version": state.state_version,
        "text": "Какой конкретный город выбрать?", "field": "place.resolution", "target_id": stay_id, "context": context}


def selection(pending):
    packet = pending["context"]["place_resolution"]
    return {"resolution_id": packet["resolution_id"], "candidate_id": packet["candidates"][0]["candidate_id"]}


def selected(location=None, target="stay-istanbul"):
    location = location or ref()
    return {"action": "selected", "operations": [{"type": "upsert_stay", "target_id": target,
        "value": {"city": location.canonical_name, "place_id": location.place_id, "hotel_id": None}}],
        "resolved_places": {location.place_id: location.model_dump(mode="json")},
        "notice": "Выбранный город сохранён.", "pending_context": None, "question": None}


class ControlledCoordinator(PlaceResolutionCoordinator):
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []
        self.clock = lambda: NOW

    async def _answer(self, method, value, pending, state, before_call):
        self.calls.append({"method": method, "value": deepcopy(value), "pending": deepcopy(pending), "state": state.model_dump(mode="json")})
        await before_call({"provider": "openmeteo", "operation": "declared", "method": "GET"})
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer(value, pending, state) if callable(answer) else deepcopy(answer)

    async def resolve(self, request, state, *, before_call):
        return await self._answer("resolve", request, None, state, before_call)

    async def select(self, value, pending, state, *, before_call):
        _read_pending(pending, state.model_dump(mode="json"), NOW)
        return await self._answer("select", value, pending, state, before_call)


class Catalog:
    def __init__(self, error=None):
        self.error, self.calls = error, []

    async def verify_places(self, values, **kw):
        self.calls.append((deepcopy(values), kw))
        if self.error:
            raise self.error
        return {key: CityRefSnapshot.model_validate(value) for key, value in values.items()}


class Store:
    def __init__(self):
        self.calls = []

    async def finish_run(self, *args, **kw):
        self.calls.append((deepcopy(args), deepcopy(kw)))


class Harness:
    def __init__(self, answers, catalog_error=None):
        self.place_resolution = ControlledCoordinator(answers)
        self.geo_catalog = Catalog(catalog_error)
        self.store = Store()
        self.fences, self.budget_calls, self.searches = 0, [], []
        self.stale = False

    async def _ensure_current_run(self, session, run):
        self.fences += 1
        if self.stale:
            raise Conflict("declared stale fence", "stale_state")

    async def _before_place_call(self, session, run, event):
        self.budget_calls.append(deepcopy(event))
        return 10.0

    async def _search_trip(self, state, **kw):
        self.searches.append(state.model_dump(mode="json"))
        return {"plans": [], "questions": [], "partial_plan": {"offers": [], "blockers": [{"code": "missing_transfer"}]}}


def context(state=None, pending=None, waiting=None):
    state = state or sample_trip()
    state.search_policy = SearchPolicy(mode="HYBRID", hotel_provider="liteapi", flight_provider="duffel", guest_nationality="RU")
    session = {"session_id": "session-declared", "trip_id": state.trip_id, "state": state.model_dump(mode="json"),
        "state_version": state.state_version, "pending_question": pending, "waiting_reason": waiting, "status": "TRIP_DRAFT"}
    run = {"run_id": "run-declared", "session_id": session["session_id"], "input_text": "declared input"}
    return state, session, run


@pytest.mark.asyncio
async def test_independent_facts_and_owned_selection_commit_once_with_exact_catalog_and_virtual_version():
    state = sample_trip(); pending = ask(state)
    state, session, run = context(state, pending)
    before = deepcopy(session)
    old_id = selection(pending)["candidate_id"]
    h = Harness([selected()])
    await finish_place_run(h, session, run, state, selection=selection(pending),
        operations=[Operation(type="set_budget", value={"amount_minor": 7000000})],
        model_meta={"provider": "controlled", "model": "declared", "reply": "DO NOT DISPLAY MODEL REPLY"})
    assert len(h.store.calls) == 1 and session == before
    args, kw = h.store.calls[0]
    assert args[1] == 0 and args[2]["state_version"] == 1
    assert args[2]["budget"]["amount_minor"] == 7000000
    assert args[2]["stays"][0]["place_id"] == ref().place_id
    assert args[4] == "Выбранный город сохранён." and "MODEL REPLY" not in args[4]
    coor = h.place_resolution.calls[0]
    assert coor["state"]["state_version"] == 1 and coor["value"]["candidate_id"] != old_id
    assert coor["value"]["candidate_id"] == selection(coor["pending"])["candidate_id"]
    assert len(h.geo_catalog.calls) == 1 and h.geo_catalog.calls[0][1]["require_fresh"] is True
    assert h.geo_catalog.calls[0][1]["require_current"] is True
    assert kw["pending_question"] is None and len(h.searches) == 1


@pytest.mark.asyncio
async def test_ask_keeps_facts_binds_question_and_does_not_start_search():
    h = Harness([{"action": "ask", "question": "Какой именно город в Аргентине выбрать?",
        "pending_context": {"field": "place.city_query", "target_id": "stay-istanbul"}}])
    state, session, run = context()
    await finish_place_run(h, session, run, state, request={"role": "destination", "kind": "country", "query": "Аргентина", "stay_id": "stay-istanbul"},
        operations=[Operation(type="set_budget", value={"amount_minor": 7000000})])
    args, kw = h.store.calls[0]
    assert args[2]["budget"]["amount_minor"] == 7000000 and args[2]["state_version"] == 1
    assert args[3] == "WAITING_USER" and args[4] == "Какой именно город в Аргентине выбрать?"
    pending = kw["pending_question"]
    assert pending["state_version"] == 1 and pending["field"] == "place.city_query" and pending["target_id"] == "stay-istanbul"
    assert kw["waiting_reason"] == "clarification" and not h.searches and not h.geo_catalog.calls


@pytest.mark.asyncio
async def test_source_exception_uses_fixed_safe_question_but_retains_independent_facts():
    h = Harness([RuntimeError("SECRET_PROVIDER_CREDENTIAL")])
    state, session, run = context()
    await finish_place_run(h, session, run, state, request={"role": "origin", "kind": "city", "query": "Buenos Aires", "stay_id": None},
        operations=[Operation(type="set_budget", value={"amount_minor": 7000000})])
    args, kw = h.store.calls[0]
    assert args[2]["budget"]["amount_minor"] == 7000000
    assert kw["result"]["error"]["code"] == "PLACE_RESOLUTION_UNAVAILABLE"
    assert "SECRET" not in str(h.store.calls)


@pytest.mark.asyncio
async def test_catalog_rejection_discards_only_selected_place_not_prior_facts():
    h = Harness([selected()], catalog_error=RuntimeError("SECRET_STALE_SNAPSHOT"))
    state = sample_trip(); state, session, run = context(state, ask(state))
    await finish_place_run(h, session, run, state, selection=selection(session["pending_question"]),
        operations=[Operation(type="set_budget", value={"amount_minor": 7000000})])
    args, _kw = h.store.calls[0]
    assert args[2]["budget"]["amount_minor"] == 7000000 and "location_refs" not in args[2]
    assert args[2]["stays"][0]["city"] == "Стамбул"
    assert not h.searches and "SECRET" not in str(h.store.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("waiting,control", [(None, "wait"), ("user_pause", "none")])
async def test_user_pause_saves_validated_deferred_action_and_facts_without_any_external_calls(waiting, control):
    h = Harness([])
    state, session, run = context(waiting=waiting)
    await finish_place_run(h, session, run, state, request={"role": "origin", "kind": "city", "query": "Buenos Aires", "stay_id": None},
        operations=[Operation(type="set_budget", value={"amount_minor": 7000000})], control=control)
    args, kw = h.store.calls[0]
    assert args[2]["budget"]["amount_minor"] == 7000000 and args[2]["state_version"] == 1
    assert kw["waiting_reason"] == "user_pause"
    deferred = kw["pending_question"]["context"]["place_deferred_action"]
    assert deferred["request"]["query"] == "Buenos Aires" and deferred["state_version"] == 1
    assert deferred["owner"] == "place_run.deferred.v1" and deferred["scope_state"]["origin"] == "Казань"
    assert not h.place_resolution.calls and not h.geo_catalog.calls and not h.budget_calls and not h.searches


@pytest.mark.asyncio
async def test_foreign_or_scope_changed_selection_is_not_rebased_or_sent_to_sources():
    state = sample_trip(); state, session, run = context(state, ask(state))
    h = Harness([])
    await finish_place_run(h, session, run, state, selection=selection(session["pending_question"]),
        operations=[Operation(type="upsert_stay", target_id="stay-istanbul", value={"city": "Москва"})])
    args, kw = h.store.calls[0]
    assert args[2]["stays"][0]["city"] == "Москва"
    assert not h.place_resolution.calls and not h.geo_catalog.calls
    assert kw["result"]["error"]["code"] == "PLACE_RESOLUTION_UNAVAILABLE"


@pytest.mark.asyncio
async def test_true_independent_conflicts_block_entire_batch_before_sources_or_persistence():
    h = Harness([]); state, session, run = context()
    with pytest.raises(DomainError) as error:
        await finish_place_run(h, session, run, state, request={"role": "origin", "kind": "city", "query": "Buenos Aires", "stay_id": None},
            operations=[Operation(type="set_budget", value={"amount_minor": 7000000}), Operation(type="set_budget", value={"amount_minor": 9000000})])
    assert error.value.code == "conflicting_operations" and not h.store.calls and not h.place_resolution.calls


@pytest.mark.asyncio
async def test_stale_lease_is_not_swallowed_as_a_source_failure():
    state, session, run = context()
    h = Harness([RuntimeError("ignored")]); h.stale = True
    with pytest.raises(Conflict):
        await finish_place_run(h, session, run, state, request={"role": "origin", "kind": "city", "query": "Buenos Aires", "stay_id": None})
    assert not h.place_resolution.calls and not h.store.calls


@pytest.mark.asyncio
async def test_selected_place_then_next_owned_request_keeps_one_revision_and_defers_search():
    state = sample_trip(); state, session, run = context(state, ask(state))
    next_job = {"role": "destination", "kind": "city", "query": "Cordoba", "stay_id": "stay-antalya"}
    h = Harness([selected(), {"action": "ask", "question": "Какую Кордову выбрать?", "pending_context": {"field": "place.resolution", "target_id": "stay-antalya"}}])
    await finish_place_run(h, session, run, state, selection=selection(session["pending_question"]),
        remaining_requests=[next_job])
    args, _kw = h.store.calls[0]
    assert args[1] == 0 and args[2]["state_version"] == 1 and args[3] == "WAITING_USER"
    assert args[2]["stays"][0]["city"] == ref().canonical_name and args[4] == "Какую Кордову выбрать?"
    assert len(h.place_resolution.calls) == 2 and h.place_resolution.calls[-1]["value"] == next_job
    assert h.place_resolution.calls[-1]["state"]["state_version"] == 1 and not h.searches


@pytest.mark.asyncio
async def test_country_then_explicit_same_role_city_uses_new_owned_country_ref_without_repeat_question():
    state, session, run = context()
    h = Harness([])
    def country(value, pending, current):
        country = {"country_iso2": "AR", "name": "Argentina"}
        metadata = {"source_sha256": "a" * 64, "retrieved_at": NOW.isoformat(), "expires_at": (NOW + timedelta(days=1)).isoformat()}
        data = current.model_dump(mode="json")
        return h.place_resolution._ask_city(data, value, h.place_resolution._country_ref(data, value, country, metadata))
    def city(value, pending, current):
        assert value["country_ref"]["country_iso2"] == "AR"
        assert value["country_ref"]["state_version"] == current.state_version == 1
        return {"action": "ask", "question": "Какой Буэнос-Айрес выбрать?", "pending_context": {"field": "place.resolution", "target_id": "stay-istanbul"}}
    h.place_resolution.answers = [country, city]
    await finish_place_run(h, session, run, state,
        request={"role": "destination", "kind": "country", "query": "Аргентина", "stay_id": "stay-istanbul"},
        remaining_requests=[{"role": "destination", "kind": "city", "query": "Буэнос-Айрес", "stay_id": "stay-istanbul"}],
        operations=[Operation(type="set_budget", value={"amount_minor": 7000000})])
    assert len(h.place_resolution.calls) == 2 and h.store.calls[0][0][4] == "Какой Буэнос-Айрес выбрать?"
    assert not h.searches


@pytest.mark.asyncio
async def test_remaining_jobs_are_saved_not_lost_when_current_resolution_needs_a_choice():
    state, session, run = context(); h = Harness([{"action": "ask", "question": "Какой город выбрать?", "pending_context": {"field": "place.resolution"}}])
    queue = [{"role": "destination", "kind": "city", "query": "Cordoba", "stay_id": "stay-antalya"}]
    await finish_place_run(h, session, run, state, request={"role": "origin", "kind": "city", "query": "Buenos Aires", "stay_id": None}, remaining_requests=queue)
    assert h.store.calls[0][1]["pending_question"]["context"]["place_remaining_requests"] == queue


@pytest.mark.asyncio
async def test_unavailable_packet_cannot_echo_raw_exception_text_or_fake_a_ready_plan():
    h = Harness([{"action": "unavailable", "question": "SECRET_SOURCE_TEXT", "error": {"code": "SECRET_ERROR"}}])
    state, session, run = context()
    await finish_place_run(h, session, run, state, request={"role": "origin", "kind": "city", "query": "Buenos Aires", "stay_id": None})
    assert "SECRET" not in str(h.store.calls) and not h.store.calls[0][1]["result"]["plans"]


@pytest.mark.asyncio
async def test_new_explicit_wait_without_domain_edits_is_one_control_revision():
    state, session, run = context(); h = Harness([])
    await finish_place_run(h, session, run, state, control="wait",
        request={"role": "origin", "kind": "city", "query": "Buenos Aires", "stay_id": None})
    args, kw = h.store.calls[0]
    assert args[1] == 0 and args[2]["state_version"] == 1 and kw["changes"][0]["operation"] == "control_pause"
    assert not h.budget_calls and not h.place_resolution.calls
    assert kw["pending_question"]["context"]["place_deferred_action"]["state_version"] == 1


@pytest.mark.asyncio
async def test_already_paused_without_fact_changes_has_no_extra_revision():
    state, session, run = context(waiting="user_pause"); h = Harness([])
    await finish_place_run(h, session, run, state,
        request={"role": "origin", "kind": "city", "query": "Buenos Aires", "stay_id": None})
    args, kw = h.store.calls[0]
    assert args[2] is None and kw["changes"] == [] and kw["pending_question"]["state_version"] == 0


def test_control_rebase_updates_only_owned_epoch_and_candidate_ids_preserving_source_ttl():
    from otravel.place_run import rebase_place_control
    state = sample_trip(); pending = ask(state); old = deepcopy(pending)
    changed = rebase_place_control(pending, state, 1, now=NOW)
    packet = changed["context"]["place_resolution"]
    assert changed["state_version"] == packet["state_version"] == 1
    assert packet["expires_at"] == old["context"]["place_resolution"]["expires_at"]
    assert selection(changed)["candidate_id"] != selection(old)["candidate_id"]
    _read_pending(changed, state.model_copy(update={"state_version": 1}).model_dump(mode="json"), NOW)
    assert pending == old


@pytest.mark.parametrize("fault", ["expired", "foreign_trip", "foreign_version", "removed_target", "bad_candidate_id"])
def test_control_rebase_never_revives_expired_or_foreign_authority(fault):
    from otravel.place_run import rebase_place_control
    state = sample_trip(); pending = ask(state)
    packet = pending["context"]["place_resolution"]
    if fault == "expired": packet["expires_at"] = (NOW - timedelta(seconds=1)).isoformat()
    elif fault == "foreign_trip": packet["trip_id"] = "other-trip"
    elif fault == "foreign_version": packet["state_version"] = 3
    elif fault == "removed_target": packet["stay_id"] = "not-owned-stay"
    else: packet["candidates"][0]["candidate_id"] = "pc-" + "f" * 32
    assert rebase_place_control(pending, state, 1, now=NOW) is None


@pytest.mark.asyncio
async def test_pause_selection_control_resume_rebases_nested_packet_and_selection_together():
    from otravel.place_run import rebase_place_control
    state = sample_trip(); state, session, run = context(state, ask(state))
    h = Harness([])
    await finish_place_run(h, session, run, state, selection=selection(session["pending_question"]), control="wait")
    args, kw = h.store.calls[0]
    saved = TripState.model_validate(args[2]); old = kw["pending_question"]
    updated = rebase_place_control(old, saved, 2, now=NOW)
    deferred = updated["context"]["place_deferred_action"]
    assert deferred["state_version"] == deferred["pending_question"]["state_version"] == 2
    assert deferred["selection"] == selection(deferred["pending_question"])
    assert deferred["selection"]["candidate_id"] != old["context"]["place_deferred_action"]["selection"]["candidate_id"]
    assert not h.place_resolution.calls


@pytest.mark.asyncio
async def test_maintenance_budget_and_child_keep_owned_country_question_without_directory_or_search():
    state, session, run = context(); h = Harness([])
    req = {"role": "destination", "stay_id": "stay-istanbul", "query": "Argentina"}
    country = {"country_iso2": "AR", "name": "Argentina"}
    metadata = {"source_sha256": "a" * 64, "retrieved_at": NOW.isoformat(), "expires_at": (NOW + timedelta(days=1)).isoformat()}
    data = state.model_dump(mode="json")
    country_packet = h.place_resolution._ask_city(data, req, h.place_resolution._country_ref(data, req, country, metadata))
    session["pending_question"] = {"question_id": "old", "text": country_packet["question"], "state_version": 0,
        "field": "place.city_query", "target_id": "stay-istanbul", "context": country_packet["pending_context"]}
    await finish_place_run(h, session, run, state, operations=[
        Operation(type="set_budget", value={"amount_minor": 7000000}),
        Operation(type="update_traveler", target_id="child-1", value={"age": 4})])
    args, kw = h.store.calls[0]
    pending = kw["pending_question"]; source = pending["context"]["place_resolution"]["country_ref"]
    assert args[2]["budget"]["amount_minor"] == 7000000 and next(p for p in args[2]["travelers"] if p["id"] == "child-1")["age"] == 4
    assert args[2]["state_version"] == pending["state_version"] == source["state_version"] == 1
    assert source["country_iso2"] == "AR" and "Argentina" in args[4]
    assert source["expires_at"] == metadata["expires_at"]
    assert not h.place_resolution.calls and not h.geo_catalog.calls and not h.searches


@pytest.mark.asyncio
async def test_maintenance_deleted_target_reasks_without_retargeting_other_stop():
    state = sample_trip(); state, session, run = context(state, ask(state)); h = Harness([])
    await finish_place_run(h, session, run, state,
        operations=[Operation(type="remove_stay", target_id="stay-istanbul")])
    args, kw = h.store.calls[0]
    assert [s["id"] for s in args[2]["stays"]] == ["stay-antalya"]
    assert kw["pending_question"]["target_id"] is None
    assert "place_resolution" not in kw["pending_question"]["context"]
    assert kw["result"]["error"]["code"] == "PLACE_PENDING_STALE" and not h.place_resolution.calls


@pytest.mark.asyncio
async def test_false_informational_mode_cannot_accidentally_mutate_trip():
    h = Harness([]); state, session, run = context()
    with pytest.raises(DomainError) as error:
        await finish_place_run(h, session, run, state, informational=True,
            request={"role": "origin", "kind": "city", "query": "Buenos Aires", "stay_id": None})
    assert error.value.code == "place_information_mode_unavailable"
    assert not h.store.calls and not h.place_resolution.calls


@pytest.mark.asyncio
async def test_selected_snapshot_must_match_the_exact_shown_physical_candidate():
    wrong = ref().model_copy(update={"country_iso2": "ES", "timezone": "Europe/Madrid"})
    h = Harness([selected(wrong)])
    state = sample_trip(); state, session, run = context(state, ask(state))
    await finish_place_run(h, session, run, state, selection=selection(session["pending_question"]))
    args, kw = h.store.calls[0]
    assert args[2] is None and not h.searches
    assert kw["result"]["error"]["code"] == "PLACE_RESOLUTION_UNAVAILABLE"


@pytest.mark.asyncio
async def test_precomputed_owned_server_question_does_not_repeat_resolution_or_http():
    state = sample_trip(); state, session, run = context(state)
    h = Harness([])
    pending = ask(state)
    packet = {"action": "ask", "question": "Какой конкретный Буэнос-Айрес выбрать?",
        "pending_context": pending["context"], "operations": [], "resolved_places": {}}
    await finish_place_run(h, session, run, state, precomputed_packet=packet)
    args, kw = h.store.calls[0]
    assert args[2] is None and args[4] == packet["question"]
    assert kw["pending_question"]["context"]["place_resolution"] == pending["context"]["place_resolution"]
    assert not h.place_resolution.calls and not h.budget_calls and not h.searches


@pytest.mark.asyncio
async def test_precomputed_foreign_packet_is_not_rebound_to_another_session():
    state, session, run = context(); h = Harness([])
    pending = ask(state)
    pending["context"]["place_resolution"]["trip_id"] = "foreign-trip"
    await finish_place_run(h, session, run, state, precomputed_packet={"action": "ask", "question": "Secretly wrong?",
        "pending_context": pending["context"]})
    args, kw = h.store.calls[0]
    assert args[2] is None and args[4] != "Secretly wrong?"
    assert kw["result"]["error"]["code"] == "PLACE_RESOLUTION_UNAVAILABLE"
    assert not h.place_resolution.calls


@pytest.mark.asyncio
async def test_precomputed_source_packet_cannot_inject_a_selected_setter_or_independent_changes():
    state, session, run = context(); h = Harness([])
    await finish_place_run(h, session, run, state, precomputed_packet=selected())
    args, kw = h.store.calls[0]
    assert args[2] is None and not h.geo_catalog.calls
    assert kw["result"]["error"]["code"] == "PLACE_RESOLUTION_UNAVAILABLE"
    with pytest.raises(ValueError):
        await finish_place_run(h, session, run, state, precomputed_packet={"action": "unavailable"},
            operations=[Operation(type="set_budget", value={"amount_minor": 7000000})])
    assert len(h.store.calls) == 1


@pytest.mark.asyncio
async def test_source_callback_needs_actual_positive_deadline_permission():
    state, session, run = context(); h = Harness([{"action": "ask", "question": "This must not be reached"}])
    async def denied(*args):
        return False
    h._before_place_call = denied
    await finish_place_run(h, session, run, state,
        request={"role": "origin", "kind": "city", "query": "Buenos Aires", "stay_id": None})
    assert h.store.calls[0][1]["result"]["error"]["code"] == "PLACE_RESOLUTION_UNAVAILABLE"
    assert h.place_resolution.answers == [{"action": "ask", "question": "This must not be reached"}]
