"""Deterministic answer adapter for local end-to-end wiring checks only."""

from collections.abc import Sequence

from app.models import ConversationTurn, EvidenceInput, GeneratedAnswer


class LocalTestAnswerGeneration:
    async def generate(
        self,
        question: str,
        evidence: Sequence[EvidenceInput],
        *,
        think: bool = False,
        conversation_history: Sequence[ConversationTurn] = (),
        conversation_summary: str = "",
    ) -> GeneratedAnswer:
        del question, think, conversation_history, conversation_summary
        if not evidence:
            return GeneratedAnswer(answer="", evidence_ids=[])
        return GeneratedAnswer(
            answer="Local test mode returned the first retrieved passage. [1]",
            evidence_ids=[evidence[0].evidence_id],
        )
