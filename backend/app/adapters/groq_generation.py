"""Groq answer-generation adapter, used only as the Vertex fallback."""

import json
import logging
from collections.abc import Sequence

from groq import AsyncGroq
from pydantic import ValidationError

from app.grounded_prompt import build_grounded_prompt
from app.models import ConversationTurn, EvidenceInput, GeneratedAnswer
from app.ports import AnswerGenerationPort, DependencyUnavailable
from app.settings import Settings

logger = logging.getLogger(__name__)


class GroqAnswerGenerationAdapter(AnswerGenerationPort):
    def __init__(self, settings: Settings) -> None:
        if not settings.groq_api_key:
            raise ValueError("GROQ_API_KEY is required for Groq fallback generation.")
        self._model = settings.groq_model_id
        self._max_tokens = settings.groq_generation_max_tokens
        self._reasoning_effort = settings.groq_generation_reasoning_effort
        self._max_passage_chars = settings.groq_max_passage_chars
        self._max_context_chars = settings.groq_max_context_chars
        self._client = AsyncGroq(
            api_key=settings.groq_api_key,
            timeout=settings.groq_generation_timeout_seconds,
            max_retries=0,
        )

    async def generate(
        self,
        question: str,
        evidence: Sequence[EvidenceInput],
        *,
        think: bool = False,
        conversation_history: Sequence[ConversationTurn] = (),
    ) -> GeneratedAnswer:
        bounded_evidence: list[EvidenceInput] = []
        context_chars = 0
        for item in evidence:
            remaining = self._max_context_chars - context_chars
            if remaining <= 0:
                break
            text = item.text[: min(self._max_passage_chars, remaining)]
            if not text.strip():
                continue
            bounded_evidence.append(
                EvidenceInput(
                    evidence_id=item.evidence_id,
                    text=text,
                    document_id=item.document_id,
                    original_page_numbers=item.original_page_numbers,
                )
            )
            context_chars += len(text)
        if not bounded_evidence:
            return GeneratedAnswer(answer="", evidence_ids=[])
        prompt = build_grounded_prompt(
            question,
            bounded_evidence,
            think=think,
            conversation_history=conversation_history,
        )
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0,
                max_tokens=self._max_tokens,
                reasoning_effort=self._reasoning_effort,
                include_reasoning=False,
            )
        except Exception as exc:
            logger.warning("Groq fallback generation failed (%s)", type(exc).__name__)
            raise DependencyUnavailable("Groq fallback answer generation failed.") from exc

        if not response.choices or not response.choices[0].message.content:
            raise DependencyUnavailable("Groq returned an empty answer response.")
        try:
            return GeneratedAnswer.model_validate(
                json.loads(response.choices[0].message.content)
            )
        except (json.JSONDecodeError, ValidationError) as exc:
            raise DependencyUnavailable("Groq returned an invalid answer response.") from exc

    async def healthcheck(self) -> bool:
        try:
            models = await self._client.models.list()
        except Exception:
            return False
        return any(model.id == self._model for model in models.data)

    async def close(self) -> None:
        await self._client.close()
