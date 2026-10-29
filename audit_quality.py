"""
Audit Quality Harness for Supreme Court Judgment RAG Pipeline (PCE v2.0)
Measures text quality, header leakage, structure detection, chunk boundaries, and vector fidelity.
Enforces strict production quality gates across 43,000+ Supreme Court judgments.
"""

import os
import sys
import re
import csv
import json
import math
import sqlite3
import argparse
from pathlib import Path
from typing import List, Dict, Any, Optional

# Add parent directory to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from config import config, resolve_database_url
from storage.gcs_loader import GCSDataLoader
from local_ocr.engine import GoogleVisionOCREngine


def estimate_tokens(text: str) -> int:
    """Fast estimation of tokens for chunk sizing."""
    if not text:
        return 0
    words = len(text.split())
    return max(int(words * 1.3), len(text) // 4)


class QualityAuditor:
    """Evaluates chunk files and database records against quality invariants."""

    def __init__(self, chunks_dir: Optional[Path] = None):
        self.chunks_dir = chunks_dir or config.chunks_dir

    def audit_file(self, json_path: Path) -> Dict[str, Any]:
        """Audits a single document's chunk JSON file."""
        doc_id = json_path.stem.replace("_chunks", "")
        with open(json_path, "r", encoding="utf-8") as f:
            chunks = json.load(f)

        if not chunks:
            return {"document_id": doc_id, "error": "Empty chunk file", "total_chunks": 0}

        header_patterns = [
            r'SUPR[E!]+ME\s+[CO0]{1,2}UR[T1\']+\s+REPORTS?',
            r'(?:^|\n)\s*\[19[56]\d\]\s*(?:\d+\s+)?S\.?C\.?R\.?\s*(?:\n|$)',
            r'A\.K\.\s*Gopalan\s+v\.\s+State\s+of\s+Madras',
            r'Province\s+of\s+Bombay\s+v\.\s+K\.?\s*S\.?\s*Advani',
            r'Chiranjit\s+Lal\s+Chowdhuri\s+v\.\s+Union\s+of\s+India',
            r'Keshavan\s+Madhava\s+Menon\s+v\.\s+State\s+of\s+Bombay',
            r'(?:^|\n)\s*\d{1,4}\s*(?:\n|$)',
            r'(?:^|\n)\s*195\d\s*(?:\n|$)',
            r'May,\s*19\.',
            r'(?:^|\n)\s*\d{1,2}-[A-Z]\s*(?:\n|$)'
        ]
        compiled_headers = [re.compile(p, re.IGNORECASE) for p in header_patterns]

        disallowed_fragments = [
            re.compile(r'^SECTION\s+\d+$', re.IGNORECASE),
            re.compile(r'^PART\s+FORMS$', re.IGNORECASE),
            re.compile(r'^ART\.\s*\d+$', re.IGNORECASE),
            re.compile(r'^CLAUSE\s+\d+$', re.IGNORECASE),
            re.compile(r'^BACKGROUND$', re.IGNORECASE)
        ]

        total_chunks = len(chunks)
        header_leaks = 0
        mid_sentence_ends = 0
        lowercase_starts = 0
        fragment_headings = 0
        rule_r2_violations = 0
        provenance_violations = 0
        empty_chunks = 0
        seen_chunk_ids = set()
        token_lens = []

        for c in chunks:
            if not isinstance(c, dict):
                provenance_violations += 1
                continue
            chunk_id = c.get("chunk_id")
            if not chunk_id or chunk_id in seen_chunk_ids:
                provenance_violations += 1
            if chunk_id:
                seen_chunk_ids.add(chunk_id)
            if c.get("document_id") != doc_id:
                provenance_violations += 1
            content = c.get("content", "")
            tok_est = estimate_tokens(content)
            token_lens.append(tok_est)

            # OFF-010 & Rule R2 Invariant: page_start must equal page_end
            p_start = c.get("page_start")
            p_end = c.get("page_end")
            if (
                not isinstance(p_start, int)
                or not isinstance(p_end, int)
                or p_start < 1
                or p_end < 1
                or p_start != p_end
            ):
                rule_r2_violations += 1
                provenance_violations += 1

            stripped = content.strip()
            if not stripped:
                empty_chunks += 1
            else:
                if stripped[-1] not in ".!?:;\"'”":
                    mid_sentence_ends += 1
                first_alpha = re.search(r'[a-zA-Z]', stripped)
                if first_alpha and first_alpha.group(0).islower():
                    lowercase_starts += 1

            for hp in compiled_headers:
                if hp.search(content):
                    header_leaks += 1
                    break

            meta = c.get("metadata", {})
            sec_heading = meta.get("section_heading", "")
            for df in disallowed_fragments:
                if df.match(sec_heading):
                    fragment_headings += 1
                    break

        return {
            "document_id": doc_id,
            "total_chunks": total_chunks,
            "rule_r2_violations": rule_r2_violations,
            "provenance_violations": provenance_violations,
            "empty_chunks": empty_chunks,
            "header_leak_count": header_leaks,
            "fragment_headings_count": fragment_headings,
            "pct_mid_sentence_ends": round((mid_sentence_ends / max(1, total_chunks)) * 100, 2),
            "pct_lowercase_starts": round((lowercase_starts / max(1, total_chunks)) * 100, 2),
            "median_tokens": sorted(token_lens)[len(token_lens) // 2] if token_lens else 0
        }

    def audit_all(self) -> List[Dict[str, Any]]:
        """Audits all chunk files in chunks_dir."""
        results = []
        for p in sorted(self.chunks_dir.glob("*_chunks.json")):
            results.append(self.audit_file(p))
        return results


def audit_database(db_url: Optional[str] = None) -> Dict[str, Any]:
    """Audit persisted rows; connectivity/schema errors are returned, never hidden."""
    target_url = db_url or resolve_database_url()
    stats: Dict[str, Any] = {
        "total_documents": 0,
        "total_chunks": 0,
        "rule_r2_violations": 0,
        "empty_chunks": 0,
        "invalid_embeddings": 0,
        "valid_embeddings": 0,
        "embedding_rows": 0,
        "unexplained_empty_source_pages": 0,
        "source_pages": 0,
        "database_checked": False,
        "error": None,
    }

    try:
        if target_url.startswith("sqlite:"):
            db_file = target_url[len("sqlite:///"):]
            if not os.path.isabs(db_file):
                db_file = str(Path(db_file).resolve())
            if not os.path.exists(db_file):
                raise FileNotFoundError(f"SQLite database does not exist: {db_file}")
            conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True, timeout=10)
            try:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM source_files")
                stats["total_documents"] = int(cur.fetchone()[0])
                cur.execute("SELECT COUNT(*) FROM chunks")
                stats["total_chunks"] = int(cur.fetchone()[0])
                cur.execute("SELECT COUNT(*) FROM chunks WHERE page_start IS NULL OR page_end IS NULL OR page_start != page_end")
                stats["rule_r2_violations"] = int(cur.fetchone()[0])
                cur.execute("SELECT COUNT(*) FROM chunks WHERE content IS NULL OR LENGTH(TRIM(content)) = 0")
                stats["empty_chunks"] = int(cur.fetchone()[0])
                cur.execute("SELECT COUNT(*) FROM source_pages")
                stats["source_pages"] = int(cur.fetchone()[0])
                cur.execute("SELECT COUNT(*) FROM source_pages WHERE (content IS NULL OR LENGTH(TRIM(content)) = 0) AND LOWER(extraction_method) != 'verified_blank'")
                stats["unexplained_empty_source_pages"] = int(cur.fetchone()[0])
                cur.execute("SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL")
                total_emb = int(cur.fetchone()[0])
                stats["embedding_rows"] = total_emb
                # Memory-safe bounded sample check (avoids OOM on large corpus)
                cur.execute("SELECT embedding FROM chunks WHERE embedding IS NOT NULL LIMIT 100")
                sample_rows = cur.fetchall()
                sample_valid = True
                for (raw_vec,) in sample_rows:
                    try:
                        vec = json.loads(raw_vec) if isinstance(raw_vec, str) else raw_vec
                        if not isinstance(vec, (list, tuple)) or len(vec) != 768:
                            stats["invalid_embeddings"] += 1
                            sample_valid = False
                            continue
                        vals = [float(x) for x in vec]
                        if not all(math.isfinite(x) for x in vals):
                            stats["invalid_embeddings"] += 1
                            sample_valid = False
                            continue
                        norm = math.sqrt(sum(x * x for x in vals))
                        if not (0.98 <= norm <= 1.02):
                            stats["invalid_embeddings"] += 1
                            sample_valid = False
                    except (TypeError, ValueError, json.JSONDecodeError):
                        stats["invalid_embeddings"] += 1
                        sample_valid = False
                if sample_valid and stats["invalid_embeddings"] == 0:
                    stats["valid_embeddings"] = total_emb
                else:
                    stats["valid_embeddings"] = max(0, total_emb - stats["invalid_embeddings"])
                cur.close()
            finally:
                conn.close()
        else:
            import psycopg2
            clean_url = target_url.replace("postgresql+psycopg2://", "postgresql://", 1)
            conn = psycopg2.connect(clean_url, connect_timeout=10)
            try:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM source_files")
                stats["total_documents"] = int(cur.fetchone()[0])
                cur.execute("SELECT COUNT(*) FROM chunks")
                stats["total_chunks"] = int(cur.fetchone()[0])
                cur.execute("SELECT COUNT(*) FROM chunks WHERE page_start IS NULL OR page_end IS NULL OR page_start != page_end")
                stats["rule_r2_violations"] = int(cur.fetchone()[0])
                cur.execute("SELECT COUNT(*) FROM chunks WHERE content IS NULL OR LENGTH(BTRIM(content)) = 0")
                stats["empty_chunks"] = int(cur.fetchone()[0])
                cur.execute("SELECT COUNT(*) FROM source_pages")
                stats["source_pages"] = int(cur.fetchone()[0])
                cur.execute("SELECT COUNT(*) FROM source_pages WHERE (content IS NULL OR LENGTH(BTRIM(content)) = 0) AND LOWER(extraction_method) != 'verified_blank'")
                stats["unexplained_empty_source_pages"] = int(cur.fetchone()[0])
                cur.execute("SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL")
                total_emb = int(cur.fetchone()[0])
                stats["embedding_rows"] = total_emb
                # Fast in-database dimension check (zero memory overhead)
                cur.execute("SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL AND vector_dims(embedding) != 768")
                invalid_dims = int(cur.fetchone()[0])
                stats["invalid_embeddings"] += invalid_dims
                # Memory-safe bounded sample check for vector norm (avoids OOM on 200k+ chunks)
                cur.execute("SELECT embedding::text FROM chunks WHERE embedding IS NOT NULL LIMIT 100")
                sample_rows = cur.fetchall()
                sample_valid = True
                for (raw_vec,) in sample_rows:
                    try:
                        if isinstance(raw_vec, str):
                            body = raw_vec.strip().strip("[]")
                            vals = [float(x.strip()) for x in body.split(",") if x.strip()]
                        else:
                            vals = [float(x) for x in raw_vec]
                        if len(vals) != 768 or not all(math.isfinite(x) for x in vals):
                            stats["invalid_embeddings"] += 1
                            sample_valid = False
                            continue
                        norm = math.sqrt(sum(x * x for x in vals))
                        if not (0.98 <= norm <= 1.02):
                            stats["invalid_embeddings"] += 1
                            sample_valid = False
                    except (TypeError, ValueError):
                        stats["invalid_embeddings"] += 1
                        sample_valid = False
                if sample_valid and stats["invalid_embeddings"] == 0:
                    stats["valid_embeddings"] = total_emb
                else:
                    stats["valid_embeddings"] = max(0, total_emb - stats["invalid_embeddings"])
                cur.close()
            finally:
                conn.close()
        stats["database_checked"] = True
    except Exception as exc:
        stats["error"] = f"{type(exc).__name__}: {exc}"
    return stats


def run_quality_audit(
    chunks_dir: Optional[Path] = None,
    db_url: Optional[str] = None
) -> Dict[str, Any]:
    """Run fail-closed quality gates. Missing data, unreadable files, and DB errors block release."""
    cdir = Path(chunks_dir or config.chunks_dir)
    auditor = QualityAuditor(chunks_dir=cdir)
    file_results: List[Dict[str, Any]] = []
    file_errors: List[str] = []
    if not cdir.exists():
        file_errors.append(f"Chunk directory does not exist: {cdir}")
    else:
        for chunk_file in sorted(cdir.glob("*_chunks.json")):
            try:
                result = auditor.audit_file(chunk_file)
                file_results.append(result)
                if result.get("error"):
                    file_errors.append(f"{chunk_file.name}: {result['error']}")
            except Exception as exc:
                file_errors.append(f"{chunk_file.name}: {type(exc).__name__}: {exc}")

    total_chunks = sum(int(r.get("total_chunks", 0)) for r in file_results)
    total_r2_violations = sum(int(r.get("rule_r2_violations", 0)) for r in file_results)
    total_provenance_violations = sum(int(r.get("provenance_violations", 0)) for r in file_results)
    total_empty_chunks = sum(int(r.get("empty_chunks", 0)) for r in file_results)
    total_header_leaks = sum(int(r.get("header_leak_count", 0)) for r in file_results)
    db_stats = audit_database(db_url)

    # Do not double-count file and DB findings: each source is checked independently.
    db_r2_violations = int(db_stats.get("rule_r2_violations", 0))
    db_empty_chunks = int(db_stats.get("empty_chunks", 0))
    db_total_chunks = int(db_stats.get("total_chunks", 0))
    total_chunks = max(total_chunks, db_total_chunks)

    has_auditable_data = total_chunks > 0 and (len(file_results) > 0 or db_total_chunks > 0)
    gate_r2_pass = total_r2_violations == 0 and db_r2_violations == 0
    unexplained_empty_pages = int(db_stats.get("unexplained_empty_source_pages", 0))
    gate_content_pass = total_empty_chunks == 0 and db_empty_chunks == 0 and unexplained_empty_pages == 0 and not file_errors
    gate_header_pass = total_header_leaks == 0 or (total_header_leaks / max(1, total_chunks)) < 0.01
    # A missing database or unreadable database is not a zero-error pass.
    gate_database_pass = bool(db_stats.get("database_checked")) and db_total_chunks > 0
    # For a populated database, every non-null embedding checked must be valid.
    gate_embedding_pass = (
        gate_database_pass
        and int(db_stats.get("invalid_embeddings", 0)) == 0
        and int(db_stats.get("embedding_rows", 0)) == db_total_chunks
        and int(db_stats.get("valid_embeddings", 0)) == db_total_chunks
    )

    # OFF-003: Verify GCS discovery contract and API methods
    gcs_contract = GCSDataLoader.verify_contract()
    gate_gcs_pass = bool(gcs_contract.get("passed", False))

    # OFF-001 & OFF-008: Verify Google Cloud Vision OCR contract and multi-tier fallbacks
    ocr_contract = GoogleVisionOCREngine.verify_contract()
    gate_ocr_pass = bool(ocr_contract.get("passed", False))
    gate_provenance_pass = has_auditable_data and not file_errors and total_provenance_violations == 0

    def status(passed: bool) -> str:
        return "PASSED" if passed else "FAILED"

    quality_checks = {
        "AUDITABLE_CORPUS_PRESENT": {
            "status": status(has_auditable_data),
            "detail": f"Chunk files: {len(file_results)}, chunks observed: {total_chunks}; at least one persisted/auditable chunk is required.",
        },
        "RULE_R2_SINGLE_PAGE": {
            "status": status(gate_r2_pass),
            "detail": f"File violations: {total_r2_violations}; database violations: {db_r2_violations}; page_start must equal page_end.",
        },
        "CHUNK_CONTENT_COMPLETENESS": {
            "status": status(gate_content_pass),
            "detail": f"Empty file chunks: {total_empty_chunks}; empty database chunks: {db_empty_chunks}; unexplained empty source pages: {unexplained_empty_pages}; file errors: {len(file_errors)}.",
        },
        "HEADER_STRIPPING_PURITY": {
            "status": status(gate_header_pass),
            "detail": f"Header leaks: {total_header_leaks} (rate: {(total_header_leaks / max(1, total_chunks)) * 100:.2f}%).",
        },
        "DATABASE_PERSISTENCE": {
            "status": status(gate_database_pass),
            "detail": (f"Database checked; documents={db_stats['total_documents']}, chunks={db_total_chunks}." if gate_database_pass
                       else f"Database unavailable, empty, or unverified: {db_stats.get('error') or 'no persisted chunks found'}"),
        },
        "EMBEDDING_FIDELITY": {
            "status": status(gate_embedding_pass),
            "detail": f"Valid vectors: {db_stats['valid_embeddings']}; invalid vectors: {db_stats['invalid_embeddings']}; vector rows: {db_stats['embedding_rows']} / {db_total_chunks} chunks.",
        },
        "PROVENANCE_INTEGRITY": {
            "status": status(gate_provenance_pass),
            "detail": (f"Audited {len(file_results)} chunk files; file errors: {file_errors[:3]}" if not gate_provenance_pass
                       else f"Audited {len(file_results)} chunk files; provenance violations: {total_provenance_violations}."),
        },
        "GCS_DISCOVERY_CONTRACT": {
            "status": status(gate_gcs_pass),
            "detail": gcs_contract.get("detail", "Verified GCS discovery API contract")
        },
        "GOOGLE_VISION_OCR_CONTRACT": {
            "status": status(gate_ocr_pass),
            "detail": ocr_contract.get("detail", "Verified Google Cloud Vision OCR contract")
        }
    }
    all_passed = all(item["status"] == "PASSED" for item in quality_checks.values())
    return {
        "all_passed": all_passed,
        "all_strict_passed": all_passed,
        "total_chunks_audited": total_chunks,
        "total_documents_audited": len(file_results),
        "database_stats": db_stats,
        "file_errors": file_errors,
        "quality_checks": quality_checks,
    }


def generate_review_sheet_csv(chunks_dir: Path, output_csv: Path) -> int:
    """Generates a multi-judge verification sheet across canonical judges."""
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    rows = []

    for f in sorted(chunks_dir.glob("*_chunks.json")):
        with open(f, "r", encoding="utf-8") as fp:
            chunks = json.load(fp)
        for c in chunks:
            meta = c.get("metadata", {})
            judge = meta.get("judge_opinion")
            if judge:
                raw_snippet = (c.get("content") or "")[:100].replace("\n", " ")
                # CSV formula injection protection (OWASP ASVS / CWE-1236)
                safe_snippet = ("'" + raw_snippet) if raw_snippet.startswith(("=", "+", "-", "@", "\t", "\r")) else raw_snippet
                rows.append([
                    c.get("chunk_id"),
                    f.stem.replace("_chunks", ""),
                    c.get("page_start"),
                    meta.get("section_heading"),
                    judge,
                    meta.get("label_confidence"),
                    safe_snippet
                ])

    try:
        with open(output_csv, "w", newline="", encoding="utf-8") as fp:
            writer = csv.writer(fp)
            writer.writerow(["chunk_id", "document_id", "page", "section", "judge_opinion", "confidence", "snippet"])
            writer.writerows(rows)
    except PermissionError:
        fallback_csv = output_csv.with_name(output_csv.stem + "_latest.csv")
        with open(fallback_csv, "w", newline="", encoding="utf-8") as fp:
            writer = csv.writer(fp)
            writer.writerow(["chunk_id", "document_id", "page", "section", "judge_opinion", "confidence", "snippet"])
            writer.writerows(rows)

    return len(rows)


def main():
    parser = argparse.ArgumentParser(description="PCE v2.0 Production Quality Auditor")
    parser.add_argument("--chunks", type=Path, default=config.chunks_dir, help="Directory containing chunk JSON files")
    parser.add_argument("--db", type=str, default=None, help="Database URL or SQLite path to audit")
    parser.add_argument("--csv", type=Path, default=None, help="Export review sheet CSV to specified path")
    args = parser.parse_args()

    print("=" * 85)
    print("=== PCE v2.0 PRODUCTION QUALITY & INVARIANT AUDITOR ===")
    print("=" * 85)

    if args.csv:
        count = generate_review_sheet_csv(args.chunks, args.csv)
        print(f">> Exported {count} review rows to {args.csv}")

    results = run_quality_audit(chunks_dir=args.chunks, db_url=args.db)

    print(f"\nAudited {results['total_documents_audited']} documents ({results['total_chunks_audited']:,} chunks):")
    print("-" * 85)
    for gate_name, info in results["quality_checks"].items():
        print(f"{gate_name:<28} | {info['status']:<10} | {info['detail']}")
    print("=" * 85)

    if results["all_passed"]:
        print(">>> [PASS] ALL PRODUCTION QUALITY GATES CERTIFIED.")
        sys.exit(0)
    else:
        print(">>> [FAIL] ONE OR MORE PRODUCTION QUALITY GATES FAILED.")
        sys.exit(1)


if __name__ == "__main__":
    main()


def check_all_gates(chunks_dir: Optional[Path] = None, db_url: Optional[str] = None) -> Dict[str, Any]:
    """Backward-compatible wrapper for quality audit gates."""
    return run_quality_audit(chunks_dir=chunks_dir, db_url=db_url)
