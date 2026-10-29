# Supreme Court RAG Documentation Library (PCE v2.0)

This directory consolidates all engineering specifications, architecture blueprints, team handoffs, reliability remediation reports, and evaluation benchmarks for the offline ingestion pipeline.

---

## 🎯 Primary Handover Documents

| Document | Purpose & Target Audience |
| :--- | :--- |
| **[`HANDOFF_PRATIK_RETRIEVAL_ENGINEER.md`](./HANDOFF_PRATIK_RETRIEVAL_ENGINEER.md)** | **🔥 Lead Retrieval Engineer Handover (Pratik)**: Detailed token bounds ($1,500$ char target, $\le 2,000$ char max), Rule R2 single-page guarantees, 768-dim $L_2$-normalized Vertex AI embeddings, AlloyDB HNSW / BM25 index definitions, `chunks.metadata` schema, and hybrid search SQL queries. |
| **[`INDEX.md`](./INDEX.md)** | **Master Library Index**: Complete categorized catalog of all 28 technical specifications, architecture blueprints, and evaluation reports. |
| **[`TEAM_HANDOFF_MASTER_INDEX.md`](./TEAM_HANDOFF_MASTER_INDEX.md)** | **Team Handoff Matrix**: Multi-discipline handoff pointers for Database, Retrieval, DevOps, QA, and CI/CD engineers. |
| **[`PRODUCTION_DEPLOYMENT_CERTIFICATION.md`](./PRODUCTION_DEPLOYMENT_CERTIFICATION.md)** | **Production Certification**: Lead engineer verification and resolution of all QA audit findings (OFF-001 through OFF-010). |

---

## 📚 Core Architecture & Engineering References

| Document | Purpose & Scope |
| :--- | :--- |
| **[`MASTER_V1_TO_V2_FULL_ARCHITECTURE_AND_CODE_WALKTHROUGH.md`](./MASTER_V1_TO_V2_FULL_ARCHITECTURE_AND_CODE_WALKTHROUGH.md)** | **Architecture Deep Dive**: Line-by-line code and system walkthrough covering PyMuPDF coordinate zoning, multi-tier Google Cloud Vision OCR, single-page chunking, and AlloyDB persistence. |
| **[`PARSING_CHUNKING_EMBEDDING_APPROACH.md`](./PARSING_CHUNKING_EMBEDDING_APPROACH.md)** | **Chunking & Layout Specification**: Coordinate bounding box zoning, margin isolation, and single-page invariant (Rule R2). |
| **[`V1_VS_V2_PROBLEMS_AND_SOLUTIONS.md`](./V1_VS_V2_PROBLEMS_AND_SOLUTIONS.md)** | **Master Evolution Report**: Failure modes faced in legacy systems and exact architectural solutions implemented in PCE v2.0. |
| **[`PCE_v2_20_Queries_Retrieval_Benchmark_Report.md`](./PCE_v2_20_Queries_Retrieval_Benchmark_Report.md)** | **20-Query Legal Benchmark**: Empirical evaluation across 20 legal domains showing top retrieved cases and legal holdings. |
