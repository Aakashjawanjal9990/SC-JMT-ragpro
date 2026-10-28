"""Unit tests for app.ports.

Run from the repository root (with backend on PYTHONPATH):
    $env:PYTHONPATH = "$PWD\\backend"
    python -m pytest tests\\test_ports.py --cov=app.ports --cov-report=term-missing -v
"""

import asyncio

import pytest

from app.ports import (
    AdapterNotConfigured,
    AnswerGenerationPort,
    DependencyUnavailable,
    QueryEmbeddingPort,
    RetrievalPort,
    SourceRecordPort,
    UnconfiguredAnswerGeneration,
    UnconfiguredRetrieval,
    UnconfiguredSourceRecords,
    UnsupportedQueryFilter,
)


def test_adapter_not_configured_is_runtime_error() -> None:
    error = AdapterNotConfigured("adapter missing")
    assert isinstance(error, RuntimeError)
    assert str(error) == "adapter missing"


def test_dependency_unavailable_is_runtime_error() -> None:
    error = DependencyUnavailable("service unavailable")
    assert isinstance(error, RuntimeError)
    assert str(error) == "service unavailable"


def test_unsupported_query_filter_is_value_error() -> None:
    error = UnsupportedQueryFilter("unsupported filter")
    assert isinstance(error, ValueError)
    assert str(error) == "unsupported filter"


def test_unconfigured_retrieval_raises_adapter_not_configured() -> None:
    adapter = UnconfiguredRetrieval()

    with pytest.raises(
        AdapterNotConfigured,
        match="Retrieval adapter is not configured",
    ):
        asyncio.run(adapter.retrieve("question", None, 5))


def test_unconfigured_source_records_raises_adapter_not_configured() -> None:
    adapter = UnconfiguredSourceRecords()

    with pytest.raises(
        AdapterNotConfigured,
        match="Source-record adapter is not configured",
    ):
        asyncio.run(adapter.resolve_many([]))


def test_unconfigured_answer_generation_raises_adapter_not_configured() -> None:
    adapter = UnconfiguredAnswerGeneration()

    with pytest.raises(
        AdapterNotConfigured,
        match="Answer-generation adapter is not configured",
    ):
        asyncio.run(adapter.generate("question", []))


def test_unconfigured_answer_generation_accepts_optional_arguments_before_raising() -> None:
    adapter = UnconfiguredAnswerGeneration()

    with pytest.raises(
        AdapterNotConfigured,
        match="Answer-generation adapter is not configured",
    ):
        asyncio.run(
            adapter.generate(
                "question",
                [],
                think=True,
                conversation_history=[],
            )
        )


def test_protocols_expose_expected_methods() -> None:
    # Protocols define the contracts for adapters; their methods are checked
    # without instantiating Protocol classes or requiring concrete models.
    assert callable(RetrievalPort.retrieve)
    assert callable(SourceRecordPort.resolve_many)
    assert callable(AnswerGenerationPort.generate)
    assert callable(QueryEmbeddingPort.embed_query)
