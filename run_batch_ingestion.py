"""
Batch Ingestion and Quality Gate Certification Harness (PCE v2.0)
Processes Supreme Court judgment PDFs (1950 historical scans & 2026 digital) into production database.
Supports high-throughput parallel multiprocessing across 6,000+ judgments with strict quality certification.
"""

import os
import sys
import time
import shutil
import argparse
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor

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


def worker_process_pdf(args):
    """Worker task executed across subprocesses: parses layout, chunks pages, and computes embeddings."""
    pdf_path, yr = args
    try:
        pipeline = IngestionPipeline(enable_db=False)
        return pipeline.process_pdf(pdf_path, year=yr, save_db=False)
    except Exception as e:
        print(f"[WORKER ERROR] Failed on {Path(pdf_path).name}: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="PCE v2.0 Batch Ingestion and Quality Certification")
    parser.add_argument("--gcs", action="store_true", help="Stream PDFs directly from Google Cloud Storage (GCS)")
    parser.add_argument("--all", action="store_true", help="Process ALL PDFs in data directory (full corpus)")
    parser.add_argument("--year", type=int, default=None, help="Target judgment year")
    parser.add_argument("--samples-per-year", type=int, default=None, help="Number of judgments to process per year")
    parser.add_argument("--limit", type=int, default=None, help="Limit total number of PDFs processed")
    parser.add_argument("--workers", type=int, default=6, help="Number of parallel worker processes (default: 6)")
    parser.add_argument("--reset", action="store_true", help="Wipe database and output JSONs before ingestion")
    args = parser.parse_args()

    print("=" * 85)
    print("=== PCE v2.0 BATCH INGESTION & QUALITY GATE CERTIFICATION ===")
    print("=" * 85)

    db_path = config.output_dir / "scj_rag_v2.db"

    if args.reset:
        print(">> Reset flag passed: Wiping previous database and JSON directories...")
        for p in [db_path, db_path.with_name("scj_rag_v2.db-wal"), db_path.with_name("scj_rag_v2.db-shm")]:
            if p.exists():
                try:
                    os.remove(p)
                except OSError:
                    pass
        for folder in [config.chunks_dir, config.documents_dir, config.logss_dir]:
            if folder.exists():
                shutil.rmtree(folder, ignore_errors=True)
            folder.mkdir(parents=True, exist_ok=True)

    print(f">> Target Database: {db_path.resolve()}")
    init_database(db_path)

    pipeline = IngestionPipeline(db_url=f"sqlite:///{db_path.resolve().as_posix()}", enable_db=True)

    target_files = []
    if args.gcs:
        target_year = args.year or 1950
        print(f">> Streaming directly from GCS bucket: gs://{config.gcs_bucket}/ (Year: {target_year})")
        gcs_loader = GCSDataLoader(bucket_name=config.gcs_bucket, project_id=config.gcp_project)
        pdf_uris = gcs_loader.list_year_pdfs(year=target_year, prefix_base=config.gcs_prefix)
        if args.limit:
            pdf_uris = pdf_uris[:args.limit]
        for u in pdf_uris:
            target_files.append((u, target_year))
    else:
        year_dirs = sorted([d for d in config.data_dir.glob("year=*") if d.is_dir()])
        if args.year:
            year_dirs = [d for d in year_dirs if d.name == f"year={args.year}"]

        if year_dirs:
            print(f">> Scanning {len(year_dirs)} year partition directories in {config.data_dir.resolve()}...")
            for ydir in year_dirs:
                try:
                    yr_val = int(ydir.name.replace("year=", ""))
                except ValueError:
                    yr_val = 2000
                pdfs = sorted(list(ydir.rglob("*.pdf")))
                if not pdfs:
                    continue
                if args.all:
                    chosen = pdfs
                elif args.samples_per_year:
                    chosen = pdfs[:args.samples_per_year]
                else:
                    chosen = pdfs[:1]

                for p in chosen:
                    target_files.append((str(p), yr_val))
                print(f"   * {ydir.name}: Loaded {len(chosen):,} PDF(s) (out of {len(pdfs):,} available)")
        else:
            # Fallback benchmark samples
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

    if args.limit:
        target_files = target_files[:args.limit]

    total_count = len(target_files)
    print(f"\n>> Commencing ingestion of {total_count:,} Supreme Court judgments...")
    print(f">> Concurrency: {args.workers} worker processes | Target DB: {db_path.name}")
    t0 = time.time()
    total_saved = 0
    total_chunks = 0
    log_interval = 25 if total_count <= 200 else 100

    if args.workers <= 1:
        for idx, (fpath, yr) in enumerate(target_files, 1):
            name = Path(fpath).name
            rec = pipeline.process_pdf(fpath, yr, save_db=True)
            if rec:
                total_saved += 1
                total_chunks += len(rec.chunks)
            if idx % log_interval == 0 or idx == total_count:
                elapsed = time.time() - t0
                rate = total_saved / max(0.001, elapsed)
                pct = (idx / total_count) * 100
                rem_sec = (total_count - idx) / max(0.001, rate)
                print(f"[{idx:>5}/{total_count}] ({pct:>5.1f}%) Ingested {name:<30} | Chunks: {total_chunks:>6,} | Rate: {rate:>4.1f} pdf/s | ETA: {rem_sec/60:.1f}m")
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            for idx, rec in enumerate(executor.map(worker_process_pdf, target_files, chunksize=4), 1):
                if rec:
                    pipeline.loader.save_document_record(rec)
                    total_saved += 1
                    total_chunks += len(rec.chunks)
                    name = Path(rec.file_path).name
                else:
                    name = "error_skip"

                if idx % log_interval == 0 or idx == total_count:
                    elapsed = time.time() - t0
                    rate = total_saved / max(0.001, elapsed)
                    pct = (idx / total_count) * 100
                    rem_sec = (total_count - idx) / max(0.001, rate)
                    print(f"[{idx:>5}/{total_count}] ({pct:>5.1f}%) Ingested {name:<30} | Chunks: {total_chunks:>6,} | Rate: {rate:>4.1f} pdf/s | ETA: {rem_sec/60:.1f}m")

    elapsed = time.time() - t0
    print(f"\n>> Ingestion finished in {elapsed:.2f}s ({elapsed/60:.2f}m)!")
    print(f">> Total judgments saved: {total_saved:,} / {total_count:,} | Total active chunks: {total_chunks:,}")

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
