"""Country-topic continuation fences with pure fake history, never models/DB."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from otravel.config import Settings
from otravel.domain import TripState, sample_trip
from otravel.harness import Harness


class History:
    def __init__(self, *, version=0):
        self.version = version
        self.messages = [
            {"id": "assistant", "role": "assistant", "sequence": 1, "run_id": "country"},
            {"id": "input", "role": "user", "sequence": 2, "run_id": "active", "text": "давай"},
        ]
        self.prior = {"session_id": "own", "status": "completed", "result": {"state_version": version,
            "result": {"conversation": {"speech_act": "ASK", "subject": {
                "kind": "country", "name": "Сербия", "country_code": "RS"}}}}}
        self.reads = []

    async def list_messages(self, sid):
        self.reads.append(("messages", sid))
        return deepcopy(self.messages)

    async def get_run(self, rid):
        self.reads.append(("run", rid))
        return {"status": "running"} if rid == "active" else deepcopy(self.prior)

    async def get_session(self, sid):
        self.reads.append(("session", sid))
        return {"state_version": self.version, "waiting_reason": None, "active_run_id": "active"}


def setup(*, state=None, version=0):
    state = TripState().model_dump(mode="json") if state is None else deepcopy(state)
    state["state_version"] = version
    store = History(version=version)
    harness = Harness(Settings("unused", "groq", 1, 8000, 40, 1), store, SimpleNamespace(), SimpleNamespace())
    session = {"session_id": "own", "state_version": version, "state": state,
        "waiting_reason": None, "pending_question": None}
    run = {"run_id": "active", "input_message_id": "input", "input_text": "давай"}
    return harness, store, session, run


@pytest.mark.asyncio
async def test_exact_current_owned_country_offer_can_continue_only_in_fresh_trip_without_model():
    h, store, session, run = setup(version=3)
    original = deepcopy(session)
    extracted, metadata = await h._interpret(session, run)
    assert metadata is None and extracted.operations == []
    assert extracted.destination_request["country_code"] == "RS"
    assert extracted.pending_context["requested_destination"]["country_code"] == "RS"
    assert len(extracted.unknowns) == 1 and "город" in extracted.unknowns[0]
    assert session == original and all(kind in {"messages", "run", "session"} for kind, _ in store.reads)


@pytest.mark.asyncio
@pytest.mark.parametrize("prior_version", [0, 5])
async def test_original_and_same_version_existing_route_counterexamples_cannot_recover_country_intake(prior_version):
    h, store, session, run = setup(state=sample_trip().model_dump(mode="json"), version=5)
    store.prior["result"]["state_version"] = prior_version
    original = deepcopy(session)
    assert await h._recent_country_topic(session, run) is None
    assert session == original and store.reads == []


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("origin", "Москва"), ("budget", {"amount_minor": 100, "currency": "RUB"}),
    ("travelers", [{"id": "p", "type": "adult"}]), ("groups", [{"id": "g", "traveler_ids": []}]),
    ("stays", [{"id": "s", "city": "Анталья"}]), ("legs", [{"id": "l", "mode": "flight"}]),
    ("constraints", [{"key": "breakfast", "hard": True, "value": True}]),
    ("preferences", [{"key": "departure_time", "value": "early_morning"}]),
])
async def test_any_existing_planning_fact_fences_the_country_offer_recovery(field, value):
    h, store, session, run = setup()
    session["state"][field] = deepcopy(value)
    original = deepcopy(session)
    assert await h._recent_country_topic(session, run) is None
    assert session == original and store.reads == []


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["missing", "older", "future", "null", "string", "foreign", "failed", "no_result"])
async def test_topic_version_owner_and_completed_status_require_exact_fresh_proof(variant):
    h, store, session, run = setup(version=3)
    if variant == "missing":
        store.prior["result"].pop("state_version")
    elif variant == "older":
        store.prior["result"]["state_version"] = 2
    elif variant == "future":
        store.prior["result"]["state_version"] = 4
    elif variant == "null":
        store.prior["result"]["state_version"] = None
    elif variant == "string":
        store.prior["result"]["state_version"] = "3"
    elif variant == "foreign":
        store.prior["session_id"] = "foreign"
    elif variant == "failed":
        store.prior["status"] = "failed"
    else:
        store.prior["result"] = None
    original = deepcopy(session)
    assert await h._recent_country_topic(session, run) is None and session == original


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["missing_current_message", "future_assistant", "latest_has_no_run", "latest_has_no_topic"])
async def test_only_the_latest_preceding_assistant_packet_can_supply_context_without_backfill(variant):
    h, store, session, run = setup()
    if variant == "missing_current_message":
        store.messages.pop()
    elif variant == "future_assistant":
        store.messages[0]["sequence"] = 3
    elif variant == "latest_has_no_run":
        store.messages.insert(1, {"id": "newer", "role": "assistant", "sequence": 1.5, "run_id": None})
    else:
        store.prior["result"]["result"] = {"notice": "Unrelated answer"}
    assert await h._recent_country_topic(session, run) is None


@pytest.mark.asyncio
async def test_malformed_country_identity_cannot_reuse_an_owned_packet_as_a_new_destination():
    h, store, session, run = setup()
    store.prior["result"]["result"]["conversation"]["subject"]["country_code"] = "TR"
    assert await h._recent_country_topic(session, run) is None
