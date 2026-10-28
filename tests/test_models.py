"""Unit tests for the query backend's Pydantic API/port models.

Adjust the import candidates below if the production module has a different name.
Run from the repository root with PYTHONPATH=backend.
"""
from datetime import date
from uuid import uuid4

import pytest
from pydantic import ValidationError

# Support the most common module names without changing the tests themselves.
try:
    from app import query_models as models
except ImportError:
    try:
        from app import models
    except ImportError:
        from app import schemas as models


def valid_uuid() -> str:
    return str(uuid4())


class TestQueryFilters:
    def test_defaults(self):
        result = models.QueryFilters()
        assert result.case_name is None
        assert result.date_from is None
        assert result.date_to is None
        assert result.document_ids == []

    def test_case_name_is_trimmed(self):
        assert models.QueryFilters(case_name="  State v. Person  ").case_name == "State v. Person"

    @pytest.mark.parametrize("value", ["", "   ", "\t\n"])
    def test_blank_case_name_rejected(self, value):
        with pytest.raises(ValidationError):
            models.QueryFilters(case_name=value)

    def test_case_name_max_length(self):
        with pytest.raises(ValidationError):
            models.QueryFilters(case_name="x" * 501)

    def test_valid_date_range(self):
        result = models.QueryFilters(date_from=date(2020, 1, 1), date_to=date(2020, 1, 2))
        assert result.date_from < result.date_to

    def test_same_date_range_is_allowed(self):
        result = models.QueryFilters(date_from=date(2020, 1, 1), date_to=date(2020, 1, 1))
        assert result.date_from == result.date_to

    def test_reversed_date_range_rejected(self):
        with pytest.raises(ValidationError, match="date_from must be on or before date_to"):
            models.QueryFilters(date_from=date(2022, 1, 1), date_to=date(2021, 1, 1))

    def test_valid_document_ids_are_normalized(self):
        identifier = valid_uuid()
        result = models.QueryFilters(document_ids=[identifier.upper()])
        assert result.document_ids == [str(__import__("uuid").UUID(identifier))]

    @pytest.mark.parametrize("value", ["not-a-uuid", "", "  "])
    def test_invalid_document_id_rejected(self, value):
        with pytest.raises(ValidationError, match="document_ids must contain valid judgment UUIDs"):
            models.QueryFilters(document_ids=[value])

    def test_duplicate_document_ids_rejected_after_normalization(self):
        identifier = valid_uuid()
        with pytest.raises(ValidationError, match="document_ids cannot contain duplicates"):
            models.QueryFilters(document_ids=[identifier, identifier.upper()])

    def test_document_ids_limit(self):
        with pytest.raises(ValidationError):
            models.QueryFilters(document_ids=[valid_uuid() for _ in range(101)])

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            models.QueryFilters(unexpected="value")


class TestQueryOptions:
    def test_default_max_sources(self):
        assert models.QueryOptions().max_sources == 8

    @pytest.mark.parametrize("value", [1, 8, 20])
    def test_valid_max_sources(self, value):
        assert models.QueryOptions(max_sources=value).max_sources == value

    @pytest.mark.parametrize("value", [0, -1, 21])
    def test_invalid_max_sources(self, value):
        with pytest.raises(ValidationError):
            models.QueryOptions(max_sources=value)

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            models.QueryOptions(extra_option=True)


class TestQueryRequest:
    def test_defaults_and_question_normalization(self):
        result = models.QueryRequest(question="  What did the court decide?  ")
        assert result.question == "What did the court decide?"
        assert isinstance(result.filters, models.QueryFilters)
        assert isinstance(result.options, models.QueryOptions)
        assert result.options.max_sources == 8

    @pytest.mark.parametrize("question", ["", " ", "\t\n"])
    def test_empty_or_whitespace_question_rejected(self, question):
        with pytest.raises(ValidationError):
            models.QueryRequest(question=question)

    def test_question_max_length(self):
        with pytest.raises(ValidationError):
            models.QueryRequest(question="x" * 10001)

    def test_nested_models_accept_valid_values(self):
        identifier = valid_uuid()
        result = models.QueryRequest(
            question="Explain the judgment",
            filters={"document_ids": [identifier]},
            options={"max_sources": 4},
        )
        assert result.filters.document_ids == [identifier]
        assert result.options.max_sources == 4

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            models.QueryRequest(question="Valid question", unknown="x")


