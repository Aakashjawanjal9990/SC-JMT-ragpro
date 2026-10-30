"""BM25 tokenizer that keeps statutory references intact.

"Section 438", "sec. 438", "u/s 438" and "s.438" all produce the canonical token
"s.438" plus the bare number "438"; "Article 21-A" produces "art.21-a" and "21-a"
(kept distinct from "21"). "498A" and "498-A" both become "498-a". Dotted
acronyms like "Cr.P.C." and "I.P.C." collapse to "crpc" and "ipc".

Bump TOKENIZER_VERSION whenever output changes: saved BM25 indexes record it and
refuse to load with a different tokenizer.
"""

import re

TOKENIZER_VERSION = "1"

# Canonical prefixes for provision references.
_REFERENCE_PREFIXES = {
    "s": "s",
    "ss": "s",
    "sec": "s",
    "secs": "s",
    "section": "s",
    "sections": "s",
    "art": "art",
    "arts": "art",
    "article": "art",
    "articles": "art",
    "r": "r",
    "rule": "r",
    "rules": "r",
    "o": "o",
    "ord": "o",
    "order": "o",
    "cl": "cl",
    "clause": "cl",
    "reg": "reg",
    "regulation": "reg",
}

STOPWORDS = frozenset(
    """
    a about above after again against all also am an and any are as at be because been
    before being below between both but by can could did do does doing down during each
    few for from further had has have having he her here hers herself him himself his how
    i if in into is it its itself just me more most my myself nor of off on once only or
    other our ours ourselves out over own same she should so some such than that the their
    theirs them themselves then there these they this those through to too under until up
    upon very was we were what when where which while who whom why will with would you
    your yours yourself yourselves shall may might must thereof therein thereto whereas
    said per vs v
    """.split()
)

_CRPC = re.compile(r"\bcr\.?\s?p\.?\s?c\b\.?")
_DOTTED_ACRONYM = re.compile(r"\b(?:[a-z]\.){2,}(?:[a-z]\b\.?)?")
_UNDER_SECTION = re.compile(r"\bu/ss?\b\.?")
_TOKEN = re.compile(
    r"(?P<ref>[a-z]{1,12}\.\s?\d+[a-z]?(?:-[a-z0-9]+)?)"  # s.438, art. 21-a, sec.302
    r"|(?P<num>\d+[a-z]?(?:-[a-z0-9]+)?)"  # 438, 21-a, 498a
    r"|(?P<word>[a-z]+)"
)
_NUM_SUFFIX = re.compile(r"^(\d+)([a-z])$")


def _normalize_number(number: str) -> str:
    return _NUM_SUFFIX.sub(r"\1-\2", number)


def _normalize_text(text: str) -> str:
    text = text.lower()
    text = _UNDER_SECTION.sub(" s.", text)
    text = _CRPC.sub(" crpc ", text)
    return _DOTTED_ACRONYM.sub(lambda m: m.group(0).replace(".", "") + " ", text)


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    pending_prefix: str | None = None  # canonical prefix from a preceding word like "section"
    for match in _TOKEN.finditer(_normalize_text(text)):
        if ref := match.group("ref"):
            prefix, number = (part.strip() for part in ref.split(".", 1))
            number = _normalize_number(number)
            tokens.append(f"{_REFERENCE_PREFIXES.get(prefix, prefix)}.{number}")
            tokens.append(number)
            pending_prefix = None
        elif number := match.group("num"):
            number = _normalize_number(number)
            if pending_prefix:
                tokens.append(f"{pending_prefix}.{number}")
            tokens.append(number)
            pending_prefix = None
        else:
            word = match.group("word")
            pending_prefix = _REFERENCE_PREFIXES.get(word)
            if len(word) > 1 and word not in STOPWORDS:
                tokens.append(word)
    return tokens
