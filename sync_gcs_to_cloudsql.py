"""
Sync Deliverables from Google Cloud Storage into Cloud SQL PostgreSQL (scrag)
Reads pre-computed chunks and embeddings from:
    gs://sc-judgments-rag-prod-data/deliverables/chunks/year=YYYY/
and loads them directly into Cloud SQL PostgreSQL without re-parsing or re-embedding.

Usage:
    python sync_gcs_to_cloudsql.py --db "postgresql://postgres:PASSWORD@34.93.53.176:5432/scrag"
    python sync_gcs_to_cloudsql.py --db "postgresql://postgres:PASSWORD@34.93.53.176:5432/scrag" --year 1950
"""

import os
import sys
import json
import uuid
import hashlib
import logging
import argparse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, List, Optional

import sqlalchemy as sa
from sqlalchemy import create_engine, text
from google.cloud import storage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s"
)
logger = logging.getLogger("GCSToCloudSQLSync")


class GCSCloudSQLSyncer:
    def __init__(self, db_url: str, bucket_name: str = "sc-judgments-rag-prod-data"):
        self.db_url = db_url
        self.bucket_name = bucket_name
        self.engine = create_engine(
            self.db_url,
            pool_size=10,
            max_overflow=20,
            pool_recycle=1800,
            pool_pre_ping=True
        )
        self.storage_client = storage.Client()
        self.bucket = self.storage_client.bucket(self.bucket_name)
        self.verify_connection()

    def verify_connection(self):
        """Verifies database connectivity, aligns schema with pgvector, and registers embedding model."""
        with self.engine.begin() as conn:
            res = conn.execute(text("SELECT current_database(), current_user;")).fetchone()
            logger.info(f"Connected to Cloud SQL PostgreSQL: DB='{res[0]}', User='{res[1]}'")
            
            # 1. Enable pgvector extension
            try:
                conn.execute(text('CREATE EXTENSION IF NOT EXISTS "vector";'))
                logger.info("Extension 'vector' enabled successfully.")
            except Exception as e:
                logger.warning(f"Could not enable extension 'vector': {e}")

            # 2. Add pgvector and chunk_id columns to chunks table if missing from Anuradha's schema
            try:
                conn.execute(text('ALTER TABLE chunks ADD COLUMN IF NOT EXISTS chunk_id TEXT;'))
                conn.execute(text('ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding VECTOR(768);'))
                conn.execute(text('CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);'))
                logger.info("Verified chunks table: chunk_id and embedding VECTOR(768) columns are ready.")
            except Exception as e:
                logger.warning(f"Schema alignment note for chunks: {e}")

            # 3. Seed Vertex AI text-embedding-004 in embedding_models
            try:
                model_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, "scj.model.vertex_ai.text-embedding-004.v1"))
                conn.execute(text("""
                    INSERT INTO embedding_models (
                        embedding_model_uuid, model_id, provider, version, dimensions, is_active
                    ) VALUES (
                        :m_uuid, 'text-embedding-004', 'vertex_ai', 'v1', 768, TRUE
                    )
                    ON CONFLICT (provider, model_id, version) DO NOTHING;
                """), {"m_uuid": model_uuid})
                logger.info("Registered 'text-embedding-004' (768 dimensions) in embedding_models.")
            except Exception as e:
                logger.warning(f"Model registration note: {e}")

    def load_single_chunk_file(self, blob_name: str) -> Dict[str, Any]:
        """Downloads a single _chunks.json from GCS and inserts into Cloud SQL."""
        try:
            blob = self.bucket.blob(blob_name)
            chunks_text = blob.download_as_text(encoding="utf-8")
            chunks_data = json.loads(chunks_text)

            if not chunks_data or not isinstance(chunks_data, list):
                return {"status": "EMPTY", "blob": blob_name, "chunks": 0}

            first_chunk = chunks_data[0]
            doc_id = first_chunk.get("document_id") or Path(blob_name).stem.replace("_chunks", "")
            case_number = first_chunk.get("case_number")
            appellant = first_chunk.get("appellant")
            respondent = first_chunk.get("respondent")
            date_of_judgment = first_chunk.get("date_of_judgment")
            judges = first_chunk.get("judges", [])
            
            # Generate deterministic UUIDs
            judgment_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"scj.judgment.{doc_id}"))
            source_file_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"scj.file.{doc_id}"))
            
            with self.engine.begin() as conn:
                # 1. Upsert judgments
                upsert_judgment = text("""
                    INSERT INTO judgments (
                        judgment_uuid, judgment_id, court, case_number,
                        citation_text, legal_status, is_primary
                    ) VALUES (
                        :j_uuid, :doc_id, 'Supreme Court of India', :case_number,
                        :citation_text, 'REPORTED', TRUE
                    )
                    ON CONFLICT (judgment_id) DO UPDATE SET
                        case_number = COALESCE(EXCLUDED.case_number, judgments.case_number),
                        updated_at = CURRENT_TIMESTAMP;
                """)
                conn.execute(upsert_judgment, {
                    "j_uuid": judgment_uuid,
                    "doc_id": doc_id,
                    "case_number": case_number,
                    "citation_text": f"{appellant} v. {respondent}" if appellant and respondent else doc_id
                })

                # 2. Upsert source_files
                upsert_file = text("""
                    INSERT INTO source_files (
                        source_file_uuid, judgment_uuid, version_number, is_current,
                        original_filename, storage_path, page_count, ingestion_status,
                        pipeline_version
                    ) VALUES (
                        :s_uuid, :j_uuid, 'v1.0', TRUE,
                        :filename, :storage_path, :page_count, 'COMPLETED',
                        'scj-pipeline-gcs-sync'
                    )
                    ON CONFLICT (judgment_uuid, version_number) DO UPDATE SET
                        storage_path = EXCLUDED.storage_path,
                        updated_at = CURRENT_TIMESTAMP;
                """)
                conn.execute(upsert_file, {
                    "s_uuid": source_file_uuid,
                    "j_uuid": judgment_uuid,
                    "filename": f"{doc_id}.pdf",
                    "storage_path": f"gs://{self.bucket_name}/{blob_name}",
                    "page_count": max((c.get("original_page_number", 1) for c in chunks_data), default=1)
                })

                # 3. Clean previous chunks for this file
                conn.execute(text("DELETE FROM chunks WHERE source_file_uuid = :s_uuid"), {"s_uuid": source_file_uuid})

                # 4. Insert chunks with embeddings
                insert_chunk_sql = text("""
                    INSERT INTO chunks (
                        chunk_uuid, chunk_id, judgment_uuid, source_file_uuid,
                        chunk_index, page_start, page_end, content, section,
                        metadata, content_sha256, is_active, embedding
                    ) VALUES (
                        :c_uuid, :chunk_id, :j_uuid, :s_uuid,
                        :chunk_index, :page_start, :page_end, :content, :section,
                        :metadata, :content_sha256, TRUE, :embedding
                    )
                    ON CONFLICT (source_file_uuid, chunk_index) DO UPDATE SET
                        content = EXCLUDED.content,
                        embedding = EXCLUDED.embedding,
                        updated_at = CURRENT_TIMESTAMP;
                """)

                chunk_records = []
                chunk_embeddings_records = []
                model_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, "scj.model.vertex_ai.text-embedding-004.v1"))

                for idx, c in enumerate(chunks_data, 1):
                    c_id = c.get("chunk_id") or f"{doc_id}_c{idx}"
                    c_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"scj.chunk.{c_id}"))
                    c_hash = hashlib.sha256(c["content"].encode("utf-8")).hexdigest()
                    page_num = c.get("original_page_number", 1)
                    
                    emb_val = c.get("embedding")
                    emb_str = json.dumps(emb_val) if isinstance(emb_val, list) else None

                    meta = {
                        "case_number": case_number,
                        "appellant": appellant,
                        "respondent": respondent,
                        "judges": judges,
                        "date_of_judgment": date_of_judgment
                    }

                    chunk_records.append({
                        "c_uuid": c_uuid,
                        "chunk_id": c_id,
                        "j_uuid": judgment_uuid,
                        "s_uuid": source_file_uuid,
                        "chunk_index": idx,
                        "page_start": page_num,
                        "page_end": page_num,
                        "content": c["content"],
                        "section": c.get("section_heading"),
                        "metadata": json.dumps(meta),
                        "content_sha256": c_hash,
                        "embedding": emb_str
                    })

                    if emb_val:
                        chunk_embeddings_records.append({
                            "ce_uuid": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"scj.embed.{c_id}")),
                            "chunk_uuid": c_uuid,
                            "model_uuid": model_uuid,
                            "provider": "vertex_ai",
                            "vector_store": "pgvector",
                            "sync_status": "SYNCED"
                        })

                # Execute in batch
                for batch_start in range(0, len(chunk_records), 200):
                    conn.execute(insert_chunk_sql, chunk_records[batch_start:batch_start + 200])

                # 5. Populate chunk_embeddings registry
                if chunk_embeddings_records:
                    insert_ce_sql = text("""
                        INSERT INTO chunk_embeddings (
                            chunk_embedding_uuid, chunk_uuid, embedding_model_uuid,
                            provider, vector_store, sync_status
                        ) VALUES (
                            :ce_uuid, :chunk_uuid, :model_uuid,
                            :provider, :vector_store, :sync_status
                        )
                        ON CONFLICT (chunk_uuid, embedding_model_uuid) DO NOTHING;
                    """)
                    for b_start in range(0, len(chunk_embeddings_records), 200):
                        conn.execute(insert_ce_sql, chunk_embeddings_records[b_start:b_start + 200])

            return {"status": "SUCCESS", "blob": blob_name, "chunks": len(chunks_data)}
        except Exception as e:
            logger.error(f"Error loading {blob_name}: {e}")
            return {"status": "FAILED", "blob": blob_name, "error": str(e)}

    def sync_all(self, year: Optional[int] = None, workers: int = 8):
        """Scans GCS deliverables and syncs into Cloud SQL."""
        prefix = f"deliverables/chunks/year={year}/" if year else "deliverables/chunks/"
        logger.info(f"Listing chunk files from gs://{self.bucket_name}/{prefix}...")

        blobs = [
            b.name for b in self.storage_client.list_blobs(self.bucket_name, prefix=prefix)
            if b.name.endswith("_chunks.json")
        ]

        logger.info(f"Found {len(blobs)} chunk JSON files in GCS. Starting parallel database sync ({workers} workers)...")

        total_chunks = 0
        successful_docs = 0
        failed_docs = 0

        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_blob = {executor.submit(self.load_single_chunk_file, b): b for b in blobs}
            for future in as_completed(future_to_blob):
                res = future.result()
                if res.get("status") == "SUCCESS":
                    successful_docs += 1
                    total_chunks += res.get("chunks", 0)
                else:
                    failed_docs += 1

                if (successful_docs + failed_docs) % 50 == 0:
                    logger.info(f"Progress: {successful_docs + failed_docs}/{len(blobs)} documents synced ({total_chunks} chunks loaded)...")

        print("\n" + "=" * 80)
        print(" GCS TO CLOUDSQL SYNC COMPLETE")
        print("=" * 80)
        print(f"  • Total Documents Synced: {successful_docs}")
        print(f"  • Total Chunks Loaded:    {total_chunks}")
        print(f"  • Failed Documents:       {failed_docs}")
        print("=" * 80)

        # Print live database statistics
        with self.engine.connect() as conn:
            j_cnt = conn.execute(text("SELECT COUNT(*) FROM judgments")).scalar()
            c_cnt = conn.execute(text("SELECT COUNT(*) FROM chunks")).scalar()
            e_cnt = conn.execute(text("SELECT COUNT(*) FROM chunk_embeddings")).scalar()
            print(f"\nLive Database Verification in Cloud SQL (scrag):")
            print(f"  • judgments table count:        {j_cnt}")
            print(f"  • chunks table count:           {c_cnt}")
            print(f"  • chunk_embeddings table count: {e_cnt}")
            print("=" * 80)


def main():
    parser = argparse.ArgumentParser(description="Sync GCS Chunk Deliverables directly into Cloud SQL PostgreSQL")
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    default_db = os.environ.get("DATABASE_URL")
    parser.add_argument("--db", default=default_db, help="PostgreSQL connection string (defaults to DATABASE_URL in .env)")
    parser.add_argument("--bucket", default=os.environ.get("GCS_BUCKET", "sc-judgments-rag-prod-data"), help="GCS Bucket Name")
    parser.add_argument("--year", type=int, default=None, help="Sync specific year (e.g. 1950)")
    parser.add_argument("--workers", type=int, default=16, help="Parallel worker threads (default: 16)")
    args = parser.parse_args()

    if not args.db:
        print("\nERROR: No database connection string provided!")
        print("Either pass --db 'postgresql://...' or set DATABASE_URL in your .env file.\n")
        sys.exit(1)

    syncer = GCSCloudSQLSyncer(db_url=args.db, bucket_name=args.bucket)
    syncer.sync_all(year=args.year, workers=args.workers)


if __name__ == "__main__":
    main()

