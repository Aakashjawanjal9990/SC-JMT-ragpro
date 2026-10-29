# Supreme Court Judgment RAG — Team Handoff Master Index

**Author**: Kedar (Data Ingestion & Pipeline Engineer)  
**System Scope**: ~43,000 Indian Supreme Court judgment PDFs (1950–Present) $\rightarrow$ ~3.5 Million Chunks  
**Status**: Production Ready (Cloud Run Jobs + Vertex AI `text-embedding-004` + PostgreSQL `pgvector`)  

---

## 1. Team Dependency Matrix

Below is the directory of tailored handoff documents created for each team member depending on this pipeline:

| Teammate | Role | What They Depend On Kedar For | Dedicated Handoff File |
| :--- | :--- | :--- | :--- |
| **Pratik** | Retrieval & Search Engineer | $\le 500$ token chunk size guarantees, deterministic chunk IDs, 768-dim $L_2$-normalized dense vectors, HNSW index parameters, `chunks.metadata` schema, and hybrid search SQL queries. | [**`PARSING_CHUNKING_EMBEDDING_APPROACH.md`**](./PARSING_CHUNKING_EMBEDDING_APPROACH.md) |
| **Anuradha** | Database Architect | 9-table schema DDL, `VECTOR(768)` pgvector integration, HNSW index parameters, and batch loader throughput. | [**`PRODUCTION_DEPLOYMENT_CERTIFICATION.md`**](./PRODUCTION_DEPLOYMENT_CERTIFICATION.md) |
| **Prathmesh** | Cloud Infrastructure | Cloud Run Jobs sizing (77 parallel tasks, 4 vCPU, 8 GB RAM), IAM roles, Docker container, and Secret Manager. | [**`PRODUCTION_DEPLOYMENT_CERTIFICATION.md`**](./PRODUCTION_DEPLOYMENT_CERTIFICATION.md) |
| **Nilesh** | RAG Integrator | Strict page-boundary isolation, legal citation format, section heading context (`FACTS`, `JUDGMENT`), and Pydantic models. | [**`PARSING_CHUNKING_EMBEDDING_APPROACH.md`**](./PARSING_CHUNKING_EMBEDDING_APPROACH.md) |
| **Kusuma** | QA & Testing | Automated test suite (`audit_quality.py`), error reporting format, edge case matrix, and synthetic PDF generator. | [**`PCE_V2_TEST_EXECUTION_AND_VERIFICATION_REPORT.md`**](./PCE_V2_TEST_EXECUTION_AND_VERIFICATION_REPORT.md) |
| **Aakash** | CI/CD & Delivery | GitHub Actions workflow, Python 3.11 container specifications, Cloud Build triggers, and rollout commands. | [**`PRODUCTION_DEPLOYMENT_CERTIFICATION.md`**](./PRODUCTION_DEPLOYMENT_CERTIFICATION.md) |

---

## 2. Key Architecture Artifacts

- **Master Documentation Library Index**: [`INDEX.md`](./INDEX.md)
- **Master Production Sign-Off & Verification**: [`PRODUCTION_DEPLOYMENT_CERTIFICATION.md`](./PRODUCTION_DEPLOYMENT_CERTIFICATION.md)
- **Full Architecture Deep Dive**: [`MASTER_V1_TO_V2_FULL_ARCHITECTURE_AND_CODE_WALKTHROUGH.md`](./MASTER_V1_TO_V2_FULL_ARCHITECTURE_AND_CODE_WALKTHROUGH.md)
- **Authoritative SQL Contract**: [`db/schema.sql`](../db/schema.sql)
- **Quality Audit Script**: [`audit_quality.py`](../audit_quality.py)
