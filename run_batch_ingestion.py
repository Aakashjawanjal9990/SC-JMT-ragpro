"""
Batch Ingestion and Quality Certification Harness (PCE v2.0)
Processes Supreme Court judgment PDFs (1950 historical scans & 2026 digital) into production database.
Supports high-throughput parallel multiprocessing across 43,000+ judgments with strict quality certification.
"""

import os
import sys
import time
import shutil
import logging
import argparse
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor

logger = logging.getLogger("BatchIngestion")

# Add repo root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from config import config, resolve_database_url
from storage.init_db import init_database
from pipeline import IngestionPipeline
from storage.gcs_loader import GCSDataLoader
from audit_quality import run_quality_audit


def worker_process_pdf(args):
    """Worker task executed across subprocesses: parses layout, chunks pages, and computes embeddings."""
    pdf_path, yr = args
    try:
        pipeline = IngestionPipeline(enable_db=False)
        rec = pipeline.process_pdf(pdf_path, year=yr, save_db=False)
        if rec is None:
            return {"success": False, "record": None, "error": "Pipeline returned None", "file": pdf_path}
        return {"success": True, "record": rec, "error": None, "file": pdf_path}
    except Exception as e:
        return {"success": False, "record": None, "error": str(e), "file": pdf_path}


def main():
    parser = argparse.ArgumentParser(description="PCE v2.0 Batch Ingestion and Quality Certification")
    parser.add_argument("--gcs", action="store_true", help="Stream PDFs directly from Google Cloud Storage (GCS)")
    parser.add_argument("--all", action="store_true", help="Process ALL PDFs in data directory (full corpus)")
    parser.add_argument("--pdf", type=str, default=None, help="Process a single PDF file directly")
    parser.add_argument("--dir", type=str, default=None, help="Process all PDFs in a specified directory")
    parser.add_argument("--year", type=int, default=None, help="Target judgment year")
    parser.add_argument("--limit", type=int, default=None, help="Limit total number of PDFs processed")
    parser.add_argument("--workers", type=int, default=config.default_ingestion_workers, help=f"Number of parallel worker processes (default: {config.default_ingestion_workers})")
    parser.add_argument("--target", choices=["local", "alloydb"], default=os.getenv("DATABASE_TARGET", "local"), help="Target database: 'local' (SQLite) or 'alloydb' (Google Cloud AlloyDB PostgreSQL 17)")
    parser.add_argument("--reset", action="store_true", help="Wipe database and output JSONs before ingestion (local dev only)")
    parser.add_argument("--yes", "-y", action="store_true", help="Acknowledge and confirm destructive operations without interactive prompt")
    args = parser.parse_args()

    print("=" * 85)
    print("=== PCE v2.0 BATCH INGESTION & DATA QUALITY CERTIFICATION ===")
    print("=" * 85)

    use_alloydb = (args.target == "alloydb" or os.getenv("DATABASE_TARGET", "").lower() in ("alloydb", "postgres", "postgresql"))

    if args.reset:
        env_mode = os.getenv("ENVIRONMENT", "development").lower()
        if env_mode in ("production", "prod") or use_alloydb:
            raise RuntimeError("CRITICAL SAFETY GUARD: Destructive --reset is strictly prohibited in PRODUCTION / AlloyDB environment.")
        if not args.yes:
            try:
                confirm = input("CAUTION: --reset will permanently delete the local database and output folders. Type 'YES' to proceed: ")
                if confirm.strip() != "YES":
                    print("Reset aborted by user.")
                    sys.exit(0)
            except (EOFError, KeyboardInterrupt):
                print("\nReset aborted.")
                sys.exit(0)

        print(">> Performing clean reset of local output directory and database...")
        db_path = config.output_dir / "scj_rag_v2.db"
        if db_path.exists():
            try:
                os.remove(db_path)
            except Exception as e:
                print(f"Warning removing database file: {e}")

        for sub in [config.chunks_dir, config.documents_dir, config.logss_dir]:
            if sub.exists():
                shutil.rmtree(sub, ignore_errors=True)
            sub.mkdir(parents=True, exist_ok=True)
        print(">> Clean reset complete.")

    # Initialize Target Database Schema (Frozen production schema)
    target_db_url = resolve_database_url(target=args.target)
    print(f">> Initializing Database schema on target: '{args.target}'...")
    if target_db_url.startswith("sqlite"):
        sqlite_file = Path(target_db_url.replace("sqlite:///", ""))
        init_database(sqlite_file)
    print(">> Database schema initialized and verified.")

    pipeline = IngestionPipeline(db_url=target_db_url, enable_db=True)

    # Collect Input Judgment PDFs
    target_files = []
    if args.pdf:
        p_path = Path(args.pdf)
        if not p_path.exists():
            print(f"[ERROR] Specified PDF does not exist: {args.pdf}")
            sys.exit(1)
        target_files.append((str(p_path), args.year or 2026))
    elif args.dir:
        d_path = Path(args.dir)
        if not d_path.exists():
            print(f"[ERROR] Specified directory does not exist: {args.dir}")
            sys.exit(1)
        for f in sorted(d_path.glob("**/*.pdf")):
            if args.limit and len(target_files) >= args.limit:
                break
            target_files.append((str(f), args.year or 2026))
    elif args.gcs:
        print(">> Connecting to Google Cloud Storage (GCS)...")
        gcs_loader = GCSDataLoader()
        if not gcs_loader.is_available():
            raise RuntimeError(f"Cannot stream from GCS: Client or bucket '{gcs_loader.bucket_name}' is unavailable.")

        uris = gcs_loader.list_available_pdfs(year=args.year)
        if args.limit:
            uris = uris[:args.limit]
        for uri in uris:
            target_files.append((uri, args.year))
    else:
        # Default local scan from data directory
        data_pdfs = list(config.data_dir.glob("**/*.pdf")) if config.data_dir.exists() else []
        for f in data_pdfs[:args.limit or len(data_pdfs)]:
            target_files.append((str(f), args.year or 2026))

    total_count = len(target_files)
    print(f"\n>> Queued {total_count} judgment PDFs for parallel batch ingestion (Workers: {args.workers})...\n")

    if total_count == 0:
        print(">> No PDFs found to process. Specify --pdf <path>, --dir <path>, --gcs, or place PDFs in data/ directory.")
        return

    t0 = time.time()
    total_saved = 0
    total_chunks = 0
    failed_files = []
    log_interval = max(1, total_count // 20)

    if args.workers <= 1 or total_count <= 2:
        for idx, (fpath, yr) in enumerate(target_files, 1):
            name = Path(fpath).name
            try:
                rec = pipeline.process_pdf(fpath, year=yr, save_db=True)
                if rec:
                    total_saved += 1
                    total_chunks += len(rec.chunks)
                else:
                    failed_files.append(name)
            except Exception as e:
                print(f"[ERROR] Ingestion failed for {name}: {e}")
                failed_files.append(name)

            if idx % log_interval == 0 or idx == total_count:
                elapsed = time.time() - t0
                rate = total_saved / max(0.001, elapsed)
                pct = (idx / total_count) * 100
                rem_sec = (total_count - idx) / max(0.001, rate)
                print(f"[{idx:>5}/{total_count}] ({pct:>5.1f}%) Ingested {name:<30} | Chunks: {total_chunks:>6,} | Rate: {rate:>4.1f} pdf/s | ETA: {rem_sec/60:.1f}m")
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            for idx, res in enumerate(executor.map(worker_process_pdf, target_files, chunksize=4), 1):
                if res and res.get("success") and res.get("record"):
                    rec = res["record"]
                    pipeline.loader.save_document_record(rec)
                    total_saved += 1
                    total_chunks += len(rec.chunks)
                    name = Path(rec.file_path).name
                else:
                    file_name = Path(res.get("file", "unknown")).name if res else "unknown_file"
                    err_msg = res.get("error") if res else "Worker failure"
                    print(f"[WORKER ERROR] Failed on {file_name}: {err_msg}")
                    failed_files.append(file_name)
                    name = f"FAILED: {file_name}"

                if idx % log_interval == 0 or idx == total_count:
                    elapsed = time.time() - t0
                    rate = total_saved / max(0.001, elapsed)
                    pct = (idx / total_count) * 100
                    rem_sec = (total_count - idx) / max(0.001, rate)
                    print(f"[{idx:>5}/{total_count}] ({pct:>5.1f}%) Ingested {name:<30} | Chunks: {total_chunks:>6,} | Rate: {rate:>4.1f} pdf/s | ETA: {rem_sec/60:.1f}m")

    elapsed = time.time() - t0
    print(f"\n>> Ingestion finished in {elapsed:.2f}s ({elapsed/60:.2f}m)!")
    print(f">> Total judgments saved: {total_saved:,} / {total_count:,} | Total active chunks: {total_chunks:,}")

    # Run Data Quality and Integrity Verification (OFF-002, OFF-010)
    print("\n" + "=" * 85)
    print("=== RUNNING DATA QUALITY & INTEGRITY VERIFICATION ===")
    print("=" * 85)
    gate_results = run_quality_audit(chunks_dir=config.chunks_dir, db_url=target_db_url)
    checks = gate_results.get("quality_checks", {})
    for check_name, info in checks.items():
        print(f"{check_name:<28} | {info['status']:<10} | {info['detail']}")
    print("-" * 85)

    # OFF-005 & OFF-010: Strict failure exit contract
    has_failed_docs = len(failed_files) > 0
    all_passed = bool(gate_results.get("all_passed", False))

    if has_failed_docs:
        print(f"\n[CRITICAL ERROR] Batch ingestion failed on {len(failed_files)} documents:")
        for fn in failed_files:
            print(f"  - {fn}")

    if all_passed and not has_failed_docs:
        print(">>> [SUCCESS] ALL DATA QUALITY AND INTEGRITY CHECKS VERIFIED (100% PASS). CERTIFIED FOR PRODUCTION.")
        sys.exit(0)
    else:
        print("\n[CRITICAL ERROR] Production certification failed: quality gates failed or document failures occurred.")
        sys.exit(1)
    print("=" * 85)


if __name__ == "__main__":
    main()
