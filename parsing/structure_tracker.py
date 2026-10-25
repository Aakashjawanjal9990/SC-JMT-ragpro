"""
Law Report Structure and Opinion State Tracker (PCE v2.0)
Tracks judicial opinions, front matter isolation, operative order chunks, and confidence decay.
"""

import re
from typing import List, Tuple, Optional, Dict, Any


class LawReportStructureTracker:
    """Maintains document-level structure state across pages with confidence scoring."""

    GLOBAL_JUDGES = [
        "Harilal Kania", "H.J. Kania", "Kania",
        "Saiyid Fazl Ali", "Fazl Ali", "Fazal Ali",
        "M. Patanjali Sastri", "Patanjali Sastri",
        "Mehr Chand Mahajan", "M.C. Mahajan", "Mahajan",
        "Bijan Kumar Mukherjea", "B.K. Mukherjea", "Mukherjea",
        "Sudhi Ranjan Das", "S.R. Das", "Das",
        "N. Chandrasekhara Aiyar", "Vivian Bose",
        "Sanjay Kumar", "K. Vinod Chandran", "Vinod Chandran",
        "B.R. Gavai", "Surya Kant", "D.Y. Chandrachud"
    ]

    OPINION_DELIVERY_PATTERNS = [
        re.compile(r"^\s*(?:19\d\d[.,\s]+[A-Za-z]+[.,\s]+\d{1,2}[.,\s]+)?The\s+Judgment\s+of\s+the\s+Court\s+was\s+delivered\s+by\s+(.*)$", re.IGNORECASE),
        re.compile(r"^\s*(?:19\d\d[.,\s]+[A-Za-z]+[.,\s]+\d{1,2}[.,\s]+)?The\s+(?:following\s+)?Judgments\s+were\s+delivered\b", re.IGNORECASE),
        re.compile(r"^\s*(?:19\d\d[.,\s]+[A-Za-z]+[.,\s]+\d{1,2}[.,\s]+)?The\s+Court\s+delivered\s+judgment\b", re.IGNORECASE),
        re.compile(r"^\s*Judgment\s*/\s*Order\s+of\s+the\s+Supreme\s+Court\s*$", re.IGNORECASE),
        re.compile(r"^\s*Per\s+([A-Z\s,.'–—\-]+?)\s*(?:C\.?\s*J\.?|JJ?\.?)\s*[-–—]\s*(.*)$", re.IGNORECASE),
    ]

    OPINION_START_PATTERNS = [
        re.compile(r"^\s*([A-Z\s,.'–—\-]+?)\s*(?:C\.?\s*J\.?|JJ?\.?)\s*[-–—]\s*(.*)$", re.IGNORECASE),
        re.compile(r"^\s*([A-Za-z\s,.'–—\-]+?),\s*J\.?\s*$", re.IGNORECASE),
    ]

    OPERATIVE_ORDER_PATTERNS = [
        re.compile(r"\b(?:appeal|petition)\s+(?:is\s+|stands\s+)?(?:accordingly\s+)?(?:allowed|dismissed)(?:\s+with\s+costs|\s*[\.\!\?]|\s*$)", re.IGNORECASE),
        re.compile(r"\b(?:dismiss\s+it\s+with\s+costs|petition\s+fails\s+and\s+is\s+dismissed)\b", re.IGNORECASE),
        re.compile(r"\b(?:The\s+)?appeal\s+therefore\s+fails\s+and\s+is\s+dismissed\b", re.IGNORECASE),
        re.compile(r"\bResult\s+of\s+the\s+case:\s*.*", re.IGNORECASE),
        re.compile(r"\bAppeal\s+(?:dismissed|allowed)\.?\s*$", re.IGNORECASE),
        re.compile(r"\bIn\s+our\s+view,\s+this\s+appeal\s+has\s+no\s+substance\b", re.IGNORECASE),
        re.compile(r"\bPending\s+application\(s\).*disposed\s+of\b", re.IGNORECASE),
        re.compile(r"^\s*Agent\s+for\s+the\s+(?:appellants?|respondents?|petitioner)\s*:", re.IGNORECASE),
    ]

    COUNSEL_PATTERNS = [
        re.compile(r"\bfor\s+the\s+(?:appellants?|respondents?|petitioners?|State|Union)\b", re.IGNORECASE),
        re.compile(r"\bAppearances\s+for\s+Parties\b", re.IGNORECASE),
        re.compile(r"\bAdvs\.\s+for\s+the\b", re.IGNORECASE),
        re.compile(r"\bAPPEAL\s+from\s+the\s+High\s+Court\b", re.IGNORECASE),
        re.compile(r"\bPETITION\s+under\s+Article\s+32\b", re.IGNORECASE),
    ]

    FRONT_MATTER_HEADINGS = [
        "Headnotes", "Issue for Consideration", "Case Law Cited",
        "List of Acts", "List of Keywords", "Case Arising From", "Appearances for Parties"
    ]

    OPINION_SPLIT_REGEX = re.compile(
        r'(?=\n\s*(?:'
        r'(?:19\d\d[.,\s]+[A-Za-z]+[.,\s]+\d{1,2}[.,\s]+)?The\s+Judgment\s+of\s+the\s+Court\s+was\s+delivered\s+by|'
        r'(?:19\d\d[.,\s]+[A-Za-z]+[.,\s]+\d{1,2}[.,\s]+)?The\s+(?:following\s+)?Judgments\s+were\s+delivered|'
        r'(?:19\d\d[.,\s]+[A-Za-z]+[.,\s]+\d{1,2}[.,\s]+)?The\s+Court\s+delivered\s+judgment|'
        r'Judgment\s*/\s*Order\s+of\s+the\s+Supreme\s+Court|'
        r'Judgment\s*\n\s*[A-Za-z\s,.\'–—\-]+?,\s*J\.?'
        r'))',
        re.IGNORECASE
    )

    ORDER_SPLIT_REGEX = re.compile(
        r'(?=\n\s*(?:'
        r'Result\s+of\s+the\s+case:\s*|'
        r'(?:\d+\.\s*)?(?:The\s+)?Appeal\s+(?:stands\s+|is\s+accordingly\s+)?(?:dismissed|allowed)\.?|'
        r'Appeal\s+(?:dismissed|allowed)\.?|'
        r'(?:\d+\.\s*)?Pending\s+application\(s\)|'
        r'In\s+our\s+view,\s+this\s+appeal\s+has\s+no\s+substance|'
        r'Agent\s+for\s+the\s+(?:appellants?|respondents?)'
        r'))',
        re.IGNORECASE
    )

    def __init__(self, bench_judges: Optional[List[str]] = None):
        self.bench_judges = bench_judges or []
        self.current_section = "HEADNOTE"
        self.current_judge = None
        self.confirmed_opinion_page = None
        self.is_front_matter_complete = False
        self.counsel_seen = False

    def split_and_classify_paragraphs(
        self,
        raw_paragraphs: List[str],
        page_number: int
    ) -> List[Tuple[str, str, Optional[str], float]]:
        expanded = []
        for p in raw_paragraphs:
            p_str = p.strip()
            if not p_str:
                continue

            if not self.is_front_matter_complete:
                parts = [pt.strip() for pt in self.OPINION_SPLIT_REGEX.split(p_str) if pt.strip()]
            else:
                parts = [p_str]

            for pt in parts:
                if self.is_front_matter_complete:
                    sub_parts = [spt.strip() for spt in self.ORDER_SPLIT_REGEX.split(pt) if spt.strip()]
                    expanded.extend(sub_parts)
                else:
                    expanded.append(pt)

        classified = []
        pending_heading = None

        for p in expanded:
            # If paragraph is merely a heading, buffer it to attach to the judge opinion start
            if re.match(r"^\s*(?:Judgment\s*/\s*Order\s+of\s+the\s+Supreme\s+Court|Judgment)\s*$", p, re.IGNORECASE):
                pending_heading = (pending_heading + "\n" + p) if pending_heading else p
                continue

            if pending_heading:
                p = pending_heading + "\n" + p
                pending_heading = None

            if not self.counsel_seen and any(cp.search(p) for cp in self.COUNSEL_PATTERNS):
                self.counsel_seen = True

            found_judge = None
            for dp in self.OPINION_DELIVERY_PATTERNS:
                dm = dp.search(p)
                if dm:
                    cand = dm.group(1).strip() if dm.groups() and dm.group(1) else ""
                    if cand:
                        found_judge = self._validate_judge(cand)
                    self.is_front_matter_complete = True
                    break

            if not found_judge:
                lines = [ln.strip() for ln in p.splitlines() if ln.strip()]
                for ln in lines:
                    if not self.counsel_seen and page_number <= 4:
                        if any(w in ln.lower() for w in ["dissenting", "concurring", "held", "(", ")"]):
                            continue
                    for sp in self.OPINION_START_PATTERNS:
                        sm = sp.match(ln)
                        if sm:
                            cand = sm.group(1)
                            if "and" in cand.lower() and not self.counsel_seen:
                                continue
                            v = self._validate_judge(cand)
                            if v:
                                found_judge = v
                                self.is_front_matter_complete = True
                                break
                    if found_judge:
                        break

            if found_judge:
                self.current_judge = found_judge
                self.current_section = "OPINION"
                self.confirmed_opinion_page = page_number

            if self.is_front_matter_complete and self.is_operative_order_paragraph(p):
                classified.append((p, "ORDER", None, 0.90))
                continue

            if not self.is_front_matter_complete:
                sec = "HEADNOTE" if page_number <= 2 else "FRONT_MATTER"
                classified.append((p, sec, None, 0.50))
            else:
                conf = self.compute_confidence(page_number)
                classified.append((p, "OPINION", self.current_judge, conf))

        return classified

    def update_from_text(self, page_text: str, page_number: int) -> Tuple[str, Optional[str], float]:
        """Convenience method returning overall page classification."""
        if not page_text or not page_text.strip():
            return self.current_section, self.current_judge, self.compute_confidence(page_number)
        paras = [p.strip() for p in page_text.split("\n\n") if p.strip()]
        classified = self.split_and_classify_paragraphs(paras, page_number)
        if classified:
            return classified[-1][1], classified[-1][2], classified[-1][3]
        return self.current_section, self.current_judge, self.compute_confidence(page_number)

    def compute_confidence(self, current_page: int) -> float:
        if not self.is_front_matter_complete:
            return 0.50

        if self.confirmed_opinion_page is not None:
            pages_since_confirmed = max(0, current_page - self.confirmed_opinion_page)
            return max(0.70, 0.95 - (0.02 * pages_since_confirmed))

        return 0.75

    def _validate_judge(self, raw_name: str) -> Optional[str]:
        clean = re.sub(r"^[,\s\-–—.;]+|[,\s\-–—.;]+$", "", raw_name)
        clean = re.sub(r"\b(Shri|Hon'ble|Justice|Mr\.|The Judgment of the Court was delivered by|Per)\b", "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"\bC\.?\s*J\.?|\bJJ?\.?", "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"[,\s\-–—.;]+", " ", clean).strip()
        if len(clean) < 3:
            return None

        # Check against case bench if available
        for bj in self.bench_judges:
            if clean.lower() in bj.lower() or bj.lower() in clean.lower():
                return bj

        # Check against global judge lexicon
        for gj in self.GLOBAL_JUDGES:
            if clean.lower() in gj.lower() or gj.lower() in clean.lower():
                return gj

        return clean

    @classmethod
    def is_operative_order_paragraph(cls, paragraph_text: str) -> bool:
        if not paragraph_text:
            return False
        return any(p.search(paragraph_text) for p in cls.OPERATIVE_ORDER_PATTERNS)
