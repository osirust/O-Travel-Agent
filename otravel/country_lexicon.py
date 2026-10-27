"""Conservative RU/EN country mentions from pinned Unicode CLDR data.

Mentions are evidence, never destination-role assignment or permission to edit.
No city list, fuzzy matching, nationality inference, or runtime network is used.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from functools import lru_cache
from importlib.resources import files

DATA_SHA256 = "74dd233f299cb6f5f3a620dd53f62d1a778aff4bb4799872725b384109eb5fa4"
SOURCE_COMMIT = "7d2b9cb11cec08f4d333ad73209deb3df0711c3b"
_NONCURRENT = re.compile(r"\b(?:раньше|ранее|прежде|вчера|обсуждал\w*|говорил\w*|писал\w*|сказал\w*|"
    r"цитирую|если|допустим|предположим|гипотетическ\w*|бы|previously|earlier|yesterday|hypothetically|if|suppose)\b")
_PERSONAL = re.compile(r"\b(?:гражданств\w*|гражданин|гражданка|граждане|паспорт\w*|поддан\w*|"
    r"citizenship|citizen\w*|passport\w*|nationality)\b")
_NEGATION = re.compile(r"\b(?:не|нет|без|not|never|don['’]?t)\b")
_QUOTES = re.compile(r'«[^»]*»|“[^”]*”|"[^"\n]*"|(?<!\w)\'[^\'\n]*\'(?!\w)|`[^`]*`')
_ORIGIN_OR_DEST = re.compile(r"\b(из|от|from|в|во|to|in|into)\b")
_COUNTRY_CUE = re.compile(r"\b(?:страна|стране|страну|государство|country(?:\s+of)?)\s*$")


class CountryLexiconError(ValueError):
    """The vendored reference must load completely or fail closed."""


def _key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
        .replace("–", "-").replace("—", "-").replace("‑", "-").split())


def _russian_forms(value: str) -> set[str]:
    """Small ending rules on source labels; irregular forms are not guessed."""
    words = _key(value).split()
    result = {" ".join(words)}
    if not words or len(words) > 3 or any(not re.fullmatch(r"[а-я-]+", word) for word in words):
        return result
    noun = words[-1]
    accusative, genitive = noun, noun
    if noun.endswith("а"):
        accusative = noun[:-1] + "у"
        genitive = noun[:-1] + ("и" if len(noun) > 1 and noun[-2] in "гкхжчшщ" else "ы")
    elif noun.endswith("я"):
        accusative, genitive = noun[:-1] + "ю", noun[:-1] + "и"
    elif noun[-1] in "бвгджзклмнпрстфхцчшщ":
        genitive = noun + "а"
    else:
        return result
    for form, noun_form in (("acc", accusative), ("gen", genitive)):
        prefix = []
        for word in words[:-1]:
            if word.endswith("ая"):
                prefix.append(word[:-2] + ("ую" if form == "acc" else "ой"))
            elif word.endswith("яя"):
                prefix.append(word[:-2] + ("юю" if form == "acc" else "ей"))
            elif form == "gen" and word.endswith(("ый", "ой")):
                prefix.append(word[:-2] + "ого")
            elif form == "gen" and word.endswith("ий"):
                prefix.append(word[:-2] + "его")
            else:
                prefix.append(word)
        result.add(" ".join([*prefix, noun_form]))
    return result


@lru_cache(maxsize=1)
def _lexicon():
    try:
        content = files("otravel").joinpath("data/cldr_territories_ru_en.json").read_bytes()
        if hashlib.sha256(content).hexdigest() != DATA_SHA256:
            raise ValueError
        source = json.loads(content)
        rows = source["countries"]
        if source["version"] != 1 or source["source"]["commit"] != SOURCE_COMMIT or len(rows) != 249:
            raise ValueError
        aliases = defaultdict(set)
        for code, row in rows.items():
            if not re.fullmatch(r"[A-Z]{2}", code) or set(row) != {"ru", "en", "aliases"}:
                raise ValueError
            for locale in ("ru", "en"):
                for label in row["aliases"][locale]:
                    forms = _russian_forms(label) if locale == "ru" else {_key(label)}
                    for form in forms:
                        aliases[form].add(code)
            aliases[code.casefold()].add(code)
        pattern = re.compile(r"(?<![\w-])(?:" + "|".join(re.escape(a) for a in sorted(aliases, key=lambda a: (-len(a), a))) + r")(?![\w-])")
        return rows, dict(aliases), pattern
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise CountryLexiconError("country_lexicon_data_invalid") from error


def _mask(text: str) -> str | None:
    masked = list(text)
    for match in _QUOTES.finditer(text):
        masked[match.start():match.end()] = " " * len(match[0])
    unquoted = "".join(masked)
    if (any(mark in unquoted for mark in ('"', '«', '»', '“', '”', '`'))
            or re.search(r"(?<!\w)'|'(?!\w)", unquoted)):
        return None
    for line in re.finditer(r"[^\n]*(?:\n|$)", unquoted):
        if line[0].lstrip().startswith(">"):
            masked[line.start():line.end()] = " " * len(line[0])
    unquoted = "".join(masked)
    for clause in re.finditer(r"[^,;.!?\n]+", unquoted):
        normalized = _key(clause[0])
        if _NONCURRENT.search(normalized) or _PERSONAL.search(normalized) or _NEGATION.search(normalized):
            masked[clause.start():clause.end()] = " " * len(clause[0])
    return "".join(masked)


def _normalized_spans(text: str):
    output, indices = [], []
    for index, character in enumerate(text):
        for char in unicodedata.normalize("NFKC", character).casefold().replace("ё", "е"):
            char = "-" if char in "–—‑" else char
            if char.isspace():
                if output and output[-1] != " ":
                    output.append(" ")
                    indices.append(index)
            else:
                output.append(char)
                indices.append(index)
    return "".join(output), indices


def literal_country_mentions(text: str) -> list[dict]:
    """Collect literal current country labels with original character offsets.

    Bare ASCII ISO codes require uppercase (``in`` is not India). Straightforward
    source/origin clauses are excluded, but callers must still bind destination
    roles explicitly: a unique mention alone never proves a destination.
    """
    if not isinstance(text, str) or not 0 < len(text) <= 20_000:
        return []
    masked = _mask(text)
    if masked is None:
        return []
    normalized, offsets = _normalized_spans(masked)
    rows, aliases, pattern = _lexicon()
    result = []
    for match in pattern.finditer(normalized):
        start, end = offsets[match.start()], offsets[match.end()-1] + 1
        literal = text[start:end]
        if re.fullmatch(r"[a-z]{2}", match[0]) and not literal.isupper():
            continue
        prefix = normalized[max(0, match.start()-100):match.start()]
        sentence = re.split(r"[;.!?\n]", prefix)[-1]
        cues = list(_ORIGIN_OR_DEST.finditer(sentence))
        if cues and cues[-1][1] in {"из", "от", "from"}:
            continue
        if match[0] == "georgia" and not _COUNTRY_CUE.search(prefix):
            continue  # Country/state homonym needs an explicit country cue.
        for code in sorted(aliases[match[0]]):
            result.append({"code": code, "name": rows[code]["ru"], "span": (start, end)})
    return result


def literal_country_destination_request(state: dict, text: str) -> dict | None:
    """Recognise only a complete, literal country wish for an unchosen stop.

    This creates a directory question, never a selected city or country fact.
    Mixed messages, existing routes and informational questions remain with NLU.
    """
    if not isinstance(state, dict) or not isinstance(text, str):
        return None
    stays = state.get("stays") or []
    if len(stays) > 1 or any(stay.get("city") or stay.get("place_id") for stay in stays):
        return None
    mentions = literal_country_mentions(text)
    if len(mentions) != 1:
        return None
    mention = mentions[0]
    start, end = mention["span"]
    prefix, suffix = _key(text[:start]).strip(), _key(text[end:]).strip(" .!")
    prefix = re.sub(r"^(?:наверное|наверно|пожалуй)\s+", "", prefix)
    forward = re.fullmatch(r"(?:я\s+)?(?:хочу|хотим|хотелось бы)(?:\s+(?:поехать|отправиться))?\s+(?:в|во)", prefix)
    reverse = prefix in {"в", "во"} and suffix in {"хочу", "хотим", "хочу наверное", "хотим наверное"}
    if not (forward and suffix in {"", "наверное", "наверно", "пожалуй"} or reverse):
        return None
    return {"role": "destination", "kind": "country", "query": mention["code"],
        "country_iso2": mention["code"], "stay_id": stays[0]["id"] if stays else "new-stay-1"}
