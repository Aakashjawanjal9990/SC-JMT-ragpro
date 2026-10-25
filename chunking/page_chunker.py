"""
Paragraph-Preserving Single-Page Chunker (PCE v2.0)
Enforces Rule R2 (page_start == page_end), continuity flags, and ORDER isolation.
"""

import re
from typing import List, Optional, Tuple
try:
    from PCEV2.core.models import ChunkRecord, ChunkMetadata
    from PCEV2.parsing.structure_tracker import LawReportStructureTracker
except ImportError:
    from core.models import ChunkRecord, ChunkMetadata
    from parsing.structure_tracker import LawReportStructureTracker


class PageChunker:
    """Chunks single pages while preserving paragraph boundaries and tracking continuity."""

    def __init__(
        self,
        target_chunk_chars: int = 1500,
        max_chunk_chars: int = 2000,
        min_chunk_chars: int = 150
    ):
        self.target_chunk_chars = target_chunk_chars
        self.max_chunk_chars = max_chunk_chars
        self.min_chunk_chars = min_chunk_chars

    def chunk_page(
        self,
        document_id: str,
        page_number: int,
        cleaned_text: str,
        structure_tracker: Optional[LawReportStructureTracker] = None,
        section_heading: Optional[str] = None,
        judge_opinion: Optional[str] = None,
        printed_page_number: Optional[int] = None,
        ocr_quality: float = 0.95,
        prev_page_ended_mid: bool = False,
        page_shared_with: Optional[str] = None,
        footnotes: Optional[List[str]] = None,
        margin_notes: Optional[List[str]] = None,
        page_header: Optional[str] = None
    ) -> List[ChunkRecord]:
        """Splits page text into deterministic single-page chunks."""
        if not cleaned_text or not cleaned_text.strip():
            return []

        if structure_tracker is None:
            structure_tracker = LawReportStructureTracker()
            if section_heading:
                structure_tracker.current_section = section_heading
                if section_heading == "OPINION":
                    structure_tracker.is_front_matter_complete = True
            if judge_opinion:
                structure_tracker.current_judge = judge_opinion

        raw_paras = [p.strip() for p in cleaned_text.split("\n\n") if p.strip()]
        if not raw_paras:
            return []

        classified = structure_tracker.split_and_classify_paragraphs(raw_paras, page_number)
        if not classified:
            return []

        chunk_groups: List[Tuple[str, str, Optional[str], float]] = []
        cur_text = ""
        cur_sec = ""
        cur_judge = None
        cur_conf = 0.50

        for p_txt, p_sec, p_judge, p_conf in classified:
            if not cur_text:
                cur_text = p_txt
                cur_sec = p_sec
                cur_judge = p_judge
                cur_conf = p_conf
            elif cur_sec == p_sec and cur_judge == p_judge and (
                len(cur_text) + len(p_txt) + 2 <= self.target_chunk_chars
                or (len(cur_text) < self.min_chunk_chars and len(cur_text) + len(p_txt) + 2 <= self.max_chunk_chars)
            ):
                cur_text += "\n\n" + p_txt
            else:
                chunk_groups.append((cur_text, cur_sec, cur_judge, cur_conf))
                # Trailing 1-sentence overlap only if section and judge are the same
                if cur_sec == p_sec and cur_judge == p_judge:
                    sentences = re.split(r"(?<=[.!?])\s+", cur_text)
                    overlap = sentences[-1] if len(sentences) > 1 and len(sentences[-1]) < 300 else ""
                    cur_text = (overlap + "\n\n" + p_txt).strip() if overlap else p_txt
                else:
                    cur_text = p_txt
                cur_sec = p_sec
                cur_judge = p_judge
                cur_conf = p_conf

        if cur_text:
            chunk_groups.append((cur_text, cur_sec, cur_judge, cur_conf))

        records = []
        for idx, (ctext, csec, cjudge, cconf) in enumerate(chunk_groups, 1):
            chunk_id = f"{document_id}_p{page_number}_c{idx}"
            stripped = ctext.strip()
            ends_mid = bool(stripped and stripped[-1] not in ".!?:;\"'”")

            continues_mid = False
            if idx == 1 and prev_page_ended_mid:
                first_alpha = re.search(r"[a-zA-Z]", stripped)
                if first_alpha and first_alpha.group(0).islower():
                    continues_mid = True

            chunk_meta = ChunkMetadata(
                section_heading=csec,
                judge_opinion=cjudge if csec == "OPINION" else None,
                label_confidence=cconf,
                printed_page_number=printed_page_number or page_number,
                ends_mid_sentence=ends_mid,
                continues_from_prev_page=continues_mid,
                page_shared_with=page_shared_with,
                footnotes=footnotes or [],
                margin_notes=margin_notes or [],
                page_header=page_header,
                chunking_version="v3.0",
                cleaning_version="v3.0",
                embedding_text_version="v3.0_short"
            )

            records.append(ChunkRecord(
                chunk_id=chunk_id,
                document_id=document_id,
                page_start=page_number,
                page_end=page_number,
                chunk_index=idx - 1,
                content=ctext,
                section=csec,
                metadata=chunk_meta
            ))

        return records
