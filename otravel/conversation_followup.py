"""Narrow social continuation cues, without edits or invented travel facts."""
import re

_ASSENT = {'да', 'давай', 'давайте', 'давай обсудим', 'давайте обсудим',
           'давай попробуем', 'да помоги', 'давай начнем', 'давай начнём'}
_HELP = {'помоги', 'помоги с поездкой', 'ты поможешь или нет', 'поможешь или нет',
         'ты поможешь', 'ты можешь помочь', 'можешь помочь', 'поможешь',
         'сам разбери', 'разберись сам'}
_ACK = _ASSENT | {'сохраняем','сохряняем','сохраним','сохрани','оставляем','оставим'}


def followup_kind(text):
    if not isinstance(text, str) or not 0 < len(text) <= 160:
        return None
    value = ' '.join(text.casefold().split())
    value = re.sub(r'[.!?]+$', '', value).strip()
    if value in _HELP:
        return 'help'
    if value in _ASSENT:
        return 'assent'
    return None


def last_owned_country_topic(prior_payload):
    """Accept only the server's canonical country discussion packet."""
    if not isinstance(prior_payload, dict):
        return None
    from .geography import parse_destination

    conversation = prior_payload.get('conversation')
    subject = conversation.get('subject') if isinstance(conversation, dict) else None
    if not isinstance(subject, dict) or subject.get('kind') != 'country':
        return None
    known = parse_destination(subject.get('name', ''))['requested_destination']
    if known.get('kind') != 'country' or known.get('country_code') != subject.get('country_code'):
        return None
    return known


def nonanswer_pending_question(pending, text, state):
    """An assent is not a missing date/count/city; keep the exact owned slot."""
    if (not isinstance(pending,dict) or pending.get('reason')!='clarification'
            or not isinstance(text,str) or not 0<len(text)<=160):
        return None
    key=re.sub(r'[.!?]+$','',' '.join(text.casefold().split())).strip()
    if key not in _ACK:
        return None
    context=pending.get('context') or {}
    if (context.get('confirmation_required') is True
            or any(value is not None and (name.endswith('_proposal') or name in {
                'budget_scope_ambiguous','room_reallocation_required','room_target_selection_required',
                'target_selection_required'}) for name,value in context.items())):
        return None
    field=pending.get('field')
    prefixes={'stay.check_in':'Нужна конкретная дата. ', 'stay.check_out':'Нужна конкретная дата. ',
        'leg.departure_date':'Нужна конкретная дата. ', 'stay.rooms':'Нужно указать число номеров. ',
        'party.adults':'Нужно указать число взрослых. ', 'traveler.age':'Нужно указать возраст ребёнка. ',
        'origin.city':'Нужно назвать город. ', 'destination.city':'Нужно назвать город. ',
        'search.guest_nationality':'Нужно назвать страну гражданства. ',
        'budget.amount_minor':'Нужны сумма и валюта бюджета. '}
    if field=='party.children':
        return 'Сколько именно детей поедет с вами?'
    if field not in prefixes:
        return None
    from .clarifications import human_question
    return prefixes[field]+human_question(pending,state,context)
