"""Controlled NO_MATCH ownership and explicit source-revision confirmation."""
from copy import deepcopy
from datetime import timedelta

import pytest
from test_place_resolution import NOW, System, request, selection, state
from test_place_run import Harness, context

from otravel.domain import DomainError, Operation, TripState, apply_operations
from otravel.place_refs import CityRefSnapshot
from otravel.place_resolution import _choice_id, interpret_short_reply
from otravel.place_run import finish_place_run


@pytest.mark.asyncio
async def test_no_match_clarification_retains_exact_query_scope_country_and_ttl():
    system = System(rows=[])
    original = state()
    try:
        country = await system.coordinator.resolve(request(kind="country", query="AR"), original, before_call=system.before)
        country_ref = country["pending_context"]["place_resolution"]["country_ref"]
        city_request = request(query="Буенос-Айрес", country_ref=country_ref)
        outcome = await system.coordinator.resolve(city_request, original, before_call=system.before)
        assert outcome["action"] == "no_match"
        before = deepcopy(outcome)
        clarified = system.coordinator.no_match_clarification(city_request, outcome, original)
        assert clarified["operations"] == [] and clarified["resolved_places"] == {}
        assert "Буенос-Айрес" in clarified["question"] and "Argentina" in clarified["question"]
        assert clarified["pending_context"] == outcome["pending_context"] and outcome == before
        assert len(system.wires) == 1 and not system.catalog.admissions
    finally:
        await system.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutate", ["trip", "version", "query", "scope", "expired"])
async def test_no_match_helper_rejects_unowned_or_stale_packets(mutate):
    system, original = System(rows=[]), state()
    try:
        req = request(query="Ошибочный город")
        outcome = await system.coordinator.resolve(req, original, before_call=system.before)
        if mutate == "trip": original["trip_id"] = "foreign"
        elif mutate == "version": original["state_version"] += 1
        elif mutate == "query": req["query"] = "Другой город"
        elif mutate == "scope": req["role"] = "origin"
        else: system.clock = system.clock.replace(hour=13)
        with pytest.raises(ValueError):
            system.coordinator.no_match_clarification(req, outcome, original)
        assert len(system.wires) == 1
    finally:
        await system.close()


def old_and_new():
    common = {"place_id": "geo:openmeteo:3435910", "country_iso2": "AR",
        "geographic_source": {"provider": "openmeteo", "external_id": 3435910},
        "latitude": -34.61315, "longitude": -58.37723, "feature_code": "PPLC"}
    old = CityRefSnapshot.model_validate({**common, "canonical_name": "Buenos Aires old", "ref_version": 1,
        "source_fact_sha256": "a" * 64})
    new = CityRefSnapshot.model_validate({**common, "canonical_name": "Buenos Aires", "ref_version": 2,
        "source_fact_sha256": "b" * 64})
    before = apply_operations(TripState(trip_id="revision-owned-trip"), [
        Operation(type="set_origin", value={"city": old.canonical_name, "place_id": old.place_id}),
        Operation(type="upsert_stay", target_id="stop", value={"city": "Стамбул",
            "check_in": "2026-11-01", "check_out": "2026-11-04"}),
        Operation(type="set_route_policy", value={"kind": "open_jaw", "end_city": old.canonical_name,
            "end_place_id": old.place_id}),
    ], resolved_places={old.place_id: old})[0]
    return before, old, new


def test_domain_revision_default_stays_immutable_but_server_replacement_updates_all_bound_labels():
    before, old, new = old_and_new()
    with pytest.raises(DomainError, match="неявно"):
        apply_operations(before, [], resolved_places={new.place_id: new})
    after, changes = apply_operations(before, [], resolved_places={new.place_id: new},
        replace_place_versions={old.place_id: old.ref_version})
    assert before.location_refs[old.place_id] == old and after.location_refs[new.place_id] == new
    assert after.origin == after.route_policy.end_city == new.canonical_name
    assert after.legs[0].origin == after.legs[-1].destination == new.canonical_name
    assert [leg.id for leg in after.legs] == [leg.id for leg in before.legs] and changes


@pytest.mark.parametrize("replacement", [None, {}, {"geo:openmeteo:3435910": 2}, {"geo:openmeteo:3435910": True}, {"other": 1}])
def test_domain_revision_requires_exact_previous_version(replacement):
    before, _old, new = old_and_new()
    with pytest.raises(DomainError):
        apply_operations(before, [], resolved_places={new.place_id: new}, replace_place_versions=replacement)


async def revision_system():
    from test_place_resolution import BASE
    system = System(rows=[BASE])
    admit = system.catalog.admit_place
    verified = []
    async def admit_revision(*args, **kwargs):
        ref = await admit(*args, **kwargs)
        return ref.model_copy(update={"ref_version": 2})
    async def verify(values, **kwargs):
        verified.append((deepcopy(values), deepcopy(kwargs)))
        return {key: CityRefSnapshot.model_validate(value) for key, value in values.items()}
    system.catalog.admit_place = admit_revision
    system.catalog.verify_places = verify
    original, old, _new = old_and_new()
    query = request(role="origin")
    ask = await system.coordinator.resolve(query, original, before_call=system.before)
    update = await system.coordinator.select(selection(ask), ask["pending_context"], original, before_call=system.before)
    return system, original, old, update, verified


