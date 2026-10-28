"""
Legal Document Structure Recovery & Validation for Local OCR Outputs
Integrates local OCR / PPStructure extractions with existing legal parser rules:
- Case title, parties (appellant / respondent), case number, date
- Headings and hierarchy (HEADNOTE, FACTS, ARGUMENTS, JUDGMENT, ORDER)
- Numbered paragraphs and reading order sequence verification
- Opinions: Majority, Concurring, Dissenting with verified SC judge attribution
- Statutory provisions and precedent citations
- Tables and footnotes
- 100% preservation of original page numbers and source-document IDs
"""

import re
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple

from local_ocr.engine import LocalOCRResult, PPStructureResult
from parsing.structure_tracker import LawReportStructureTracker


@dataclass
class RecoveredDocumentStructure:
    document_id: str
    page_number: int
    case_title: Optional[str] = None
    appellant: Optional[str] = None
    respondent: Optional[str] = None
    case_number: Optional[str] = None
    judgment_date: Optional[str] = None
    headings: List[Dict[str, Any]] = field(default_factory=list)
    numbered_paragraphs: List[Dict[str, Any]] = field(default_factory=list)
    paragraph_order_valid: bool = True
    opinions: List[Dict[str, Any]] = field(default_factory=list)
    statutory_provisions: List[str] = field(default_factory=list)
    cited_precedents: List[str] = field(default_factory=list)
    tables: List[Dict[str, Any]] = field(default_factory=list)
    footnotes: List[str] = field(default_factory=list)
    is_valid: bool = True
    validation_warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "document_id": self.document_id,
            "page_number": self.page_number,
            "case_title": self.case_title,
            "appellant": self.appellant,
            "respondent": self.respondent,
            "case_number": self.case_number,
            "judgment_date": self.judgment_date,
            "headings_count": len(self.headings),
            "headings": self.headings,
            "numbered_paragraphs_count": len(self.numbered_paragraphs),
            "paragraph_order_valid": self.paragraph_order_valid,
            "opinions_count": len(self.opinions),
            "opinions": self.opinions,
            "statutory_provisions_count": len(self.statutory_provisions),
            "statutory_provisions": self.statutory_provisions[:10],
            "cited_precedents_count": len(self.cited_precedents),
            "cited_precedents": self.cited_precedents[:10],
            "tables_count": len(self.tables),
            "footnotes_count": len(self.footnotes),
            "is_valid": self.is_valid,
            "validation_warnings": self.validation_warnings
        }


