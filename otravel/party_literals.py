"""Shared literal zero-child facts; a missing count is never inferred as zero."""
from __future__ import annotations

import re

from .operation_aliases import _without_quoted_spans
from .route_policy_intent import current_clauses

_STATEMENTS = frozenset({"детей нет", "без детей", "нет детей", "дети не едут",
    "детей не будет", "детей пока не будет", "детей пока нет"})
_BOUND_SHORT_ANSWERS = frozenset({"нет", "не надо", "не меняем", "не будет", "0"})
_UNCERTAIN = re.compile(r"\b(?:может|возможно|наверное|вероятно|if|would|maybe|previously|earlier|yesterday)\b")


def explicit_no_children(text: str, *, answer: bool = False) -> bool:
    """Recognize only current complete facts or a bound child's exact answer.

    ``answer=True`` is for an already-owned party.children slot. It does not
    consume compound edits, questions, quoted examples or a hypothetical count.
    No traveler is created/removed and no session metadata is written here.
    """
    if not isinstance(text, str) or not 0 < len(text) <= 20_000:
        return False
    raw = text.casefold().replace("ё", "е")
    unquoted = _without_quoted_spans(re.sub(r"`[^`]*`", " ", raw))
    if unquoted is None or "`" in unquoted:
        return False
    if answer:
        # Removing quoted content cannot turn a quoted example into an answer.
        if unquoted != raw or "?" in raw or raw.lstrip().startswith(">"):
            return False
        key = " ".join(raw.split()).strip(" .!")
        return key in _STATEMENTS | _BOUND_SHORT_ANSWERS
    return any(clause in _STATEMENTS and not _UNCERTAIN.search(clause)
        for clause in current_clauses(unquoted) if not _UNCERTAIN.search(unquoted))
