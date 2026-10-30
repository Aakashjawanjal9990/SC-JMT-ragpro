"""Build the BM25 index from the citable chunks in the source database.

    python -m app.retrieval.build_bm25

With RETRIEVAL_BM25_BACKEND=postgres (default) the index is written to the BM25
database (BM25_DB_* settings) and swapped in atomically; with "file" it is saved to
RETRIEVAL_BM25_INDEX_PATH. Rebuild after each ingestion run; the retriever logs a
warning at startup when the index is older than the source chunks.
"""

import argparse
import logging
import time

from app.retrieval.bm25_index import BM25Index
from app.retrieval.bm25_pg import PostgresBM25Index
from app.retrieval.config import RetrievalSettings
from app.retrieval.db import Database
from app.retrieval.pg_store import PgVectorStore
from app.retrieval.vector_store import open_vector_store


def main(argv: list[str] | None = None) -> None:
    settings = RetrievalSettings.from_environment()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--backend", default=settings.bm25_backend, choices=("postgres", "file"))
    parser.add_argument("--output", default=settings.bm25_index_path, help="file backend only")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    start = time.perf_counter()
    databases: list[Database] = []
    try:
        if settings.vector_store == "pgvector":
            source_db = Database(settings.source_db, "source database")
            databases.append(source_db)
            source = PgVectorStore(source_db)
        else:
            source = open_vector_store(settings)

        if args.backend == "postgres":
            bm25_db = Database(settings.bm25_db, "BM25 database")
            databases.append(bm25_db)
            meta = PostgresBM25Index.build(source, bm25_db, schema=settings.bm25_schema)
            print(
                f"Indexed {meta['num_chunks']} chunks ({meta['num_postings']} postings) into "
                f"schema {settings.bm25_schema!r} in {meta['seconds']}s"
            )
        else:
            if not args.output:
                parser.error("pass --output or set RETRIEVAL_BM25_INDEX_PATH")
            index = BM25Index.build(source.iter_chunks(), method=settings.bm25_method)
            index.save(args.output)
            print(
                f"Indexed {len(index)} chunks into {args.output} "
                f"in {time.perf_counter() - start:.1f}s"
            )
    finally:
        for database in databases:
            database.close()


if __name__ == "__main__":
    main()
