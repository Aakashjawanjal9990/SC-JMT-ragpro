"""Conversation memory stored in the existing query_runs.metadata JSONB field."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.models import ConversationTurn
from app.ports import DependencyUnavailable
from app.settings import Settings

logger = logging.getLogger(__name__)
_MEMORY_KEY = "conversation_memory"


@dataclass(frozen=True)
class ConversationMemoryState:
    summary: str = ""
    version: int = 0
    last_summarized_run_id: str | None = None
    last_summarized_created_at: datetime | None = None

    @classmethod
    def from_json(cls, value: Any) -> ConversationMemoryState:
        if not isinstance(value, dict):
            return cls()
        try:
            version = max(0, int(value.get("version", 0)))
        except (TypeError, ValueError):
            version = 0
        created_at = value.get("last_summarized_created_at")
        try:
            parsed_created_at = datetime.fromisoformat(created_at) if created_at else None
        except (TypeError, ValueError):
            parsed_created_at = None
        return cls(
            summary=value.get("summary", "") if isinstance(value.get("summary"), str) else "",
            version=version,
            last_summarized_run_id=(
                value.get("last_summarized_run_id")
                if isinstance(value.get("last_summarized_run_id"), str)
                else None
            ),
            last_summarized_created_at=parsed_created_at,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "summary": self.summary,
            "last_summarized_run_id": self.last_summarized_run_id,
            "last_summarized_created_at": (
                self.last_summarized_created_at.isoformat()
                if self.last_summarized_created_at
                else None
            ),
        }


class ConversationManager:
    """Load recent turns and summarize older ones without deleting the transcript."""

    def __init__(self, engine: Any, summarizer: Any, settings: Settings) -> None:
        self._engine = engine
        self._summarizer = summarizer
        self._settings = settings
        self._window_turns = settings.conversation_max_messages // 2

    async def prepare(
        self, user_id: str, conversation_id: UUID
    ) -> tuple[ConversationMemoryState, list[ConversationTurn]]:
        state = await self._load_state(user_id, conversation_id)
        if self._summarizer is not None:
            try:
                async with asyncio.timeout(self._settings.request_timeout_seconds):
                    state = await self._summarize_overflow(user_id, conversation_id, state)
            except (DependencyUnavailable, TimeoutError, SQLAlchemyError):
                # The complete transcript remains in query_runs; continue with the
                # last saved summary and bounded recent messages.
                logger.warning(
                    "conversation_summary.skipped",
                    extra={"conversation_id": str(conversation_id)},
                    exc_info=True,
                )
        recent = await self._load_recent_turns(user_id, conversation_id)
        return state, recent

    async def _load_state(self, user_id: str, conversation_id: UUID) -> ConversationMemoryState:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                text(
                    """SELECT metadata->'conversation_memory' AS memory
                       FROM query_runs
                       WHERE user_uuid = :user_id AND request_type = 'CHAT'
                         AND metadata->>'conversation_id' = :conversation_id
                         AND metadata ? 'conversation_memory'
                       ORDER BY created_at DESC, query_run_uuid DESC LIMIT 1"""
                ),
                {"user_id": UUID(user_id), "conversation_id": str(conversation_id)},
            )
            row = result.mappings().first()
        return ConversationMemoryState.from_json(row["memory"] if row else None)

    async def _summarize_overflow(
        self,
        user_id: str,
        conversation_id: UUID,
        initial_state: ConversationMemoryState,
    ) -> ConversationMemoryState:
        state = initial_state
        while True:
            unsummarized_count = await self._count_unsummarized(user_id, conversation_id, state)
            eligible_count = max(0, unsummarized_count - self._window_turns)
            if not eligible_count:
                return state
            batch = await self._load_unsummarized_batch(
                user_id,
                conversation_id,
                state,
                min(eligible_count, self._settings.conversation_summary_batch_turns),
            )
            if not batch:
                return state
            summary = await self._summarizer.summarize(
                state.summary,
                [turn for row in batch for turn in _turns_from_row(row)],
                max_chars=self._settings.conversation_summary_max_chars,
            )
            last = batch[-1]
            next_state = ConversationMemoryState(
                summary=summary,
                version=state.version + 1,
                last_summarized_run_id=str(last["query_run_uuid"]),
                last_summarized_created_at=last["created_at"],
            )
            saved_state = await self._save_summary(
                user_id, conversation_id, state.version, next_state
            )
            if saved_state.version != next_state.version:
                # Another request advanced the checkpoint first. Its summary is
                # authoritative; never replace a newer version with this result.
                return saved_state
            state = saved_state

    async def _count_unsummarized(
        self, user_id: str, conversation_id: UUID, state: ConversationMemoryState
    ) -> int:
        params: dict[str, Any] = {
            "user_id": UUID(user_id),
            "conversation_id": str(conversation_id),
            "has_cursor": bool(state.last_summarized_created_at and state.last_summarized_run_id),
            "cursor_at": state.last_summarized_created_at,
            "cursor_id": UUID(state.last_summarized_run_id)
            if state.last_summarized_run_id
            else None,
        }
        async with self._engine.connect() as connection:
            result = await connection.execute(
                text(
                    """SELECT count(*) FROM query_runs
                        WHERE user_uuid = :user_id AND request_type = 'CHAT'
                          AND response_text IS NOT NULL
                          AND metadata->>'conversation_id' = :conversation_id
                          AND (NOT :has_cursor OR
                               (created_at, query_run_uuid) > (:cursor_at, :cursor_id))"""
                ),
                params,
            )
            return int(result.scalar_one())

    async def _load_unsummarized_batch(
        self,
        user_id: str,
        conversation_id: UUID,
        state: ConversationMemoryState,
        limit: int,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "user_id": UUID(user_id),
            "conversation_id": str(conversation_id),
            "limit": limit,
            "has_cursor": bool(state.last_summarized_created_at and state.last_summarized_run_id),
            "cursor_at": state.last_summarized_created_at,
            "cursor_id": UUID(state.last_summarized_run_id)
            if state.last_summarized_run_id
            else None,
        }
        async with self._engine.connect() as connection:
            result = await connection.execute(
                text(
                    """SELECT query_run_uuid, created_at, query_text, response_text
                        FROM query_runs
                        WHERE user_uuid = :user_id AND request_type = 'CHAT'
                          AND response_text IS NOT NULL
                          AND metadata->>'conversation_id' = :conversation_id
                          AND (NOT :has_cursor OR
                               (created_at, query_run_uuid) > (:cursor_at, :cursor_id))
                        ORDER BY created_at, query_run_uuid LIMIT :limit"""
                ),
                params,
            )
            return [dict(row) for row in result.mappings().all()]

    async def _save_summary(
        self,
        user_id: str,
        conversation_id: UUID,
        expected_version: int,
        state: ConversationMemoryState,
    ) -> ConversationMemoryState:
        async with self._engine.begin() as connection:
            anchor = (
                (
                    await connection.execute(
                        text(
                            """SELECT query_run_uuid, metadata->'conversation_memory' AS memory
                           FROM query_runs
                           WHERE user_uuid = :user_id AND request_type = 'CHAT'
                             AND response_text IS NOT NULL
                             AND metadata->>'conversation_id' = :conversation_id
                           ORDER BY created_at DESC, query_run_uuid DESC
                           LIMIT 1 FOR UPDATE"""
                        ),
                        {"user_id": UUID(user_id), "conversation_id": str(conversation_id)},
                    )
                )
                .mappings()
                .first()
            )
            if anchor is None:
                return ConversationMemoryState()
            current = ConversationMemoryState.from_json(anchor["memory"])
            if current.version != expected_version:
                return current
            result = await connection.execute(
                text(
                    """UPDATE query_runs
                       SET metadata = jsonb_set(COALESCE(metadata, '{}'::jsonb),
                           '{conversation_memory}', CAST(:memory AS jsonb), true)
                       WHERE query_run_uuid = :run_id AND user_uuid = :user_id"""
                ),
                {
                    "memory": json.dumps(state.to_json()),
                    "run_id": anchor["query_run_uuid"],
                    "user_id": UUID(user_id),
                },
            )
            return state if result.rowcount == 1 else current

    async def _load_recent_turns(
        self, user_id: str, conversation_id: UUID
    ) -> list[ConversationTurn]:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                text(
                    """SELECT query_text, response_text FROM query_runs
                       WHERE user_uuid = :user_id AND request_type = 'CHAT'
                         AND response_text IS NOT NULL
                         AND metadata->>'conversation_id' = :conversation_id
                       ORDER BY created_at DESC, query_run_uuid DESC LIMIT :limit"""
                ),
                {
                    "user_id": UUID(user_id),
                    "conversation_id": str(conversation_id),
                    "limit": self._window_turns,
                },
            )
            rows = result.mappings().all()
        turns = [turn for row in reversed(rows) for turn in _turns_from_row(row)]
        return turns[-self._settings.conversation_max_messages :]


def _turns_from_row(row: Any) -> list[ConversationTurn]:
    turns: list[ConversationTurn] = []
    question = (row["query_text"] or "").strip()[-2_000:]
    answer = (row["response_text"] or "").strip()[-2_000:]
    if question:
        turns.append(ConversationTurn(role="user", text=question))
    if answer:
        turns.append(ConversationTurn(role="assistant", text=answer))
    return turns
