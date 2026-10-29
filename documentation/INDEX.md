# Master Documentation Library Index (PCE v2.0)

**Project**: Supreme Court of India Judgment Legal RAG Ingestion Pipeline  
**Corpus**: 43,000+ Supreme Court Judgments (1950 Historical Scanned Reports to 2026 Born-Digital Judgments)  
**Location**: `documentation/`

This library contains all **engineering specifications, architectural walkthroughs, team handoff reports, QA audit resolutions, OCR benchmarking reports, and retrieval evaluation studies** produced for the PCE v2.0 offline ingestion pipeline.

---

## 1. Team Handover & Production Deployment

| Document | Purpose & Scope |
| :--- | :--- |
| **[`HANDOFF_PRATIK_RETRIEVAL_ENGINEER.md`](./HANDOFF_PRATIK_RETRIEVAL_ENGINEER.md)** | **🔥 Lead Retrieval Engineer Handover (Pratik)**: Definitive retrieval engineering specification covering chunk token bounds ($1,500$ char target, $\le 2,000$ char hard max), Rule R2 single-page guarantee, 768-dim $L_2$-normalized Vertex AI embeddings, AlloyDB HNSW / BM25 index definitions, `chunks.metadata` JSONB schema, and ready-to-run hybrid search SQL templates. |
| **[`PRODUCTION_DEPLOYMENT_CERTIFICATION.md`](./PRODUCTION_DEPLOYMENT_CERTIFICATION.md)** | **Master Production Sign-Off**: Lead engineer accountability certification, root-cause resolution of Verity QA audit findings (OFF-001 to OFF-010), Cloud Run readiness, and deployment defense guide. |
| **[`REVIEWER_WALKTHROUGH_PCE_V2.md`](./REVIEWER_WALKTHROUGH_PCE_V2.md)** | **Reviewer Walkthrough & Defense**: Line-by-line guide for evaluators demonstrating zero-DDL compliance, single-page chunking (Rule R2), and fail-closed quality gates. |
| **[`TEAM_HANDOFF_MASTER_INDEX.md`](./TEAM_HANDOFF_MASTER_INDEX.md)** | **Team Handoff & Operations**: Master operational checklist, CLI cheat sheet, environment variables reference, and failure recovery protocols. |
| **[`implementation_plan.md`](./implementation_plan.md)** | **Master Execution Roadmap**: Phased engineering implementation plan from baseline ingestion to full production deployment. |

---

## 2. Architecture & Technical Specifications

| Document | Purpose & Scope |
| :--- | :--- |
| **[`MASTER_V1_TO_V2_FULL_ARCHITECTURE_AND_CODE_WALKTHROUGH.md`](./MASTER_V1_TO_V2_FULL_ARCHITECTURE_AND_CODE_WALKTHROUGH.md)** | **Exhaustive 45 KB Architecture Deep Dive**: Complete line-by-line code and system walkthrough covering PyMuPDF coordinate zoning, multi-tier Google Cloud Vision OCR routing, single-page paragraph chunking, 768-dim embedding, and AlloyDB/PostgreSQL persistence. |
| **[`PCE_V2_Comprehensive_Architecture_Process_and_Changelog.md`](./PCE_V2_Comprehensive_Architecture_Process_and_Changelog.md)** | **Comprehensive Architecture & Changelog**: Full architectural workflow, stage-by-stage data contracts, and complete system changelog. |
| **[`PCE_V2_APPROACH_AND_ARCHITECTURE.md`](./PCE_V2_APPROACH_AND_ARCHITECTURE.md)** | **Core Architecture Blueprint**: Data pipelines, relational integrity models, coordinate bounding-box math, and relational database loading. |
| **[`PARSING_CHUNKING_EMBEDDING_APPROACH.md`](./PARSING_CHUNKING_EMBEDDING_APPROACH.md)** | **Layout-Aware Approach Specification**: Mathematical formulas for coordinate zoning, margin note stripping, citation protection, and single-page invariant enforcement. |
| **[`CURRENT_PROJECT_APPROACH_PCE_V2.md`](./CURRENT_PROJECT_APPROACH_PCE_V2.md)** | **Operational Project Blueprint**: Summary of active pipeline components, directory layouts, and execution parameters. |
| **[`MASTER_PROMPT_40K.md`](./MASTER_PROMPT_40K.md)** | **43,000+ Corpus Requirements**: Formal invariant definitions, throughput targets, and scale specifications across the 1950–2026 corpus. |

---

## 3. Evolution & Remediation Reports (V1 vs V2)

