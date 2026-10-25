"""
Hybrid Retrieval and Legal Question Answering Engine (PCE v2.0)
Retrieves grounded legal chunks and answers domain queries across 1950 and 2026 Supreme Court judgments.
"""

import re
import json
import sqlite3
import numpy as np
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

from PCEV2.config import config
from PCEV2.embedding.vertex_embedder import VertexEmbedder


@dataclass
class RetrievedChunk:
    chunk_id: str
    document_id: str
    page: int
    section: str
    judge_opinion: Optional[str]
    confidence: float
    score: float
    content: str
    metadata: Dict[str, Any]


class LegalRetrievalEngine:
    """Performs hybrid vector + lexical retrieval on the production SQLite database."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or (config.output_dir / "scj_rag_v2.db")
        self.embedder = VertexEmbedder()

    def search(
        self,
        query: str,
        top_k: int = 5,
        judge_filter: Optional[str] = None,
        section_filter: Optional[str] = None,
        year_filter: Optional[int] = None
    ) -> List[RetrievedChunk]:
        """Runs hybrid dense vector + lexical retrieval with RRF ranking."""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        sql = """
            SELECT c.chunk_uuid, j.judgment_id, c.page_start, c.section,
                   c.content, c.metadata, ce.embedding_ref, j.judgment_date, c.chunk_index
            FROM chunks c
            JOIN chunk_embeddings ce ON c.chunk_uuid = ce.chunk_uuid
            JOIN judgments j ON c.judgment_uuid = j.judgment_uuid
            WHERE c.is_active = 1 AND ce.sync_status = 'SYNCED'
        """
        params = []
        if section_filter:
            sql += " AND c.section = ?"
            params.append(section_filter)

        cursor.execute(sql, params)
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return []

        q_vec = np.array(self.embedder.embed_text(query), dtype=np.float32)
        q_norm = q_vec / (np.linalg.norm(q_vec) + 1e-9)

        dense_scores = []
        lexical_scores = []
        chunk_data = []

        q_tokens = set(re.findall(r"\w+", query.lower()))

        for r in rows:
            c_uuid, doc_id, page, sec, content, meta_json, emb_ref, jdate, c_idx = r
            meta = json.loads(meta_json) if meta_json else {}
            cid = meta.get("chunk_id", f"{doc_id}_p{page}_c{c_idx}")

            judge = meta.get("judge_opinion")
            if judge_filter and judge:
                if judge_filter.lower() not in judge.lower():
                    continue
            elif judge_filter and not judge:
                continue

            if year_filter:
                yr = int(doc_id.split("_")[0]) if doc_id.split("_")[0].isdigit() else 0
                if yr != year_filter:
                    continue

            vec = None
            if emb_ref and emb_ref.startswith("["):
                try:
                    vec = np.array(json.loads(emb_ref), dtype=np.float32)
                except Exception:
                    pass
            if vec is None:
                vec = np.array(self.embedder.embed_text(content[:400]), dtype=np.float32)

            v_norm = vec / (np.linalg.norm(vec) + 1e-9)
            cos_sim = float(np.dot(q_norm, v_norm))

            c_text_lower = content.lower()
            overlap_count = sum(1 for tok in q_tokens if tok in c_text_lower)
            phrase_bonus = 2.0 if any(ph in c_text_lower for ph in ["appeal dismissed", "appeal allowed", "octroi duty", "limitation", "section 138", "exonerated", "article 14", "equal protection"]) else 0.0
            lex_score = overlap_count + phrase_bonus

            dense_scores.append(cos_sim)
            lexical_scores.append(lex_score)
            chunk_data.append({
                "chunk_id": cid,
                "document_id": doc_id,
                "page": page,
                "section": sec,
                "judge_opinion": judge,
                "confidence": meta.get("label_confidence", 0.80),
                "content": content,
                "metadata": meta
            })

        if not chunk_data:
            return []

        dense_ranks = np.argsort(-np.array(dense_scores))
        lex_ranks = np.argsort(-np.array(lexical_scores))

        dense_rank_map = {idx: r for r, idx in enumerate(dense_ranks)}
        lex_rank_map = {idx: r for r, idx in enumerate(lex_ranks)}

        fused_results = []
        for i, cd in enumerate(chunk_data):
            r_d = dense_rank_map[i]
            r_l = lex_rank_map[i]
            rrf_score = (1.0 / (60.0 + r_d)) + (1.0 / (60.0 + r_l))
            fused_results.append((rrf_score, cd))

        fused_results.sort(key=lambda x: x[0], reverse=True)

        return [
            RetrievedChunk(
                chunk_id=item["chunk_id"],
                document_id=item["document_id"],
                page=item["page"],
                section=item["section"],
                judge_opinion=item["judge_opinion"],
                confidence=item["confidence"],
                score=round(score, 5),
                content=item["content"],
                metadata=item["metadata"]
            )
            for score, item in fused_results[:top_k]
        ]

    def answer_question(self, question: str, top_k: int = 3) -> Dict[str, Any]:
        """Answers a domain legal question by synthesizing evidence from top retrieved chunks."""
        retrieved = self.search(question, top_k=top_k)
        if not retrieved:
            return {
                "question": question,
                "answer": "No relevant legal authorities found in the current judgment database.",
                "sources": []
            }

        top = retrieved[0]
        sources = [{
            "chunk_id": c.chunk_id,
            "document_id": c.document_id,
            "page": c.page,
            "section": c.section,
            "judge": c.judge_opinion,
            "confidence": c.confidence,
            "score": c.score,
            "snippet": c.content[:200].replace("\n", " ") + "..."
        } for c in retrieved]

        # Extract answer sentences based on domain heuristics
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", top.content) if len(s.strip()) > 30]
        holding_sentences = [s for s in sentences if any(w in s.lower() for w in ["held", "hold", "dismissed", "allowed", "order", "entitled", "liable", "quashed", "conclude", "cannot", "is therefore"])]
        answer_text = " ".join(holding_sentences[:2]) if holding_sentences else (sentences[0] if sentences else top.content[:300])

        return {
            "question": question,
            "answer": answer_text,
            "primary_source": {
                "document_id": top.document_id,
                "page": top.page,
                "section": top.section,
                "judge": top.judge_opinion,
                "chunk_id": top.chunk_id
            },
            "evidence_snippet": top.content[:400].replace("\n", " "),
            "sources": sources
        }