@pytest.mark.asyncio
async def test_reselect_new_source_revision_requires_separate_explicit_confirmation():
    system, original, old, update, verified = await revision_system()
    try:
        assert update["action"] == "ask" and not update["operations"] and not update["resolved_places"]
        pending = update["pending_context"]
        assert pending["place_resolution"]["stage"] == "ref_revision_confirm"
        assert "1" in update["question"] and "2" in update["question"] and "Buenos Aires old" in update["question"]
        assert not verified
        result = await system.coordinator.select("да", pending, original, before_call=system.before)
        assert result["action"] == "selected" and result["replace_place_versions"] == {old.place_id: 1}
        assert len(verified) == 1 and verified[0][1]["require_fresh"] and verified[0][1]["require_current"]
        after, changes = apply_operations(original, result["operations"], resolved_places=result["resolved_places"],
            replace_place_versions=result["replace_place_versions"])
        assert after.origin == after.route_policy.end_city == "Buenos Aires" and changes
    finally:
        await system.close()


@pytest.mark.asyncio
async def test_declining_revision_keeps_original_and_makes_no_new_source_call():
    system, original, _old, update, verified = await revision_system()
    try:
        before = deepcopy(original.model_dump(mode="json"))
        wires = len(system.wires)
        answer = interpret_short_reply(update["pending_context"], "нет", original, now=NOW)
        assert answer["selection"]["decision"] == "reject"
        result = await system.coordinator.select(answer["selection"], update["pending_context"], original, before_call=system.before)
        assert result["action"] == "retained" and not result["operations"] and not result["resolved_places"]
        assert original.model_dump(mode="json") == before and len(system.wires) == wires and not verified
    finally:
        await system.close()


@pytest.mark.asyncio
async def test_worker_commits_confirmed_revision_once_but_rejects_unconfirmed_replacement_map():
    system, original, _old, update, _verified = await revision_system()
    try:
        selected_packet = await system.coordinator.select("да", update["pending_context"], original, before_call=system.before)
        pending = {"field": "place.resolution", "text": update["question"], "context": update["pending_context"]}
        state_, session, run = context(original, pending)
        h = Harness([selected_packet])
        await finish_place_run(h, session, run, state_, selection=selection(update))
        args, _kwargs = h.store.calls[0]
        assert args[2]["state_version"] == 1 and args[2]["location_refs"]["geo:openmeteo:3435910"]["ref_version"] == 2
        assert args[2]["origin"] == args[2]["route_policy"]["end_city"] == "Buenos Aires"
        assert len(h.store.calls) == 1
    finally:
        await system.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["trip", "version", "expired", "scope", "old_hash", "new_hash", "snapshot", "foreign_id"])
async def test_revision_confirmation_rejects_changed_authority_before_catalog_lookup(change):
    system, original, _old, update, verified = await revision_system()
    try:
        pending = deepcopy(update["pending_context"])
        choose = selection(update)
        packet = pending["place_resolution"]
        candidate = packet["candidates"][0]
        if change == "trip": original = original.model_copy(update={"trip_id": "foreign"})
        elif change == "version": original = original.model_copy(update={"state_version": 1})
        elif change == "expired": system.clock += timedelta(minutes=16)
        elif change == "scope":
            original = apply_operations(original, [Operation(type="set_origin", value={"city": "Москва"})])[0]
        elif change == "old_hash": candidate["revision"]["previous_snapshot_sha256"] = "0" * 64
        elif change == "new_hash": candidate["revision"]["new_snapshot_sha256"] = "0" * 64
        elif change == "snapshot": candidate["revision"]["new_snapshot"]["canonical_name"] = "Invented"
        else: choose["candidate_id"] = "pc-" + "f" * 32
        if change in {"old_hash", "new_hash", "snapshot"}:
            candidate["candidate_id"] = _choice_id(packet, candidate)
            choose["candidate_id"] = candidate["candidate_id"]
        result = await system.coordinator.select(choose, pending, original, before_call=system.before)
        assert result["action"] in {"ask", "unavailable"} and not result["operations"] and not result["resolved_places"]
        assert not verified
    finally:
        await system.close()


@pytest.mark.asyncio
async def test_catalog_latest_change_between_question_and_confirmation_blocks_replacement():
    system, original, _old, update, verified = await revision_system()
    try:
        async def stale(*args, **kwargs):
            verified.append("stale")
            raise ValueError("stale_snapshot")
        system.catalog.verify_places = stale
        result = await system.coordinator.select("да", update["pending_context"], original, before_call=system.before)
        assert result["action"] == "unavailable" and not result["operations"] and not result["resolved_places"]
        assert verified == ["stale"] and original.location_refs["geo:openmeteo:3435910"].ref_version == 1
    finally:
        await system.close()


