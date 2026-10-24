# Supreme Court Judgment RAG — Team Handoff Master Index

**Author**: Kedar (Data Ingestion & Pipeline Engineer)  
**System Scope**: ~43,000 Indian Supreme Court judgment PDFs (1950–Present) $\rightarrow$ ~3.5 Million Chunks  
**Status**: Production Ready (Cloud Run Jobs + Vertex AI `text-embedding-004` + PostgreSQL `pgvector`)  

---

## 1. Team Dependency Matrix

Below is the directory of tailored handoff documents created for each team member depending on this pipeline:

| Teammate | Role | What They Depend On Kedar For | Dedicated Handoff File |
| :--- | :--- | :--- | :--- |
| **Anuradha** | Database Architect | 9-table schema DDL, `VECTOR(768)` pgvector integration, HNSW index parameters, and batch loader throughput. | [**`HANDOFF_ANURADHA_DATABASE.md`**](handoffs/HANDOFF_ANURADHA_DATABASE.md) |
| **Pratik** | Retrieval & Search | $<512$ token chunk size guarantees, deterministic chunk IDs, 768-dim dense vectors, and hybrid search queries. | [**`HANDOFF_PRATIK_RETRIEVAL.md`**](handoffs/HANDOFF_PRATIK_RETRIEVAL.md) |
| **Prathmesh** | Cloud Infrastructure | Cloud Run Jobs sizing (77 parallel tasks, 4 vCPU, 8 GB RAM), IAM roles, Docker container, and Secret Manager. | [**`HANDOFF_PRATHMESH_DEVOPS.md`**](handoffs/HANDOFF_PRATHMESH_DEVOPS.md) |
| **Nilesh** | RAG Integrator | Strict page-boundary isolation, legal citation format, section heading context (`FACTS`, `JUDGMENT`), and Pydantic models. | [**`HANDOFF_NILESH_RAG_INTEGRATOR.md`**](handoffs/HANDOFF_NILESH_RAG_INTEGRATOR.md) |
| **Tanvi** | Frontend & UI | Clickable PDF citation locators (`document_id`, `original_page_number`), case metadata summary cards, and deep-link URLs. | [**`HANDOFF_TANVI_FRONTEND.md`**](handoffs/HANDOFF_TANVI_FRONTEND.md) |
| **Kusuma** | QA & Testing | Automated test suite (`pytest tests/ -v`), error reporting format, edge case matrix, and synthetic PDF generator. | [**`HANDOFF_KUSUMA_QA.md`**](handoffs/HANDOFF_KUSUMA_QA.md) |
| **Aakash** | CI/CD & Delivery | GitHub Actions workflow, Python 3.11 container specifications, Cloud Build triggers, and rollout commands. | [**`HANDOFF_AAKASH_CICD.md`**](handoffs/HANDOFF_AAKASH_CICD.md) |

---

## 2. Key Architecture Artifacts

- **Final Master Architecture & Solutions Record**: [`docs/FINAL_ARCHITECTURE_AND_SOLUTIONS_RECORD.md`](FINAL_ARCHITECTURE_AND_SOLUTIONS_RECORD.md)
- **Audit Rebuttal & Engineering Response Report**: [`docs/AUDIT_REBUTTAL_AND_ENGINEERING_RESPONSE_REPORT.md`](AUDIT_REBUTTAL_AND_ENGINEERING_RESPONSE_REPORT.md)
- **Data Cleaning & Pre-Embedding Verification Spec**: [`docs/DATA_CLEANING_AND_PRE_EMBEDDING_VERIFICATION.md`](DATA_CLEANING_AND_PRE_EMBEDDING_VERIFICATION.md)
- **Authoritative 20-Table SQL Contract**: [`legal_rag_cloudsql_postgresql_schema.sql`](../legal_rag_cloudsql_postgresql_schema.sql)
- **Embedding Architecture Guide**: [`docs/HOW_WE_DID_THE_EMBEDDING.md`](HOW_WE_DID_THE_EMBEDDING.md)
- **Database Logging & Audit Spec**: [`docs/DATABASE_LOGGING_AND_AUDIT_SPEC.md`](DATABASE_LOGGING_AND_AUDIT_SPEC.md)
- **Cloud Run Deployment Script**: [`deploy_cloud_run.sh`](../deploy_cloud_run.sh)
- **Container Definition**: [`Dockerfile`](../Dockerfile) (Python 3.11 Non-Root)
- **Automated Regression Tests**: [`tests/test_db_loader_strict.py`](../tests/test_db_loader_strict.py) & [`tests/test_ingestion.py`](../tests/test_ingestion.py) (12 Tests Passing)
