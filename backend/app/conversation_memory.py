"""Token-aware conversation context helpers.

Conversation history remains in ``query_runs``. These helpers only select and
bound the context sent to a model; they never delete source messages.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from app.models import ConversationTurn


@dataclass(frozen=True)
class ConversationContext:
    summary: str
    recent_turns: tuple[ConversationTurn, ...]
    evidence_texts: tuple[str, ...]
    estimated_tokens: int


def estimate_tokens(value: str) -> int:
    """Return a conservative, provider-independent token estimate.

    The active models do not share a tokenizer. Three UTF-8 bytes per token is
    intentionally conservative for English and remains bounded for Unicode.
    """
    if not value:
        return 0
    return max(1, math.ceil(len(value.encode("utf-8")) / 3))


def _clip_text(value: str, max_tokens: int) -> str:
    if max_tokens <= 0:
        return ""
    if estimate_tokens(value) <= max_tokens:
        return value
    max_bytes = max_tokens * 3
    if max_bytes <= 32:
        return value.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
    marker = "\n[…older context clipped…]\n"
    edge_bytes = max(1, (max_bytes - len(marker.encode("utf-8"))) // 2)
    raw = value.encode("utf-8")
    head = raw[:edge_bytes].decode("utf-8", errors="ignore")
    tail = raw[-edge_bytes:].decode("utf-8", errors="ignore")
    return f"{head}{marker}{tail}"


class SlidingWindowManager:
    """Keep the newest complete exchanges that fit a bounded context budget."""

    def __init__(
        self, *, max_messages: int, max_context_tokens: int, output_reserve_tokens: int
    ) -> None:
        if max_messages < 2 or max_context_tokens < 256 or output_reserve_tokens < 0:
            raise ValueError("Conversation context limits are invalid.")
        self.max_messages = max_messages
        self.max_context_tokens = max_context_tokens
        self.output_reserve_tokens = output_reserve_tokens

    def build(
        self,
        summary: str,
        turns: Sequence[ConversationTurn],
        *,
        question: str,
        evidence: Sequence[str] = (),
    ) -> ConversationContext:
        """Fit summary and recent turns while reserving space for evidence/answer."""
        base_tokens = estimate_tokens(question) + self.output_reserve_tokens + 512
        evidence_budget = max(0, self.max_context_tokens - base_tokens)
        fitted_evidence: list[str] = []
        evidence_tokens = 0
        for item in evidence:
            remaining = evidence_budget - evidence_tokens
            if remaining <= 0:
                break
            cost = estimate_tokens(item)
            if cost <= remaining:
                fitted_evidence.append(item)
                evidence_tokens += cost
            else:
                # Preserve the top-ranked passage prefix when the last passage
                # crosses the budget; do not send uncapped evidence downstream.
                clipped = item.encode("utf-8")[: remaining * 3].decode("utf-8", errors="ignore")
                if clipped:
                    fitted_evidence.append(clipped)
                    evidence_tokens += estimate_tokens(clipped)
                break
        base_tokens += evidence_tokens
        available = max(0, self.max_context_tokens - base_tokens)
        summary_budget = available
        if turns and summary.strip():
            summary_budget = max(0, available - min(1_024, available // 2))
        fitted_summary = _clip_text(summary.strip(), summary_budget)
        remaining = max(0, available - estimate_tokens(fitted_summary))

        # A stored exchange is two messages. Keep only complete, newest exchanges.
        exchange_size = 2
        candidates = list(turns[-self.max_messages :])
        if len(candidates) % exchange_size:
            candidates = candidates[1:]
        selected: list[ConversationTurn] = []
        used = estimate_tokens(fitted_summary)
        for offset in range(len(candidates) - exchange_size, -1, -exchange_size):
            exchange = candidates[offset : offset + exchange_size]
            cost = sum(estimate_tokens(item.text) for item in exchange)
            if used + cost > available:
                # The window is chronological: never include an older exchange
                # after a newer exchange did not fit.
                break
            selected[0:0] = exchange
            used += cost

        # Preserve the current query and the summary when older turns do not fit.
        return ConversationContext(
            summary=fitted_summary,
            recent_turns=tuple(selected),
            evidence_texts=tuple(fitted_evidence),
            estimated_tokens=base_tokens + used,
        )
