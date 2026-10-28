"""Connected PG replay of cached actual CF answers plus stated continuations.

This is harness qualification with injected adapter responses, not remote NLU.
"""
from copy import deepcopy

import pytest
from test_api import StubLLM
from test_api import api_factory as geo_api_factory
from test_geo_cf_r2_regressions import BELGRADE, observed
from test_harness_geo_dialogues_20261027 import assert_turn

api_factory = geo_api_factory


def answer(case, step):
    return observed(case, step)['raw_validated'][0]


def assert_no_supplier_effects(events):
    assert not any(event['event_type'] in {'TOOL_CALL_STARTED', 'QUOTE_CREATED', 'DEMO_ORDER_CREATED'} for event in events)


@pytest.mark.asyncio
async def test_twelve_turns_cached_cf_belgrade_conflict_refusal_child_budget_then_accept(api_factory):
    # The captured T6 answer is preserved in the fixture, but the exact owned
    # assertion now resolves before generation. Keep every business invariant.
    model = StubLLM([answer('PF-GEO-01', '01'), answer('PF-GEO-01', '07'),
        {'operations': [{'type': 'set_budget', 'value': {'amount_minor': 7_000_000, 'currency': 'RUB'}}]}])
    from test_api import _session
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, {'search_policy': {'mode': 'DEMO'}})
        store = client._transport.app.state.store
        _, first, _ = await assert_turn(client, sid, token, observed('PF-GEO-01', '01')['text'])
        assert first['trip']['origin'] == 'Москва' and first['trip']['budget']['amount_minor'] == 8_000_000
        assert first['pending_question']['context']['requested_destination']['country_code'] == 'RS'
        _, second, _ = await assert_turn(client, sid, token, 'Стамбул')
        assert second['trip'] == first['trip'] and second['pending_question']['context'].get('confirmation_required') is True, second['pending_question']
        _, third, _ = await assert_turn(client, sid, token, 'нет')
        assert third['trip'] == first['trip'] and 'proposed_destination' not in third['pending_question']['context']
        _, fourth, _ = await assert_turn(client, sid, token, 'да')
        assert fourth['trip'] == first['trip']
        _, fifth, notice = await assert_turn(client, sid, token, observed('PF-GEO-01', '05')['text'])
        assert fifth['pending_question']['context']['requested_destination'] == BELGRADE
        assert 'Понял как «Белград», Сербия.' in notice
        _, sixth, _ = await assert_turn(client, sid, token, observed('PF-GEO-01', '06')['text'])
        assert sixth['trip'] == first['trip']
        assert sixth['pending_question']['context']['requested_destination'] == BELGRADE
        assert sixth['pending_question']['context']['proposed_destination']['name'] == 'Стамбул'
        _, seventh, reply = await assert_turn(client, sid, token, observed('PF-GEO-01', '07')['text'])
        assert seventh['trip']['travelers'][0] == first['trip']['travelers'][0]
        assert [p['age'] for p in seventh['trip']['travelers'] if p['type'] == 'child'] == [7]
        assert seventh['trip']['budget'] == first['trip']['budget'] and seventh['trip']['stays'] == []
        assert seventh['pending_question']['context']['requested_destination'] == BELGRADE
        assert 'Белград' in reply and 'поиск' in reply and 'proposed_destination' not in seventh['pending_question']['context']
        _, eighth, _ = await assert_turn(client, sid, token, 'Установи общий бюджет 70000 рублей.')
        assert eighth['trip']['budget']['amount_minor'] == 7_000_000
        assert eighth['trip']['travelers'] == seventh['trip']['travelers']
        assert eighth['pending_question']['context']['requested_destination'] == BELGRADE
        _, ninth, _ = await assert_turn(client, sid, token, 'да')
        assert ninth['trip'] == eighth['trip'] and ninth['pending_question']['context']['requested_destination'] == BELGRADE
        _, tenth, _ = await assert_turn(client, sid, token, 'Стамбул')
        assert tenth['trip'] == eighth['trip'] and tenth['pending_question']['context'].get('confirmation_required') is True, tenth['pending_question']
        _, eleventh, _ = await assert_turn(client, sid, token, 'да')
        assert eleventh['trip']['stays'][0]['city'] == 'Стамбул'
        assert eleventh['trip']['budget'] == eighth['trip']['budget'] and eleventh['trip']['travelers'] == seventh['trip']['travelers']
        assert eleventh['pending_question']['context']['requested_destination']['country_code'] == 'TR'
        assert 'proposed_destination' not in eleventh['pending_question']['context']
        _, twelfth, geography = await assert_turn(client, sid, token, 'Стамбул в Турции? Это вопрос, ничего не меняй.')
        assert twelfth['trip'] == eleventh['trip'] and 'Турц' in geography
        assert len(model.calls) == 3 and not model.answers
        assert_no_supplier_effects(await store.list_events(sid))
        assert await store.list_orders(sid) == []


