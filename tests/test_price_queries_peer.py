"""Independent pure review of price routing; no DB, model or provider calls."""
from copy import deepcopy
from datetime import UTC, datetime

import pytest

from otravel.harness import Harness
from otravel.price_queries import saved_price_answer, whole_trip_price_question


class PriceHarnessProbe:
    def __init__(self, plans=None):
        self.plans = plans or []
        self.fences = 0

    async def _latest_current_plans(self, *_args):
        return deepcopy(self.plans)

    async def _ensure_current_run(self, *_args):
        self.fences += 1


def session(field=None, context=None):
    pending = None if field is None else {
        "field": field, "target_id": "stay" if field.startswith("stay.") else None,
        "text": "Подтвердите конкретный сохранённый выбор.", "context": context or {},
        "state_version": 3, "reason": "clarification"}
    return {"state": {"trip_id": "trip", "state_version": 3, "origin": "Москва",
        "travelers": [{"id": "adult", "type": "adult"}],
        "stays": [{"id": "stay", "city": "Стамбул", "check_in": "2026-12-28", "check_out": "2027-01-05"}],
        "budget": {"amount_minor": 10000000, "currency": "RUB", "scope": "whole_trip"}},
        "state_version": 3, "pending_question": pending,
        "waiting_reason": "clarification" if pending else None}


@pytest.mark.asyncio
@pytest.mark.parametrize("field,context", [
    ("budget.amount_minor", {"budget_scope_ambiguous": True}),
    ("stay.check_out", {"date_proposal": {"field": "stay.check_out"}}),
    ("stay.check_out", {"duration_proposal": {"days": 8}}),
    ("stay.rooms", {"room_reallocation_required": True}),
    ("destination.city", {"confirmation_required": True}),
    ("party.removal", {"force_clarification": True}),
])
async def test_whole_price_does_not_confirm_or_replace_a_pending_decision(field, context):
    own = session(field, context)
    before = deepcopy(own)
    probe = PriceHarnessProbe()
    result, meta = await Harness._interpret(probe, own, {"input_text": "сколько будет стоить"})
    assert result.response_source == "canonical_saved_answer"
    assert result.control.action == "none" and result.operations == []
    assert own["pending_question"]["text"] in result.reply
    assert own == before and meta is None and probe.fences == 1


@pytest.mark.asyncio
async def test_optional_budget_without_ambiguity_does_not_block_price_search():
    own = session("budget.amount_minor", {"children_known": True})
    own["state"]["budget"] = None
    before = deepcopy(own)
    result, meta = await Harness._interpret(PriceHarnessProbe(), own, {"input_text": "сколько стоит?"})
    assert result.response_source == "canonical_price_search" and result.control.action == "continue"
    assert own == before and result.operations == [] and meta is None


@pytest.mark.asyncio
async def test_unknown_children_are_still_required_when_user_asks_price():
    own = session("party.children", {"children_known": False})
    result, _meta = await Harness._interpret(PriceHarnessProbe(), own, {"input_text": "сколько стоит?"})
    assert result.response_source == "canonical_saved_answer" and result.control.action == "none"
    assert "дет" in result.reply.casefold()


@pytest.mark.asyncio
async def test_legacy_unbound_question_does_not_crash_price_request():
    own = session("party.children")
    own["pending_question"]["field"] = None
    result, meta = await Harness._interpret(PriceHarnessProbe(), own, {"input_text": "сколько стоит"})
    assert result.response_source == "canonical_price_search" and result.operations == [] and meta is None


@pytest.mark.parametrize("text", [
    "сколько стоит? Добавь ребёнка", "Сколько стоит, если перенести поездку?",
    "Сколько стоит отель?", "А сколько будет стоить бизнес-класс?",
    "Не ищи. Сколько стоит?", "Сколько стоит — пока не отвечай",
    'Пользователь сказал: «сколько стоит»', "Сколько стоит в Белграде?",
])
def test_non_whole_price_requests_keep_normal_interpretation(text):
    assert whole_trip_price_question(text) is False


def plan(source="DEMO"):
    return {"plan_id": "shown", "trip_id": "trip", "state_version": 3,
        "total_minor": 2808681, "currency": "RUB", "source_type": source,
        "offers": [{"source_type": source, "expires_at": "2026-10-27T12:00:00Z"}]}


def test_external_offer_expiry_at_reference_clock_is_expired():
    assert saved_price_answer(session()["state"], [plan("SANDBOX")],
        now=datetime(2026, 10, 27, 12, tzinfo=UTC)) is None


def test_one_foreign_plan_does_not_get_renumbered_out_of_displayed_batch():
    plans = [plan(), plan()]
    plans[0]["trip_id"] = "foreign"
    plans[1]["plan_id"] = "second"
    assert saved_price_answer(session()["state"], plans) is None


def test_saved_reference_price_warns_about_fx_and_never_mutates_inputs():
    own, plans = session(), [plan()]
    plans[0]["fx_reference_only"] = True
    before = deepcopy((own, plans))
    reply = saved_price_answer(own["state"], plans)
    assert "28 086,81 RUB" in reply and "курс оплаты" in reply
    assert (own, plans) == before
