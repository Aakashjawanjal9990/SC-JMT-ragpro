"""Public API and internal port models for the query backend."""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class QueryFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_name: str | None = Field(default=None, min_length=1, max_length=500)
    date_from: date | None = None
    date_to: date | None = None
    document_ids: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("document_ids")
    @classmethod
    def document_ids_must_be_nonempty(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            try:
                normalized.append(str(UUID(value.strip())))
            except (ValueError, AttributeError) as exc:
                raise ValueError("document_ids must contain valid judgment UUIDs") from exc
        if any(not value for value in normalized):
            raise ValueError("document_ids cannot contain blank values")
        if any(len(value) > 256 for value in normalized):
            raise ValueError("document_ids cannot exceed 256 characters")
        if len(normalized) != len(set(normalized)):
            raise ValueError("document_ids cannot contain duplicates")
        return normalized

    @model_validator(mode="after")
    def date_range_must_be_ordered(self) -> "QueryFilters":
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must be on or before date_to")
        return self

    @field_validator("case_name")
    @classmethod
    def case_name_must_contain_text(cls, value: str | None) -> str | None:
        if value is not None:
            value = value.strip()
            if not value:
                raise ValueError("case_name cannot be blank")
        return value


class QueryOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_sources: int = Field(default=8, ge=1, le=20)


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=10_000)
    filters: QueryFilters = Field(default_factory=QueryFilters)
    options: QueryOptions = Field(default_factory=QueryOptions)

    @field_validator("question")
    @classmethod
    def question_must_contain_non_whitespace(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must contain non-whitespace text")
        return value.strip()


class ConversationTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    text: str = Field(min_length=1, max_length=2_000)


class RetrievedPassage(BaseModel):
    """Passage returned by retrieval; page numbers refer to the source PDF."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str = Field(min_length=1, max_length=256)
    document_id: str = Field(min_length=1, max_length=256)
    source_pdf_id: str = Field(min_length=1, max_length=256)
    text: str = Field(min_length=1, max_length=20_000)
    original_page_numbers: list[int] = Field(min_length=1, max_length=100)
    score: float | None = Field(default=None, allow_inf_nan=False)
    rank: int | None = Field(default=None, ge=1)

    @field_validator("original_page_numbers")
    @classmethod
    def pages_must_be_positive_and_ordered(cls, pages: list[int]) -> list[int]:
        return validate_page_numbers(pages)


class SourceMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_name: str | None = Field(default=None, max_length=500)
    judgment_title: str | None = Field(default=None, max_length=1_000)
    decision_date: date | None = None
    citation: str | None = Field(default=None, max_length=250)


class ResolvedEvidence(BaseModel):
    """Canonical source record used to verify one retrieved passage."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str = Field(min_length=1, max_length=256)
    document_id: str = Field(min_length=1, max_length=256)
    source_pdf_id: str = Field(min_length=1, max_length=256)
    text: str = Field(min_length=1, max_length=20_000)
    original_page_numbers: list[int] = Field(min_length=1, max_length=100)
    source: SourceMetadata

    @field_validator("original_page_numbers")
    @classmethod
    def pages_must_be_positive_and_ordered(cls, pages: list[int]) -> list[int]:
        return validate_page_numbers(pages)


class EvidenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=20_000)


class GeneratedAnswer(BaseModel):
    """Model output must reference request-local evidence IDs, never invent pages."""

    model_config = ConfigDict(extra="forbid")

    answer: str = Field(max_length=20_000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("evidence_ids")
    @classmethod
    def evidence_ids_must_be_bounded_and_nonempty(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 64 for value in values):
            raise ValueError("evidence IDs must be nonempty and at most 64 characters")
        return values

    @field_validator("answer")
    @classmethod
    def normalize_answer(cls, value: str) -> str:
        return value.strip()


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    citation_id: str
    document_id: str
    chunk_id: str
    source_pdf_id: str
    case_name: str | None = None
    judgment_title: str | None = None
    decision_date: date | None = None
    citation: str | None = None
    original_page_numbers: list[int]
    excerpt: str
    verified: Literal[True] = True


class QueryResponse(BaseModel):
    request_id: str
    status: Literal["answered", "partial", "insufficient_evidence"]
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    check_type: Literal["live"] = "live"
    dependencies: dict[str, bool]


def validate_page_numbers(pages: list[int]) -> list[int]:
    if any(page < 1 for page in pages):
        raise ValueError("original page numbers must be positive and one-based")
    if pages != sorted(set(pages)):
        raise ValueError("original page numbers must be unique and in source order")
    return pages
