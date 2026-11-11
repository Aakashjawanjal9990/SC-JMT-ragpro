"""Use Groq only when primary Vertex answer generation fails."""

import inspect
import logging
from collections.abc import Sequence

from app.models import ConversationTurn, EvidenceInput, GeneratedAnswer
from app.ports import AdapterNotConfigured, AnswerGenerationPort, DependencyUnavailable

logger = logging.getLogger(__name__)


class FallbackAnswerGeneration(AnswerGenerationPort):
    def __init__(
        self,
        primary: AnswerGenerationPort,
        fallback: AnswerGenerationPort,
    ) -> None:
        self._primary = primary
        self._fallback = fallback

    def warmup(self) -> None:
        for adapter in (self._primary, self._fallback):
            warmup = getattr(adapter, "warmup", None)
            if warmup is not None:
                try:
                    warmup()
                except Exception:
                    logger.warning(
                        "Answer-generation provider warmup failed (%s)",
                        type(adapter).__name__,
                    )

    async def generate(
        self,
        question: str,
        evidence: Sequence[EvidenceInput],
        *,
        think: bool = False,
        conversation_history: Sequence[ConversationTurn] = (),
    ) -> GeneratedAnswer:
        try:
            return await self._primary.generate(
                question,
                evidence,
                think=think,
                conversation_history=conversation_history,
            )
        except (AdapterNotConfigured, DependencyUnavailable) as exc:
            logger.warning(
                "Vertex answer generation unavailable; using Groq fallback (%s)",
                type(exc.__cause__ or exc).__name__,
            )
        return await self._fallback.generate(
            question,
            evidence,
            think=think,
            conversation_history=conversation_history,
        )

    async def healthcheck(self) -> bool:
        for adapter in (self._primary, self._fallback):
            probe = getattr(adapter, "healthcheck", None)
            if probe is None:
                continue
            try:
                if await probe():
                    return True
            except Exception:
                continue
        return False

    async def close(self) -> None:
        for adapter in (self._primary, self._fallback):
            close = getattr(adapter, "close", None)
            if close is None:
                continue
            try:
                result = close()
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.exception("Answer-generation adapter failed to close cleanly")
