"""Query optimization before search: one technique per retriever.

Dense — pseudo query embedding (HyDE): an LLM (Groq) writes a short passage in the
style of a judgment that would answer the question. Its document embedding is mixed
with the question's query embedding, so the search vector sits closer to how
judgments are actually written. The passage is only used to search: it is never
returned, shown or cited.

BM25 — lexical pseudo-relevance feedback (RM3-style): the top BM25 hits for the
original question are assumed relevant; terms that are frequent in them, rare in the
corpus and present in several different judgments among them are added to the query
with a lower weight, and BM25 runs again. The question is never sent anywhere for
this step.
"""

import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence

from app.retrieval.llm import GroqChat
from app.retrieval.models import DOCUMENT_ID, RetrievedChunk
from app.retrieval.tokenizer import tokenize

_PSEUDO_PASSAGE_PROMPT = (
    "Write one paragraph of 3-5 sentences in the style of an Indian Supreme Court "
    "judgment that would answer the user's question. Use the constitutional provisions, "
    "statutes, sections and legal terms such a judgment would use. Do not invent case "
    "names or citations. Output only the paragraph."
)
_PLAIN_NUMBER = re.compile(r"^\d+(-[a-z0-9]+)?$")  # years, paragraph numbers, "21-a"


class GroqPseudoQuery:
    """Writes the pseudo passage with a Groq chat model."""

    def __init__(self, chat: GroqChat) -> None:
        self.chat = chat
        self.timeout_seconds = chat.timeout_seconds

    def warmup(self) -> None:
        self.chat.warmup()

    def generate(self, question: str) -> str:
        passage = self.chat.complete(_PSEUDO_PASSAGE_PROMPT, question)
        if not passage:
            raise ValueError("empty pseudo passage")
        return passage


def prf_terms(
    query_terms: Sequence[str],
    feedback: Sequence[RetrievedChunk],
    idf: Callable[[list[str]], Mapping[str, float]],
    *,
    num_terms: int,
    min_judgments: int = 2,
) -> dict[str, float]:
    """Expansion terms from feedback chunks, scored in (0, 1] (1 = best).

    score(t) = idf(t) * sum over feedback chunks d of w(d) * tf(t, d) / |d|, where
    w(d) is d's share of the feedback BM25 score. Query terms, plain numbers (years,
    paragraph numbers) and terms found in fewer than min_judgments different judgments
    are skipped, so one judgment that fills the feedback set cannot pull the query
    towards its own facts (party names, places). Statutory references like "s.438" are
    kept.
    """
    total = sum(max(chunk.bm25_score or 0.0, 0.0) for chunk in feedback)
    if not feedback or total <= 0 or num_terms <= 0:
        return {}
    skip = set(query_terms)
    weighted_tf: Counter[str] = Counter()
    judgments: dict[str, set] = {}
    for chunk in feedback:
        tokens = tokenize(chunk.text)
        if not tokens:
            continue
        doc_weight = max(chunk.bm25_score or 0.0, 0.0) / total
        for term, tf in Counter(tokens).items():
            if term in skip or _PLAIN_NUMBER.match(term):
                continue
            weighted_tf[term] += doc_weight * tf / len(tokens)
            judgments.setdefault(term, set()).add(chunk.metadata.get(DOCUMENT_ID, chunk.id))
    distinct = len({chunk.metadata.get(DOCUMENT_ID, chunk.id) for chunk in feedback})
    needed = min(min_judgments, distinct)
    candidates = [term for term in weighted_tf if len(judgments[term]) >= needed]
    idf_by_term = idf(candidates)
    scores = {term: weighted_tf[term] * idf_by_term[term] for term in candidates if term in idf_by_term}
    best = sorted(scores, key=lambda term: (-scores[term], term))[:num_terms]
    if not best:
        return {}
    top = scores[best[0]]
    return {term: scores[term] / top for term in best}
