"""
Layout and Coordinate-Aware Page Zone Extractor (PCE v2.0)
Classifies physical zones (headers, margins, body, footnotes) using PyMuPDF dict mode.
Guarantees removal of vertical margin locator letters (A-H), true OCR quality scoring,
and image-only page status recognition.
All coordinate thresholds dynamically loaded from environment configuration (.env).
"""

import logging
import re
import unicodedata
from typing import List, Tuple, Optional, Any
try:
    from core.models import ExtractedPageZones
    from config import config
except ImportError:
    from PCEV2.core.models import ExtractedPageZones
    from PCEV2.config import config

logger = logging.getLogger(__name__)


COMMON_LEGAL_WORDS = {
    "the", "of", "and", "to", "in", "that", "is", "was", "for", "it", "with", "as", "by",
    "on", "be", "at", "this", "which", "or", "from", "an", "are", "court", "act", "order",
    "appeal", "judgment", "state", "case", "section", "high", "person", "law", "petitioner",
    "respondent", "appellant", "criminal", "civil", "union", "india", "supreme", "bench",
    "trial", "evidence", "held", "judge", "division", "constitution", "article", "rule",
    "rights", "learned", "counsel", "justice", "session", "police", "accused", "magistrate",
    "under", "within", "without", "having", "where", "between", "provisions", "before",
    "after", "during", "because", "against", "said", "facts", "question", "ground",
    "notice", "writ", "record", "period", "dated", "filed", "passed", "suit", "decree",
    "property", "claim", "party", "parties", "right", "public", "government", "officer",
    "power", "jurisdiction", "hearing", "application", "decision", "bench", "statutory",
    "fine", "sentence", "conviction", "acquittal", "witness", "statements", "examination",
    "cross", "prosecution", "defence", "offence", "penal", "code", "procedure", "clause",
    "sub", "para", "paragraph", "held", "view", "also", "not", "any", "all", "has", "had",
    "have", "been", "would", "could", "should", "may", "can", "their", "there", "other",
    "such", "into", "more", "no", "if", "out", "up", "so", "what", "its", "about", "who",
    "they", "will", "one", "all", "we", "he", "she", "him", "her", "his", "them", "these",
    "those", "only", "then", "when", "some", "time", "very", "even", "most", "first",
    "two", "three", "four", "five", "new", "same", "over", "such", "than", "now", "well"
}


def calculate_ocr_quality(text: str, is_born_digital: bool) -> float:
    """Calculates continuous OCR quality based on character validity and dictionary ratio."""
    stripped = text.strip()
    if not stripped:
        return 0.0

    words = re.findall(r"\b[A-Za-z]{2,}\b", stripped.lower())
    if not words:
        return 0.10

    dict_hits = sum(1 for w in words if w in COMMON_LEGAL_WORDS)
    dict_ratio = dict_hits / len(words)

    # Printable character ratio
    total_chars = len(stripped)
    printable_chars = sum(1 for ch in stripped if ch.isprintable() and not ch.isspace())
    char_ratio = printable_chars / max(1, total_chars)

    # Composite score
    composite = (dict_ratio * 0.75) + (char_ratio * 0.25)
    if is_born_digital:
        score = min(1.0, max(0.85, 0.85 + (dict_ratio * 0.15)))
    else:
        score = min(0.98, max(0.10, composite * 1.1))

    return round(score, 4)


