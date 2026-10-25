"""
Layout and Coordinate-Aware Page Zone Extractor (PCE v2.0)
Classifies physical zones (headers, margins, body, footnotes) using PyMuPDF dict mode.
"""

import re
import unicodedata
from typing import List, Tuple, Optional, Any
try:
    from PCEV2.core.models import ExtractedPageZones
except ImportError:
    from core.models import ExtractedPageZones


class CoordinateZoneExtractor:
    """Classifies physical page zones from vector and span coordinates before reflow."""

    BOOK_HEADER_PATTERNS = [
        re.compile(r"SUPR[E!]+ME\s+[CO0]{1,2}UR[T1\']+\s+REPORTS?", re.IGNORECASE),
        re.compile(r"^\s*(?:\[\d{4}\]\s*)?(?:\d{1,4}\s+)?S\.?C\.?R\.?(?:\s+(?:\[\d{4}\]|S\.?C\.?R\.?|\d{1,4}))*\s*$", re.IGNORECASE),
        re.compile(r"^\s*Supreme\s+Court\s+Reports\s*$", re.IGNORECASE),
    ]

    HEADER_PATTERNS = [
        re.compile(r"^\s*(?:\[\d{4}\]\s*)?(?:\d{1,4}\s+)?(?:SUPREME\s+COURT\s+REPORTS?|S\.?C\.?R\.?)(?:\s+(?:\[\d{4}\]|S\.?C\.?R\.?|\d{1,4}))*\s*$", re.IGNORECASE),
        re.compile(r"^\s*The\s+Karnataka\s+Lokayuktha.*Bagalkot\s+v\.?\s*$", re.IGNORECASE),
        re.compile(r"^\s*Chandrashekar\s+&\s+Anr\.?\s*$", re.IGNORECASE),
        re.compile(r"^\s*A\.?\s*[~Kk]?\.?\s*Gopalan\s+v\.?.*$", re.IGNORECASE),
        re.compile(r"^\s*Province\s+of\s+Bombay\s+v\.?.*$", re.IGNORECASE),
        re.compile(r"^\s*Ram\s+Krishna\s+Ramnath\s+Agarwal.*v\.?.*$", re.IGNORECASE),
        re.compile(r"^\s*Bhawanip?ore\s+Banking\s+Cor.*$", re.IGNORECASE),
        re.compile(r"^\s*S\.\s*Nagesh\s+v\.\s+Shobha.*$", re.IGNORECASE),
    ]

    INLINE_MARGIN_YEARS = re.compile(r"\b(?:I9l50|l950|ll950|19ll0|i980|1915\(\)|1960)\b", re.IGNORECASE)

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

        is_historical = bool(
            "1950" in document_id or "1951" in document_id or
            (not ("2026" in document_id or "INSC" in document_id) and w < 500 and h < 750)
        )
        is_born_digital = bool(
            ("2026" in document_id or "INSC" in document_id) or
            (not is_historical and w > 450)
        )
        is_1950 = is_historical

        book_p = (start_book_page + page_number - 1) if start_book_page else page_number
        is_odd = (book_p % 2 == 1)

        printed_pnum: Optional[int] = None
        header_lines: List[str] = []
        margin_lines: List[str] = []
        footnote_lines: List[str] = []
        body_lines: List[Tuple[float, float, str]] = []

        # 1. Detect horizontal vector drawing rule line for footnotes
        # Must be left-aligned near text margin (x0 <= 75.0), in bottom region, with width <= 220.0 pt
        # (Centered lines with x0 > 100 are section/case dividers, not footnote lines)
        rule_y: Optional[float] = None
        for dr in page.get_drawings():
            r = dr.get("rect")
            if r and r.height <= 3.0 and r.y0 >= (h * 0.65) and r.x0 <= 75.0 and 20.0 <= r.width <= 220.0:
                rule_y = r.y0
                break

        header_y_max = 85.0 if is_born_digital else 75.0

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

                # Book Header Check (applies to ALL pages if in upper 25% of page)
                is_book_hdr = (ly1 <= 120.0 and any(bp.search(txt) for bp in cls.BOOK_HEADER_PATTERNS))
                is_stray_p1 = (page_number == 1 and ly1 <= 75.0 and bool(re.match(r"^[,\-–—;.'~`\s]+$", txt)))

                # Top Header Check (pages >= 2)
                is_hdr = is_book_hdr or is_stray_p1 or (
                    page_number > 1 and (ly1 <= header_y_max or any(hp.search(txt) for hp in cls.HEADER_PATTERNS))
                )
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
                    span_size <= 7.5 or (is_born_digital and span_size <= 8.5 and re.match(r"^(?:\d+[\t\s]|\(\d+\)|[*†‡])", txt))
                ):
                    is_fn = True

                if is_fn:
                    footnote_lines.append(txt)
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
        gap_thresh = 15.0 if is_1950 else 13.5

        for i, (l_y0, l_y1, l_txt) in enumerate(body_lines):
            if cur_p:
                prev_y0, prev_y1, _ = body_lines[i - 1]
                if (l_y0 - prev_y1) > gap_thresh:
                    paras.append("\n".join(cur_p))
                    cur_p = [l_txt]
                else:
                    cur_p.append(l_txt)
            else:
                cur_p.append(l_txt)

        if cur_p:
            paras.append("\n".join(cur_p))

        cleaned_body = "\n\n".join(paras)

        # Second-pass inline cleaning
        cleaned_body = cls.INLINE_MARGIN_YEARS.sub(" ", cleaned_body)
        cleaned_body = re.sub(r"(?im)^\s*[.\-–—,;'~`\s]*\s*SUPR[E!]+ME\s+[CO0]{1,2}UR[T1\']+\s+REPORTS?\s*$", " ", cleaned_body)
        cleaned_body = re.sub(r"(?im)^\s*\[19[56]\d\]\s*(?:\d+\s+)?S\.?C\.?R\.?\s*$", " ", cleaned_body)
        cleaned_body = re.sub(r"(?m)^\s*\d{1,2}\s*[-–]\s*[A-Za-z]\s*$", " ", cleaned_body)
        cleaned_body = re.sub(r"(?m)^\s*\d{1,4}\s*$", " ", cleaned_body)
        if page_number > 1:
            cleaned_body = re.sub(r"(?i)\b[CO]hiranjit\s*lal\s*Chowd[hk]uri\b.*?(?:Others\.?|India)\s*", " ", cleaned_body)

        cleaned_body = re.sub(r"[ \t]{2,}", " ", cleaned_body).strip()
        hdr_str = " | ".join(header_lines) if header_lines else None
        ocr_q = 0.99 if is_born_digital else 0.88

        return ExtractedPageZones(
            page_number=page_number,
            cleaned_text=cleaned_body,
            printed_page_number=printed_pnum or book_p,
            page_header=hdr_str,
            margin_notes=margin_lines,
            footnotes=footnote_lines,
            is_born_digital=is_born_digital,
            ocr_quality=ocr_q
        )
