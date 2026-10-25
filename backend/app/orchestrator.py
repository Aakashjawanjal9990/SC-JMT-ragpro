"""Query orchestration and citation provenance checks."""

from collections.abc import Sequence
from uuid import uuid4

from app.models import (
    Citation,
    EvidenceInput,
    QueryRequest,
    QueryResponse,
    ResolvedEvidence,
    RetrievedPassage,
)
from app.ports import AnswerGenerationPort, RetrievalPort, SourceRecordPort

DEFAULT_MAX_CONTEXT_CHARS = 80_000


class QueryOrchestrator:
    def __init__(
        self,
        retrieval: RetrievalPort,
        source_records: SourceRecordPort,
        answer_generation: AnswerGenerationPort,
        max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
    ) -> None:
        if max_context_chars < 1_000:
            raise ValueError("max_context_chars must be at least 1000.")
        self._retrieval = retrieval
        self._source_records = source_records
        self._answer_generation = answer_generation
        self._max_context_chars = max_context_chars

    async def answer(self, request: QueryRequest, request_id: str | None = None) -> QueryResponse:
        request_id = request_id or str(uuid4())
        candidates = await self._retrieval.retrieve(
            question=request.question,
            filters=request.filters,
            limit=request.options.max_sources,
        )
        usable = self._usable_unique_passages(candidates)
        usable = self._fit_context_budget(usable, self._max_context_chars)
        if not usable:
            return self._insufficient(request_id, "No usable passages were retrieved.")

        evidence = [
            EvidenceInput(evidence_id=f"E{index}", text=passage.text)
            for index, passage in enumerate(usable, start=1)
        ]
        passages_by_id = {
            item.evidence_id: passage for item, passage in zip(evidence, usable, strict=True)
        }
        generated = await self._answer_generation.generate(request.question, evidence)
        if not generated.answer.strip():
            return self._insufficient(request_id, "The answer generator returned no answer text.")
        cited_ids = list(dict.fromkeys(generated.evidence_ids))
        proposed_ids = [item for item in cited_ids if item in passages_by_id]

        if not proposed_ids:
            return self._insufficient(
                request_id,
                "The generated answer did not cite any supplied evidence.",
            )

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
        has_invalid_reference = any(item not in passages_by_id for item in generated.evidence_ids)
        has_unverified_reference = any(item not in valid_ids for item in proposed_ids)
        if has_invalid_reference or has_unverified_reference:
            return self._insufficient(
                request_id,
                "One or more cited passages could not be verified; "
                "the generated answer was withheld.",
            )
        return QueryResponse(
            request_id=request_id,
            status="answered",
            answer=generated.answer.strip(),
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
    def _records_match(
        passage: RetrievedPassage, record: ResolvedEvidence
    ) -> bool:
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
