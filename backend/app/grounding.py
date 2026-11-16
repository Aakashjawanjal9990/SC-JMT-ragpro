"""Sentence-level citation parsing for legal answer support checks."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

_ABBREVIATION = re.compile(
    r"\b(?:v|vs|no|nos|s\.c\.r|s\.c|a\.i\.r|scc|air|j|jj|art|sec|dr|mr|mrs|ltd|inc|e\.g|i\.e|cf)\.",
    re.IGNORECASE,
)
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\u201c\"'(\[])")
_CITATION_MARKER = re.compile(r"\[((?:E|P)?\d+(?:\s*,\s*(?:E|P)?\d+)*)\]", re.IGNORECASE)
_CITATION_NUMBER = re.compile(r"(?:E|P)?(\d+)", re.IGNORECASE)
_POST_SENTENCE_CITATION = re.compile(
    r"([.!?])\s*(\[(?:E|P)?\d+(?:\s*,\s*(?:E|P)?\d+)*\])", re.IGNORECASE
)


@dataclass(frozen=True)
class AnswerClaim:
    text: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class ParsedGroundedAnswer:
    claims: tuple[AnswerClaim, ...]
    evidence_ids: tuple[str, ...]
    rendered_answer: str


def parse_grounded_answer(
    answer: str,
    generated_evidence_ids: Sequence[str],
    available_evidence_ids: set[str],
) -> ParsedGroundedAnswer | None:
    """Require an inline, valid evidence reference on every answer sentence."""
    # Accept either common model format: ``sentence [E1].`` or ``sentence. [E1]``.
    normalized_answer = _POST_SENTENCE_CITATION.sub(r" \2\1", answer)
    protected = _ABBREVIATION.sub(
        lambda match: match.group(0).replace(".", "\x00"), normalized_answer
    )
    sentences = [
        item.replace("\x00", ".").strip()
        for item in _SENTENCE_BOUNDARY.split(protected)
        if item.strip()
    ]
    if not sentences or len(sentences) > 100:
        return None

    allowed = set(generated_evidence_ids)
    claims: list[AnswerClaim] = []
    used: list[str] = []
    for sentence in sentences:
        markers = _CITATION_MARKER.findall(sentence)
        sentence_ids = list(
            dict.fromkeys(
                f"E{number}" for marker in markers for number in _CITATION_NUMBER.findall(marker)
            )
        )
        if not sentence_ids:
            return None
        if any(
            evidence_id not in allowed or evidence_id not in available_evidence_ids
            for evidence_id in sentence_ids
        ):
            return None
        claim_text = _CITATION_MARKER.sub("", sentence).strip()
        claim_text = re.sub(r"\s+", " ", claim_text)
        claim_text = re.sub(r"\s+([.,;:!?])", r"\1", claim_text)
        if not claim_text:
            return None
        claims.append(AnswerClaim(text=claim_text, evidence_ids=tuple(sentence_ids)))
        for evidence_id in sentence_ids:
            if evidence_id not in used:
                used.append(evidence_id)

    # The adapter's global evidence list must agree with inline sentence citations.
    if set(generated_evidence_ids) != set(used):
        return None
    rendered = _CITATION_MARKER.sub(
        lambda match: "[" + ", ".join(_CITATION_NUMBER.findall(match.group(1))) + "]",
        normalized_answer.strip(),
    )
    return ParsedGroundedAnswer(
        claims=tuple(claims), evidence_ids=tuple(used), rendered_answer=rendered
    )


def all_claims_supported(verdicts: Sequence[bool], expected_count: int) -> bool:
    """Accept only a complete list of literal ``True`` support verdicts."""
    return len(verdicts) == expected_count and all(verdict is True for verdict in verdicts)