class LegalStructureRecovery:
    """Reconstructs and validates legal structure from local OCR output."""

    KNOWN_JUDGES = set(j.lower() for j in LawReportStructureTracker.GLOBAL_JUDGES)

    CASE_NUMBER_PATTERNS = [
        re.compile(r"\b(?:Criminal|Civil)\s+Appeal\s+No[s]?\.?\s*(\d+\s*(?:of|/)\s*\d{4})\b", re.IGNORECASE),
        re.compile(r"\bWrit\s+Petition\s*(?:\([A-Za-z]+\))?\s*No[s]?\.?\s*(\d+\s*(?:of|/)\s*\d{4})\b", re.IGNORECASE),
        re.compile(r"\bSpecial\s+Leave\s+Petition\s*(?:\([A-Za-z]+\))?\s*No[s]?\.?\s*(\d+\s*(?:of|/)\s*\d{4})\b", re.IGNORECASE),
        re.compile(r"\bCase\s+No\.?\s*(\d+\s*of\s*\d{4})\b", re.IGNORECASE),
    ]

    DATE_PATTERNS = [
        re.compile(r"\b(?:Dated|Date|Delivered\s+on|Decided\s+on)\s*[:\-–]?\s*([A-Za-z]+\s+\d{1,2},\s*\d{4}|\d{1,2}[./-]\d{1,2}[./-]\d{4}|\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+,?\s+\d{4})", re.IGNORECASE),
        re.compile(r"\b(\d{4}\s+[A-Za-z]+\s+\d{1,2})\b"),
    ]

    STATUTE_PATTERNS = [
        re.compile(r"\b(?:Section|Sec\.)\s+(\d+[A-Za-z]?(?:\s*\([0-9a-z]+\))?)\b", re.IGNORECASE),
        re.compile(r"\bArticle\s+(\d+[A-Za-z]?(?:\s*\([0-9a-z]+\))?)\b", re.IGNORECASE),
        re.compile(r"\bOrder\s+([IVXLCDM]+|[0-9]+)\s+Rule\s+([0-9]+)\b", re.IGNORECASE),
    ]

    PRECEDENT_PATTERNS = [
        re.compile(r"\[\d{4}\]\s*(?:\d+\s+)?S\.?C\.?R\.?\s+\d+", re.IGNORECASE),
        re.compile(r"A\.?I\.?R\.?\s+\d{4}\s+S\.?C\.?\s+\d+", re.IGNORECASE),
        re.compile(r"\(\d{4}\)\s*\d+\s*S\.?C\.?C\.?\s+\d+", re.IGNORECASE),
    ]

    @classmethod
    def extract_case_metadata(cls, text: str) -> Dict[str, Optional[str]]:
        """Extracts title, parties, case number, and judgment date."""
        appellant, respondent, case_num, j_date = None, None, None, None

        # Title / Parties: e.g. "Petitioner v. Respondent"
        v_match = re.search(r"(?:^|\n)[ \t]*([A-Za-z0-9 .,'()–—\-]+?)[ \t]+(?:v[s\.]?|versus)[ \t]+([A-Za-z0-9 .,'()–—\-]+?)(?:\r?\n|$)", text, re.IGNORECASE)
        if v_match:
            app = v_match.group(1).strip()
            resp = v_match.group(2).strip()
            if 3 <= len(app) <= 120 and 3 <= len(resp) <= 120:
                appellant = app
                respondent = resp

        # Case Number
        for pat in cls.CASE_NUMBER_PATTERNS:
            m = pat.search(text)
            if m:
                case_num = m.group(0).strip()
                break

        # Date of Judgment
        for pat in cls.DATE_PATTERNS:
            m = pat.search(text)
            if m:
                j_date = m.group(1).strip()
                break

        case_title = f"{appellant} v. {respondent}" if (appellant and respondent) else None
        return {
            "case_title": case_title,
            "appellant": appellant,
            "respondent": respondent,
            "case_number": case_num,
            "judgment_date": j_date
        }

    @classmethod
    def extract_headings(cls, text: str) -> List[Dict[str, Any]]:
        """Extracts headings and section titles."""
        headings = []
        for line in text.splitlines():
            line_str = line.strip()
            if not line_str:
                continue
            # Markdown heading or all-caps standard heading
            if line_str.startswith("#") or (line_str.isupper() and 4 <= len(line_str) <= 50):
                title = line_str.lstrip("#").strip()
                if title:
                    headings.append({
                        "heading_text": title,
                        "section_type": cls._classify_heading(title)
                    })
        return headings

    @staticmethod
    def _classify_heading(title: str) -> str:
        t = title.upper()
        if "HEADNOTE" in t or "SYLLABUS" in t:
            return "HEADNOTE"
        if "JUDGMENT" in t or "ORDER" in t or "OPINION" in t:
            return "JUDGMENT"
        if "FACT" in t:
            return "FACTS"
        if "ARGUMENT" in t or "SUBMISSION" in t:
            return "ARGUMENTS"
        if "ISSUE" in t or "QUESTION" in t:
            return "ISSUES"
        return "SECTION"

    @classmethod
    def extract_numbered_paragraphs(cls, text: str) -> Tuple[List[Dict[str, Any]], bool]:
        """Extracts numbered paragraphs and verifies sequence without regressions."""
        paras = []
        pattern = re.compile(r"(?:^|\n)\s*(?:\[(\d+)\]|\((\d+)\)|(\d+)\.)\s+([A-Z][^\n]+)", re.MULTILINE)

        numbers_seen = []
        for m in pattern.finditer(text):
            num_str = m.group(1) or m.group(2) or m.group(3)
            num = int(num_str)
            snippet = m.group(4).strip()
            numbers_seen.append(num)
            paras.append({
                "para_number": num,
                "snippet": snippet[:100]
            })

        is_sequential = True
        if len(numbers_seen) >= 2:
            for i in range(1, len(numbers_seen)):
                if numbers_seen[i] < numbers_seen[i - 1]:
                    is_sequential = False
                    break

        return paras, is_sequential

    @classmethod
    def extract_opinions_and_judges(cls, text: str) -> List[Dict[str, Any]]:
        """Identifies judicial opinions and validates judge attributions."""
        opinions = []
        pats = [
            re.compile(r"\b([A-Za-z .\'–—\-]+?),\s*(?:C\.?J\.?|JJ?\.?)\b", re.IGNORECASE),
            re.compile(r"\bPer\s+([A-Za-z .\'–—\-]+?)\s*(?:C\.?J\.?|JJ?\.?)?\b", re.IGNORECASE),
            re.compile(r"\bThe\s+Judgment\s+of\s+the\s+Court\s+was\s+delivered\s+by\s+([A-Za-z .\'–—\-]+?)\b", re.IGNORECASE),
        ]

        for p in pats:
            for m in p.finditer(text):
                raw_name = m.group(1).strip()
                clean_name = re.sub(r"^(?:Mr\.|Justice|Hon'ble)\s+", "", raw_name, flags=re.IGNORECASE).strip()
                if 2 <= len(clean_name) <= 35:
                    is_verified = any(kj in clean_name.lower() for kj in cls.KNOWN_JUDGES)
                    opinions.append({
                        "raw_match": m.group(0),
                        "judge_name": clean_name,
                        "verified_sc_judge": is_verified,
                        "opinion_type": "majority"
                    })
        return opinions

    @classmethod
    def extract_citations(cls, text: str) -> Tuple[List[str], List[str]]:
        """Extracts statutory provisions and precedent citations."""
        statutes = set()
        for pat in cls.STATUTE_PATTERNS:
            for m in pat.finditer(text):
                statutes.add(m.group(0).strip())

        precedents = set()
        for pat in cls.PRECEDENT_PATTERNS:
            for m in pat.finditer(text):
                precedents.add(m.group(0).strip())

        return sorted(list(statutes)), sorted(list(precedents))

    @classmethod
    def recover_and_validate(
        cls,
        document_id: str,
        page_number: int,
        ocr_result: LocalOCRResult,
        structure_result: Optional[PPStructureResult] = None
    ) -> RecoveredDocumentStructure:
        """Runs legal structure reconstruction and validation on OCR outputs."""
        text = ocr_result.text or (structure_result.markdown_text if structure_result else "")
        warnings = []

        if not text.strip():
            return RecoveredDocumentStructure(
                document_id=document_id,
                page_number=page_number,
                is_valid=False,
                validation_warnings=["Empty OCR output returned."]
            )

        meta = cls.extract_case_metadata(text)
        headings = cls.extract_headings(text)
        paras, is_seq = cls.extract_numbered_paragraphs(text)
        if not is_seq:
            warnings.append("Disordered numbered paragraphs detected: number regression observed.")

        opinions = cls.extract_opinions_and_judges(text)
        statutes, precedents = cls.extract_citations(text)

        tables = structure_result.tables if structure_result else []
        is_valid = len(text.strip()) >= 20 and is_seq

        return RecoveredDocumentStructure(
            document_id=document_id,
            page_number=page_number,
            case_title=meta.get("case_title"),
            appellant=meta.get("appellant"),
            respondent=meta.get("respondent"),
            case_number=meta.get("case_number"),
            judgment_date=meta.get("judgment_date"),
            headings=headings,
            numbered_paragraphs=paras,
            paragraph_order_valid=is_seq,
            opinions=opinions,
            statutory_provisions=statutes,
            cited_precedents=precedents,
            tables=tables,
            is_valid=is_valid,
            validation_warnings=warnings
        )

    @classmethod
    def purge_ocr_running_headers(cls, text: str, is_first_page: bool = False) -> str:
        """Purges running headers, page numbers, and book titles from OCR page text."""
        lines = text.splitlines()
        if not lines:
            return ""

        header_patterns = [
            # Supreme Court Reports (handling any corruptions like SUPEME, SUP!EME, SUPREME)
            re.compile(r".*SUP.*?[CO0]{1,2}UR[T1\']*\s+REPORTS?.*", re.IGNORECASE),
            # SCR citation patterns: [1974] 1 S.C.R., [2007] 10 S.C.R., S.C.R., [1950] S.C.R.
            re.compile(r"^\s*(?:\[\d{4}\]\s*)?(?:\d{1,4}\s+)?S\.?\s*C\.?\s*R\.?.*$", re.IGNORECASE),
            re.compile(r"^\s*\[\d{4}\].*?S\.?\s*C\.?\s*R\.?.*$", re.IGNORECASE),
            # Standalone page number at top
            re.compile(r"^\s*\d{1,4}\s*$", re.IGNORECASE),
            # Lone margin letter at top
            re.compile(r"^\s*[A-H]\s*$", re.IGNORECASE),
        ]

        if not is_first_page:
            header_patterns.extend([
                # Generalized legal adversary short title: "Party A v. Party B", "Party A vs. Party B"
                re.compile(r"^\s*[\w\s.,\'&()/-]+\s+(?:v\.?|vs\.?|versus)(?:\s+[\w\s.,\'&()/-]+)?$", re.IGNORECASE),
                # Generalized plural party suffixes: "... & Anr." or "... & Ors."
                re.compile(r"^\s*[\w\s.,\'()/-]+\s+&\s+(?:Anr|Ors)\.?\s*$", re.IGNORECASE),
                # Generalized corporate / entity party titles in top running headers
                re.compile(r"^\s*[\w\s.,\'()/-]+(?:Banking\s+Cor|Corp(?:oration)?|Co(?:mpany)?|Ltd|Limited)\.?\s*$", re.IGNORECASE),
            ])

        start_idx = 0
        top_limit = min(4, len(lines))
        while start_idx < top_limit:
            l_str = lines[start_idx].strip()
            if not l_str:
                start_idx += 1
                continue
            if any(p.match(l_str) for p in header_patterns):
                start_idx += 1
                continue
            break

        clean_lines = lines[start_idx:]
        # Purge any stray standalone page numbers, margin years, or margin letters across OCR text
        filtered_lines = []
        for l in clean_lines:
            l_strip = l.strip()
            if not l_strip:
                filtered_lines.append(l)
                continue
            if re.match(r"^\s*\d{1,4}\s*$", l_strip):
                continue
            if re.match(r"^\s*195\d\s*$", l_strip):
                continue
            if re.match(r"^\s*[A-H]\s*$", l_strip):
                continue
            filtered_lines.append(l)

        return "\n".join(filtered_lines).strip()


def purge_ocr_running_headers(text: str, is_first_page: bool = False) -> str:
    """Convenience module-level helper to purge running headers from OCR text."""
    return LegalStructureRecovery.purge_ocr_running_headers(text, is_first_page=is_first_page)