| Document | Purpose & Scope |
| :--- | :--- |
| **[`V1_vs_V2_Problems_and_Solutions_Report.md`](./V1_vs_V2_Problems_and_Solutions_Report.md)** | **Master Problem-to-Solution Matrix**: Deep analysis of all V1 failure modes (header leakage, DB locks, memory crashes, cross-page chunks) and exact V2 solutions. |
| **[`V1_VS_V2_PROBLEMS_AND_SOLUTIONS.md`](./V1_VS_V2_PROBLEMS_AND_SOLUTIONS.md)** | **Evolution & Lessons Learned**: Detailed comparative technical analysis of legacy vs modern pipeline architecture. |
| **[`Supreme_Court_RAG_Comprehensive_Resolution_Report.md`](./Supreme_Court_RAG_Comprehensive_Resolution_Report.md)** | **Comprehensive Issue Resolution**: Systematic resolution log covering historical scan anomalies, shared boundary splits, and metadata extraction. |
| **[`PCE_V2_SOLUTIONS_TO_RELIABILITY_REVIEW.md`](./PCE_V2_SOLUTIONS_TO_RELIABILITY_REVIEW.md)** | **Reliability Review Remediation**: Exact solutions implemented in response to engineering review findings. |
| **[`PCE_V2_APPROACH_FINAL_WITH_CHANGES.md`](./PCE_V2_APPROACH_FINAL_WITH_CHANGES.md)** | **Final Approach Changelog**: Key structural changes made during final optimization and hardening. |
| **[`PCE_V2_APPROACH_BASELINE_PRE_OCR.md`](./PCE_V2_APPROACH_BASELINE_PRE_OCR.md)** | **Pre-OCR Baseline**: Original baseline metrics before the integration of conditional OCR. |

---

## 4. OCR & Multi-Modal Engineering

| Document | Purpose & Scope |
| :--- | :--- |
| **[`HANDOFF_PRATIK_RETRIEVAL_ENGINEER.md`](./HANDOFF_PRATIK_RETRIEVAL_ENGINEER.md)** | **Google Cloud Vision OCR Specification**: Section 7 details the 4-tier fallback hierarchy (Google Cloud Vision gRPC $\to$ REST $\to$ PyMuPDF stream rescue $\to$ Tesseract), header purging, and OCR degradation healing. |
| **[`Local_Conditional_PaddleOCR_Completion_Report.md`](./Local_Conditional_PaddleOCR_Completion_Report.md)** | **Historical OCR Phase 1 Report**: Prior local OCR evaluation report detailing router design patterns and initial 8-rule routing logic. |
| **[`Local_OCR_20_PDFs_E2E_Pipeline_Test_Report.md`](./Local_OCR_20_PDFs_E2E_Pipeline_Test_Report.md)** | **20-PDF OCR Pipeline Validation**: Full empirical test results processing 20 historical scanned PDFs and modern born-digital judgments. |
| **[`LlamaParse_Conditional_OCR_PoC_Report.md`](./LlamaParse_Conditional_OCR_PoC_Report.md)** | **Cloud vs Local OCR Comparison**: Comparative PoC evaluating cloud-based parsers against local/cloud vision engines on cost, latency, and privacy. |

---

## 5. Quality Audits, Benchmarks & Evaluations

| Document | Purpose & Scope |
| :--- | :--- |
| **[`PCE_v2_20_Queries_Retrieval_Benchmark_Report.md`](./PCE_v2_20_Queries_Retrieval_Benchmark_Report.md)** | **20-Query Legal Retrieval Benchmark**: Empirical evaluation across 20 legal domains showing top retrieved cases, exact single-page pin-points, authoring judges, and legal holdings. |
| **[`PCE_v2_Corpus_Evaluation_and_Retrieval_Report.md`](./PCE_v2_Corpus_Evaluation_and_Retrieval_Report.md)** | **Corpus Retrieval Report**: Retrieval quality, similarity distributions, and reciprocal rank evaluation on the full corpus. |
| **[`Master_Data_Folder_Testing_and_Evaluation_Report.md`](./Master_Data_Folder_Testing_and_Evaluation_Report.md)** | **Master Dataset Evaluation**: Full test report evaluating data integrity, OCR quality scores, and chunk distribution on master datasets. |
| **[`PCE_V2_TEST_EXECUTION_AND_VERIFICATION_REPORT.md`](./PCE_V2_TEST_EXECUTION_AND_VERIFICATION_REPORT.md)** | **Verification Quality Gates**: Audit results demonstrating 100% pass rate across Rule R2, header purity, and vector fidelity. |
| **[`PCE_v2_Executive_Report.md`](./PCE_v2_Executive_Report.md)** | **Executive Summary**: High-level overview of throughput, architecture, and business outcomes. |
| **[`Supreme_Court_RAG_Baseline_Report.md`](./Supreme_Court_RAG_Baseline_Report.md)** | **Baseline Measurements**: Initial benchmark and performance baselines. |
| **[`Supreme_Court_RAG_PCE_v2_Engineering_Approach.md`](./Supreme_Court_RAG_PCE_v2_Engineering_Approach.md)** | **Engineering Methodology**: High-level engineering approach and design decisions. |
