"""
Domain Models for Supreme Court Judgment RAG Pipeline (PCE v2.0)
Conforms strictly to db/schema.sql (Rule R1) and single-page invariant (Rule R2).
"""

from typing import List, Dict, Optional
from dataclasses import dataclass, field


@dataclass
class PageMetadata:
    """Document-level legal metadata extracted primarily from page 1."""
    case_number: Optional[str] = None
    appellant: Optional[str] = None
    respondent: Optional[str] = None
    date_of_judgment: Optional[str] = None
    year: Optional[int] = None
    scr_citation: Optional[str] = None
    court: str = "Supreme Court of India"
    judges: List[str] = field(default_factory=list)
    author: Optional[str] = None
    counsel: List[str] = field(default_factory=list)
    case_law_cited: List[Dict[str, str]] = field(default_factory=list)
    ocr_quality: float = 0.95


@dataclass
class ExtractedPageZones:
    """Page text partitioned into layout-aware coordinate zones."""
    page_number: int
    cleaned_text: str
    printed_page_number: Optional[int] = None
    page_header: Optional[str] = None
    margin_notes: List[str] = field(default_factory=list)
    footnotes: List[str] = field(default_factory=list)
    is_born_digital: bool = False
    ocr_quality: float = 0.95
    extraction_method: str = "pymupdf_vector"


@dataclass
class ChunkMetadata:
    """Extra metadata stored in chunks.metadata JSONB without DDL."""
    section_heading: Optional[str] = None
    judge_opinion: Optional[str] = None
    label_confidence: float = 1.0
    printed_page_number: Optional[int] = None
    ends_mid_sentence: bool = False
    continues_from_prev_page: bool = False
    page_shared_with: Optional[str] = None
    footnotes: List[str] = field(default_factory=list)
    margin_notes: List[str] = field(default_factory=list)
    page_header: Optional[str] = None
    parent_page_number: Optional[int] = None
    chunk_type: str = "child"
    chunking_version: str = "v3.0"
    cleaning_version: str = "v3.0"
    embedding_text_version: str = "v3.0_short"


@dataclass
class ChunkRecord:
    """Single-page chunk strictly obeying Rule R2: page_start == page_end."""
    chunk_id: str                          # Format: {document_id}_p{page}_c{n}
    document_id: str
    page_start: int
    page_end: int                          # Must equal page_start
    chunk_index: int                       # 0-based contiguous per document
    content: str
    section: Optional[str] = None
    embedding: Optional[List[float]] = None
    metadata: ChunkMetadata = field(default_factory=ChunkMetadata)

    def __post_init__(self):
        if self.page_start != self.page_end:
            raise ValueError(f"Invariant violation: page_start ({self.page_start}) != page_end ({self.page_end})")


@dataclass
class DocumentRecord:
    """Full document representation for pipeline tracking."""
    document_id: str
    file_path: str
    year: int
    metadata: PageMetadata
    pages: List[ExtractedPageZones] = field(default_factory=list)
    chunks: List[ChunkRecord] = field(default_factory=list)
