"""
4-Stage Document Lifecycle Tracker and CSV Exporter (PCE v2.0)
Tracks parsing -> chunking -> embedding -> vector_db stages.
"""

import csv
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Optional
try:
    from PCEV2.config import config
except ImportError:
    from config import config


def safe_filename(name: str) -> str:
    """Restrict a document ID to a safe filename component (DB IDs are unchanged)."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", str(name)).lstrip(".")
    return cleaned or "document"


def _atomic_write_text(path: Path, text: str) -> None:
    """Write text via a same-directory temp file so readers never see partial output."""
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


class DocumentStageTracker:
    """Tracks document processing status across the 4 ingestion stages."""

    def __init__(self, document_id: str, file_path: str = "", year: int = 0, case_title: str = ""):
        self.document_id = document_id
        self.file_path = file_path
        self.year = year
        self.case_title = case_title or document_id
        self.stages = {
            "provenance": {"status": "PENDING", "duration_sec": 0.0},
            "parsing": {"status": "PENDING", "duration_sec": 0.0},
            "chunking": {"status": "PENDING", "duration_sec": 0.0},
            "embedding": {"status": "PENDING", "duration_sec": 0.0},
            "vector_db": {"status": "PENDING", "duration_sec": 0.0},
        }
        self.overall_status = "PENDING"
        self._t0 = time.time()
        self._current_stage_t0 = None

    def start_stage(self, stage_name: str):
        if stage_name in self.stages:
            self.stages[stage_name]["status"] = "IN_PROGRESS"
            self._current_stage_t0 = time.time()

    def complete_stage(self, stage_name: str, status: str = "COMPLETED"):
        if stage_name in self.stages:
            dur = (time.time() - self._current_stage_t0) if self._current_stage_t0 else 0.0
            self.stages[stage_name]["status"] = status
            self.stages[stage_name]["duration_sec"] = round(dur, 3)


    def fail_stage(self, stage_name: Optional[str] = None) -> None:
        """Mark the named/current in-progress stage failed without masking the root error."""
        if stage_name and stage_name in self.stages:
            self.stages[stage_name]["status"] = "FAILED"
            return
        for name, details in self.stages.items():
            if details["status"] == "IN_PROGRESS":
                details["status"] = "FAILED"
                return

    def finalize(self, total_pages: int, total_chunks: int, ocr_quality: float, error: Optional[str] = None):
        all_ok = all(s["status"] == "COMPLETED" for s in self.stages.values())
        self.overall_status = "SUCCESS" if all_ok and error is None else "FAILED"
        total_time = round(time.time() - self._t0, 3)

        payload = {
            "document_id": self.document_id,
            "case_title": self.case_title,
            "year": self.year,
            "file_path": str(self.file_path),
            "stages": self.stages,
            "overall_status": self.overall_status,
            "total_pages": total_pages,
            "total_chunks": total_chunks,
            "ocr_quality": round(ocr_quality, 4),
            "total_duration_sec": total_time,
            "error": error
        }

        # Save year-partitioned deliverables
        year_dir = config.logss_dir / f"year={self.year}"
        year_dir.mkdir(parents=True, exist_ok=True)
        file_stem = safe_filename(self.document_id)
        json_path = year_dir / f"{file_stem}_status.json"
        md_path = year_dir / f"{file_stem}_status.md"

        md_lines = [
            f"# Status Log: {self.document_id}\n\n",
            f"- **Case Title**: {self.case_title}\n",
            f"- **Year**: {self.year}\n",
            f"- **Overall Status**: `{self.overall_status}`\n",
            f"- **Pages**: {total_pages} | **Chunks**: {total_chunks} | **OCR Quality**: {ocr_quality:.3f}\n",
            f"- **Duration**: {total_time}s\n\n",
            "| Stage | Status | Duration (s) |\n| :--- | :---: | :---: |\n",
        ]
        for st, info in self.stages.items():
            md_lines.append(f"| `{st}` | {info['status']} | {info['duration_sec']} |\n")

        _atomic_write_text(json_path, json.dumps(payload, indent=2))
        _atomic_write_text(md_path, "".join(md_lines))

        return payload


def export_verification_csv(log_payloads: Optional[Any] = None, year_filter: Optional[int] = None) -> Path:
    """Exports status payloads into a structured CSV verification sheet."""
    filename = f"pipeline_verification_sheet_{year_filter}.csv" if year_filter else "pipeline_verification_master.csv"
    out_dir = (config.logss_dir / f"year={year_filter}") if year_filter else config.logss_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / filename

    if not log_payloads or isinstance(log_payloads, (Path, str)):
        payloads = []
        for jpath in sorted(config.logss_dir.glob("year=*/*_status.json")):
            try:
                with open(jpath, "r", encoding="utf-8") as f:
                    payloads.append(json.load(f))
            except (OSError, json.JSONDecodeError):
                payloads.append({})
    else:
        payloads = log_payloads

    filtered = [p for p in payloads if (year_filter is None or p.get("year") == year_filter)]
    headers = [
        "document_id", "case_title", "year", "overall_status", "total_pages", "total_chunks",
        "ocr_quality", "parsing_status", "chunking_status", "embedding_status", "vector_db_status",
        "total_duration_sec"
    ]

    try:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            for p in filtered:
                stages = p.get("stages", {})
                writer.writerow([
                    p.get("document_id"),
                    p.get("case_title"),
                    p.get("year"),
                    p.get("overall_status"),
                    p.get("total_pages"),
                    p.get("total_chunks"),
                    p.get("ocr_quality"),
                    stages.get("parsing", {}).get("status"),
                    stages.get("chunking", {}).get("status"),
                    stages.get("embedding", {}).get("status"),
                    stages.get("vector_db", {}).get("status"),
                    p.get("total_duration_sec")
                ])
    
    except (PermissionError, OSError):
        pass

    return csv_path
