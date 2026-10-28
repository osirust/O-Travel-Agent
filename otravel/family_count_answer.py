"""Closed family-count answers only; the caller owns the current pending slot."""
import re

_COUNTS = {"два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5,
           "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10}
_NUMBER = r"(?:[2-9]|10|" + "|".join(_COUNTS) + r")"
_PLAIN = re.compile(rf"(?P<count>{_NUMBER})(?:\s+сем(?:ья|ьи|ей))?[.!]?")
_WE = re.compile(rf"(?:у\s+)?нас\s+(?P<count>{_NUMBER})\s+сем(?:ья|ьи|ей)[.!]?")


def closed_family_count_answer(text: str) -> int | None:
    """Return2..10 for one count, never consume membership or independent edits.

    No state, groups, members or intent is inferred here. In particular a full
    roster, budget, preference, question, quotation or hypothesis falls through
    to the caller's normal extraction/readonly handling.
    """
    if not isinstance(text, str) or not 0 < len(text) <= 80:
        return None
    key = " ".join(text.casefold().replace("ё", "е").split())
    match = _PLAIN.fullmatch(key) or _WE.fullmatch(key)
    if match is None:
        return None
    value = match["count"]
    return int(value) if value.isascii() and value.isdigit() else _COUNTS[value]
