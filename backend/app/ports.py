"""Technology-neutral interfaces owned by adjacent system components."""

from collections.abc import Sequence
from typing import Protocol

from app.models import (
    ConversationTurn,
    EvidenceInput,
    GeneratedAnswer,
    QueryFilters,
    ResolvedEvidence,
    RetrievedPassage,
)


class RetrievalPort(Protocol):
    async def retrieve(
        self, question: str, filters: QueryFilters, limit: int
    ) -> Sequence[RetrievedPassage]: ...


class SourceRecordPort(Protocol):
    async def resolve_many(
        self, passages: Sequence[RetrievedPassage]
    ) -> Sequence[ResolvedEvidence]: ...


class AnswerGenerationPort(Protocol):
    async def generate(
        self,
        question: str,
        evidence: Sequence[EvidenceInput],
        *,
        think: bool = False,
        conversation_history: Sequence[ConversationTurn] = (),
    ) -> GeneratedAnswer: ...


class AdapterNotConfigured(RuntimeError):
    """Raised until the owning module's adapter is wired into the application."""


class DependencyUnavailable(RuntimeError):
    """Raised when a configured external service cannot complete an operation."""


class UnsupportedQueryFilter(ValueError):
    """Raised when persistence cannot safely apply a requested query filter."""


class UnconfiguredRetrieval:
    async def retrieve(
        self, question: str, filters: QueryFilters, limit: int
    ) -> Sequence[RetrievedPassage]:
        raise AdapterNotConfigured("Retrieval adapter is not configured")


class UnconfiguredSourceRecords:
    async def resolve_many(
        self, passages: Sequence[RetrievedPassage]
    ) -> Sequence[ResolvedEvidence]:
        raise AdapterNotConfigured("Source-record adapter is not configured")


class UnconfiguredAnswerGeneration:
    async def generate(
        self,
        question: str,
        evidence: Sequence[EvidenceInput],
        *,
        think: bool = False,
        conversation_history: Sequence[ConversationTurn] = (),
    ) -> GeneratedAnswer:
        raise AdapterNotConfigured("Answer-generation adapter is not configured")
