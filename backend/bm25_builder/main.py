"""BM25 index builder — run as a Cloud Run Job after each ingestion run.

Reads every citable chunk from the source database (Cloud SQL `scrag`) and writes
the BM25 index the retrieval module queries (app/retrieval/bm25_pg.py) into the
BM25 database (AlloyDB):

    <schema>.meta(key, value)                      tokenizer_version, corpus stats
    <schema>.chunks(chunk_uuid PK, judgment_uuid, source_file_uuid, judgment_date, doc_len)
    <schema>.postings(term, chunk_uuid, weight)    PK (term, chunk_uuid),
                                                   index (term, weight DESC) INCLUDE (chunk_uuid)

weight = Lucene BM25 contribution: idf(term) * tf * (k1 + 1) / (tf + k1 * (1 - b + b * dl / avgdl)).

Steps: take an advisory lock (one build at a time) -> read chunks twice inside one
REPEATABLE READ snapshot (pass 1: document frequencies and lengths; pass 2: weights,
streamed with COPY into a staging schema) -> build indexes -> atomic swap. The old
index is kept as <schema>_previous for rollback. Exits non-zero on failure so the
Cloud Run Job can retry.
"""

import csv
import importlib.util
import io
import json
import logging
import math
import os
import re
import sys
import time
from collections import Counter
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

BUILDER_VERSION = "1"

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
logger = logging.getLogger("bm25_builder")


def log(message: str, **fields: Any) -> None:
    """One JSON line per event; Cloud Logging parses these as structured logs."""
    logger.info(json.dumps({"severity": fields.pop("severity", "INFO"), "message": message, **fields}, default=str))