@pytest.mark.asyncio
async def test_precomputed_no_match_rebases_independent_budget_without_losing_country_or_new_question():
    system, original = System(rows=[]), TripState(trip_id="no-match-independent")
    try:
        country = await system.coordinator.resolve(request(kind="country", query="AR"), original, before_call=system.before)
        req = request(query="Буенос-Айрес", country_ref=country["pending_context"]["place_resolution"]["country_ref"])
        outcome = await system.coordinator.resolve(req, original, before_call=system.before)
        clarified = system.coordinator.no_match_clarification(req, outcome, original)
        original_expiry = clarified["pending_context"]["place_resolution"]["expires_at"]
        state_, session, run = context(original)
        h = Harness([])
        await finish_place_run(h, session, run, state_, precomputed_packet=clarified,
            operations=[Operation(type="set_budget", value={"amount_minor": 9000000})])
        args, kw = h.store.calls[0]
        assert args[2]["state_version"] == 1 and args[2]["budget"]["amount_minor"] == 9000000
        assert args[4] == clarified["question"] and "Буенос-Айрес" in args[4]
        packet = kw["pending_question"]["context"]["place_resolution"]
        assert packet["state_version"] == packet["country_ref"]["state_version"] == 1
        assert packet["expires_at"] == original_expiry and not h.place_resolution.calls and not h.searches
    finally:
        await system.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["no_context", "foreign", "expired"])
async def test_marked_no_match_without_current_owned_authority_cannot_grant_independent_edits(change):
    from test_place_run import ask

    from otravel.place_resolution import NO_MATCH_OWNER
    state_,session,run=context()
    pending=ask(state_)
    proof=pending["context"]["place_resolution"]
    proof.update(stage="city_query",candidates=[])
    if change=="foreign": proof["trip_id"]="foreign"
    elif change=="expired": proof["expires_at"]=(NOW-timedelta(seconds=1)).isoformat()
    packet={"action":"no_match","question":"Unproved question","clarification_owner":NO_MATCH_OWNER,
        "pending_context":None if change=="no_context" else pending["context"]}
    h=Harness([])
    with pytest.raises(ValueError):
        await finish_place_run(h,session,run,state_,precomputed_packet=packet,
            operations=[Operation(type="set_budget",value={"amount_minor":9000000})])
    assert not h.store.calls and not h.searches and not h.place_resolution.calls


@pytest.mark.asyncio
async def test_worker_decline_clears_revision_question_and_preserves_ref_without_search():
    system, original, old, update, _verified = await revision_system()
    try:
        pending = {"field": "place.resolution", "text": update["question"], "context": update["pending_context"]}
        choose = interpret_short_reply(pending, "нет", original, now=NOW)["selection"]
        retained = await system.coordinator.select(choose, pending, original, before_call=system.before)
        state_, session, run = context(original, pending)
        h = Harness([retained])
        await finish_place_run(h, session, run, state_, selection=choose)
        args, kw = h.store.calls[0]
        assert args[2] is None and kw["pending_question"] is None
        assert kw["result"]["place_resolution"]["action"] == "revision_declined"
        assert not h.searches and not h.geo_catalog.calls and original.location_refs[old.place_id] == old
    finally:
        await system.close()


@pytest.mark.asyncio
async def test_worker_rejects_replacement_map_without_revision_confirmation_stage():
    from test_place_run import ask
    system, original, _old, update, _verified = await revision_system()
    try:
        selected_packet = await system.coordinator.select("да", update["pending_context"], original, before_call=system.before)
        pending = ask(original, role="origin", stay_id=None)
        state_, session, run = context(original, pending)
        h = Harness([selected_packet])
        choose = {"resolution_id": pending["context"]["place_resolution"]["resolution_id"],
            "candidate_id": pending["context"]["place_resolution"]["candidates"][0]["candidate_id"]}
        await finish_place_run(h, session, run, state_, selection=choose)
        args, kw = h.store.calls[0]
        assert args[2] is None and kw["result"]["error"]["code"] == "PLACE_RESOLUTION_UNAVAILABLE"
        assert not h.searches
    finally:
        await system.close()


@pytest.mark.parametrize("version", [0, 1])
def test_server_approval_cannot_downgrade_or_overwrite_same_version(version):
    before, old, new = old_and_new()
    with pytest.raises((DomainError, ValueError)):
        apply_operations(before, [], resolved_places={new.place_id: new.model_copy(update={"ref_version": version})},
            replace_place_versions={old.place_id: 1})


def test_unused_server_replacement_approval_is_rejected_without_mutation():
    before, old, _new = old_and_new()
    snapshot = before.model_dump(mode="json")
    with pytest.raises(DomainError):
        apply_operations(before, [], replace_place_versions={old.place_id: 1})
    assert before.model_dump(mode="json") == snapshot
