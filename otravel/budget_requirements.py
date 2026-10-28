"""Current financial ambiguity and explicitly unpriced whole-trip costs.

No price estimate or percentage interpretation is invented. Unpriced extras
remain canonical hard conditions, so the existing plan/quote validator blocks
an unsupported total rather than treating a supplier subtotal as all expenses.
"""
from __future__ import annotations

import re
from copy import deepcopy

from .operation_aliases import _without_quoted_spans
from .route_policy_intent import current_clauses

RESERVE_ID = 'HARNESS-004-FINAL40-BUDGET-RESERVE-001'
SCOPE_ID = 'HARNESS-004-FINAL40-BUDGET-COMPONENT-SCOPE-001'
COST_KEYS = {'food': 'unpriced_food_costs', 'baggage': 'unpriced_baggage_costs'}
_UNCERTAIN = re.compile(r'\b(?:если|бы|допустим|например|может|наверное|ранее|раньше|вчера|цитат\w*)\b')
_RESERVE = re.compile(r'\b(?:резерв\w*|запас\w*|непредвиденн\w*)\b')
_PERCENT = re.compile(r'\d\s*%|\bпроцент\w*\b')
_MONEY_GOAL = re.compile(r'\b(?:бюджет\w*|лимит\w*|потолок\w*|улож\w*|на\s+всех|на\s+всю\s+поездку)\b')
_INCLUDE = re.compile(r'\b(?:включая|включи(?:те)?|включить|учти(?:те)?|учесть|с\s+учетом|входит|входят)\b')
_COST_WORDS = {'food': r'(?:ед[ауыой]*|питани\w*)', 'baggage': r'багаж\w*'}


def _get(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _current(text):
    if not isinstance(text, str) or not 0 < len(text) <= 20000 or text.startswith('\x1eOTRAVEL_TYPED_V1:'):
        return ''
    normalized = text.casefold().replace('ё', 'е')
    clean = _without_quoted_spans(re.sub(r'`[^`]*`', ' ', normalized))
    if clean is None or '`' in clean:
        return ''
    return ' '.join(part for part in current_clauses(clean) if not _UNCERTAIN.search(part))


def _excluded(text, words):
    return bool(re.search(
        rf'\b(?:без\s+(?:учета\s+)?{words}|не\s+учитыва\w*\s+(?:стоимость\s+)?{words}|'
        rf'{words}\s+(?:пока\s+)?не\s+учитыва\w*|{words}\s+(?:счита\w*\s+|оплачива\w*\s+)?отдельно(?!\s+не\b))\b', text))


def guard_budget_requirements(state, operations, text):
    model_type = next((type(op) for op in operations if hasattr(op, 'model_dump')), None)
    copied = [op.model_dump(mode='json') if hasattr(op, 'model_dump') else deepcopy(op) for op in operations]
    notes = []
    current = _current(text)
    # Absence of a current assertion is never authority to waive an existing
    # hard cost. Keep the protection below active for hypotheses and quotes.
    budget_ops = [op for op in copied if op.get('type') == 'set_budget']
    if budget_ops and _MONEY_GOAL.search(current) and _RESERVE.search(current) and _PERCENT.search(current):
        copied = [op for op in copied if op.get('type') != 'set_budget']
        notes.append({'finding_id': RESERVE_ID, 'field': 'budget.amount_minor',
            'status': 'clarification_required', 'code': 'budget_reserve_requires_spendable_limit',
            'operation_suppressed': True, 'reason': 'percentage_reserve_scope_ambiguous',
            'question': 'Уточните максимальную сумму, которую можно потратить на саму поездку. '
                'Процент резерва входит в названную сумму или добавляется сверху? '
                'До уточнения сохранённый бюджет не меняю.'})
    existing = {c.key for c in _get(state, 'constraints', []) if hasattr(c, 'key') and c.key in COST_KEYS.values()}
    existing.update(c.get('key') for c in _get(state, 'constraints', [])
                    if isinstance(c, dict) and c.get('key') in COST_KEYS.values())
    for category, key in COST_KEYS.items():
        waived = _excluded(current, _COST_WORDS[category])
        attempted = [op for op in copied if op.get('type') in {'set_constraint', 'clear_constraint'}
                     and isinstance(op.get('value'), dict) and op['value'].get('key') == key]
        # Internal uncertainty is not waived by an unrelated model rewrite.
        if key in existing and not waived:
            removed = [op for op in attempted if op['type'] == 'clear_constraint'
                       or op['value'].get('value') is not True or op['value'].get('hard', True) is not True]
            if removed:
                copied = [op for op in copied if op not in removed]
                notes.append({'finding_id': SCOPE_ID, 'code': 'unpriced_cost_clear_not_authorized',
                              'status': 'suppressed', 'key': key})
        if waived and key in existing:
            copied = [op for op in copied if op not in attempted]
            copied.append({'type': 'clear_constraint', 'target_id': None, 'value': {'key': key}})
            notes.append({'finding_id': SCOPE_ID, 'code': 'unpriced_cost_excluded_explicitly', 'key': key})
    if (budget_ops or _get(state, 'budget') is not None) and _INCLUDE.search(current) and _MONEY_GOAL.search(current):
        added = []
        for category, key in COST_KEYS.items():
            if not re.search(rf'\b{_COST_WORDS[category]}\b', current) or _excluded(current, _COST_WORDS[category]):
                continue
            copied = [op for op in copied if not (op.get('type') in {'set_constraint', 'clear_constraint'}
                and isinstance(op.get('value'), dict) and op['value'].get('key') == key)]
            copied.append({'type': 'set_constraint', 'target_id': None,
                           'value': {'key': key, 'value': True, 'hard': True}})
            added.append('питание' if category == 'food' else 'багажные расходы')
        if added:
            notes.append({'finding_id': SCOPE_ID, 'field': 'budget.amount_minor',
                'status': 'clarification_required', 'code': 'unpriced_cost_scope_retained',
                'question': 'В общий лимит включены ' + ' и '.join(added) + '. '
                    'Их полная стоимость источниками не подтверждена, поэтому итог всей поездки пока не рассчитан. '
                    'Уточните отдельный лимит на билеты, проживание и трансферы либо явно исключите эти расходы из расчёта.'})
    if model_type is not None:
        copied = [model_type.model_validate(op) for op in copied]
    return copied, notes


def budget_scope_unresolved(session):
    pending = session.get('pending_question') or {}
    return (session.get('waiting_reason') == 'clarification'
            and (pending.get('context') or {}).get('budget_scope_ambiguous') is True)
