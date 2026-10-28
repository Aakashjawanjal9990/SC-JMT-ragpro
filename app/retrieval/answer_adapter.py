"""Adapter the backend calls to get an LLM answer: retrieve passages, then generate a
grounded, cited answer from them. Wiring mirrors RetrievalPortAdapter:

    rag = AnswerPortAdapter.from_config(RetrievedPassage, QueryAnswer)  # reads config.yaml
    rag.warmup()   # at startup
    result = await rag.answer(question, filters, limit)
    rag.close()    # at shutdown

`answer()` returns answer_factory(**fields) with:

    answer        str    the answer, citing passages as [n] (n = passage rank); or a fixed
                         "not answered" message
    answered      bool   False when no passages were found or they do not answer the question
    grounded      bool   answered and cites at least one passage; treat False as unverified
    cited_ranks   list[int]  ranks cited in the answer, in order of first citation
    passages      list   passage_factory(...) for every passage given to the model; each
                         rank matches the [n] in the answer
    model         str | None  answer model id (None when the model was not called)

Errors: retrieval errors propagate unchanged; GenerationTimeoutError /
GenerationFailure (503, retryable) if the model fails. See errors.py.
"""

import asyncio
from collections.abc import Callable
from typing import Any

from app.retrieval.config import load_config
from app.retrieval.errors import GenerationFailure, GenerationTimeoutError
from app.retrieval.generator import AnswerGenerator
from app.retrieval.llm import GroqChat
from app.retrieval.port_adapter import RetrievalPortAdapter


class AnswerPortAdapter:
    def __init__(
        self,
        retrieval: RetrievalPortAdapter,
        generator: AnswerGenerator,
        passage_factory: Callable[..., Any] = dict,
        answer_factory: Callable[..., Any] = dict,
        timeout_seconds: float | None = None,
    ) -> None:
        # `retrieval` must return plain dicts (passage_factory=dict); conversion to the
        # backend's passage type happens here, after generation.
        self._retrieval = retrieval
        self._generator = generator
        self._passage_factory = passage_factory
        self._answer_factory = answer_factory
        self._timeout_seconds = timeout_seconds

    @classmethod
    def from_config(
        cls,
        passage_factory: Callable[..., Any] = dict,
        answer_factory: Callable[..., Any] = dict,
        config: dict[str, Any] | None = None,
    ) -> "AnswerPortAdapter":
        config = config or load_config()
        generation = config["generation"]
        chat = GroqChat(
            generation["model"],
            generation["api_key_env"],
            max_tokens=generation["max_tokens"],
            timeout_seconds=generation["timeout_seconds"],
            reasoning_effort=generation.get("reasoning_effort"),
        )
        generator = AnswerGenerator(
            chat,
            max_passage_chars=generation["max_passage_chars"],
            max_context_chars=generation["max_context_chars"],
        )
        return cls(
            RetrievalPortAdapter.from_config(dict, config),
            generator,
            passage_factory,
            answer_factory,
            timeout_seconds=generation["timeout_seconds"],
        )

    def warmup(self) -> None:
        self._retrieval.warmup()
        self._generator.warmup()

    def close(self) -> None:
        self._retrieval.close()

    async def answer(self, question: str, filters: Any, limit: int) -> Any:
        passages = await self._retrieval.retrieve(question, filters, limit)
        try:
            generated, included = await asyncio.wait_for(
                asyncio.to_thread(self._generator.generate, question, passages),
                timeout=self._timeout_seconds,
            )
        except (asyncio.TimeoutError, TimeoutError) as exc:
            raise GenerationTimeoutError("Answer generation exceeded its deadline") from exc
        except Exception as exc:
            raise GenerationFailure("Answer generation failed") from exc
        return self._answer_factory(
            answer=generated.answer,
            answered=generated.answered,
            grounded=generated.answered and bool(generated.cited_ranks),
            cited_ranks=generated.cited_ranks,
            passages=[self._passage_factory(**passage) for passage in included],
            model=generated.model,
        )
