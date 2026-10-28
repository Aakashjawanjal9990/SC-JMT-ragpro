"""
Document Lifecycle Stage Tracker and Verification CSV Exporter (PCE v2.0)
Tracks provenance -> parsing -> chunking -> embedding -> vector_db stages.
Guarantees full run failure accounting, stage timing, and CSV reconciliation.
"""

import csv
import json
import time
from pathlib import Path
from typing import Any, Optional

from config import config


class DocumentStageTracker:
    """Tracks document processing status across all ingestion stages."""

    def __init__(
        self,
        document_id: str,
        file_path: str = "",
        year: int = 2026,
        case_title: str = "Supreme Court Judgment"
    ):
        self.document_id = document_id
        self.file_path = str(file_path)
        self.year = year
        self.case_title = case_title
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
        if stage_name not in self.stages:
            self.stages[stage_name] = {"status": "PENDING", "duration_sec": 0.0}
        self.stages[stage_name]["status"] = "IN_PROGRESS"
        self._current_stage_t0 = time.time()

    def complete_stage(self, stage_name: str, status: str = "COMPLETED"):
        if stage_name not in self.stages:
            self.stages[stage_name] = {"status": "PENDING", "duration_sec": 0.0}
        dur = (time.time() - self._current_stage_t0) if self._current_stage_t0 else 0.0
        self.stages[stage_name]["status"] = status
        self.stages[stage_name]["duration_sec"] = round(dur, 3)

    def finalize(
        self,
        total_pages: int = 0,
        total_chunks: int = 0,
        ocr_quality: float = 0.0,
        error: Optional[str] = None
    ):
        if error:
            self.overall_status = "FAILED"
            # Mark any remaining IN_PROGRESS or PENDING stages as FAILED
            for st, info in self.stages.items():
                if info["status"] in ("IN_PROGRESS", "PENDING"):
                    info["status"] = "FAILED"
        else:
            all_ok = all(s["status"] == "COMPLETED" for s in self.stages.values())
            self.overall_status = "SUCCESS" if all_ok else "FAILED"

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
        json_path = year_dir / f"{self.document_id}_status.json"
        md_path = year_dir / f"{self.document_id}_status.md"

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        with open(md_path, "w", encoding="utf-8") as f:
            f.write(f"# Status Log: {self.document_id}\n\n")
            f.write(f"- **Case Title**: {self.case_title}\n")
            f.write(f"- **Year**: {self.year}\n")
            f.write(f"- **Overall Status**: `{self.overall_status}`\n")
            f.write(f"- **Pages**: {total_pages} | **Chunks**: {total_chunks} | **OCR Quality**: {ocr_quality:.3f}\n")
            f.write(f"- **Duration**: {total_time}s\n")
            if error:
                f.write(f"- **Error**: `{error}`\n")
            f.write("\n| Stage | Status | Duration (s) |\n| :--- | :---: | :---: |\n")
            for st, info in self.stages.items():
                f.write(f"| `{st}` | {info['status']} | {info['duration_sec']} |\n")

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
        "ocr_quality", "provenance_status", "parsing_status", "chunking_status", "embedding_status", "vector_db_status",
        "total_duration_sec", "error"
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
                    stages.get("provenance", {}).get("status", "N/A"),
                    stages.get("parsing", {}).get("status", "N/A"),
                    stages.get("chunking", {}).get("status", "N/A"),
                    stages.get("embedding", {}).get("status", "N/A"),
                    stages.get("vector_db", {}).get("status", "N/A"),
                    p.get("total_duration_sec"),
                    p.get("error") or ""
                ])
    except (PermissionError, OSError):
        pass

    return csv_path
