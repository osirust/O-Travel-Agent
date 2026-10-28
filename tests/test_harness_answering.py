import pytest
from test_answering import answer
from test_api import StubLLM, _plans, _preview, _session, _snapshot
from test_api import api_factory as qa_api_factory
from test_harness_conversational import initial_search, known_trip
from test_slot_continuation import _message, _reply

api_factory = qa_api_factory


@pytest.mark.asyncio
async def test_compound_information_uses_one_small_role_call_and_preserves_quote(api_factory, monkeypatch):
    model = StubLLM([answer("party,budget")])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, known_trip())
        search = await initial_search(client, sid, token)
        before = await _snapshot(client, sid, token)
        quote = await _preview(client, sid, token, _plans(search)[0], before["state_version"])
        repository = client._transport.app.state.store
        old_quote = await repository.get_quote(quote["quote_id"])

        async def no_search(*args, **kwargs):
            raise AssertionError("Information must not search")

        monkeypatch.setattr(client._transport.app.state.harness, "_search_trip", no_search)
        run, after = await _message(
            client,
            sid,
            token,
            "Ты не ответил о составе: кто едет и какой общий бюджет сейчас? Ничего не меняй.",
        )
        assert len(model.calls) == 1 and model.calls[0]["__response_mode"] == "readonly_answer_v1"
        assert "operation_contract" not in model.calls[0]
        assert run["result"]["result"]["response_source"] == "readonly_answer"
        assert "75000" in (await _reply(client, sid, token, run)).replace(" ", "")
        for key in ("trip", "state_version", "status", "waiting_reason", "pending_question"):
            assert after[key] == before[key]
        assert await repository.get_quote(quote["quote_id"]) == old_quote
        assert await repository.list_orders(sid) == []


@pytest.mark.asyncio
async def test_invalid_readonly_contract_has_no_repair_or_state_change(api_factory):
    invalid = answer()
    invalid["operations"] = [{"type": "set_budget", "value": {"amount_minor": 1}}]
    model = StubLLM([invalid, answer()])
    async with api_factory(model) as (client, _):
        sid, token, before = await _session(client, known_trip())
        from test_api import _headers, _wait

        response = await client.post(
            f"/api/v1/sessions/{sid}/messages",
            headers=_headers(token),
            json={"client_message_id": "invalid-answer-contract", "text": "Ты помнишь наш бюджет и состав?"},
        )
        run = await _wait(client, response.json()["run_id"], token)
        after = await _snapshot(client, sid, token)
        assert run["status"] == "failed" and len(model.calls) == 1
        for key in ("trip", "state_version", "status", "waiting_reason", "pending_question"):
            assert after[key] == before[key]
        assert await client._transport.app.state.store.list_orders(sid) == []


@pytest.mark.asyncio
async def test_persistent_answer_role_allows_only_one_attempt_across_provider_buckets(api_factory):
    import asyncio
    from uuid import uuid4

    async with api_factory() as (client, _):
        sid, _, initial = await _session(client, known_trip())
        repository = client._transport.app.state.store
        raw, _ = await repository.intake_message(
            sid, str(uuid4()), "controlled role reservation", initial["state_version"]
        )
        run_id = raw["id"]
        assert await repository.claim_run(run_id)
        await repository.select_model_role(sid, run_id, initial["state_version"], "readonly_answer_v1")
        results = await asyncio.gather(
            *(
                repository.reserve_model_call(sid, run_id, 1_000_000, name)
                for name in ("test_answer_first", "test_answer_second", "test_answer_third")
            )
        )
        assert results.count(True) == 1
        await repository.select_model_role(sid, run_id, initial["state_version"], "readonly_answer_v1")
        assert not await repository.reserve_model_call(sid, run_id, 1_000_000, "test_answer_after_restart")
        events = [event for event in await repository.list_events(sid) if event["run_id"] == run_id]
        assert sum(event["event_type"] == "MODEL_CALL_STARTED" for event in events) == 1
        assert sum(event["event_type"] == "MODEL_ROLE_SELECTED" for event in events) == 1
        await repository.fail_run(run_id, {"code": "synthetic_reservation_cleanup"}, preserve_session=True)


@pytest.mark.asyncio
async def test_pause_between_role_selection_and_reservation_prevents_model_start(api_factory):
    from uuid import uuid4

    from otravel.store import Conflict

    async with api_factory() as (client, _):
        sid, _, initial = await _session(client, known_trip())
        repository = client._transport.app.state.store
        raw, _ = await repository.intake_message(
            sid, str(uuid4()), "controlled role race", initial["state_version"]
        )
        run_id = raw["id"]
        assert await repository.claim_run(run_id)
        await repository.select_model_role(sid, run_id, initial["state_version"], "readonly_answer_v1")
        await repository.set_control(sid, "pause")
        with pytest.raises(Conflict, match="Изменились версия"):
            await repository.reserve_model_call(sid, run_id, 1_000_000, "test_paused_answer")
        events = [event for event in await repository.list_events(sid) if event["run_id"] == run_id]
        assert not any(event["event_type"] == "MODEL_CALL_STARTED" for event in events)
