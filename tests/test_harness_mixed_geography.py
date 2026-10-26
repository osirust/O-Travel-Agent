"""Cached CF ASK+continue intake must keep literal geography and party facts."""
import pytest
from test_api import StubLLM, _session
from test_api import api_factory as mixed_geo_api_factory
from test_slot_continuation import _message, _reply

api_factory = mixed_geo_api_factory


@pytest.mark.asyncio
@pytest.mark.parametrize('text,subject,reply_terms,expected_kind', [
    ('Один взрослый из Москвы хочет в Georgia. Пока город не выбрал.', 'Georgia', ('Грузи', 'штат'), 'ambiguous'),
    ('Из Москвы в Тбилиси, один взрослый. Если направление не поддерживается, скажи прямо.', 'Тбилиси', ('Тбилиси', 'не покрывают'), 'city'),
])
async def test_mixed_intake_asks_owned_geography_instead_of_repeating_generic_city(api_factory, text, subject, reply_terms, expected_kind):
    raw = {'operations': [
        {'type': 'set_origin', 'value': {'city': 'Москва'}},
        {'type': 'add_traveler', 'value': {'id': 'new-traveler-1', 'type': 'adult', 'age': None}}],
        'speech_act': 'ASK', 'control': {'action': 'continue'}, 'unknowns': ['В какой город вы хотите поехать?'],
        'conflicts': [], 'information_subject': subject, 'reply': 'Состав сохранён.'}
    model = StubLLM([raw])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client)
        run, after = await _message(client, sid, token, text)
        reply = await _reply(client, sid, token, run)
        assert after['trip']['origin'] == 'Москва'
        assert len(after['trip']['travelers']) == 1
        assert all(term.casefold() in reply.casefold() for term in reply_terms), reply
        destination = after['pending_question']['context']['requested_destination']
        assert destination['kind'] == expected_kind
        if expected_kind == 'ambiguous':
            assert destination.get('country_code') is None
            assert after['trip']['stays'] == []
        else:
            # An unsupported search location is still a valid saved wish.
            assert [stay['city'] for stay in after['trip']['stays']] == ['Тбилиси']
            assert destination['country_code'] == 'GE'
        assert len(model.calls) == 1
