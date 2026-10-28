"""Pytest unit tests for QueryOrchestrator.

Run from the repository root, for example:
    $env:PYTHONPATH = "$PWD\backend"
    python -m pytest tests\test_query_orchestrator.py -v
    python -m pytest tests\test_query_orchestrator.py --cov=app.query_orchestrator --cov-report=term-missing -v

If QueryOrchestrator lives in a differently named module, update the import below
and the --cov target to match the actual filename.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models import (
    ConversationTurn,
    EvidenceInput,
    QueryRequest,
    RetrievedPassage,
    ResolvedEvidence,
)
from app.orchestrator import QueryOrchestrator


def make_passage(
    *,
    chunk_id: str = "chunk-1",
    document_id: str = "doc-1",
    source_pdf_id: str = "pdf-1",
    text: str = "Relevant legal passage.",
    pages: list[int] | None = None,
) -> RetrievedPassage:
    """Use model_construct so fixtures stay focused on orchestration behavior."""
    return RetrievedPassage.model_construct(
        chunk_id=chunk_id,
        document_id=document_id,
        source_pdf_id=source_pdf_id,
        text=text,
        original_page_numbers=pages if pages is not None else [1],
    )


def make_source(**overrides):
    values = {
        "case_name": "A v B",
        "judgment_title": "A v B judgment",
        "decision_date": "2024-01-15",
        "citation": "2024 SCC 1",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def make_resolved(passage: RetrievedPassage, **overrides) -> ResolvedEvidence:
    values = {
        "chunk_id": passage.chunk_id,
        "document_id": passage.document_id,
        "source_pdf_id": passage.source_pdf_id,
        "text": passage.text,
        "original_page_numbers": passage.original_page_numbers,
        "source": make_source(),
    }
    values.update(overrides)
    return ResolvedEvidence.model_construct(**values)


def make_request(*, question: str = "What is the legal rule?", max_sources: int = 5):
    return QueryRequest.model_construct(
        question=question,
        filters={},
        options=SimpleNamespace(max_sources=max_sources),
    )


def make_generated(answer: str = "The legal rule applies.", evidence_ids=None):
    return SimpleNamespace(
        answer=answer,
        evidence_ids=list(evidence_ids if evidence_ids is not None else ["E1"]),
    )


def make_orchestrator(
    passages=None,
    *,
    generated=None,
    resolved=None,
    max_context_chars=80_000,
):
    retrieval = SimpleNamespace(
        retrieve=AsyncMock(return_value=list(passages if passages is not None else []))
    )
    source_records = SimpleNamespace(
        resolve_many=AsyncMock(return_value=list(resolved if resolved is not None else []))
    )
    answer_generation = SimpleNamespace(
        generate=AsyncMock(return_value=generated or make_generated())
    )
    orchestrator = QueryOrchestrator(
        retrieval=retrieval,
        source_records=source_records,
        answer_generation=answer_generation,
        max_context_chars=max_context_chars,
    )
    return orchestrator, retrieval, source_records, answer_generation


def test_rejects_context_budget_below_minimum():
    with pytest.raises(ValueError, match="at least 1000"):
        QueryOrchestrator(SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), 999)


@pytest.mark.asyncio
async def test_no_usable_passages_returns_insufficient_and_skips_generation():
    orchestrator, retrieval, source_records, generator = make_orchestrator()
    result = await orchestrator.answer(make_request(), request_id="req-1")

    assert result.status == "insufficient_evidence"
    assert result.request_id == "req-1"
    assert result.answer == ""
    assert "No usable passages" in result.limitations[0]
    generator.generate.assert_not_awaited()
    source_records.resolve_many.assert_not_awaited()
    retrieval.retrieve.assert_awaited_once()


@pytest.mark.asyncio
async def test_successful_answer_resolves_and_returns_citation():
    passage = make_passage()
    orchestrator, retrieval, source_records, generator = make_orchestrator(
        [passage], resolved=[make_resolved(passage)]
    )

    result = await orchestrator.answer(make_request(), request_id="req-success")

    assert result.status == "answered"
    assert result.request_id == "req-success"
    assert result.answer == "The legal rule applies."
    assert len(result.citations) == 1
    assert result.citations[0].citation_id == "E1"
    assert result.citations[0].chunk_id == "chunk-1"
    assert result.citations[0].excerpt == passage.text
    source_records.resolve_many.assert_awaited_once_with([passage])
    generator.generate.assert_awaited_once()


@pytest.mark.asyncio
async def test_uses_retrieval_question_and_passes_think_and_history():
    passage = make_passage()
    history = [ConversationTurn.model_construct(role="user", content="Earlier question")]
    orchestrator, retrieval, _, generator = make_orchestrator(
        [passage], resolved=[make_resolved(passage)]
    )

    await orchestrator.answer(
        make_request(question="Original question"),
        retrieval_question="Rewritten retrieval question",
        think=True,
        conversation_history=history,
    )

    retrieval.retrieve.assert_awaited_once_with(
        question="Rewritten retrieval question", filters={}, limit=5
    )
    generator.generate.assert_awaited_once()
    args = generator.generate.await_args
    assert args.args[0] == "Original question"
    assert args.kwargs["think"] is True
    assert args.kwargs["conversation_history"] == history
    assert all(isinstance(item, EvidenceInput) for item in args.args[1])


@pytest.mark.asyncio
async def test_blank_generated_answer_returns_insufficient():
    passage = make_passage()
    orchestrator, _, source_records, _ = make_orchestrator(
        [passage], generated=make_generated("   ", ["E1"])
    )

    result = await orchestrator.answer(make_request(), request_id="req-blank")

    assert result.status == "insufficient_evidence"
    assert "no answer text" in result.limitations[0]
    source_records.resolve_many.assert_not_awaited()


@pytest.mark.asyncio
async def test_answer_without_supplied_evidence_citation_returns_insufficient():
    passage = make_passage()
    orchestrator, _, source_records, _ = make_orchestrator(
        [passage], generated=make_generated("An answer.", ["UNKNOWN"])
    )

    result = await orchestrator.answer(make_request())

    assert result.status == "insufficient_evidence"
    assert "did not cite" in result.limitations[0]
    source_records.resolve_many.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_generated_evidence_reference_withholds_answer():
    passage = make_passage()
    orchestrator, _, _, _ = make_orchestrator(
        [passage],
        generated=make_generated("Potentially unsupported answer.", ["E1", "E404"]),
        resolved=[make_resolved(passage)],
    )

    result = await orchestrator.answer(make_request())

    assert result.status == "insufficient_evidence"
    assert result.answer == ""
    assert "withheld" in result.limitations[0]


@pytest.mark.asyncio
async def test_unverified_source_record_returns_insufficient():
    passage = make_passage()
    wrong_record = make_resolved(passage, text="Different source text")
    orchestrator, _, _, _ = make_orchestrator([passage], resolved=[wrong_record])

    result = await orchestrator.answer(make_request())

    assert result.status == "insufficient_evidence"
    assert "could not be verified" in result.limitations[0]


@pytest.mark.asyncio
async def test_duplicate_resolved_chunk_is_treated_as_ambiguous():
    passage = make_passage()
    orchestrator, _, _, _ = make_orchestrator(
        [passage], resolved=[make_resolved(passage), make_resolved(passage)]
    )

    result = await orchestrator.answer(make_request())

    assert result.status == "insufficient_evidence"


@pytest.mark.asyncio
async def test_duplicate_generated_evidence_ids_are_deduplicated():
    passage = make_passage()
    orchestrator, _, source_records, _ = make_orchestrator(
        [passage],
        generated=make_generated("A verified answer.", ["E1", "E1"]),
        resolved=[make_resolved(passage)],
    )

    result = await orchestrator.answer(make_request())

    assert result.status == "answered"
    assert len(result.citations) == 1
    source_records.resolve_many.assert_awaited_once_with([passage])


@pytest.mark.asyncio
async def test_default_request_id_is_generated():
    passage = make_passage()
    orchestrator, _, _, _ = make_orchestrator(
        [passage], resolved=[make_resolved(passage)]
    )

    result = await orchestrator.answer(make_request())

    assert result.request_id
    assert result.status == "answered"


def test_usable_unique_passages_rejects_invalid_and_duplicate_passages():
    good = make_passage(chunk_id="good")
    blank_chunk = make_passage(chunk_id="   ")
    blank_document = make_passage(chunk_id="blank-doc", document_id=" ")
    blank_pdf = make_passage(chunk_id="blank-pdf", source_pdf_id="")
    blank_text = make_passage(chunk_id="blank-text", text=" ")
    no_pages = make_passage(chunk_id="no-pages", pages=[])
    invalid_page = make_passage(chunk_id="invalid-page", pages=[0])
    duplicate_one = make_passage(chunk_id="duplicate")
    duplicate_two = make_passage(chunk_id="duplicate", text="Another duplicate")

    result = QueryOrchestrator._usable_unique_passages(
        [
            good, blank_chunk, blank_document, blank_pdf, blank_text,
            no_pages, invalid_page, duplicate_one, duplicate_two,
        ]
    )

    assert result == [good]


def test_fit_context_budget_skips_oversized_passage_and_keeps_later_fit():
    first = make_passage(chunk_id="first", text="a" * 600)
    too_large = make_passage(chunk_id="large", text="b" * 500)
    later = make_passage(chunk_id="later", text="c" * 400)

    result = QueryOrchestrator._fit_context_budget([first, too_large, later], 1_000)

    assert result == [first, later]


def test_records_match_checks_all_provenance_fields():
    passage = make_passage()
    matching = make_resolved(passage)
    assert QueryOrchestrator._records_match(passage, matching)

    assert not QueryOrchestrator._records_match(
        passage, make_resolved(passage, chunk_id="other-chunk")
    )
    assert not QueryOrchestrator._records_match(
        passage, make_resolved(passage, document_id="other-doc")
    )
    assert not QueryOrchestrator._records_match(
        passage, make_resolved(passage, source_pdf_id="other-pdf")
    )
    assert not QueryOrchestrator._records_match(
        passage, make_resolved(passage, text="altered text")
    )
    assert not QueryOrchestrator._records_match(
        passage, make_resolved(passage, original_page_numbers=[0])
    )


def test_citation_truncates_excerpt_to_1200_characters():
    passage = make_passage(text="x" * 1_500)
    record = make_resolved(passage)

    citation = QueryOrchestrator._citation("E7", record)

    assert citation.citation_id == "E7"
    assert len(citation.excerpt) == 1_200
    assert citation.original_page_numbers == [1]


def test_insufficient_helper_returns_empty_answer_and_limitation():
    result = QueryOrchestrator._insufficient("req-x", "A reason.")

    assert result.request_id == "req-x"
    assert result.status == "insufficient_evidence"
    assert result.answer == ""
    assert result.limitations == ["A reason."]


@pytest.mark.asyncio
async def test_context_budget_is_applied_before_generation():
    first = make_passage(chunk_id="first", text="a" * 700)
    second = make_passage(chunk_id="second", text="b" * 700)
    orchestrator, _, _, generator = make_orchestrator(
        [first, second], resolved=[make_resolved(first)], max_context_chars=1_000
    )

    result = await orchestrator.answer(make_request())

    assert result.status == "answered"
    evidence = generator.generate.await_args.args[1]
    assert len(evidence) == 1
    assert evidence[0].text == first.text


@pytest.mark.asyncio
async def test_retrieval_limit_uses_request_max_sources():
    passage = make_passage()
    orchestrator, retrieval, _, _ = make_orchestrator(
        [passage], resolved=[make_resolved(passage)]
    )

    await orchestrator.answer(make_request(max_sources=2))

    assert retrieval.retrieve.await_args.kwargs["limit"] == 2
