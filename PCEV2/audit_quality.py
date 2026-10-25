"""
Audit Quality Harness for Supreme Court Judgment RAG Pipeline (PCE v2.0)
Measures text quality, header leakage, structure detection, chunk boundaries, and vector fidelity.
"""

import os
import re
import json
import math
from pathlib import Path
from typing import List, Dict, Any, Optional

from PCEV2.config import config


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    words = len(text.split())
    return max(int(words * 1.3), len(text) // 4)


class QualityAuditor:
    """Evaluates chunk files against quality invariants."""

    def __init__(self, chunks_dir: Optional[Path] = None):
        self.chunks_dir = chunks_dir or config.chunks_dir

    def audit_file(self, json_path: Path) -> Dict[str, Any]:
        doc_id = json_path.stem.replace("_chunks", "")
        with open(json_path, "r", encoding="utf-8") as f:
            chunks = json.load(f)

        if not chunks:
            return {"document_id": doc_id, "error": "Empty chunk file"}

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
        token_lens = []

        for c in chunks:
            content = c.get("content", "")
            tok_est = estimate_tokens(content)
            token_lens.append(tok_est)

            stripped = content.strip()
            if stripped:
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
            "header_leak_count": header_leaks,
            "fragment_headings_count": fragment_headings,
            "pct_mid_sentence_ends": round((mid_sentence_ends / max(1, total_chunks)) * 100, 2),
            "pct_lowercase_starts": round((lowercase_starts / max(1, total_chunks)) * 100, 2),
            "median_tokens": sorted(token_lens)[len(token_lens) // 2] if token_lens else 0
        }

    def audit_all(self) -> List[Dict[str, Any]]:
        results = []
        for p in sorted(self.chunks_dir.glob("*_chunks.json")):
            results.append(self.audit_file(p))
        return results


def generate_review_sheet_csv(chunks_dir: Path, output_csv: Path) -> int:
    """Generates a multi-judge verification sheet across canonical judges."""
    import csv
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    rows = []

    for f in sorted(chunks_dir.glob("*_chunks.json")):
        with open(f, "r", encoding="utf-8") as fp:
            chunks = json.load(fp)
        for c in chunks:
            meta = c.get("metadata", {})
            judge = meta.get("judge_opinion")
            if judge:
                rows.append([
                    c.get("chunk_id"),
                    f.stem.replace("_chunks", ""),
                    c.get("page_start"),
                    meta.get("section_heading"),
                    judge,
                    meta.get("label_confidence"),
                    c.get("content")[:100].replace("\n", " ")
                ])

    with open(output_csv, "w", newline="", encoding="utf-8") as fp:
        writer = csv.writer(fp)
        writer.writerow(["chunk_id", "document_id", "page", "section", "judge_opinion", "confidence", "snippet"])
        writer.writerows(rows)

    return len(rows)
