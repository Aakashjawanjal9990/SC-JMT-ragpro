"""
Batch Ingestion and Quality Gate Certification Harness (PCE v2.0)
Processes Supreme Court judgment PDFs (1950 historical scans & 2026 digital) into production database.
Supports streaming directly from Google Cloud Storage (GCS) and local files.
"""

import os
import sys
import time
import shutil
import argparse
from pathlib import Path

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

try:
    from config import config
    from storage.init_db import init_database
    from pipeline import IngestionPipeline
    from check_gates import check_all_gates
    from storage.gcs_loader import GCSDataLoader
except ImportError:
    from PCEV2.config import config
    from PCEV2.storage.init_db import init_database
    from PCEV2.pipeline import IngestionPipeline
    from PCEV2.check_gates import check_all_gates
    from PCEV2.storage.gcs_loader import GCSDataLoader


def main():
    parser = argparse.ArgumentParser(description="PCE v2.0 Batch Ingestion and Quality Certification")
    parser.add_argument("--gcs", action="store_true", help="Stream PDFs directly from Google Cloud Storage (GCS)")
    parser.add_argument("--year", type=int, default=1950, help="Target judgment year when streaming from GCS")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of PDFs processed (for testing)")
    args = parser.parse_args()

    print("=" * 85)
    print("=== PCE v2.0 BATCH INGESTION & QUALITY GATE CERTIFICATION ===")
    print("=" * 85)

    db_path = config.output_dir / "scj_rag_v2.db"
    print(f">> Target Database: {db_path.resolve()}")
    init_database(db_path)

    pipeline = IngestionPipeline(db_url=f"sqlite:///{db_path.resolve().as_posix()}")

    target_files = []
    if args.gcs:
        print(f">> Streaming directly from GCS bucket: gs://{config.gcs_bucket}/")
        gcs_loader = GCSDataLoader(bucket_name=config.gcs_bucket, project_id=config.gcp_project)
        pdf_uris = gcs_loader.list_year_pdfs(year=args.year, prefix_base=config.gcs_prefix)
        if args.limit:
            pdf_uris = pdf_uris[:args.limit]
        for u in pdf_uris:
            target_files.append((u, args.year))
    else:
        # Default representative benchmark judgments from local samples
        benchmark_samples = [
            ("year=1950/english/1950_1_15_25_EN.pdf", 1950),
            ("year=1950/english/1950_1_25_29_EN.pdf", 1950),
            ("year=1950/english/1950_1_869_940_EN.pdf", 1950),
            ("year=2026/english/2026_1_183_190_EN.pdf", 2026),
            ("year=2026/english/2026_1_191_205_EN.pdf", 2026),
            ("year=2026/english/2026_1_119_129_EN.pdf", 2026),
            ("year=2026/english/2026_1_130_140_EN.pdf", 2026),
        ]
        for rel_path, yr in benchmark_samples:
            pdf_path = config.data_dir / rel_path
            if not pdf_path.exists():
                alt_path = config.fixtures_dir / Path(rel_path).name
                if alt_path.exists():
                    pdf_path = alt_path
                else:
                    continue
            target_files.append((str(pdf_path), yr))

    print(f">> Beginning ingestion of {len(target_files)} judgments...")
    t0 = time.time()
    ingested_records = []

    for file_path, yr in target_files:
        name = Path(file_path).name
        print(f"\n[INGESTING] {name} (Year {yr})...")
        rec = pipeline.process_pdf(file_path, yr)
        ingested_records.append(rec)
        print(f"  -> Extracted {len(rec.pages)} pages, generated {len(rec.chunks)} chunks.")

    elapsed = time.time() - t0
    total_chunks = sum(len(r.chunks) for r in ingested_records)
    print(f"\n>> Ingestion finished in {elapsed:.2f}s! Total active chunks created: {total_chunks}")

    # Run Strict Quality Gates on Database
    print("\n" + "=" * 85)
    print("=== RUNNING STRICT QUALITY GATES CERTIFICATION ===")
    print("=" * 85)
    gate_results = check_all_gates(chunks_dir=config.chunks_dir, db_url=f"sqlite:///{db_path.resolve().as_posix()}")
    for gid, ginfo in gate_results["strict_gates"].items():
        print(f"{gid:<28} | {ginfo['status']:<10} | {ginfo['detail']}")
    print("-" * 85)

    if gate_results["all_strict_passed"]:
        print(">>> [SUCCESS] ALL STRICT QUALITY GATES MET (100% PASS). CERTIFIED FOR PRODUCTION.")
    else:
        print("[WARNING] Strict quality gates check incomplete.")
    print("=" * 85)


if __name__ == "__main__":
    main()
