"""Query orchestration and citation provenance checks."""

import logging
from collections.abc import Sequence
from uuid import uuid4

from app.conversation_memory import SlidingWindowManager
from app.grounding import all_claims_supported, parse_grounded_answer
from app.models import (
    Citation,
    ConversationTurn,
    EvidenceInput,
    QueryRequest,
    QueryResponse,
    ResolvedEvidence,
    RetrievedPassage,
)
from app.ports import (
    AnswerGenerationPort,
    AnswerSupportPort,
    RetrievalPort,
    SourceRecordPort,
    UnconfiguredAnswerSupport,
)

DEFAULT_MAX_CONTEXT_CHARS = 80_000
DEFAULT_CONVERSATION_CONTEXT_TOKENS = 12_000
DEFAULT_CONVERSATION_OUTPUT_RESERVE_TOKENS = 2_048
DEFAULT_CONVERSATION_MAX_MESSAGES = 12
logger = logging.getLogger(__name__)


class QueryOrchestrator:
    def __init__(
        self,
        retrieval: RetrievalPort,
        source_records: SourceRecordPort,
        answer_generation: AnswerGenerationPort,
        max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
        max_context_tokens: int = DEFAULT_CONVERSATION_CONTEXT_TOKENS,
        output_reserve_tokens: int = DEFAULT_CONVERSATION_OUTPUT_RESERVE_TOKENS,
        max_conversation_messages: int = DEFAULT_CONVERSATION_MAX_MESSAGES,
        answer_support: AnswerSupportPort | None = None,
    ) -> None:
        if max_context_chars < 1_000:
            raise ValueError("max_context_chars must be at least 1000.")
        self._retrieval = retrieval
        self._source_records = source_records
        self._answer_generation = answer_generation
        self._answer_support = answer_support or UnconfiguredAnswerSupport()
        self._max_context_chars = max_context_chars
        self._conversation_window = SlidingWindowManager(
            max_messages=max_conversation_messages,
            max_context_tokens=max_context_tokens,
            output_reserve_tokens=output_reserve_tokens,
        )

    async def answer(
        self,
        request: QueryRequest,
        request_id: str | None = None,
        *,
        retrieval_question: str | None = None,
        think: bool = False,
        conversation_history: Sequence[ConversationTurn] = (),
        conversation_summary: str = "",
    ) -> QueryResponse:
        request_id = request_id or str(uuid4())
        candidates = await self._retrieval.retrieve(
            question=retrieval_question or request.question,
            filters=request.filters,
            limit=request.options.max_sources,
        )
        usable = self._usable_unique_passages(candidates)
        usable = self._fit_context_budget(usable, self._max_context_chars)
        if not usable:
            return self._insufficient(request_id, "No usable passages were retrieved.")

        evidence = [
            EvidenceInput(
                evidence_id=f"E{index}",
                text=passage.text,
                document_id=passage.document_id,
                original_page_numbers=passage.original_page_numbers,
            )
            for index, passage in enumerate(usable, start=1)
        ]
        passages_by_id = {
            item.evidence_id: passage for item, passage in zip(evidence, usable, strict=True)
        }
        context = self._conversation_window.build(
            conversation_summary,
            conversation_history,
            question=request.question,
            evidence=[passage.text for passage in usable],
        )
        evidence = [
            item.model_copy(update={"text": context.evidence_texts[index]})
            for index, item in enumerate(evidence[: len(context.evidence_texts)])
        ]
        if not evidence:
            return self._insufficient(request_id, "Evidence exceeded the model context budget.")
        passages_by_id = {
            item.evidence_id: passage for item, passage in zip(evidence, usable, strict=True)
        }
        generated = await self._answer_generation.generate(
            request.question,
            evidence,
            think=think,
            conversation_history=context.recent_turns,
            conversation_summary=context.summary,
        )
        if not generated.answer.strip():
            return self._insufficient(request_id, "The answer generator returned no answer text.")
        parsed = parse_grounded_answer(
            generated.answer,
            generated.evidence_ids,
            set(passages_by_id),
        )
        if parsed is None:
            return self._insufficient(
                request_id,
                "Every answer sentence must cite supplied evidence.",
            )
        proposed_ids = list(parsed.evidence_ids)

        resolved_records = await self._source_records.resolve_many(
            [passages_by_id[item] for item in proposed_ids]
        )
        resolved_by_chunk: dict[str, ResolvedEvidence] = {}
        ambiguous_chunks: set[str] = set()
        for record in resolved_records:
            if record.chunk_id in resolved_by_chunk:
                ambiguous_chunks.add(record.chunk_id)
            else:
                resolved_by_chunk[record.chunk_id] = record
        citations: list[Citation] = []
        for evidence_id in proposed_ids:
            passage = passages_by_id[evidence_id]
            record = resolved_by_chunk.get(passage.chunk_id)
            if (
                record is None
                or record.chunk_id in ambiguous_chunks
                or not self._records_match(passage, record)
            ):
                continue
            citations.append(self._citation(evidence_id, record))

        if not citations:
            return self._insufficient(
                request_id,
                "The cited passages could not be verified against source records.",
            )

        valid_ids = {citation.citation_id for citation in citations}
        if set(proposed_ids) != valid_ids:
            return self._insufficient(
                request_id,
                "One or more cited passages could not be verified; "
                "the generated answer was withheld.",
            )

        # Validate only the exact passage text shown to the answer model. The
        # separate source-record comparison above proves that text came from the
        # canonical source; using the full record here could validate unseen text.
        model_evidence = {item.evidence_id: item.text for item in evidence}
        cited_evidence = {evidence_id: model_evidence[evidence_id] for evidence_id in proposed_ids}
        try:
            support_results = await self._answer_support.validate(parsed.claims, cited_evidence)
        except Exception as exc:
            logger.warning(
                "answer_support.validation_unavailable",
                extra={"exception_type": type(exc).__name__},
            )
            return self._insufficient(
                request_id,
                "The answer could not be checked against its cited passages.",
            )
        if not all_claims_supported(support_results, len(parsed.claims)):
            return self._insufficient(
                request_id,
                "One or more answer sentences were not supported by their cited passages.",
            )
        return QueryResponse(
            request_id=request_id,
            status="answered",
            answer=parsed.rendered_answer,
            citations=citations,
        )

    @staticmethod
    def _usable_unique_passages(
        candidates: Sequence[RetrievedPassage],
    ) -> list[RetrievedPassage]:
        counts: dict[str, int] = {}
        for passage in candidates:
            if passage.chunk_id.strip():
                counts[passage.chunk_id] = counts.get(passage.chunk_id, 0) + 1
        usable: list[RetrievedPassage] = []
        for passage in candidates:
            if (
                not passage.chunk_id.strip()
                or not passage.document_id.strip()
                or not passage.source_pdf_id.strip()
                or not passage.text.strip()
                or not passage.original_page_numbers
                or any(page < 1 for page in passage.original_page_numbers)
                or counts.get(passage.chunk_id) != 1
            ):
                continue
            usable.append(passage)
        return usable

    @staticmethod
    def _fit_context_budget(
        passages: Sequence[RetrievedPassage], max_chars: int
    ) -> list[RetrievedPassage]:
        selected: list[RetrievedPassage] = []
        used_chars = 0
        for passage in passages:
            passage_chars = len(passage.text)
            if used_chars + passage_chars > max_chars:
                continue
            selected.append(passage)
            used_chars += passage_chars
        return selected

    @staticmethod
    def _records_match(passage: RetrievedPassage, record: ResolvedEvidence) -> bool:
        return (
            passage.chunk_id == record.chunk_id
            and passage.document_id == record.document_id
            and passage.source_pdf_id == record.source_pdf_id
            and passage.text == record.text
            and passage.original_page_numbers == record.original_page_numbers
            and all(page >= 1 for page in record.original_page_numbers)
        )

    @staticmethod
    def _citation(evidence_id: str, record: ResolvedEvidence) -> Citation:
        return Citation(
            citation_id=evidence_id,
            document_id=record.document_id,
            chunk_id=record.chunk_id,
            source_pdf_id=record.source_pdf_id,
            case_name=record.source.case_name,
            judgment_title=record.source.judgment_title,
            decision_date=record.source.decision_date,
            citation=record.source.citation,
            original_page_numbers=record.original_page_numbers,
            excerpt=record.text[:1200],
        )

    @staticmethod
    def _insufficient(request_id: str, reason: str) -> QueryResponse:
        return QueryResponse(
            request_id=request_id,
            status="insufficient_evidence",
            answer="",
            limitations=[reason],
        )
