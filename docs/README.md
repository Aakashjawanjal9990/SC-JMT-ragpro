# Supreme Court RAG Documentation Library (PCE v2.0)

This directory consolidates all engineering specifications, architecture blueprints, reliability remediation reports, and evaluation benchmarks.

---

## Documentation Index

| Document | Purpose & Scope |
| :--- | :--- |
| **[`V1_VS_V2_PROBLEMS_AND_SOLUTIONS.md`](./V1_VS_V2_PROBLEMS_AND_SOLUTIONS.md)** | **Master Evolution Report**: Detailed analysis of all failure modes faced in V1 (headers, footnotes, shared pages, DB locks, silent failures, memory leaks) and how each was solved in V2. |
| **[`PCE_V2_APPROACH_AND_ARCHITECTURE.md`](./PCE_V2_APPROACH_AND_ARCHITECTURE.md)** | **Engineering Architecture**: Detailed system flow, problem-to-code solution matrix, verification quality gates, and relational integrity queries. |
| **[`RETRIEVAL_20_QUERIES_EVALUATION.md`](./RETRIEVAL_20_QUERIES_EVALUATION.md)** | **20-Query Legal Benchmark**: Empirical evaluation across 20 legal domains showing top retrieved cases, exact single-page pin-points, authoring judges, and legal holdings. |
| **[`MASTER_PROMPT_40K.md`](./MASTER_PROMPT_40K.md)** | **Production Scale Blueprint**: Original requirements and mathematical invariants for ingesting 43,000+ judgments spanning 1950 to 2026. |
| **[`PARSING_CHUNKING_EMBEDDING_APPROACH.md`](./PARSING_CHUNKING_EMBEDDING_APPROACH.md)** | **Layout-Aware Approach Specification**: Mathematical formulas for coordinate bounding box zoning, margin isolation, and single-page chunking. |
| **[`PCE_V2_SOLUTIONS_TO_RELIABILITY_REVIEW.md`](./PCE_V2_SOLUTIONS_TO_RELIABILITY_REVIEW.md)** | **Remediation Register**: Systematic solutions to all P0, P1, and P2 review findings. |
| **[`PCE_V2_TEST_EXECUTION_AND_VERIFICATION_REPORT.md`](./PCE_V2_TEST_EXECUTION_AND_VERIFICATION_REPORT.md)** | **QA Verification Report**: Quality gate audit on initial 7 benchmark documents across 1950 and 2026. |