def _load_tokenizer():
    """The retrieval module's tokenizer, so index terms match query terms exactly.
    In the image it is copied in as legal_tokenizer.py; locally it is read from the repo."""
    try:
        import legal_tokenizer  # type: ignore[import-not-found]

        return legal_tokenizer
    except ImportError:
        path = Path(__file__).resolve().parents[1] / "app" / "retrieval" / "tokenizer.py"
        spec = importlib.util.spec_from_file_location("legal_tokenizer", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


tokenizer = _load_tokenizer()

# --- configuration -------------------------------------------------------------------


def env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


SCHEMA = env("BM25_SCHEMA", "bm25")  # must match RETRIEVAL_BM25_SCHEMA in the backend
K1 = float(env("BM25_K1", "1.2"))
B = float(env("BM25_B", "0.75"))
BATCH_SIZE = int(env("BM25_BATCH_SIZE", "5000"))
COPY_BATCH_ROWS = int(env("BM25_COPY_BATCH_ROWS", "200000"))
# Refuse to replace an index with one this much smaller (protects against a bad or
# empty source read). Set BM25_ALLOW_SHRINK=true to override deliberately.
MIN_KEEP_RATIO = float(env("BM25_MIN_KEEP_RATIO", "0.5"))
ALLOW_SHRINK = (env("BM25_ALLOW_SHRINK", "false") or "").lower() in {"1", "true", "yes"}
LOCK_KEY = 726_311_438  # advisory lock id for "bm25 build"

if not re.fullmatch(r"[a-z_][a-z0-9_]{0,40}", SCHEMA or ""):
    raise SystemExit(f"Invalid BM25_SCHEMA: {SCHEMA!r}")
STAGING, PREVIOUS = f"{SCHEMA}_build", f"{SCHEMA}_previous"


def connect(prefix: str):
    """Connect using {prefix}DB_NAME/DB_USER/DB_PASSWORD and one of
    {prefix}DB_HOST (direct / Auth Proxy), {prefix}INSTANCE_CONNECTION_NAME (Cloud SQL)
    or {prefix}INSTANCE_URI (AlloyDB). Returns (connection, connector or None)."""
    name, user = env(f"{prefix}DB_NAME"), env(f"{prefix}DB_USER")
    password = os.getenv(f"{prefix}DB_PASSWORD") or None
    iam = (env(f"{prefix}DB_IAM_AUTH", "false") or "").lower() in {"1", "true", "yes"}
    ip_type = (env(f"{prefix}DB_IP_TYPE", "private") or "private").upper()
    host = env(f"{prefix}DB_HOST")
    if not name:
        raise SystemExit(f"{prefix}DB_NAME is not set")
    if host:
        import pg8000.dbapi

        port = int(env(f"{prefix}DB_PORT", "5432"))
        address = {"unix_sock": f"{host}/.s.PGSQL.{port}"} if host.startswith("/") else {"host": host, "port": port}
        return pg8000.dbapi.connect(user=user, password=password, database=name, **address), None
    if icn := env(f"{prefix}INSTANCE_CONNECTION_NAME"):
        from google.cloud.sql.connector import Connector, IPTypes
        instance = icn
    elif uri := env(f"{prefix}INSTANCE_URI"):
        from google.cloud.alloydb.connector import Connector, IPTypes
        instance = uri
    else:
        raise SystemExit(f"Set {prefix}DB_HOST, {prefix}INSTANCE_CONNECTION_NAME or {prefix}INSTANCE_URI")
    connector = Connector()
    connection = connector.connect(
        instance, "pg8000", user=user, password=None if iam else password, db=name,
        ip_type=IPTypes[ip_type], enable_iam_auth=iam,
    )
    return connection, connector


# --- source reading (Cloud SQL) --------------------------------------------------------

# Must match the retrieval module's "citable" rule (app/retrieval/pg_store.py). The
# database has only the chunks table, so there is no judgment date (written as NULL).
_SOURCE_SQL = """
    SELECT c.chunk_uuid::text, c.judgment_uuid::text, c.source_file_uuid::text,
           NULL::date AS judgment_date, c.content
    FROM chunks c
    WHERE c.is_active
      AND c.chunk_uuid > %s::uuid
    ORDER BY c.chunk_uuid
    LIMIT %s"""


def iter_source_chunks(cursor) -> Iterator[tuple[str, str, str, Any, str]]:
    after = "00000000-0000-0000-0000-000000000000"
    while True:
        cursor.execute(_SOURCE_SQL, (after, BATCH_SIZE))
        rows = cursor.fetchall()
        if not rows:
            return
        yield from rows
        after = rows[-1][0]


# --- target writing (AlloyDB) ------------------------------------------------------------


def copy_rows(connection, table: str, rows: Iterable[tuple[Any, ...]]) -> int:
    """Stream rows into `table` with COPY in batches; returns the row count."""
    count, buffer = 0, io.StringIO()
    writer = csv.writer(buffer)
    cursor = connection.cursor()

    def flush() -> None:
        if buffer.tell():
            buffer.seek(0)
            cursor.execute(f"COPY {table} FROM STDIN WITH (FORMAT csv)", stream=buffer)
            buffer.seek(0)
            buffer.truncate()

    for row in rows:
        writer.writerow(["" if value is None else value for value in row])  # "" -> NULL
        count += 1
        if count % COPY_BATCH_ROWS == 0:
            flush()
    flush()
    connection.commit()
    return count


def existing_chunk_count(cursor) -> int | None:
    cursor.execute("SELECT to_regclass(%s)", (f"{SCHEMA}.meta",))
    if cursor.fetchone()[0] is None:
        return None
    cursor.execute(f"SELECT value FROM {SCHEMA}.meta WHERE key = 'num_chunks'")
    row = cursor.fetchone()
    return int(row[0]) if row else 0


def idf(df: int, n: int) -> float:
    return math.log(1 + (n - df + 0.5) / (df + 0.5))  # Lucene variant, always > 0


def build() -> dict[str, Any]:
    started = time.perf_counter()
    source, source_connector = connect("")
    target, target_connector = connect("BM25_")
    try:
        tcur = target.cursor()
        tcur.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,))
        if not tcur.fetchone()[0]:
            raise SystemExit("Another BM25 build is running; exiting")
        target.commit()
        previous_count = existing_chunk_count(tcur)
        target.commit()

        # One consistent snapshot of the source for both passes.
        scur = source.cursor()
        scur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        scur.execute("SET LOCAL statement_timeout = 0")

        # Pass 1: document frequencies and lengths.
        df: Counter[str] = Counter()
        chunk_rows: list[tuple[Any, ...]] = []
        total_len = 0
        for chunk_uuid, judgment_uuid, source_file_uuid, judgment_date, content in iter_source_chunks(scur):
            tokens = tokenizer.tokenize(content or "")
            df.update(set(tokens))
            total_len += len(tokens)
            chunk_rows.append((chunk_uuid, judgment_uuid, source_file_uuid, judgment_date, len(tokens)))
        n = len(chunk_rows)
        avgdl = (total_len / n) if n else 1.0
        avgdl = avgdl or 1.0
        log("pass 1 complete", chunks=n, terms=len(df), avgdl=round(avgdl, 2))

        if previous_count and n < previous_count * MIN_KEEP_RATIO and not ALLOW_SHRINK:
            raise SystemExit(
                f"Refusing to replace an index of {previous_count} chunks with {n} "
                f"(below BM25_MIN_KEEP_RATIO={MIN_KEEP_RATIO}); set BM25_ALLOW_SHRINK=true to override"
            )

        # Staging schema.
        tcur.execute("SET statement_timeout = 0")
        tcur.execute(f"DROP SCHEMA IF EXISTS {STAGING} CASCADE")
        tcur.execute(f"CREATE SCHEMA {STAGING}")
        tcur.execute(f"CREATE TABLE {STAGING}.meta (key text PRIMARY KEY, value text NOT NULL)")
        tcur.execute(
            f"""CREATE TABLE {STAGING}.chunks (
                chunk_uuid uuid PRIMARY KEY, judgment_uuid uuid NOT NULL,
                source_file_uuid uuid NOT NULL, judgment_date date, doc_len int NOT NULL)"""
        )
        tcur.execute(f"CREATE TABLE {STAGING}.postings (term text NOT NULL, chunk_uuid uuid NOT NULL, weight real NOT NULL)")
        target.commit()
        copy_rows(target, f"{STAGING}.chunks", chunk_rows)
        del chunk_rows

        # Pass 2: weights (same snapshot, so every term is in df).
        def postings() -> Iterator[tuple[str, str, float]]:
            for chunk_uuid, _, _, _, content in iter_source_chunks(scur):
                tokens = tokenizer.tokenize(content or "")
                norm = K1 * (1 - B + B * len(tokens) / avgdl)
                for term, tf in Counter(tokens).items():
                    yield term, chunk_uuid, idf(df[term], n) * tf * (K1 + 1) / (tf + norm)

        num_postings = copy_rows(target, f"{STAGING}.postings", postings())
        source.rollback()  # end the read-only snapshot
        log("pass 2 complete", postings=num_postings)

        meta = {
            "tokenizer_version": tokenizer.TOKENIZER_VERSION,
            "builder_version": BUILDER_VERSION,
            "num_chunks": n,
            "num_terms": len(df),
            "num_postings": num_postings,
            "avgdl": f"{avgdl:.4f}",
            "k1": K1,
            "b": B,
            "idf": "lucene",
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        copy_rows(target, f"{STAGING}.meta", meta.items())

        tcur.execute("SET maintenance_work_mem = '1GB'")
        tcur.execute(f"ALTER TABLE {STAGING}.postings ADD PRIMARY KEY (term, chunk_uuid)")
        tcur.execute(f"CREATE INDEX ON {STAGING}.postings (term, weight DESC) INCLUDE (chunk_uuid)")
        tcur.execute(f"CREATE INDEX ON {STAGING}.chunks (judgment_uuid)")
        tcur.execute(f"CREATE INDEX ON {STAGING}.chunks (judgment_date)")
        tcur.execute(f"ANALYZE {STAGING}.postings")
        tcur.execute(f"ANALYZE {STAGING}.chunks")
        target.commit()
        log("indexes built")

        # Atomic swap; keep the old index for rollback.
        tcur.execute(f"DROP SCHEMA IF EXISTS {PREVIOUS} CASCADE")
        if previous_count is not None:
            tcur.execute(f"ALTER SCHEMA {SCHEMA} RENAME TO {PREVIOUS}")
        tcur.execute(f"ALTER SCHEMA {STAGING} RENAME TO {SCHEMA}")
        target.commit()
        meta["seconds"] = round(time.perf_counter() - started)
        return meta
    finally:
        for connection in (source, target):
            try:
                connection.close()
            except Exception:
                pass
        for connector in (source_connector, target_connector):
            if connector is not None:
                connector.close()


def main() -> None:
    try:
        meta = build()
    except SystemExit as exc:
        log(str(exc), severity="ERROR")
        raise SystemExit(1) from None
    except Exception as exc:
        log("BM25 build failed", severity="ERROR", error=repr(exc))
        raise
    log("BM25 index swapped in", schema=SCHEMA, **meta)


if __name__ == "__main__":
    main()
