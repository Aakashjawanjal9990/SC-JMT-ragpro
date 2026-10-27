"""
Deterministic Legal Metadata Extractor (PCE v2.0)
Extracts citation, docket, dates, bench, author (*), citations, and counsel from front matter.
"""

import re
from typing import List, Dict, Any, Optional
try:
    from PCEV2.core.models import PageMetadata
except ImportError:
    from core.models import PageMetadata


class LegalMetadataExtractor:
    """Extracts structured document metadata from front-matter text."""

    CANONICAL_JUDGES = [
        "Harilal Kania", "H. J. Kania", "Kania C.J.", "Saiyid Fazl Ali", "Fazl Ali",
        "M. Patanjali Sastri", "Patanjali Sastri", "Mehr Chand Mahajan", "M. C. Mahajan",
        "B. K. Mukherjea", "Mukherjea", "Sudhi Ranjan Das", "S. R. Das", "N. Chandrasekhara Aiyar",
        "Vivian Bose", "Sanjay Kumar", "Alok Aradhe", "Ahsanuddin Amanullah", "K. Vinod Chandran"
    ]

    @classmethod
    def extract_metadata(cls, text: str) -> PageMetadata:
        meta = PageMetadata()
        if not text:
            return meta

        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

        # 1. SCR Citation
        scr_m = re.search(r"\[\s*(\d{4})\s*\]\s*(\d+\s+)?S\.?C\.?R\.?\s*(\d{1,4})", text, re.IGNORECASE)
        if scr_m:
            meta.scr_citation = f"[{scr_m.group(1)}] {scr_m.group(2) or ''}S.C.R. {scr_m.group(3)}".replace("  ", " ").strip()
            meta.year = int(scr_m.group(1))

        # 2. Case Number
        case_num_m = re.search(r"\b((?:Criminal|Civil)\s+Appeal\s+No\.?\s+\d+\s+of\s+\d{4}|Petition\s+No\.?\s+[A-Z0-9]+\s+of\s+\d{4}|Case\s+No\.?\s+[A-Z0-9]+\s+of\s+\d{4})", text, re.IGNORECASE)
        if case_num_m:
            meta.case_number = case_num_m.group(1).strip()

        # 3. Decision Date
        date_m = re.search(r"\b(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})\b", text, re.IGNORECASE)
        if date_m:
            meta.date_of_judgment = date_m.group(1).strip()

        # 4. Bench and Author Judge (asterisk '*')
        bench_block_m = re.search(r"\[\s*(?:SHRI|HON'BLE)?\s*([A-Za-z0-9\s,.*'’–—\-]+?)\s*JJ?\.?\s*\]", text, re.IGNORECASE)
        if bench_block_m:
            raw_bench = bench_block_m.group(1)
            judges_found = []
            author_found = None

            # Detect author judge via asterisk
            for segment in re.split(r",|\band\b", raw_bench):
                seg_clean = segment.strip()
                if not seg_clean:
                    continue
                is_author = ("*" in seg_clean)
                norm_name = re.sub(r"[*#_]", "", seg_clean).strip()
                matched_j = cls._match_judge(norm_name)
                if matched_j:
                    if matched_j not in judges_found:
                        judges_found.append(matched_j)
                    if is_author and not author_found:
                        author_found = matched_j

            meta.judges = judges_found
            meta.author = author_found

        # 5. Case Title / Parties
        cls._extract_parties(text, lines, meta)

        # 6. Structured Case Law Cited & Counsel
        meta.case_law_cited = cls.extract_case_law_cited(text)
        meta.counsel = cls.extract_counsel(text)

        return meta

    @classmethod
    def _match_judge(cls, name_fragment: str) -> Optional[str]:
        frag_clean = re.sub(r"\b(Shri|Hon'ble|Justice|Mr\.|JJ?|C\.?J\.?)\b", "", name_fragment, flags=re.IGNORECASE).strip()
        for cj in cls.CANONICAL_JUDGES:
            base_cj = re.sub(r"\b(C\.?J\.?|Justice)\b", "", cj).strip()
            if base_cj.lower() in frag_clean.lower() or frag_clean.lower() in base_cj.lower():
                return base_cj
        return frag_clean if len(frag_clean) >= 4 else None

    @classmethod
    def _extract_parties(cls, text: str, lines: List[str], meta: PageMetadata):
        # Format: Appellant v. Respondent
        v_idx = None
        for i, ln in enumerate(lines[:15]):
            if re.match(r"^\s*(?:v\.?|versus)\s*$", ln, re.IGNORECASE):
                v_idx = i
                break

        if v_idx is not None and v_idx > 0 and v_idx + 1 < len(lines):
            meta.appellant = lines[v_idx - 1].strip()
            meta.respondent = lines[v_idx + 1].strip()
        else:
            in_line_v = re.search(r"([A-Za-z0-9\s,.'-]{4,})\s+(?:v\.?|versus)\s+([A-Za-z0-9\s,.'-]{4,})", text[:800], re.IGNORECASE)
            if in_line_v:
                meta.appellant = in_line_v.group(1).strip()
                meta.respondent = in_line_v.group(2).strip()

    @classmethod
    def extract_case_law_cited(cls, text: str) -> List[Dict[str, str]]:
        citations = []
        # 1. Check for explicit editorial section
        clc_m = re.search(r"Case\s+Law\s+Cited\s*\n(.*?)(?=\n\s*(?:List\s+of\s+Acts|List\s+of\s+Keywords|Case\s+Arising|Appearances|Judgment)|$)", text, re.IGNORECASE | re.DOTALL)
        if clc_m:
            for line in clc_m.group(1).splitlines():
                ln = line.strip()
                if not ln or len(ln) < 8:
                    continue
                cit_match = re.search(r"([\[\(]\s*\d{4}\s*[\]\)].*)", ln)
                citations.append({
                    "citation_text": ln,
                    "normalized_citation": cit_match.group(1).strip() if cit_match else ln
                })

        # 2. Extract in-text precedent citations across the Global Indian Legal Space (1950-2026)
        # Robust against common OCR confusions: mismatched brackets, missing dots, letter substitutions
        global_precedent_patterns = [
            # Supreme Court Reports (SCR, SCR Supp, SCR Suppl) e.g. [1950] SCR 869, (1979) 2 SCR 1025, (1953] S.C.R. 1069
            re.compile(r"[\(\[]\s*\d{4}\s*[\)\]]\s*(?:\d+\s+)?(?:Supp\.?|Suppl\.?\s+)?S\.?\s*C\.?\s*R\.?\s*[\r\n\s]*\d+", re.IGNORECASE),
            # Supreme Court Cases (SCC, SCC (Cri), SCC (L&S)) e.g. (1998) 2 SCC 12, (2007) 10 SCC (Cri) 105
            re.compile(r"[\(\[]\s*\d{4}\s*[\)\]]\s*\d+\s*S\.?\s*C\.?\s*C\.?(?:\s*\([A-Za-z\s&]+\))?\s*[\r\n\s]*\d+", re.IGNORECASE),
            # All India Reporter (AIR SC, AIR Mad, AIR Bom, AIR Cal, AIR All, etc., handling OCR A.LR / A.l.R)
            re.compile(r"A\.?[I1l|]\.?\s*R\.?\s*[\r\n\s]*\d{4}\s*[\r\n\s]*(?:S\.?C\.?|[A-Z][a-z]{2,9}\.?)\s*[\r\n\s]*\d+", re.IGNORECASE),
            # Official Neutral Citations (2023-2026 e.g. 2024 INSC 152)
            re.compile(r"\b\d{4}\s*INSC\s*\d+\b", re.IGNORECASE),
            # Indian Law Reports (ILR - High Courts) e.g. I.L.R. 39 Mad. 617, I.L.R. 58 Cal. 1359, I.L.R. [1939] Mad. 450
            re.compile(r"\bI\.?\s*L\.?\s*R\.?\s*[\r\n\s]*(?:[\[\(]?\s*\d{4}\s*[\]\)]?|\d+)?\s*(?:[A-Za-z]+|\([A-Za-z]+\))\.?\s*[\r\n\s]*\d+", re.IGNORECASE),
            # SCALE & Judgments Today (JT)
            re.compile(r"\b(?:SCALE|JT)\s*[\r\n\s]*\d{4}\s*[\r\n\s]*\(?\d+\)?\s*[\r\n\s]*\d+", re.IGNORECASE),
            re.compile(r"[\(\[]\s*\d{4}\s*[\)\]]\s*\d+\s*SCALE\s*[\r\n\s]*\d+", re.IGNORECASE),
            # Specialized: Income Tax Reports (ITR) & Company Cases (Comp Cas)
            re.compile(r"[\(\[]\s*\d{4}\s*[\)\]]\s*\d+\s*I\.?T\.?R\.?\s*[\r\n\s]*\d+", re.IGNORECASE),
            re.compile(r"[\(\[]\s*\d{4}\s*[\)\]]\s*\d+\s*Comp\s*Cas\s*[\r\n\s]*\d+", re.IGNORECASE),
            # Historical Pre-Constitution (Federal Court FCR & Privy Council IA, handling OCR F.O.R / F.0.R)
            re.compile(r"[\(\[]\s*\d{4}\s*[\)\]]\s*(?:\d+\s+)?(?:F\.?[CO0]\.?R\.?|I\.?A\.?)\s*[\r\n\s]*\d+", re.IGNORECASE),
            # Additional Historical Regional Reporters (Calcutta Weekly Notes CWN, Bombay Law Reporter)
            re.compile(r"\b\d+\s+C\.?\s*W\.?\s*N\.?\s*[\r\n\s]*\d+\b", re.IGNORECASE),
            re.compile(r"\b\d+\s+Bom\.?\s*L\.?\s*R\.?\s*[\r\n\s]*\d+\b", re.IGNORECASE),
        ]
        seen = set(c["normalized_citation"] for c in citations)
        for pat in global_precedent_patterns:
            for match in pat.finditer(text):
                raw_c = re.sub(r"\s+", " ", match.group(0)).strip()
                # Heal OCR confusion in normalized citation
                norm_c = raw_c
                # Normalize mismatched brackets: (1953] or [1953) -> [1953]
                norm_c = re.sub(r"^[(\[]\s*(\d{4})\s*[)\]]", r"[\1]", norm_c)
                # Normalize F.O.R / F.0.R -> F.C.R.
                norm_c = re.sub(r"\bF\.[O0]\.R\b", "F.C.R.", norm_c)
                # Normalize A.LR / A.l.R -> A.I.R.
                norm_c = re.sub(r"\bA\.[l|]R\b", "A.I.R.", norm_c, flags=re.IGNORECASE)

                if norm_c not in seen:
                    seen.add(norm_c)
                    citations.append({
                        "citation_text": raw_c,
                        "normalized_citation": norm_c
                    })

        return citations

    @classmethod
    def extract_counsel(cls, text: str) -> List[str]:
        counsel_list = []
        app_m = re.search(r"Appearances\s+for\s+Parties\s*\n(.*?)(?=\n\s*(?:Issue\s+for|Judgment)|$)", text, re.IGNORECASE | re.DOTALL)
        if app_m:
            for line in app_m.group(1).splitlines():
                ln = line.strip()
                if ln and len(ln) > 3 and not re.search(r"^(?:for\s+the|Advocates?|Senior\s+Advocate)", ln, re.IGNORECASE):
                    counsel_list.append(ln)
        return counsel_list
