"""BM25 index builder — run as a Cloud Run Job after each ingestion run.

Reads every citable chunk from the vector database (databases.vector in config.yaml)
and writes the BM25 index the retrieval module queries (app/retrieval/bm25_pg.py)
into the BM25 database (AlloyDB, databases.bm25):

    <schema>.meta(key, value)                      tokenizer_version, corpus stats
    <schema>.chunks(chunk_uuid PK, judgment_uuid, source_file_uuid, judgment_date,
                    page_start, page_end, content, doc_len)
                                                   the backend returns text/pages from here
    <schema>.postings(term, chunk_uuid, weight)    PK (term, chunk_uuid),
                                                   index (term, weight DESC) INCLUDE (chunk_uuid)
    <schema>.terms(term PK, df)                    chunks containing each term (idf for
                                                   pseudo-relevance feedback)

weight = Lucene BM25 contribution: idf(term) * tf * (k1 + 1) / (tf + k1 * (1 - b + b * dl / avgdl)).

Steps: take an advisory lock (one build at a time) -> read chunks three times inside
one REPEATABLE READ snapshot (pass 1: document frequencies and lengths; pass 2: chunk
rows; pass 3: weights; both streamed with COPY into a staging schema) -> build
indexes -> atomic swap. The old
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

import yaml

BUILDER_VERSION = "3"
INDEX_FORMAT = "3"  # chunks carries content and pages, plus terms(df); must match app/retrieval/bm25_pg.py

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

# --- configuration (config.yaml, shared with app/retrieval/config.py) ----------------------


def _config_path() -> Path:
    """$RETRIEVAL_CONFIG, else config.yaml next to this file (image) or at the repo root."""
    if path := os.getenv("RETRIEVAL_CONFIG"):
        return Path(path)
    here = Path(__file__).resolve().parent
    return next((p for p in (here / "config.yaml", here.parent / "config.yaml") if p.exists()), here / "config.yaml")


def _expand_env(value: Any) -> Any:
    """Replace ${NAME} with the environment variable NAME (same rule as the backend)."""
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, str):
        def lookup(match: re.Match) -> str:
            if not (found := os.getenv(match.group(1))):
                raise SystemExit(f"config references ${{{match.group(1)}}} but it is not set")
            return found

        return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", lookup, value)
    return value


def _as_bool(value: Any) -> bool:
    return value if isinstance(value, bool) else str(value).strip().lower() in {"1", "true", "yes", "on"}


with _config_path().open(encoding="utf-8") as _handle:
    CONFIG = _expand_env(yaml.safe_load(_handle) or {})
try:
    VECTOR_DB = CONFIG["databases"]["vector"]  # source: chunks
    BM25_DB = CONFIG["databases"]["bm25"]  # target: the BM25 index
    SCHEMA = str(CONFIG["bm25"]["schema"])
    _build = CONFIG["bm25_builder"]
    K1 = float(_build["k1"])
    B = float(_build["b"])
    BATCH_SIZE = int(_build["batch_size"])
    COPY_BATCH_ROWS = int(_build["copy_batch_rows"])
    # Refuse to replace an index with one this much smaller (protects against a bad or
    # empty source read). Set allow_shrink: true to override deliberately.
    MIN_KEEP_RATIO = float(_build["min_keep_ratio"])
    ALLOW_SHRINK = _as_bool(_build["allow_shrink"])
except (KeyError, TypeError) as exc:
    raise SystemExit(f"config.yaml is missing {exc}") from None
LOCK_KEY = 726_311_438  # advisory lock id for "bm25 build"

if not re.fullmatch(r"[a-z_][a-z0-9_]{0,40}", SCHEMA):
    raise SystemExit(f"Invalid bm25.schema: {SCHEMA!r}")
STAGING, PREVIOUS = f"{SCHEMA}_build", f"{SCHEMA}_previous"


def connect(db: dict[str, Any], label: str):
    """Connect with a databases.<label> entry: name/user/password and one of host
    (direct / Auth Proxy), instance_connection_name (Cloud SQL) or instance_uri
    (AlloyDB). Returns (connection, connector or None)."""
    name, user, password = db.get("name"), db.get("user"), db.get("password") or None
    iam = _as_bool(db.get("iam_auth", False))
    ip_type = str(db.get("ip_type", "private")).upper()
    if not name:
        raise SystemExit(f"databases.{label}.name is not set")
    if host := db.get("host"):
        import pg8000.dbapi

        port = int(db.get("port", 5432))
        address = {"unix_sock": f"{host}/.s.PGSQL.{port}"} if host.startswith("/") else {"host": host, "port": port}
        return pg8000.dbapi.connect(user=user, password=password, database=name, **address), None
    if icn := db.get("instance_connection_name"):
        from google.cloud.sql.connector import Connector, IPTypes
        instance = icn
    elif uri := db.get("instance_uri"):
        from google.cloud.alloydb.connector import Connector, IPTypes
        instance = uri
    else:
        raise SystemExit(f"Set host, instance_connection_name or instance_uri in databases.{label}")
    connector = Connector()
    connection = connector.connect(
        instance, "pg8000", user=user, password=None if iam else password, db=name,
        ip_type=IPTypes[ip_type], enable_iam_auth=iam,
    )
    return connection, connector


# --- source reading (vector database) --------------------------------------------------------

# Must match the retrieval module's "citable" rule (app/retrieval/pg_store.py). The
# database has only the chunks table, so there is no judgment date (written as NULL).
_SOURCE_SQL = """
    SELECT c.chunk_uuid::text, c.judgment_uuid::text, c.source_file_uuid::text,
           NULL::date AS judgment_date, c.page_start, c.page_end, c.content
    FROM chunks c
    WHERE c.is_active
      AND c.chunk_uuid > %s::uuid
    ORDER BY c.chunk_uuid
    LIMIT %s"""


def iter_source_chunks(cursor) -> Iterator[tuple[str, str, str, Any, int, int, str]]:
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
    source, source_connector = connect(VECTOR_DB, "vector")
    target, target_connector = connect(BM25_DB, "bm25")
    try:
        tcur = target.cursor()
        tcur.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,))
        if not tcur.fetchone()[0]:
            raise SystemExit("Another BM25 build is running; exiting")
        target.commit()
        previous_count = existing_chunk_count(tcur)
        target.commit()

        # One consistent snapshot of the source for all passes.
        scur = source.cursor()
        scur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        scur.execute("SET LOCAL statement_timeout = 0")

        # Pass 1: document frequencies and lengths.
        df: Counter[str] = Counter()
        doc_lens: dict[str, int] = {}
        for chunk_uuid, *_, content in iter_source_chunks(scur):
            tokens = tokenizer.tokenize(content or "")
            df.update(set(tokens))
            doc_lens[chunk_uuid] = len(tokens)
        n = len(doc_lens)
        total_len = sum(doc_lens.values())
        avgdl = (total_len / n) if n else 1.0
        avgdl = avgdl or 1.0
        log("pass 1 complete", chunks=n, terms=len(df), avgdl=round(avgdl, 2))

        if previous_count and n < previous_count * MIN_KEEP_RATIO and not ALLOW_SHRINK:
            raise SystemExit(
                f"Refusing to replace an index of {previous_count} chunks with {n} "
                f"(below bm25_builder.min_keep_ratio={MIN_KEEP_RATIO}); set allow_shrink: true to override"
            )

        # Staging schema.
        tcur.execute("SET statement_timeout = 0")
        tcur.execute(f"DROP SCHEMA IF EXISTS {STAGING} CASCADE")
        tcur.execute(f"CREATE SCHEMA {STAGING}")
        tcur.execute(f"CREATE TABLE {STAGING}.meta (key text PRIMARY KEY, value text NOT NULL)")
        tcur.execute(
            f"""CREATE TABLE {STAGING}.chunks (
                chunk_uuid uuid PRIMARY KEY, judgment_uuid uuid NOT NULL,
                source_file_uuid uuid NOT NULL, judgment_date date,
                page_start int, page_end int, content text, doc_len int NOT NULL)"""
        )
        tcur.execute(f"CREATE TABLE {STAGING}.postings (term text NOT NULL, chunk_uuid uuid NOT NULL, weight real NOT NULL)")
        tcur.execute(f"CREATE TABLE {STAGING}.terms (term text PRIMARY KEY, df int NOT NULL)")
        target.commit()

        # Pass 2: chunk rows with text and pages, streamed (not held in memory).
        copy_rows(
            target,
            f"{STAGING}.chunks",
            (
                (*row, doc_lens[row[0]])
                for row in iter_source_chunks(scur)
            ),
        )
        del doc_lens
        log("pass 2 complete", chunks=n)

        # Pass 3: weights (same snapshot, so every term is in df).
        def postings() -> Iterator[tuple[str, str, float]]:
            for chunk_uuid, *_, content in iter_source_chunks(scur):
                tokens = tokenizer.tokenize(content or "")
                norm = K1 * (1 - B + B * len(tokens) / avgdl)
                for term, tf in Counter(tokens).items():
                    yield term, chunk_uuid, idf(df[term], n) * tf * (K1 + 1) / (tf + norm)

        num_postings = copy_rows(target, f"{STAGING}.postings", postings())
        source.rollback()  # end the read-only snapshot
        log("pass 3 complete", postings=num_postings)

        meta = {
            "tokenizer_version": tokenizer.TOKENIZER_VERSION,
            "builder_version": BUILDER_VERSION,
            "index_format": INDEX_FORMAT,
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
        copy_rows(target, f"{STAGING}.terms", df.items())

        tcur.execute("SET maintenance_work_mem = '1GB'")
        tcur.execute(f"ALTER TABLE {STAGING}.postings ADD PRIMARY KEY (term, chunk_uuid)")
        tcur.execute(f"CREATE INDEX ON {STAGING}.postings (term, weight DESC) INCLUDE (chunk_uuid)")
        tcur.execute(f"ANALYZE {STAGING}.postings")
        tcur.execute(f"ANALYZE {STAGING}.chunks")
        tcur.execute(f"ANALYZE {STAGING}.terms")
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
