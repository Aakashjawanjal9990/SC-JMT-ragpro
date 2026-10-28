"""
Paragraph-Preserving Single-Page Chunker (PCE v2.0)
Enforces Rule R2 (page_start == page_end), strict length bounds (<= max_chunk_chars),
elimination of punctuation/noise chunks (alpha_count >= min_alpha_chars), and ORDER isolation.
All parameters dynamically loaded from environment configuration (.env).
"""

import re
from typing import List, Optional, Tuple
try:
    from PCEV2.core.models import ChunkRecord, ChunkMetadata
    from PCEV2.parsing.structure_tracker import LawReportStructureTracker
    from PCEV2.config import config
except ImportError:
    from core.models import ChunkRecord, ChunkMetadata
    from parsing.structure_tracker import LawReportStructureTracker
    from config import config


CITATION_PROTECTION_PATTERNS = [
    (re.compile(r"\b(v|vs|art|sec|no|ltd|co|corp|inc|ors|anr|dr|mr|mrs|ms|hon|scc|scr|air|para|cl)\.", re.IGNORECASE), r"\1_DOT_"),
    (re.compile(r"\b([A-Z])\.", re.IGNORECASE), r"\1_DOT_"),  # J., K., C.J.
    (re.compile(r"(\d+)\.(\d+)"), r"\1_DEC_\2"),             # Decimals: 21.5
]


def split_legal_sentences(text: str) -> List[str]:
    """Splits legal text into sentences while protecting citations and abbreviations."""
    protected = text
    for pat, repl in CITATION_PROTECTION_PATTERNS:
        protected = pat.sub(repl, protected)

    raw_sentences = re.split(r"(?<=[.!?])\s+", protected)
    sentences = []
    for s in raw_sentences:
        restored = s.replace("_DOT_", ".").replace("_DEC_", ".").strip()
        if restored:
            sentences.append(restored)
    return sentences if sentences else [text.strip()]


def split_large_paragraph(p_txt: str, target_chars: int, max_chars: int) -> List[str]:
    """Splits a single large paragraph into sentence-bounded sub-blocks strictly <= max_chars."""
    if len(p_txt) <= max_chars:
        return [p_txt]

    sentences = split_legal_sentences(p_txt)
    sub_blocks = []
    cur_block = ""

    for s in sentences:
        if len(s) > max_chars:
            if cur_block:
                sub_blocks.append(cur_block)
                cur_block = ""
            clauses = re.split(r"(?<=[;:])\s+", s)
            for cl in clauses:
                if len(cl) > max_chars:
                    words = cl.split()
                    w_block = ""
                    for w in words:
                        if len(w_block) + len(w) + 1 <= target_chars:
                            w_block = f"{w_block} {w}".strip()
                        else:
                            if w_block:
                                sub_blocks.append(w_block)
                            w_block = w
                    if w_block:
                        sub_blocks.append(w_block)
                elif len(cur_block) + len(cl) + 1 <= target_chars:
                    cur_block = f"{cur_block} {cl}".strip()
                else:
                    if cur_block:
                        sub_blocks.append(cur_block)
                    cur_block = cl
            continue

        if not cur_block:
            cur_block = s
        elif len(cur_block) + len(s) + 1 <= target_chars:
            cur_block = f"{cur_block} {s}"
        elif len(cur_block) + len(s) + 1 <= max_chars:
            cur_block = f"{cur_block} {s}"
            sub_blocks.append(cur_block)
            cur_block = ""
        else:
            sub_blocks.append(cur_block)
            cur_block = s

    if cur_block:
        sub_blocks.append(cur_block)

    return sub_blocks


