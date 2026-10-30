"""Cached raw038 through ASGI + isolated PG;no remote model/travel HTTP."""
import json
from copy import deepcopy
from pathlib import Path

import pytest
from test_api import StubLLM, _session
from test_api import api_factory as alias_api_factory
from test_slot_continuation import _message, _reply

api_factory=alias_api_factory
F=json.loads((Path(__file__).parents[1]/"fixtures/regression/alias-question-stay-removal-20261028.json").read_text())
@pytest.mark.asyncio
@pytest.mark.parametrize("already_equal_budget", [False, True])
async def test_cached038_alias_question_preserves_only_stay_and_canonical_reply(api_factory, already_equal_budget):
    initial=deepcopy(F["before"]);initial.pop("state_version",None)
    initial["search_policy"]={"mode":"DEMO","demo_fallback":True}
    if already_equal_budget:
        initial["budget"]={"scope":"whole_trip","currency":"RUB","amount_minor":6500000}
    model=StubLLM([F["raw_contract"]["value"]])
    async with api_factory(model)as(client,_):
        sid,token,before=await _session(client,initial)
        run,after=await _message(client,sid,token,F["input"])
        assert after["trip"]["stays"]==before["trip"]["stays"]
        assert after["trip"]["origin"]==before["trip"]["origin"]
        assert after["trip"]["travelers"]==before["trip"]["travelers"]
        assert after["trip"]["budget"]["amount_minor"]==6500000
        assert after["state_version"]==before["state_version"]+(0 if already_equal_budget else 1)
        reply=await _reply(client,sid,token,run)
        assert "Питер" in reply and "Санкт-Петербург" in reply
        assert "удалил остановку"not in reply.casefold()
        assert "без проживания"not in reply.casefold()
        assert len(model.calls)==1
        notes=run["result"]["result"]["operation_normalizations"]
        assert any(n.get("finding_id")=="HARNESS-004-ALIAS-QUESTION-STAY-REMOVAL-001"for n in notes)
        assert await client._transport.app.state.store.list_orders(sid)==[]
