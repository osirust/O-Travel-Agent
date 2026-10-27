"""Exact social greetings only; no trip interpretation or state changes."""
import re

_PHRASES = (
    'привет', 'здравствуй', 'здравствуйте', 'доброе утро', 'добрый день', 'добрый вечер',
    'hi', 'hello', 'hey', 'good morning', 'good afternoon', 'good evening',
)
_FULL_GREETING = re.compile(r'(?:' + '|'.join(re.escape(value) for value in _PHRASES) + r')\s*[.!]*')
_REPLY = 'Здравствуйте! Готов помочь с вашей поездкой.'


def canonical_greeting(text: str) -> dict[str, str] | None:
    """Return a fresh server-owned greeting or None for any other utterance.

    Question marks, commands, place names and extra social/domain clauses are
    deliberately excluded. The caller may append its own current pending
    question; this function does not inspect, replace or resume that question.
    """
    if type(text) is not str or not 0 < len(text) <= 160:
        return None
    normalized = ' '.join(text.casefold().split())
    if _FULL_GREETING.fullmatch(normalized) is None:
        return None
    return {'kind': 'greeting', 'text': _REPLY}
