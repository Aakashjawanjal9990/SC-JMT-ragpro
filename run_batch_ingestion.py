"""
Batch Ingestion and Quality Certification Harness (PCE v2.0)
Processes Supreme Court judgment PDFs from configured local or cloud sources.
Supports high-throughput parallel multiprocessing across 43,000+ judgments with strict quality certification.
"""

import os
import sys
import time
import shutil
import logging
import argparse
import json
import tempfile
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
from audit_quality import QualityAuditor, run_quality_audit, check_all_gates


def _document_id_for(path_or_uri: str) -> str:
    """Mirror IngestionPipeline's document-ID derivation (filename stem)."""
    if path_or_uri.startswith("gs://"):
        return Path(path_or_uri[5:].split("/", 1)[-1]).stem
    return Path(path_or_uri).stem


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


def write_failure_manifest(failures, output_dir: Path) -> Path:
    """Atomically persist failed inputs without logging document contents/secrets."""
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = output_dir / "ingestion_failures.json"
    payload = {
        "failed_count": len(failures),
        "failures": failures,
    }
    fd, tmp_name = tempfile.mkstemp(prefix="ingestion_failures_", suffix=".tmp", dir=str(output_dir))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, manifest)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return manifest


def mark_document_persistence_status(document_id: str, year: int, success: bool, error: str = "") -> None:
    """Finalize the worker-created lifecycle record after parent DB persistence."""
    status_path = config.logss_dir / f"year={year}" / f"{document_id}_status.json"
    if not status_path.exists():
        raise FileNotFoundError(f"Lifecycle status file missing for {document_id}: {status_path}")
    with open(status_path, "r", encoding="utf-8") as stream:
        payload = json.load(stream)
    stages = payload.setdefault("stages", {})
    vector_stage = stages.setdefault("vector_db", {"status": "PENDING", "duration_sec": 0.0})
    vector_stage["status"] = "COMPLETED" if success else "FAILED"
    if not success:
        payload["error"] = error or "Database persistence failed in parent process"
    elif payload.get("error") is None:
        payload["error"] = None
    payload["overall_status"] = (
        "SUCCESS" if success and all(v.get("status") == "COMPLETED" for v in stages.values()) else "FAILED"
    )
    fd, tmp_name = tempfile.mkstemp(prefix=f".{status_path.name}.", suffix=".tmp", dir=str(status_path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, status_path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    markdown_path = status_path.with_suffix(".md")
    with open(markdown_path, "w", encoding="utf-8") as stream:
        stream.write(f"# Status Log: {document_id}\n\n")
        stream.write(f"- **Case Title**: {payload.get('case_title', document_id)}\n")
        stream.write(f"- **Year**: {year}\n")
        stream.write(f"- **Overall Status**: `{payload['overall_status']}`\n")
        stream.write(f"- **Pages**: {payload.get('total_pages', 0)} | **Chunks**: {payload.get('total_chunks', 0)} | **OCR Quality**: {payload.get('ocr_quality', 0):.3f}\n")
        stream.write(f"- **Duration**: {payload.get('total_duration_sec', 0)}s\n\n")
        stream.write("| Stage | Status | Duration (s) |\n| :--- | :---: | ---: |\n")
        for stage, details in stages.items():
            stream.write(f"| `{stage}` | {details.get('status')} | {details.get('duration_sec', 0)} |\n")


def _failure(file_path: str, error: str, phase: str = "ingestion") -> dict:
    return {"file": str(file_path), "phase": phase, "error": str(error)[:2000]}


def main():
    parser = argparse.ArgumentParser(description="PCE v2.0 Batch Ingestion and Quality Certification")
    parser.add_argument("--gcs", action="store_true", help="Stream PDFs directly from Google Cloud Storage (GCS)")
    parser.add_argument("--all", action="store_true", help="Process ALL PDFs in data directory (full corpus)")
    parser.add_argument("--pdf", type=str, default=None, help="Process a single PDF file directly")
    parser.add_argument("--dir", type=str, default=None, help="Process all PDFs in a specified directory")
    parser.add_argument("--year", type=int, default=None, help="Target judgment year")
    parser.add_argument("--limit", type=int, default=None, help="Limit total number of PDFs processed")
    parser.add_argument("--workers", type=int, default=config.default_ingestion_workers, help=f"Number of parallel worker processes (default: {config.default_ingestion_workers})")
    parser.add_argument("--task-index", type=int, default=None, help="Cloud Run Task Index (0 to task-count - 1)")
    parser.add_argument("--task-count", type=int, default=None, help="Total number of Cloud Run Tasks for parallel sharding")
    parser.add_argument("--target", choices=["local", "alloydb"], default=os.getenv("DATABASE_TARGET", "local"), help="Target database: 'local' (SQLite) or 'alloydb' (Google Cloud AlloyDB PostgreSQL 17)")
    parser.add_argument("--reset", action="store_true", help="Wipe database and output JSONs before ingestion (local dev only)")
    parser.add_argument("--yes", "-y", action="store_true", help="Acknowledge and confirm destructive operations without interactive prompt")
    args = parser.parse_args()

    print("=" * 85)
    print("=== PCE v2.0 BATCH INGESTION & DATA QUALITY CERTIFICATION ===")
    print("=" * 85)

    use_alloydb = (args.target == "alloydb" or os.getenv("DATABASE_TARGET", "").lower() in ("alloydb", "postgres", "postgresql"))
    config.validate_runtime(target=args.target, require_gcs=args.gcs)

    if args.workers < 1:
        parser.error("--workers must be >= 1")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be >= 1 when provided")

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
        target_files.append((str(p_path), args.year))
    elif args.dir:
        d_path = Path(args.dir)
        if not d_path.exists():
            print(f"[ERROR] Specified directory does not exist: {args.dir}")
            sys.exit(1)
        for f in sorted(d_path.glob("**/*.pdf")):
            if args.limit and len(target_files) >= args.limit:
                break
            target_files.append((str(f), args.year))
    elif args.gcs:
        print(">> Connecting to Google Cloud Storage (GCS)...")
        gcs_loader = GCSDataLoader()
        gcs = gcs_loader
        if not gcs_loader.is_available():
            raise RuntimeError(f"Cannot stream from GCS: Client or bucket '{gcs_loader.bucket_name}' is unavailable.")

        target_year = args.year
        uris = gcs.list_available_pdfs(year=target_year)
        if args.limit:
            uris = uris[:args.limit]
        for uri in uris:
            target_files.append((uri, args.year))
    else:
        # Default local scan from data directory
        data_pdfs = list(config.data_dir.glob("**/*.pdf")) if config.data_dir.exists() else []
        for f in data_pdfs[:args.limit or len(data_pdfs)]:
            target_files.append((str(f), args.year))

    # Document IDs derive from the filename stem; two inputs sharing a stem would
    # overwrite each other in the database, so fail before any work is done.
    first_seen = {}
    collisions = []
    for fpath, _yr in target_files:
        doc_id = _document_id_for(fpath)
        if doc_id in first_seen and first_seen[doc_id] != fpath:
            collisions.append(f"{doc_id}: {first_seen[doc_id]} vs {fpath}")
        first_seen.setdefault(doc_id, fpath)
    if collisions:
        raise RuntimeError(
            "Duplicate document IDs would overwrite each other: " + "; ".join(collisions[:10])
        )

    # Cloud Run Jobs Task Array sharding (CLOUD_RUN_TASK_INDEX / CLOUD_RUN_TASK_COUNT)
    task_index_env = os.getenv("CLOUD_RUN_TASK_INDEX")
    task_count_env = os.getenv("CLOUD_RUN_TASK_COUNT")
    task_index = args.task_index if args.task_index is not None else (int(task_index_env) if task_index_env is not None else 0)
    task_count = args.task_count if args.task_count is not None else (int(task_count_env) if task_count_env is not None else 1)

    if task_count > 1:
        target_files.sort(key=lambda x: x[0])
        total_unsharded = len(target_files)
        target_files = [f for idx, f in enumerate(target_files) if idx % task_count == task_index]
        print(f">> [Cloud Run Task Array] Task {task_index + 1}/{task_count}: Assigned shard of {len(target_files):,} / {total_unsharded:,} PDFs")

    total_count = len(target_files)
    print(f"\n>> Queued {total_count} judgment PDFs for parallel batch ingestion (Workers: {args.workers})...\n")

    if total_count == 0:
        print("[ERROR] No PDFs found to process. This is not a successful ingestion run.")
        sys.exit(2)

    t0 = time.time()
    total_saved = 0
    total_chunks = 0
    failed_files = []
    failure_details = []
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
                    failure_details.append(_failure(fpath, "Pipeline returned no document record"))
            except Exception as e:
                print(f"[ERROR] Ingestion failed for {name}: {e}")
                failed_files.append(name)
                failure_details.append(_failure(fpath, e))

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
                    try:
                        pipeline.loader.save_document_record(rec)
                        mark_document_persistence_status(rec.document_id, rec.year, success=True)
                        total_saved += 1
                        total_chunks += len(rec.chunks)
                        name = Path(rec.file_path).name
                    except Exception as save_error:
                        try:
                            mark_document_persistence_status(rec.document_id, rec.year, success=False, error=str(save_error))
                        except Exception:
                            logger.exception("Could not mark persistence failure for %s", rec.document_id)
                        file_name = Path(res.get("file", rec.file_path)).name
                        print(f"[PERSISTENCE ERROR] Failed on {file_name}: {save_error}")
                        failed_files.append(file_name)
                        failure_details.append(_failure(res.get("file", rec.file_path), save_error, "persistence"))
                        name = f"FAILED: {file_name}"
                else:
                    file_name = Path(res.get("file", "unknown")).name if res else "unknown_file"
                    err_msg = res.get("error") if res else "Worker failure"
                    print(f"[WORKER ERROR] Failed on {file_name}: {err_msg}")
                    failed_files.append(file_name)
                    failure_details.append(_failure(res.get("file", file_name) if res else file_name, err_msg, "worker"))
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

    if failure_details:
        try:
            failure_manifest = write_failure_manifest(failure_details, config.reports_dir)
            print(f">> Failure manifest written: {failure_manifest}")
        except Exception as manifest_error:
            print(f"[CRITICAL ERROR] Could not persist failure manifest: {manifest_error}")
            failure_details.append(_failure("<failure-manifest>", manifest_error, "reporting"))

    # Run Data Quality and Integrity Verification (OFF-002, OFF-010)
    print("\n" + "=" * 85)
    print("=== RUNNING DATA QUALITY & INTEGRITY VERIFICATION ===")
    print("=" * 85)
    try:
        gate_results = run_quality_audit(chunks_dir=config.chunks_dir, db_url=target_db_url)
    except Exception as audit_error:
        logger.exception("Quality audit could not complete")
        gate_results = {
            "all_passed": False,
            "all_strict_passed": False,
            "quality_checks": {
                "QUALITY_AUDIT_EXECUTION": {
                    "status": "BLOCKED",
                    "detail": f"Audit raised {type(audit_error).__name__}: {audit_error}",
                }
            },
        }
        failure_details.append(_failure("<quality-audit>", audit_error, "quality_gate"))
    checks = gate_results.get("quality_checks", {})
    for check_name, info in checks.items():
        print(f"{check_name:<28} | {info.get('status', 'BLOCKED'):<10} | {info.get('detail', 'Missing gate detail')}")
    print("-" * 85)

    # OFF-005 & OFF-010: Strict failure exit contract
    has_failed_docs = len(failed_files) > 0
    all_passed = bool(gate_results.get("all_passed", False)) and bool(checks) and all(
        info.get("status") == "PASSED" for info in checks.values()
    )

    if has_failed_docs:
        print(f"\n[CRITICAL ERROR] Batch ingestion failed on {len(failed_files)} documents:")
        for fn in failed_files:
            print(f"  - {fn}")

    if all_passed and not has_failed_docs and total_saved == total_count:
        print(">>> [SUCCESS] All executed quality gates passed and all queued documents were saved. This run is not, by itself, a production certification.")
        sys.exit(0)
    else:
        print("\n[WARNING] Data quality verification checks incomplete: a mandatory quality gate failed/was blocked, or queued documents were not all saved.")
        print("\n[CRITICAL ERROR] Ingestion run failed: a mandatory quality gate failed/was blocked, or queued documents were not all saved.")
        failed_gate_items = [
            _failure("<quality-gates>", f"{name}: {info.get('detail', info.get('status', 'failed'))}", "quality_gate")
            for name, info in checks.items() if info.get("status") != "PASSED"
        ]
        failure_details.extend(failed_gate_items)
        if not failure_details:
            failure_details.append(_failure("<batch>", "Saved count did not reconcile with queued count", "reconciliation"))
        try:
            write_failure_manifest(failure_details, config.reports_dir)
        except Exception:
            logger.exception("Could not update failure manifest")
        sys.exit(1)
    print("=" * 85)


if __name__ == "__main__":
    main()
