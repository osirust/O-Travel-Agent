"""Narrow social continuation cues, without edits or invented travel facts."""
import re

_ASSENT = {'да', 'давай', 'давайте', 'давай обсудим', 'давайте обсудим',
           'давай попробуем', 'да помоги', 'давай начнем', 'давай начнём'}
_HELP = {'помоги', 'помоги с поездкой', 'ты поможешь или нет', 'поможешь или нет',
         'ты поможешь', 'ты можешь помочь', 'можешь помочь', 'поможешь',
         'сам разбери', 'разберись сам'}


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