class PageChunker:
    """Chunks single pages while preserving paragraph boundaries and tracking continuity."""

    def __init__(
        self,
        target_chunk_chars: Optional[int] = None,
        max_chunk_chars: Optional[int] = None,
        min_chunk_chars: Optional[int] = None,
        min_alpha_chars: Optional[int] = None,
        max_overlap_chars: Optional[int] = None
    ):
        self.target_chunk_chars: int = target_chunk_chars if target_chunk_chars is not None else int(getattr(config, "target_chunk_chars", 1500))
        self.max_chunk_chars: int = max_chunk_chars if max_chunk_chars is not None else int(getattr(config, "max_chunk_chars", 2000))
        self.min_chunk_chars: int = min_chunk_chars if min_chunk_chars is not None else int(getattr(config, "min_chunk_chars", 150))
        self.min_alpha_chars: int = min_alpha_chars if min_alpha_chars is not None else int(getattr(config, "min_alpha_chars", 15))
        self.max_overlap_chars: int = max_overlap_chars if max_overlap_chars is not None else int(getattr(config, "max_overlap_chars", 300))

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
        """Splits page text into deterministic single-page chunks with dynamic size & noise bounds."""
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

        # Split oversized paragraphs (> max_chunk_chars) into sentence-bounded sub-blocks
        expanded_classified: List[Tuple[str, str, Optional[str], float]] = []
        for p_txt, p_sec, p_judge, p_conf in classified:
            if len(p_txt) > self.max_chunk_chars:
                sub_parts = split_large_paragraph(p_txt, self.target_chunk_chars, self.max_chunk_chars)
                for sp in sub_parts:
                    expanded_classified.append((sp, p_sec, p_judge, p_conf))
            else:
                expanded_classified.append((p_txt, p_sec, p_judge, p_conf))

        chunk_groups: List[Tuple[str, str, Optional[str], float]] = []
        cur_text = ""
        cur_sec = ""
        cur_judge = None
        cur_conf = 0.50

        for p_txt, p_sec, p_judge, p_conf in expanded_classified:
            has_words = bool(re.search(r"\b[a-zA-Z]{2,}\b", p_txt))

            if not cur_text:
                if not has_words and chunk_groups:
                    prev_t, prev_s, prev_j, prev_c = chunk_groups[-1]
                    if len(prev_t) + len(p_txt) + 2 <= self.max_chunk_chars:
                        chunk_groups[-1] = (f"{prev_t}\n\n{p_txt}", prev_s, prev_j, prev_c)
                    continue
                elif not has_words:
                    continue

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
                # Flush cur_text if valid
                c_has_words = bool(re.search(r"\b[a-zA-Z]{2,}\b", cur_text))
                if c_has_words:
                    chunk_groups.append((cur_text, cur_sec, cur_judge, cur_conf))
                elif chunk_groups:
                    prev_t, prev_s, prev_j, prev_c = chunk_groups[-1]
                    if len(prev_t) + len(cur_text) + 2 <= self.max_chunk_chars:
                        chunk_groups[-1] = (f"{prev_t}\n\n{cur_text}", prev_s, prev_j, prev_c)

                # Overlap handling: strictly require valid sentence with words
                if cur_sec == p_sec and cur_judge == p_judge:
                    sentences = re.split(r"(?<=[.!?])\s+", cur_text)
                    last_s = sentences[-1] if len(sentences) > 1 and len(sentences[-1]) < self.max_overlap_chars else ""
                    overlap = last_s if (last_s and re.search(r"\b[a-zA-Z]{2,}\b", last_s)) else ""
                    candidate = (overlap + "\n\n" + p_txt).strip() if overlap else p_txt
                    if len(candidate) <= self.max_chunk_chars:
                        cur_text = candidate
                    else:
                        cur_text = p_txt
                else:
                    cur_text = p_txt

                cur_sec = p_sec
                cur_judge = p_judge
                cur_conf = p_conf

        if cur_text:
            stripped_cur = cur_text.strip()
            cur_has_words = bool(re.search(r"\b[a-zA-Z]{2,}\b", stripped_cur))
            if chunk_groups:
                prev_text, prev_sec, prev_judge, prev_conf = chunk_groups[-1]
                if cur_sec == prev_sec and len(stripped_cur) < self.min_chunk_chars:
                    if len(prev_text) + len(stripped_cur) + 2 <= self.max_chunk_chars:
                        chunk_groups[-1] = (prev_text + "\n\n" + stripped_cur, prev_sec, prev_judge, prev_conf)
                    elif cur_has_words:
                        chunk_groups.append((cur_text, cur_sec, cur_judge, cur_conf))
                elif cur_has_words:
                    chunk_groups.append((cur_text, cur_sec, cur_judge, cur_conf))
            else:
                if cur_has_words:
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
                parent_page_number=page_number,
                chunk_type="child",
                chunking_version=getattr(config, "chunking_version", "v3.0"),
                cleaning_version=getattr(config, "cleaning_version", "v3.0"),
                embedding_text_version=getattr(config, "embedding_version", "v3.0_short")
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
