"""Adapt Pratik's grounded generator to the backend answer-generation port."""

import asyncio
import logging
from collections.abc import Sequence

from app.models import ConversationTurn, EvidenceInput
from app.models import GeneratedAnswer as BackendAnswer
from app.ports import AnswerGenerationPort, DependencyRateLimited, DependencyUnavailable
from app.retrieval.generator import AnswerGenerator
from app.retrieval.llm import GroqChat
from app.settings import Settings

logger = logging.getLogger("supreme_court_rag.query.generation")


class PratikAnswerGenerationAdapter(AnswerGenerationPort):
    def __init__(self, settings: Settings) -> None:
        chat = GroqChat(
            settings.groq_model_id,
            settings.groq_api_key or "",
            max_tokens=settings.groq_generation_max_tokens,
            timeout_seconds=settings.groq_generation_timeout_seconds,
            reasoning_effort=settings.groq_generation_reasoning_effort,
        )
        self._chat = chat
        self._generator = AnswerGenerator(
            chat,
            max_passage_chars=settings.groq_max_passage_chars,
            max_context_chars=settings.groq_max_context_chars,
        )

    def warmup(self) -> None:
        self._generator.warmup()

    async def generate(
        self,
        question: str,
        evidence: Sequence[EvidenceInput],
        *,
        think: bool = False,
        conversation_history: Sequence[ConversationTurn] = (),
    ) -> BackendAnswer:
        passages = [
            {
                "document_id": item.document_id or item.evidence_id,
                "original_page_numbers": item.original_page_numbers,
                "text": item.text,
                "rank": rank,
                "evidence_id": item.evidence_id,
            }
            for rank, item in enumerate(evidence, start=1)
        ]
        try:
            generated, included = await asyncio.to_thread(
                self._generator.generate,
                question,
                passages,
                conversation_history=[
                    {"role": turn.role, "text": turn.text} for turn in conversation_history
                ],
                think=think,
            )
        except DependencyRateLimited:
            raise
        except Exception as exc:
            logger.error(
                "query.dependency_failed",
                extra={
                    "dependency": "groq",
                    "operation": "answer_generation",
                    "exception_type": type(exc).__name__,
                    "provider_status": getattr(exc, "status_code", None),
                    "provider_request_id": getattr(exc, "request_id", None),
                },
            )
            raise DependencyUnavailable("Pratik answer generation failed.") from exc
        if not generated.answered:
            return BackendAnswer(answer="", evidence_ids=[])
        evidence_by_rank = {
            int(passage["rank"]): str(passage["evidence_id"]) for passage in included
        }
        return BackendAnswer(
            answer=generated.answer,
            evidence_ids=[
                evidence_by_rank[rank]
                for rank in generated.cited_ranks
                if rank in evidence_by_rank
            ],
        )

    async def healthcheck(self) -> bool:
        try:
            response = await asyncio.to_thread(self._chat.healthcheck)
        except Exception:
            return False
        return response

    async def close(self) -> None:
        await asyncio.to_thread(self._chat.close)
