"""Exact059 age fabrication through full API/Harness/PG; no external calls."""
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
from test_api import StubLLM, _snapshot
from test_duration_grounding_pg import message
from test_return_preservation_pg import return_api as recorded_api_fixture
from test_slot_continuation import _reply

from otravel.providers import ProviderError

return_api = recorded_api_fixture
ROOT = Path(__file__).parents[1]
SOURCE = json.loads((ROOT / 'fixtures/regression/existing-adult-age059-20261028.json').read_text(encoding='utf-8'))


class SingleRecordedLLM(StubLLM):
    def __init__(self, answer):
        super().__init__([answer])
        self.repair_requests_blocked = 0

    async def extract_operations(self, context):
        if self.calls:
            self.repair_requests_blocked += 1
            raise ProviderError('RECORDED_REPAIR_UNAVAILABLE', 'No unrecorded repair response allowed.')
        return await super().extract_operations(context)


@pytest.mark.asyncio
async def test_nonclosed059_tail_variant_keeps_existing_ages_and_uses_saved_dates_roster(return_api):
    model = SingleRecordedLLM(SOURCE['events'][0]['final_contract'])
    client, repository = await return_api(model)
    token = str(uuid4())
    own = await repository.create_session(SOURCE['before_trip'], hashlib.sha256(token.encode()).hexdigest())
    sid = own['session_id']
    before = await _snapshot(client, sid, token)
    # Exact059 is separately covered by the code-owned zero-model reader. This
    # declared nonclosed tail keeps the same before/raw while exercising the
    # model guard; the original corpus fixture is not rewritten.
    variant_input = SOURCE['input'] + ' Ответ нужен сейчас.'
    run, after = await message(client, sid, token, variant_input)
    reply = await _reply(client, sid, token, run)
    result = (run.get('result') or {}).get('result') or {}
    events = [event for event in await repository.list_events(sid) if event.get('run_id') == run['run_id']]
    notes = result.get('operation_normalizations', [])
    diagnostic = {'variant_label': '059T7_nonclosed_response_needed_tail', 'input': variant_input,
        'original_input': SOURCE['input'],
        'run_status': run['status'], 'before_trip': before['trip'], 'after_trip': after['trip'],
        'before_version': before['state_version'], 'after_version': after['state_version'],
        'reply': reply, 'model_reply': result.get('model_reply'),
        'model_reply_suppressed': result.get('model_reply_suppressed'), 'normalizations': notes,
        'recorded_adapter_calls': len(model.calls), 'repair_requests_blocked': model.repair_requests_blocked,
        'tool_calls': sum(event['event_type'] == 'TOOL_CALL_STARTED' for event in events),
        'external_model_calls': 0, 'external_travel_calls': 0}
    path = ROOT / '.local/evaluation/after80-20261028/existing-age059-r17-diagnostic.json'
    path.write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2), encoding='utf-8')
    assert run['status'] == 'completed' and not run.get('error')
    assert after['trip']['travelers'] == before['trip']['travelers']
    assert [person['age'] for person in after['trip']['travelers']] == [None, None]
    assert after['trip'] == before['trip'] and after['state_version'] == before['state_version']
    assert len(model.calls) == 1 and model.repair_requests_blocked == 0
    assert sum(note.get('code') == 'unasked_existing_adult_age_removed' for note in notes) == 2
    assert '18 лет' not in reply and 'возраст 18' not in reply
    assert any(value in reply for value in ('5 ноября', '05.11.2026', '2026-11-05', '5 ноя'))
    assert any(value in reply for value in ('12 ноября', '12.11.2026', '2026-11-12', '12 ноя'))
    assert '2 взрослых' in reply
    assert result['model_reply'] is None and result['model_reply_suppressed']
    assert diagnostic['tool_calls'] == 0
    assert await repository.list_orders(sid) == []


@pytest.mark.asyncio
async def test_current_existing_adult_age_literal_remains_a_valid_update(return_api):
    seed = deepcopy(SOURCE['before_trip'])
    seed['travelers'] = [seed['travelers'][0]]
    seed['travelers'][0]['age'] = 45
    seed['stays'][0]['rooms'][0]['traveler_ids'] = [seed['travelers'][0]['id']]
    raw = {'operations': [{'type': 'update_traveler', 'target_id': seed['travelers'][0]['id'], 'value': {'age': 46}}],
           'speech_act': 'CHANGE', 'control': {'action': 'continue'}, 'reply': 'Возраст взрослого исправлен.'}
    model = SingleRecordedLLM(raw)
    client, repository = await return_api(model)
    token = str(uuid4())
    own = await repository.create_session(seed, hashlib.sha256(token.encode()).hexdigest())
    sid = own['session_id']
    before = await _snapshot(client, sid, token)
    run, after = await message(client, sid, token, 'Исправь мой возраст: мне 46 лет.')
    assert run['status'] == 'completed' and not run.get('error')
    assert after['trip']['travelers'][0]['age'] == 46
    assert after['state_version'] == before['state_version'] + 1
    assert after['trip']['stays'] == before['trip']['stays'] and after['trip']['budget'] == before['trip']['budget']
    assert len(model.calls) == 1 and model.repair_requests_blocked == 0
    assert not any(note.get('code') == 'unasked_existing_adult_age_removed'
        for note in run['result']['result'].get('operation_normalizations', []))
    assert await repository.list_orders(sid) == []