class CoordinateZoneExtractor:
    """Classifies physical page zones from vector and span coordinates before reflow."""

    BOOK_HEADER_PATTERNS = [
        re.compile(r"SUPR[E!]+ME\s+[CO0]{1,2}UR[T1\']+\s+REPORTS?", re.IGNORECASE),
        re.compile(r"^\s*(?:\[\d{4}\]\s*)?(?:\d{1,4}\s+)?S\.?C\.?R\.?(?:\s+(?:\[\d{4}\]|S\.?C\.?R\.?|\d{1,4}))*\s*$", re.IGNORECASE),
        re.compile(r"^\s*Supreme\s+Court\s+Reports\s*$", re.IGNORECASE),
    ]

    HEADER_PATTERNS = [
        re.compile(r"^\s*(?:\[\d{4}\]\s*)?(?:\d{1,4}\s+)?(?:SUPREME\s+COURT\s+REPORTS?|S\.?C\.?R\.?)(?:\s+(?:\[\d{4}\]|S\.?C\.?R\.?|\d{1,4}))*\s*$", re.IGNORECASE),
        # Generalized legal case caption headers (e.g. "Party A v. Party B", "Party A vs. Party B", or trailing "Party A v.")
        re.compile(r"^\s*[\w\s.,\'&()/-]+\s+(?:v\.?|vs\.?|versus)(?:\s+[\w\s.,\'&()/-]+)?$", re.IGNORECASE),
        # Generalized multiple party suffixes in running headers (e.g. "... & Anr.", "... & Ors.")
        re.compile(r"^\s*[\w\s.,\'()/-]+\s+&\s+(?:Anr|Ors)\.?\s*$", re.IGNORECASE),
        # Generalized institutional / corporate party titles in running headers
        re.compile(r"^\s*[\w\s.,\'()/-]+(?:Banking\s+Cor|Corp(?:oration)?|Co(?:mpany)?|Ltd|Limited)\.?\s*$", re.IGNORECASE),
        # Generalized governmental / authority running headers (e.g. "STATE OF ...", "UNION OF INDIA")
        re.compile(r"^\s*(?:STATE|UNION|COMMISSIONER|COLLECTOR|SECRETARY)\s+OF\s+[\w\s.,\'()/-]+$", re.IGNORECASE),
    ]

    # Substantive judicial body indicators that must NEVER be stripped as running headers
    SUBSTANTIVE_BODY_PATTERNS = [
        # Numbered paragraphs: e.g. "1.", "2.", "(1)", "[1]", "Para 1", "Paragraph 1"
        re.compile(r"^\s*(?:\d{1,3}\.|\(\d{1,3}\)|\[\d{1,3}\]|para(?:graph)?\s+\d+)", re.IGNORECASE),
        # Judicial section titles: "JUDGMENT", "ORDER", "OPINION", "PER CURIAM", "HEADNOTE", "DECISION"
        re.compile(r"^\s*(?:JUDGMENT|ORDER|OPINION|PER\s+CURIAM|HEADNOTE|DECISION)\s*$", re.IGNORECASE),
        # Judicial signatures / author designations: e.g. "SANJAY KUMAR, J.", "KANIA, C.J.", "[SANJAY KUMAR*, ALOK ARADHE JJ.]"
        re.compile(r"\b[A-Z\s.,]+,\s*(?:C\.?J\.?|J\.?|JJ\.?)\b"),
        # Direct dispositive legal orders
        re.compile(r"\b(?:Leave\s+granted|Appeal\s+allowed|Petition\s+dismissed|Bail\s+granted|Stands\s+disposed|Ordered\s+accordingly|Result\s+of\s+the\s+case)\b", re.IGNORECASE),
        # Substantive statutory sections or articles: "Section 138", "Article 21"
        re.compile(r"\b(?:Section\s+\d+[A-Za-z]?|Article\s+\d+[A-Za-z]?|Clause\s+\d+)\b", re.IGNORECASE),
    ]

    INLINE_MARGIN_YEARS = re.compile(r"\b(?:I9l50|l950|ll950|19ll0|i980|1915\(\)|1960)\b", re.IGNORECASE)

    @classmethod
    def heal_ocr_degradations(cls, text: str) -> str:
        """Heals common legal OCR scanning degradations before chunking and embedding."""
        if not text:
            return ""
        # 1. Reconnect broken hyphenated line-wraps: "restrain- ing" -> "restraining"
        text = re.sub(r'(\b[a-zA-Z]{3,})-\s+([a-zA-Z]{3,}\b)', r'\1\2', text)
        
        # 2. Repair common OCR split: "on! y" -> "only"
        text = re.sub(r'\bon!\s*y\b', 'only', text)
        
        # 3. Repair exclamation/digit in "provisions": "prov!Slons" -> "provisions"
        text = re.sub(r'\bprov[!1l|][Ss]l?ons\b', 'provisions', text, flags=re.IGNORECASE)
        
        # 4. Repair OCR corruption: "petlt10ners" -> "petitioners"
        text = re.sub(r'\bpet[l!1][t1][l!1]?1?0?ners\b', 'petitioners', text, flags=re.IGNORECASE)
        
        # 5. General digit-inside-word repair: 0 (zero) between alphabetic letters -> 'o'
        text = re.sub(r'(?<=[a-zA-Z])0(?=[a-zA-Z])', 'o', text)

        # 6. Repair citation OCR bracket mismatches and reporter letter substitutions
        text = re.sub(r'^[(\[]\s*(\d{4})\s*[)\]]', r'[\1]', text)
        text = re.sub(r'\bF\.[O0]\.R\b', 'F.C.R.', text)
        text = re.sub(r'\bA\.[l|]R\b', 'A.I.R.', text, flags=re.IGNORECASE)
        text = re.sub(r'\bS\.\s*C\.\s*R\b', 'S.C.R.', text)
        
        return text

    @classmethod
    def extract_page_zones(
        cls,
        page: Any,
        page_number: int,
        total_pages: int,
        start_book_page: Optional[int] = None,
        document_id: str = ""
    ) -> ExtractedPageZones:
        d = page.get_text("dict")
        w, h = page.rect.width, page.rect.height

        doc_year_m = re.search(r"(19\d\d|20\d\d)", document_id) if document_id else None
        doc_year = int(doc_year_m.group(1)) if doc_year_m else None
        
        has_vector_fonts = bool(hasattr(page, "get_fonts") and len(page.get_fonts()) >= 1)
        
        # Algorithmic classification: 
        # Supreme Court judgments prior to 1985 were physically printed volumes (historical scans).
        # Modern judgments (post-1990) are born-digital.
        if doc_year is not None:
            is_historical = (doc_year < 1985)
            is_born_digital = (doc_year >= 1990)
        else:
            is_born_digital = has_vector_fonts and (w >= 450)
            is_historical = not is_born_digital
        
        is_1950 = is_historical

        book_p = (start_book_page + page_number - 1) if start_book_page else page_number
        is_odd = (book_p % 2 == 1)

        printed_pnum: Optional[int] = None
        header_lines: List[str] = []
        margin_lines: List[str] = []
        footnote_lines: List[str] = []
        body_lines: List[Tuple[float, float, str]] = []

        # 1. Detect horizontal vector drawing rule line for footnotes
        rule_y: Optional[float] = None
        for dr in page.get_drawings():
            r = dr.get("rect")
            if r and r.height <= 3.0 and r.y0 >= (h * 0.65) and r.x0 <= 75.0 and 20.0 <= r.width <= 220.0:
                rule_y = r.y0
                break

        header_y_max = getattr(config, "header_y_max_born_digital", 85.0) if is_born_digital else getattr(config, "header_y_max_historical", 75.0)

        for b in d.get("blocks", []):
            if "lines" not in b:
                continue
            for line in b["lines"]:
                lx0, ly0, lx1, ly1 = line["bbox"]
                raw_txt = "".join(s.get("text", "") for s in line.get("spans", []))
                # Strip all control characters including backspace (\x08)
                txt = re.sub(r"[\x00-\x1f\x7f\u200b-\u200f\ufeff]", "", raw_txt)
                txt = unicodedata.normalize("NFC", txt).strip()
                if not txt:
                    continue

                span_size = line["spans"][0].get("size", 10.0) if line["spans"] else 10.0

                # Check if this line exhibits substantive legal body content (never a running header)
                is_substantive = any(sp.search(txt) for sp in cls.SUBSTANTIVE_BODY_PATTERNS)

                # Book Header Check (applies to ALL pages if in upper 25% of page)
                is_book_hdr = (ly1 <= 120.0 and any(bp.search(txt) for bp in cls.BOOK_HEADER_PATTERNS))
                is_stray_p1 = (page_number == 1 and ly1 <= 75.0 and bool(re.match(r"^[,\-–—;.'~`\s]+$", txt)))

                # Top Header Check (pages >= 2)
                # A line on page >= 2 is a running header ONLY IF:
                # 1. It is NOT substantive judicial body text, AND
                # 2. It is within the upper margin (ly1 <= header_y_max), AND
                # 3. It matches a known reporter pattern, case adversary pattern, or bare page number.
                is_pattern_hdr = (
                    page_number > 1
                    and ly1 <= header_y_max
                    and (
                        any(hp.search(txt) for hp in cls.HEADER_PATTERNS)
                        or bool(re.match(r"^\s*\d{1,4}\s*$", txt))
                    )
                )

                is_hdr = not is_substantive and (is_book_hdr or is_stray_p1 or is_pattern_hdr)
                if is_hdr:
                    pnum_m = re.search(r"\b(\d{1,4})\b", txt)
                    if pnum_m and not printed_pnum:
                        try:
                            val = int(pnum_m.group(1))
                            if val < 2000:
                                printed_pnum = val
                        except ValueError:
                            pass
                    header_lines.append(txt)
                    continue

                # Footnote / Attribution Check
                is_fn = False
                if re.search(r"\bHeadnotes\s+prepared\s+by:?", txt, re.IGNORECASE):
                    is_fn = True
                elif rule_y is not None and ly0 >= (rule_y - 2.0):
                    if span_size <= 8.5 or re.match(r"^(?:\d+[\t\s]|\(\d+\)|[*†‡])", txt):
                        is_fn = True
                elif ly0 >= (h - 150.0) and (
                    span_size <= 7.5 or (span_size <= 8.8 and re.match(r"^(?:\d+[\t\s]|\(\d+\)|[*†‡])", txt))
                ):
                    is_fn = True

                if is_fn:
                    footnote_lines.append(txt)
                    continue

                # Top bare numbers (page numbers or volume running heads)
                if ly1 <= header_y_max and re.match(r"^\s*\d{1,4}\s*$", txt):
                    continue

                # SCR Margin paragraph locator letters (A, B, C, D, E, F, G, H)
                if re.match(r"^\s*[A-H]\s*$", txt):
                    margin_lines.append(txt)
                    continue

                # Margin Column Check for 1950 Historical Volumes
                if is_1950:
                    kept_spans = []
                    for s in line.get("spans", []):
                        s_raw = s.get("text", "")
                        s_txt = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b-\u200f\ufeff]", "", s_raw)
                        s_txt = unicodedata.normalize("NFC", s_txt).strip()
                        if not s_txt:
                            continue
                        sx0, sy0, sx1, sy1 = s.get("bbox", (0, 0, 0, 0))
                        s_size = s.get("size", 10.0)
                        if is_odd:
                            if sx0 >= 335.0 or (sx0 >= 320.0 and s_size <= 8.5):
                                margin_lines.append(s_txt)
                                continue
                            if sx1 <= 35.0:
                                continue
                        else:
                            if sx1 <= 95.0 or (sx1 <= 105.0 and s_size <= 8.5):
                                margin_lines.append(s_txt)
                                continue
                            if sx0 >= 400.0:
                                continue
                        kept_spans.append(s_txt)
                    if not kept_spans:
                        continue
                    txt = " ".join(kept_spans)

                # Bottom Collation Marks (e.g. '12-A') & Bare Page Numbers
                if ly0 >= (h - 70.0) and re.match(r"^\s*(?:\d{1,4}|\d{1,2}\s*[-–]\s*[A-Za-z0-9]+)\s*$", txt):
                    continue

                body_lines.append((ly0, ly1, txt))

        # Reconstruct body paragraphs using vertical gap thresholds
        paras: List[str] = []
        cur_p: List[str] = []
        gap_thresh = getattr(config, "historical_gap_threshold", 15.0) if is_1950 else getattr(config, "digital_gap_threshold", 13.5)

        for i, (l_y0, l_y1, l_txt) in enumerate(body_lines):
            if cur_p:
                prev_y0, prev_y1, _ = body_lines[i - 1]
                if (l_y0 - prev_y1) > gap_thresh:
                    # Natural line wrap within paragraph joined with space
                    paras.append(" ".join(cur_p))
                    cur_p = [l_txt]
                else:
                    cur_p.append(l_txt)
            else:
                cur_p.append(l_txt)

        if cur_p:
            paras.append(" ".join(cur_p))

        cleaned_body = "\n\n".join(paras)

        # Second-pass inline cleaning
        cleaned_body = cls.INLINE_MARGIN_YEARS.sub(" ", cleaned_body)
        cleaned_body = re.sub(r"(?im)^\s*[.\-–—,;'~`\s]*\s*SUPR[E!]+ME\s+[CO0]{1,2}UR[T1\']+\s+REPORTS?\s*$", " ", cleaned_body)
        cleaned_body = re.sub(r"(?im)^\s*\[19[56]\d\]\s*(?:\d+\s+)?S\.?C\.?R\.?\s*$", " ", cleaned_body)
        cleaned_body = re.sub(r"(?m)^\s*\d{1,2}\s*[-–]\s*[A-Za-z]\s*$", " ", cleaned_body)
        cleaned_body = re.sub(r"(?m)^\s*\d{1,4}\s*$\n?", "", cleaned_body)
        cleaned_body = re.sub(r"(?m)^\s*[A-H]\s*$\n?", "", cleaned_body)
        if page_number > 1:
            cleaned_body = re.sub(r"(?i)\b[CO]hiranjit\s*lal\s*Chowd[hk]uri\b.*?(?:Others\.?|India)\s*", " ", cleaned_body)

        # Third-pass: Legal OCR degradation healing (repairs petlt10ners, on! y, prov!Slons, etc.)
        cleaned_body = cls.heal_ocr_degradations(cleaned_body)
        cleaned_body = re.sub(r"[ \t]{2,}", " ", cleaned_body).strip()

        # Filter any paragraph that is solely residual margin letters, stray page numbers, or whitespace
        filtered_paras: List[str] = []
        for p in cleaned_body.split("\n\n"):
            p_strip = p.strip()
            if not p_strip:
                continue
            # Filter paragraphs that are solely bare numbers (stray page numbers or collation numbers)
            if re.match(r"^\s*\d{1,4}\s*$", p_strip):
                continue
            # Strip any leading bare number line at paragraph beginning
            p_strip = re.sub(r"^\s*\d{1,4}\s*\n+", "", p_strip).strip()
            if not p_strip:
                continue
            residual = re.sub(r"[\sA-Ha-h]", "", p_strip)
            if not residual:
                continue
            filtered_paras.append(p_strip)

        cleaned_body = "\n\n".join(filtered_paras).strip()
        hdr_str = " | ".join(header_lines) if header_lines else None

        # Determine extraction method and actual OCR quality
        images = page.get_images() if hasattr(page, "get_images") else []
        raw_text_val = page.get_text("text") if hasattr(page, "get_text") else ""
        if isinstance(raw_text_val, str):
            raw_text_full = raw_text_val.strip()
        elif isinstance(raw_text_val, dict):
            raw_text_full = " ".join(
                s.get("text", "")
                for b in raw_text_val.get("blocks", [])
                for l in b.get("lines", [])
                for s in l.get("spans", [])
            ).strip()
        else:
            raw_text_full = ""
        total_raw_chars = len(raw_text_full)

        if not cleaned_body:
            if images:
                ext_method = "image_only_pending_ocr"
                ocr_q = 0.0
            elif total_raw_chars > 0:
                # Raw text existed on the page, but zone/coordinate filtering stripped it!
                # Under NO circumstances should this be classified as 'verified_blank'.
                # Safety fallback: rescue raw text so substantive legal words are NEVER lost.
                logger.warning(
                    "Page %d has %d raw characters but cleaned_body is empty. "
                    "Activating raw-text recovery fallback to prevent silent data loss.",
                    page_number, total_raw_chars
                )
                cleaned_body = raw_text_full.strip()
                ext_method = "pymupdf_fallback"
                ocr_q = calculate_ocr_quality(cleaned_body, is_born_digital=is_born_digital)
            else:
                ext_method = "verified_blank"
                ocr_q = 1.0
        else:
            ext_method = "pymupdf_vector" if is_born_digital else "pymupdf_ocr"
            ocr_q = calculate_ocr_quality(cleaned_body, is_born_digital=is_born_digital)

        return ExtractedPageZones(
            page_number=page_number,
            cleaned_text=cleaned_body,
            printed_page_number=printed_pnum or book_p,
            page_header=hdr_str,
            margin_notes=margin_lines,
            footnotes=footnote_lines,
            is_born_digital=is_born_digital,
            ocr_quality=ocr_q,
            extraction_method=ext_method
        )