@pytest.mark.asyncio
async def test_eight_turns_cached_cf_late_origin_typo_reject_then_accept_preserves_destination(api_factory):
    model = StubLLM([answer('PF-GEO-02', '01'), answer('PF-GEO-02', '04'), answer('PF-GEO-02', '04')])
    from test_api import _session
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, {'search_policy': {'mode': 'DEMO'}})
        store = client._transport.app.state.store
        _, first, _ = await assert_turn(client, sid, token, observed('PF-GEO-02', '01')['text'])
        assert first['trip']['origin'] is None and first['trip']['stays'][0]['city'] == 'Анталья'
        _, second, _ = await assert_turn(client, sid, token, 'да')
        assert second['trip']['origin'] == 'Москва'
        _, third, _ = await assert_turn(client, sid, token, 'Анталья в Турции? Это вопрос, ничего не меняй.')
        assert third['trip'] == second['trip']
        _, fourth, question = await assert_turn(client, sid, token, observed('PF-GEO-02', '04')['text'])
        assert fourth['trip'] == second['trip']
        assert fourth['pending_question']['field'] == 'origin.city' and 'Санкт-Петербург' in question
        assert fourth['pending_question']['context']['confirmation_required'] is True
        _, fifth, _ = await assert_turn(client, sid, token, 'нет')
        assert fifth['trip'] == second['trip'] and 'proposed_destination' not in fifth['pending_question']['context']
        _, sixth, _ = await assert_turn(client, sid, token, observed('PF-GEO-02', '04')['text'])
        assert sixth['trip'] == second['trip'] and sixth['pending_question']['field'] == 'origin.city'
        _, seventh, _ = await assert_turn(client, sid, token, 'да')
        expected = deepcopy(second['trip'])
        assert seventh['trip']['origin'] == 'Санкт-Петербург'
        for key in ('stays', 'travelers', 'budget', 'groups', 'constraints', 'preferences'):
            assert seventh['trip'][key] == expected[key]
        assert 'proposed_destination' not in seventh['pending_question']['context']
        _, eighth, text = await assert_turn(client, sid, token, 'Анталья в Турции? Ничего не меняй.')
        assert eighth['trip'] == seventh['trip'] and 'Турц' in text
        assert len(model.calls) == 3 and not model.answers
        assert_no_supplier_effects(await store.list_events(sid))
        assert await store.list_orders(sid) == []


@pytest.mark.asyncio
async def test_six_turns_explicit_new_country_is_not_rolled_back_by_rejecting_conflicting_city(api_factory):
    model = StubLLM([answer('PF-GEO-01', '01'), answer('PF-GEO-01', '07'),
        {'operations': [{'type': 'set_budget', 'value': {'amount_minor': 6_000_000, 'currency': 'RUB'}}]}])
    from test_api import _session
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, {'search_policy': {'mode': 'DEMO'}})
        store = client._transport.app.state.store
        _, first, _ = await assert_turn(client, sid, token, observed('PF-GEO-01', '01')['text'])
        _, second, _ = await assert_turn(client, sid, token, 'Белгород.')
        assert second['pending_question']['context']['requested_destination'] == BELGRADE
        _, third, _ = await assert_turn(client, sid, token, 'Стамбул в России')
        assert third['trip'] == first['trip']
        requested = third['pending_question']['context']['requested_destination']
        assert requested['kind'] == 'country' and requested['country_code'] == 'RU'
        _, fourth, _ = await assert_turn(client, sid, token, observed('PF-GEO-01', '07')['text'])
        assert fourth['pending_question']['context']['requested_destination'] == requested
        assert [p['age'] for p in fourth['trip']['travelers'] if p['type'] == 'child'] == [7]
        assert 'proposed_destination' not in fourth['pending_question']['context']
        _, fifth, _ = await assert_turn(client, sid, token, 'Общий бюджет установи 60000 рублей.')
        assert fifth['trip']['budget']['amount_minor'] == 6_000_000
        assert fifth['pending_question']['context']['requested_destination'] == requested
        _, sixth, _ = await assert_turn(client, sid, token, 'Казань')
        assert sixth['trip']['stays'][0]['city'] == 'Казань' and sixth['trip']['origin'] == 'Москва'
        assert sixth['trip']['travelers'] == fourth['trip']['travelers']
        assert sixth['trip']['budget'] == fifth['trip']['budget']
        assert sixth['pending_question']['context']['requested_destination'] == {'kind': 'city', 'name': 'Казань', 'country_code': 'RU'}
        assert len(model.calls) == 3 and not model.answers
        assert_no_supplier_effects(await store.list_events(sid))
        assert await store.list_orders(sid) == []
