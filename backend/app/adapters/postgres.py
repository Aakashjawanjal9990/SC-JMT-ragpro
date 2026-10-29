"""PostgreSQL hybrid retrieval and citation-source adapter for Cloud SQL chunks."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.models import (
    QueryFilters,
    ResolvedEvidence,
    RetrievedPassage,
    SourceMetadata,
)
from app.ports import (
    DependencyUnavailable,
    RetrievalPort,
    SourceRecordPort,
    UnsupportedQueryFilter,
)


class PostgresQueryAdapter(RetrievalPort, SourceRecordPort):
    """AlloyDB native BM25 search over ``chunks.content`` and citation verification."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def healthcheck(self) -> bool:
        try:
            async with self._engine.connect() as connection:
                return bool(await connection.scalar(text("SELECT 1")))
        except SQLAlchemyError:
            return False

    async def retrieve(
        self, question: str, filters: QueryFilters, limit: int
    ) -> Sequence[RetrievedPassage]:
        if filters.case_name:
            # The live schema has case_number and citation_text, but no trusted
            # case_name field. Do not silently ignore this filter.
            raise UnsupportedQueryFilter("case_name")
        query = text(
            """
            WITH ranked AS (
                SELECT c.chunk_uuid AS chunk_id,
                       c.content <@> to_bm25query(:question, 'idx_chunks_bm25') AS bm25_score
                FROM chunks AS c
                JOIN judgments AS j ON j.judgment_uuid = c.judgment_uuid
                JOIN source_files AS sf ON sf.source_file_uuid = c.source_file_uuid
                WHERE c.is_active
                  AND sf.is_current
                  AND sf.judgment_uuid = c.judgment_uuid
                  AND c.page_start IS NOT NULL
                  AND c.page_end IS NOT NULL
                  AND (:date_from IS NULL OR j.judgment_date >= :date_from)
                  AND (:date_to IS NULL OR j.judgment_date <= :date_to)
                  AND (cardinality(CAST(:document_ids AS uuid[])) = 0
                       OR j.judgment_uuid = ANY(CAST(:document_ids AS uuid[])))
                ORDER BY c.content <@> to_bm25query(:question, 'idx_chunks_bm25') ASC
                LIMIT :candidate_limit
            )
            SELECT c.chunk_uuid::text AS chunk_id,
                   c.judgment_uuid::text AS document_id,
                   c.source_file_uuid::text AS source_pdf_id,
                   c.content AS text,
                   array_agg(sp.page_number ORDER BY sp.page_number) AS pages,
                   -ranked.bm25_score::float8 AS score
            FROM chunks AS c
            JOIN judgments AS j ON j.judgment_uuid = c.judgment_uuid
            JOIN source_files AS sf ON sf.source_file_uuid = c.source_file_uuid
            JOIN source_pages AS sp
              ON sp.source_file_uuid = c.source_file_uuid
             AND sp.page_number BETWEEN c.page_start AND c.page_end
            JOIN ranked ON ranked.chunk_id = c.chunk_uuid
            WHERE c.is_active
              AND sf.is_current
              AND sf.judgment_uuid = c.judgment_uuid
            GROUP BY c.chunk_uuid, c.judgment_uuid, c.source_file_uuid, c.content,
                     ranked.bm25_score
            ORDER BY ranked.bm25_score ASC, c.chunk_uuid
            LIMIT :limit
            """
        )
        try:
            document_ids = [UUID(value) for value in filters.document_ids]
        except ValueError:
            return []
        try:
            async with self._engine.connect() as connection:
                rows = (
                    (
                        await connection.execute(
                            query,
                            {
                                "question": question,
                                "date_from": filters.date_from,
                                "date_to": filters.date_to,
                                "document_ids": document_ids,
                                "candidate_limit": max(limit * 10, 100),
                                "limit": limit,
                            },
                        )
                    )
                    .mappings()
                    .all()
                )
        except SQLAlchemyError as exc:
            raise DependencyUnavailable("PostgreSQL retrieval query failed.") from exc
        return [
            RetrievedPassage(
                chunk_id=row["chunk_id"],
                document_id=row["document_id"],
                source_pdf_id=row["source_pdf_id"],
                text=row["text"],
                original_page_numbers=list(row["pages"]),
                score=row["score"],
                rank=rank,
            )
            for rank, row in enumerate(rows, start=1)
        ]

    async def resolve_many(
        self, passages: Sequence[RetrievedPassage]
    ) -> Sequence[ResolvedEvidence]:
        identities: list[tuple[UUID, UUID, UUID]] = []
        for passage in passages:
            try:
                identities.append(
                    (
                        UUID(passage.chunk_id),
                        UUID(passage.document_id),
                        UUID(passage.source_pdf_id),
                    )
                )
            except ValueError:
                continue
        if not identities:
            return []

        identity_params = {
            "chunk_ids": [identity[0] for identity in identities],
            "document_ids": [identity[1] for identity in identities],
            "source_pdf_ids": [identity[2] for identity in identities],
        }

        query = text(
            """
                WITH requested AS (
                    SELECT * FROM unnest(
                        CAST(:chunk_ids AS uuid[]),
                        CAST(:document_ids AS uuid[]),
                        CAST(:source_pdf_ids AS uuid[])
                    ) AS ids(chunk_id, document_id, source_pdf_id)
                )
                SELECT c.chunk_uuid::text AS chunk_id,
                       c.judgment_uuid::text AS document_id,
                       c.source_file_uuid::text AS source_pdf_id,
                       c.content AS text,
                       array_agg(sp.page_number ORDER BY sp.page_number) AS pages,
                       NULL::text AS case_name,
                       NULL::text AS judgment_title,
                       j.judgment_date AS decision_date,
                       j.citation_text AS citation
                FROM chunks AS c
                JOIN judgments AS j ON j.judgment_uuid = c.judgment_uuid
                JOIN source_files AS sf ON sf.source_file_uuid = c.source_file_uuid
                JOIN source_pages AS sp
                  ON sp.source_file_uuid = c.source_file_uuid
                 AND sp.page_number BETWEEN c.page_start AND c.page_end
                JOIN requested AS ids
                  ON ids.chunk_id = c.chunk_uuid
                 AND ids.document_id = c.judgment_uuid
                 AND ids.source_pdf_id = c.source_file_uuid
                WHERE c.is_active
                  AND sf.is_current
                  AND sf.judgment_uuid = c.judgment_uuid
                  AND c.page_start IS NOT NULL
                  AND c.page_end IS NOT NULL
                GROUP BY c.chunk_uuid, c.judgment_uuid, c.source_file_uuid,
                         c.content, j.judgment_date, j.citation_text
                """
        )
        try:
            async with self._engine.connect() as connection:
                rows = (await connection.execute(query, identity_params)).mappings().all()
        except SQLAlchemyError as exc:
            raise DependencyUnavailable("PostgreSQL source resolution failed.") from exc
        return [
            ResolvedEvidence(
                chunk_id=row["chunk_id"],
                document_id=row["document_id"],
                source_pdf_id=row["source_pdf_id"],
                text=row["text"],
                original_page_numbers=list(row["pages"]),
                source=SourceMetadata(
                    case_name=row["case_name"],
                    judgment_title=row["judgment_title"],
                    decision_date=row["decision_date"],
                    citation=row["citation"],
                ),
            )
            for row in rows
        ]