class TestConversationTurn:
    @pytest.mark.parametrize("role", ["user", "assistant"])
    def test_valid_roles(self, role):
        result = models.ConversationTurn(role=role, text="Hello")
        assert result.role == role

    def test_invalid_role_rejected(self):
        with pytest.raises(ValidationError):
            models.ConversationTurn(role="system", text="Hello")

    @pytest.mark.parametrize("text", ["", "x" * 2001])
    def test_text_length_validation(self, text):
        with pytest.raises(ValidationError):
            models.ConversationTurn(role="user", text=text)

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            models.ConversationTurn(role="user", text="Hi", extra="x")


class TestPageNumberValidation:
    def test_valid_ordered_unique_pages(self):
        assert models.validate_page_numbers([1, 2, 5]) == [1, 2, 5]

    def test_single_page_valid(self):
        assert models.validate_page_numbers([1]) == [1]

    @pytest.mark.parametrize("pages", [[0], [-1], [1, 0]])
    def test_nonpositive_pages_rejected(self, pages):
        with pytest.raises(ValueError, match="positive and one-based"):
            models.validate_page_numbers(pages)

    @pytest.mark.parametrize("pages", [[2, 1], [1, 1], [1, 3, 2]])
    def test_unsorted_or_duplicate_pages_rejected(self, pages):
        with pytest.raises(ValueError, match="unique and in source order"):
            models.validate_page_numbers(pages)


class TestRetrievedPassage:
    def valid_payload(self, **overrides):
        payload = {
            "chunk_id": "chunk-1",
            "document_id": "doc-1",
            "source_pdf_id": "pdf-1",
            "text": "Relevant passage from the judgment.",
            "original_page_numbers": [2, 3],
            "score": 0.87,
            "rank": 1,
        }
        payload.update(overrides)
        return payload

    def test_valid_passage(self):
        result = models.RetrievedPassage(**self.valid_payload())
        assert result.chunk_id == "chunk-1"
        assert result.original_page_numbers == [2, 3]
        assert result.score == pytest.approx(0.87)
        assert result.rank == 1

    @pytest.mark.parametrize(
        "field,value",
        [
            ("chunk_id", ""),
            ("document_id", ""),
            ("source_pdf_id", ""),
            ("text", ""),
            ("original_page_numbers", []),
            ("original_page_numbers", [0]),
            ("original_page_numbers", [2, 1]),
            ("original_page_numbers", [1, 1]),
            ("rank", 0),
            ("score", float("inf")),
            ("score", float("nan")),
        ],
    )
    def test_invalid_passage_values(self, field, value):
        with pytest.raises(ValidationError):
            models.RetrievedPassage(**self.valid_payload(**{field: value}))

    def test_optional_score_and_rank_can_be_none(self):
        result = models.RetrievedPassage(**self.valid_payload(score=None, rank=None))
        assert result.score is None
        assert result.rank is None

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            models.RetrievedPassage(**self.valid_payload(unexpected=True))


class TestSourceMetadata:
    def test_valid_metadata(self):
        result = models.SourceMetadata(
            case_name="A v. B",
            judgment_title="Judgment",
            decision_date=date(2024, 5, 6),
            citation="2024 SCC 1",
        )
        assert result.case_name == "A v. B"
        assert result.decision_date == date(2024, 5, 6)

    def test_all_fields_optional(self):
        result = models.SourceMetadata()
        assert result.case_name is None
        assert result.judgment_title is None
        assert result.decision_date is None
        assert result.citation is None

    def test_field_length_limits(self):
        with pytest.raises(ValidationError):
            models.SourceMetadata(case_name="x" * 501)
        with pytest.raises(ValidationError):
            models.SourceMetadata(judgment_title="x" * 1001)
        with pytest.raises(ValidationError):
            models.SourceMetadata(citation="x" * 251)

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            models.SourceMetadata(unexpected="x")


class TestResolvedEvidence:
    def valid_payload(self, **overrides):
        payload = {
            "chunk_id": "chunk-1",
            "document_id": "doc-1",
            "source_pdf_id": "pdf-1",
            "text": "Verified source passage.",
            "original_page_numbers": [4, 5],
            "source": {"case_name": "A v. B", "citation": "2024 SCC 1"},
        }
        payload.update(overrides)
        return payload

    def test_valid_evidence(self):
        result = models.ResolvedEvidence(**self.valid_payload())
        assert result.source.case_name == "A v. B"
        assert result.original_page_numbers == [4, 5]

    def test_source_required(self):
        payload = self.valid_payload()
        del payload["source"]
        with pytest.raises(ValidationError):
            models.ResolvedEvidence(**payload)

    def test_page_validation(self):
        with pytest.raises(ValidationError):
            models.ResolvedEvidence(**self.valid_payload(original_page_numbers=[3, 2]))

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            models.ResolvedEvidence(**self.valid_payload(extra="x"))


