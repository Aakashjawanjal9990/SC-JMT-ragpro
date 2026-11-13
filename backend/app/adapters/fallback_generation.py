"""Answer generation with a primary model and a fallback used only when the primary fails.

An empty answer from the primary is a valid "evidence does not support an answer"
result and is returned as is; only DependencyUnavailable (errors, timeouts, invalid
responses) switches to the fallback.
"""

import inspect
import logging
from collections.abc import Sequence

from app.models import ConversationTurn, EvidenceInput, GeneratedAnswer
from app.ports import AnswerGenerationPort, DependencyUnavailable

logger = logging.getLogger(__name__)


class FallbackAnswerGeneration(AnswerGenerationPort):
    def __init__(
        self,
        primary: AnswerGenerationPort,
        fallback: AnswerGenerationPort,
        *,
        primary_name: str = "primary",
        fallback_name: str = "fallback",
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._primary_name = primary_name
        self._fallback_name = fallback_name

    def warmup(self) -> None:
        for adapter in (self._primary, self._fallback):
            warmup = getattr(adapter, "warmup", None)
            if warmup is not None:
                warmup()

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
                question, evidence, think=think, conversation_history=conversation_history
            )
        except DependencyUnavailable as exc:
            # The cause type is logged, never the question or evidence text.
            logger.warning(
                "answer_generation.fallback",
                extra={
                    "primary": self._primary_name,
                    "fallback": self._fallback_name,
                    "cause": type(exc.__cause__ or exc).__name__,
                },
            )
        return await self._fallback.generate(
            question, evidence, think=think, conversation_history=conversation_history
        )

    async def healthcheck(self) -> bool:
        """Ready while either model is reachable; answers degrade to the fallback."""
        for adapter in (self._primary, self._fallback):
            probe = getattr(adapter, "healthcheck", None)
            if probe is None:
                continue
            try:
                if await probe():
                    return True
            except Exception:
                logger.warning("Answer generation healthcheck failed", exc_info=True)
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
                logger.exception("Answer generation adapter failed to close cleanly")
