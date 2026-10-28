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
    ) -> GeneratedAnswer:
        del question, think, conversation_history
        if not evidence:
            return GeneratedAnswer(answer="", evidence_ids=[])
        excerpt = " ".join(evidence[0].text.split())[:800]
        return GeneratedAnswer(
            answer=f"Local test mode: retrieved passage: “{excerpt}” [1]",
            evidence_ids=[evidence[0].evidence_id],
        )
