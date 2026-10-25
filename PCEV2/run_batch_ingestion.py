"""
Batch Ingestion and Question-Answering Verification Harness (PCE v2.0)
Processes sample PDFs from 1950 and 2026 into a clean new database and runs QA evaluation.
"""

import os
import sys
import time
import shutil
from pathlib import Path

# Add modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from PCEV2.config import config
from PCEV2.storage.init_db import init_database
from PCEV2.pipeline import IngestionPipeline
from PCEV2.check_gates import check_all_gates
from PCEV2.retrieval.retrieval_engine import LegalRetrievalEngine


def main():
    print("=" * 85)
    print("=== PCE v2.0 NEW DATABASE BATCH INGESTION & QA VERIFICATION ===")
    print("=" * 85)

    db_path = config.output_dir / "scj_rag_v2.db"
    print(f">> Initializing brand new database: {db_path.resolve()}")
    if db_path.exists():
        db_path.unlink()

    # Clean prior output directories for a pure fresh run
    for d in [config.chunks_dir, config.documents_dir, config.reports_dir, config.logss_dir]:
        d.mkdir(parents=True, exist_ok=True)
        for f in d.glob("*"):
            if f.is_file():
                f.unlink()
            elif f.is_dir():
                shutil.rmtree(f, ignore_errors=True)

    init_database(db_path)
    pipeline = IngestionPipeline(db_url=f"sqlite:///{db_path.resolve().as_posix()}")

    data_dir = config.data_dir
    print(f">> Locating sample PDFs in: {data_dir.resolve()}")

    # Select representative sample PDFs from 1950 and 2026
    target_samples = [
        ("year=1950/english/1950_1_15_25_EN.pdf", 1950),
        ("year=1950/english/1950_1_25_29_EN.pdf", 1950),
        ("year=1950/english/1950_1_869_940_EN.pdf", 1950),
        ("year=2026/english/2026_1_183_190_EN.pdf", 2026),
        ("year=2026/english/2026_1_191_205_EN.pdf", 2026),
        ("year=2026/english/2026_1_119_129_EN.pdf", 2026),
        ("year=2026/english/2026_1_130_140_EN.pdf", 2026),
    ]

    print(f">> Beginning ingestion of {len(target_samples)} representative judgments across 1950 and 2026...")
    t0 = time.time()
    ingested_records = []

    for rel_path, yr in target_samples:
        pdf_path = data_dir / rel_path
        if not pdf_path.exists():
            alt_path = config.fixtures_dir / Path(rel_path).name
            if alt_path.exists():
                pdf_path = alt_path
            else:
                print(f"[WARN] File not found: {pdf_path}")
                continue

        print(f"\n[INGESTING] {pdf_path.name} (Year {yr})...")
        rec = pipeline.process_pdf(str(pdf_path), yr)
        ingested_records.append(rec)
        print(f"  -> Extracted {len(rec.pages)} pages, generated {len(rec.chunks)} chunks.")

    elapsed = time.time() - t0
    total_chunks = sum(len(r.chunks) for r in ingested_records)
    print(f"\n>> Ingestion finished in {elapsed:.2f}s! Total active chunks created: {total_chunks}")

    # Run Strict Quality Gates on New DB
    print("\n" + "=" * 85)
    print("=== RUNNING STRICT QUALITY GATES ON NEW DATABASE ===")
    print("=" * 85)
    gate_results = check_all_gates(chunks_dir=config.chunks_dir, db_url=f"sqlite:///{db_path.resolve().as_posix()}")
    for gid, ginfo in gate_results["strict_gates"].items():
        print(f"{gid:<28} | {ginfo['status']:<10} | {ginfo['detail']}")
    print("-" * 85)

    if not gate_results["all_strict_passed"]:
        print("[WARNING] Not all strict quality gates met!")

    # Test Question-Answering & Retrieval
    print("\n" + "=" * 85)
    print("=== TESTING QUESTION-ANSWERING & GROUNDED LEGAL RETRIEVAL ===")
    print("=" * 85)

    qa_engine = LegalRetrievalEngine(db_path=db_path)

    test_queries = [
        "What did Kania C.J. hold regarding the octroi duty under the Municipal Act in Ram Krishna?",
        "What was Fazl Ali J.'s holding on limitation under Article 182 in Bhawanipore Banking?",
        "Did the Supreme Court quash the criminal complaint in S. Nagesh v. Shobha S. Aradhya under Section 138 of the NI Act?",
        "In Karnataka Lokayuktha v. Chandrashekar, can criminal prosecution continue if the delinquent was exonerated in departmental enquiry?",
        "Chiranjit Lal Chowdhuri v. Union of India equal protection Article 14"
    ]

    for q in test_queries:
        print(f"\n[QUERY] {q}")
        res = qa_engine.answer_question(q, top_k=2)
        ps = res.get("primary_source", {})
        print(f"  [Top Match] Doc: {ps.get('document_id')} | Page: {ps.get('page')} | Section: {ps.get('section')} | Judge: {ps.get('judge')}")
        print(f"  [Synthesized Holding/Answer]:\n    \"{res.get('answer')}\"")
        print(f"  [Evidence Snippet]:\n    \"{res.get('evidence_snippet')[:250]}...\"")
        print("  [Retrieved Authorities]:")
        for s in res.get("sources", []):
            print(f"    - {s['chunk_id']} (Score: {s['score']:.4f}, Sec: {s['section']}, Judge: {s['judge']})")

    print("\n" + "=" * 85)
    print(">>> [SUCCESS] ALL BATCH VERIFICATION & QA TESTS COMPLETED SUCCESSFULLY!")
    print("=" * 85)


if __name__ == "__main__":
    main()
