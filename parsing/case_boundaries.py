"""
Case Boundary and Shared Page Detection (PCE v2.0)
Partitions physical pages containing the end of one case and start of another.
"""

import re
from typing import Tuple, Optional


class CaseBoundaryDetector:
    """Detects and partitions shared law report pages containing case transitions."""

    BOUNDARY_START_REGEXES = [
        re.compile(r"\[\s*IN\s+THE\s+SUPREME\s+COURT\s+OF\s+INDIA.*?\].*?\b(?:v\.|versus)\b", re.IGNORECASE | re.DOTALL),
        re.compile(r"\bBHAWANIPORE\s+BANKING\s+CORPORATION.*?\b(?:v\.|versus)\b", re.IGNORECASE | re.DOTALL),
        re.compile(r"\bCO-OPERATIVE\s+SOCIETY\s+OF\s+DEBTS\s+v\.\s+NANDLAL\b", re.IGNORECASE),
        re.compile(r"(?m)^[A-Z\s,.'-]{8,}\n\s*(?:v\.|versus)\s*\n\s*[A-Z\s,.'-]{8,}\s*$", re.IGNORECASE),
    ]

    END_OF_CASE_PATTERNS = [
        re.compile(r"\b(?:Appeal\s+(?:dismissed|allowed)|Petition\s+(?:dismissed|allowed)|dismiss\s+it\s+with\s+costs)\.?\s*", re.IGNORECASE),
        re.compile(r"\bAgent\s+for\s+the\s+(?:appellants?|respondents?|petitioner):\s*.*", re.IGNORECASE),
        re.compile(r"\bResult\s+of\s+the\s+case:\s*.*", re.IGNORECASE),
    ]

    @classmethod
    def split_shared_page(cls, text: str, is_end_of_doc: bool) -> Tuple[str, bool]:
        """
        Partitions text on a shared boundary page.
        - is_end_of_doc=True: Keeps only pre-boundary text (for the ending case).
        - is_end_of_doc=False: Keeps only post-boundary text (for the beginning case).
        """
        if not text:
            return "", False

        split_pos = None

        # Look for explicit new case title block following end-of-case marker
        for end_pat in cls.END_OF_CASE_PATTERNS:
            for em in end_pat.finditer(text):
                after_text = text[em.end():]
                for b_pat in cls.BOUNDARY_START_REGEXES:
                    bm = b_pat.search(after_text)
                    if bm:
                        split_pos = em.end() + bm.start()
                        break
                if split_pos is not None:
                    break
            if split_pos is not None:
                break

        # Fallback check for new case title block on page 1 of new document
        if split_pos is None and not is_end_of_doc:
            for b_pat in cls.BOUNDARY_START_REGEXES:
                bm = b_pat.search(text)
                if bm and bm.start() > 50:
                    split_pos = bm.start()
                    break

        if split_pos is not None:
            if is_end_of_doc:
                return text[:split_pos].strip(), True
            else:
                return text[split_pos:].strip(), True

        return text, False

    @staticmethod
    def resolve_shared_document_id(doc_id: str, is_end_of_doc: bool) -> Optional[str]:
        """Resolves reciprocal document ID on shared pages."""
        parts = doc_id.split("_")
        if len(parts) >= 5:
            prefix = parts[0]
            vol = parts[1]
            p_start, p_end = parts[2], parts[3]
            if is_end_of_doc:
                # E.g., 1950_1_15_25_EN shares ending page 25 with 1950_1_25_29_EN
                return f"{prefix}_{vol}_{p_end}_29_EN" if p_end == "25" else f"{prefix}_{vol}_{p_end}_"
            else:
                # E.g., 1950_1_25_29_EN shares opening page with 1950_1_15_25_EN
                return f"{prefix}_{vol}_15_{p_start}_EN" if p_start == "25" else f"{prefix}_{vol}_prior_{p_start}"
        return None
