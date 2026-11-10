"""Read-only source-record verification for the ingestion team's local SQLite export."""

import asyncio
import sqlite3
from collections.abc import Sequence
from datetime import date, datetime

from app.models import ResolvedEvidence, RetrievedPassage, SourceMetadata
from app.ports import SourceRecordPort


class SqliteSourceRecordAdapter(SourceRecordPort):
    """Resolve retrieved passage identities and page provenance from SQLite."""

    def __init__(self, path: str) -> None:
        if not path.strip():
            raise ValueError("A SQLite source-record database path is required.")
        self._path = path

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{self._path}?mode=ro&immutable=1", uri=True)

    def _healthcheck(self) -> bool:
        try:
            with self._connect() as connection:
                return connection.execute("SELECT 1").fetchone() == (1,)
        except sqlite3.Error:
            return False

    async def healthcheck(self) -> bool:
        return await asyncio.to_thread(self._healthcheck)

    def _resolve_many(self, passages: Sequence[RetrievedPassage]) -> list[ResolvedEvidence]:
        resolved: list[ResolvedEvidence] = []
        query = """
            SELECT c.chunk_uuid, c.judgment_uuid, c.source_file_uuid, c.content,
                   c.page_start, c.page_end, j.case_number, j.judgment_date,
                   j.citation_text
            FROM chunks c
            JOIN judgments j ON j.judgment_uuid = c.judgment_uuid
            JOIN source_files sf ON sf.source_file_uuid = c.source_file_uuid
            WHERE c.chunk_uuid = ? AND c.judgment_uuid = ?
              AND c.source_file_uuid = ? AND c.is_active = 1
              AND sf.is_current = 1 AND sf.judgment_uuid = c.judgment_uuid
              AND c.page_start IS NOT NULL AND c.page_end IS NOT NULL
        """
        with self._connect() as connection:
            for passage in passages:
                row = connection.execute(
                    query,
                    (passage.chunk_id, passage.document_id, passage.source_pdf_id),
                ).fetchone()
                if row is None:
                    continue
                (
                    chunk_id,
                    document_id,
                    source_pdf_id,
                    content,
                    page_start,
                    page_end,
                    case_name,
                    decision_date,
                    citation,
                ) = row
                page_rows = connection.execute(
                    """SELECT page_number FROM source_pages
                       WHERE source_file_uuid = ? AND page_number BETWEEN ? AND ?
                       ORDER BY page_number""",
                    (source_pdf_id, page_start, page_end),
                ).fetchall()
                pages = [int(page[0]) for page in page_rows]
                if not pages:
                    continue
                try:
                    parsed_date = date.fromisoformat(decision_date) if decision_date else None
                except (TypeError, ValueError):
                    try:
                        parsed_date = datetime.strptime(decision_date, "%d %B %Y").date()
                    except (TypeError, ValueError):
                        parsed_date = None
                citation_value = (
                    citation if isinstance(citation, str) and len(citation) <= 250 else None
                )
                case_name_value = (
                    case_name if isinstance(case_name, str) and len(case_name) <= 500 else None
                )
                resolved.append(
                    ResolvedEvidence(
                        chunk_id=str(chunk_id),
                        document_id=str(document_id),
                        source_pdf_id=str(source_pdf_id),
                        text=content,
                        original_page_numbers=pages,
                        source=SourceMetadata(
                            case_name=case_name_value,
                            decision_date=parsed_date,
                            citation=citation_value,
                        ),
                    )
                )
        return resolved

    async def resolve_many(
        self, passages: Sequence[RetrievedPassage]
    ) -> Sequence[ResolvedEvidence]:
        return await asyncio.to_thread(self._resolve_many, passages)
