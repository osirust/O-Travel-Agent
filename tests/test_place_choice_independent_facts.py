"""Cached BULK005 choice/date regression; pure and controlled worker, no I/O."""
import json
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from test_place_run import Harness, context

from otravel.domain import TripState, apply_operations
from otravel.place_resolution import _read_pending, interpret_short_reply
from otravel.place_run import finish_place_run, preserve_place_choice_for_independent_facts

FIXTURE = Path(__file__).parents[1] / "fixtures/regression/place-choice-independent-date-bulk005-20261027.json"


def cached():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return data, TripState.model_validate(data["before_trip"]), deepcopy(data["before_pending"]), datetime.fromisoformat(data["reference_clock"])


def test_exact_cached_checked_date_with_other_model_unknowns_qualifies_for_owned_maintenance():
    data, before, pending, now = cached()
    original = deepcopy((data, before.model_dump(mode="json"), pending))
    assert data["checked_contract"]["value"]["unknowns"] == ["Дата выезда из Дубай"]
    operations = data["checked_contract"]["value"]["operations"]
    assert preserve_place_choice_for_independent_facts(pending, before, operations, now=now)
    assert (data, before.model_dump(mode="json"), pending) == original


@pytest.mark.asyncio
async def test_cached_independent_date_commits_but_city_identity_choice_remains_authoritative():
    data, before, pending, now = cached()
    state, session, run = context(before, pending, waiting="clarification")
    harness = Harness([])
    harness.place_resolution.clock = lambda: now
    original = deepcopy(pending)
    operations = data["checked_contract"]["value"]["operations"]
    await finish_place_run(harness, session, run, state, operations=operations,
        model_meta={"provider": "cached-zai", "model": "cached-actual-response"})
    args, fields = harness.store.calls[0]
    after = TripState.model_validate(args[2])
    saved = fields["pending_question"]
    old_packet, new_packet = original["context"]["place_resolution"], saved["context"]["place_resolution"]
    assert after.stays[0].check_in.isoformat() == "2026-11-18" and after.stays[0].check_out is None
    assert saved["field"] == "place.resolution" and saved["text"] == original["text"]
    assert saved["state_version"] == new_packet["state_version"] == after.state_version == 2
    assert new_packet["expires_at"] == old_packet["expires_at"] and new_packet["resolution_id"] == old_packet["resolution_id"]
    assert [row["source_id"] for row in new_packet["candidates"]] == [292223, 11048853, 8469788]
    assert [row["candidate_id"] for row in new_packet["candidates"]] != [row["candidate_id"] for row in old_packet["candidates"]]
    assert not after.location_refs and after.stays[0].place_id is None
    assert _read_pending(saved, after.model_dump(mode="json"), now) == new_packet
    assert interpret_short_reply(saved, "1", after, now=now)["selection"]["candidate_id"] == new_packet["candidates"][0]["candidate_id"]
    assert fields["result"]["place_resolution"]["action"] == "maintained_pending"
    assert not harness.searches and not harness.place_resolution.calls and not harness.geo_catalog.calls
    assert pending == original


@pytest.mark.parametrize("change", ["outer_epoch", "packet_epoch", "foreign_trip", "expired", "candidate_forged", "target_changed", "unowned"])
def test_dispatch_predicate_does_not_grant_authority_to_invalid_or_expired_pending(change):
    data, state, pending, now = cached()
    packet = pending["context"]["place_resolution"]
    if change == "outer_epoch": pending["state_version"] = 0
    if change == "packet_epoch": packet["state_version"] = 0
    if change == "foreign_trip": packet["trip_id"] = "foreign"
    if change == "expired": packet["expires_at"] = (now - timedelta(seconds=1)).isoformat()
    if change == "candidate_forged": packet["candidates"][0]["source_id"] = 999
    if change == "target_changed": pending["target_id"] = "other"
    if change == "unowned": packet["owner"] = "client"
    assert not preserve_place_choice_for_independent_facts(pending, state,
        data["checked_contract"]["value"]["operations"], now=now)


@pytest.mark.parametrize("operations", [
    [{"type": "upsert_stay", "target_id": "new-stay-1", "value": {"city": "Стамбул"}}],
    [{"type": "remove_stay", "target_id": "new-stay-1", "value": {}}],
    [{"type": "upsert_stay", "target_id": "new-stay-1", "value": {"place_id": "geo:openmeteo:292223"}}],
])
def test_geo_scope_changes_and_unverified_refs_are_not_independent_fact_dispatch(operations):
    _data, state, pending, now = cached()
    assert not preserve_place_choice_for_independent_facts(pending, state, operations, now=now)


def test_model_ordinary_unknowns_cannot_resolve_city_by_legacy_name_or_date():
    data, state, pending, now = cached()
    after, _changes = apply_operations(state, data["checked_contract"]["value"]["operations"])
    assert after.stays[0].city == "Дубай" and after.stays[0].place_id is None
    assert interpret_short_reply(pending, data["input"], state, now=now)["selection"] is None
    assert interpret_short_reply(pending, "Дубай", state, now=now)["selection"] is None


@pytest.mark.asyncio
async def test_canonical_notice_does_not_replace_owned_choice_or_create_revision():
    _data, state, pending, now = cached()
    state, session, run = context(state, pending, waiting="clarification")
    harness = Harness([])
    harness.place_resolution.clock = lambda: now
    notice = "Дата 31.02.2027 не существует в календаре."
    await finish_place_run(harness, session, run, state, independent_notice=notice)
    args, fields = harness.store.calls[0]
    assert args[2] is None and args[4] == notice + " " + pending["text"]
    assert fields["pending_question"]["field"] == "place.resolution"
    assert fields["pending_question"]["context"]["place_resolution"] == pending["context"]["place_resolution"]
    assert fields["result"]["independent_notice"]["source"] == "canonical_slot"
    assert not harness.searches and not harness.place_resolution.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["model_notice", "resolve_notice"])
async def test_independent_notice_accepts_only_the_server_only_maintenance_role(kind):
    _data, state, pending, now = cached()
    state, session, run = context(state, pending, waiting="clarification")
    harness = Harness([])
    harness.place_resolution.clock = lambda: now
    extra = {"model_meta": {"provider": "untrusted-prose"}} if kind == "model_notice" else {
        "request": {"role": "destination", "kind": "city", "query": "Дубай", "stay_id": "new-stay-1"}}
    with pytest.raises(ValueError):
        await finish_place_run(harness, session, run, state, independent_notice="Model prose is not canonical authority.", **extra)
    assert not harness.store.calls and not harness.searches and not harness.place_resolution.calls