class TestEvidenceInput:
    def test_valid_evidence_input(self):
        result = models.EvidenceInput(evidence_id="E1", text="Supporting passage")
        assert result.evidence_id == "E1"

    @pytest.mark.parametrize(
        "payload",
        [
            {"evidence_id": "", "text": "text"},
            {"evidence_id": "E1", "text": ""},
            {"evidence_id": "x" * 65, "text": "text"},
            {"evidence_id": "E1", "text": "x" * 20001},
        ],
    )
    def test_invalid_evidence_input(self, payload):
        with pytest.raises(ValidationError):
            models.EvidenceInput(**payload)

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            models.EvidenceInput(evidence_id="E1", text="text", extra="x")


class TestGeneratedAnswer:
    def test_answer_is_trimmed(self):
        result = models.GeneratedAnswer(answer="  Supported answer.  ", evidence_ids=["E1"])
        assert result.answer == "Supported answer."

    def test_defaults(self):
        result = models.GeneratedAnswer(answer="")
        assert result.answer == ""
        assert result.evidence_ids == []

    @pytest.mark.parametrize("ids", [[""], ["  "], ["x" * 65]])
    def test_invalid_evidence_ids_rejected(self, ids):
        with pytest.raises(ValidationError):
            models.GeneratedAnswer(answer="Answer", evidence_ids=ids)

    def test_evidence_ids_max_count(self):
        with pytest.raises(ValidationError):
            models.GeneratedAnswer(answer="Answer", evidence_ids=[f"E{i}" for i in range(21)])

    def test_answer_max_length(self):
        with pytest.raises(ValidationError):
            models.GeneratedAnswer(answer="x" * 20001)

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            models.GeneratedAnswer(answer="Answer", extra="x")


class TestCitation:
    def valid_payload(self, **overrides):
        payload = {
            "citation_id": "C1",
            "document_id": "doc-1",
            "chunk_id": "chunk-1",
            "source_pdf_id": "pdf-1",
            "case_name": "A v. B",
            "judgment_title": "Judgment",
            "decision_date": date(2024, 1, 1),
            "citation": "2024 SCC 1",
            "original_page_numbers": [10, 11],
            "excerpt": "Quoted source text",
            "verified": True,
        }
        payload.update(overrides)
        return payload

    def test_valid_citation(self):
        result = models.Citation(**self.valid_payload())
        assert result.verified is True
        assert result.original_page_numbers == [10, 11]

    def test_verified_must_be_true(self):
        with pytest.raises(ValidationError):
            models.Citation(**self.valid_payload(verified=False))

    def test_required_fields(self):
        payload = self.valid_payload()
        del payload["excerpt"]
        with pytest.raises(ValidationError):
            models.Citation(**payload)

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            models.Citation(**self.valid_payload(extra="x"))


class TestQueryResponse:
    def test_valid_response_and_defaults(self):
        result = models.QueryResponse(
            request_id="req-1",
            status="answered",
            answer="The court held ...",
        )
        assert result.citations == []
        assert result.limitations == []

    @pytest.mark.parametrize("status", ["answered", "partial", "insufficient_evidence"])
    def test_supported_statuses(self, status):
        result = models.QueryResponse(request_id="req-1", status=status, answer="")
        assert result.status == status

    def test_invalid_status_rejected(self):
        with pytest.raises(ValidationError):
            models.QueryResponse(request_id="req-1", status="error", answer="x")


class TestHealthAndReadinessResponses:
    def test_health_response_default(self):
        result = models.HealthResponse()
        assert result.status == "ok"

    def test_health_response_rejects_other_status(self):
        with pytest.raises(ValidationError):
            models.HealthResponse(status="down")

    def test_ready_response(self):
        result = models.ReadinessResponse(status="ready", dependencies={"database": True})
        assert result.check_type == "live"
        assert result.dependencies == {"database": True}

    def test_not_ready_response(self):
        result = models.ReadinessResponse(
            status="not_ready",
            dependencies={"database": False, "vector_store": True},
        )
        assert result.status == "not_ready"

    def test_invalid_readiness_status(self):
        with pytest.raises(ValidationError):
            models.ReadinessResponse(status="ok", dependencies={})

    def test_check_type_is_fixed(self):
        with pytest.raises(ValidationError):
            models.ReadinessResponse(status="ready", check_type="deep", dependencies={})
