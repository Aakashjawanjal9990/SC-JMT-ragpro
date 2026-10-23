# Production Security & Credential Hardening Guide

**System**: Supreme Court Judgment RAG Platform  
**Target Environment**: Google Cloud Platform (Cloud Run Jobs, GCS, Vertex AI, Cloud SQL)  
**Security Standard**: Zero-Trust, Least-Privilege, Secret Zero Compliance  

---

## 1. Security Architecture Overview

All credentials, keys, and tokens follow strict separation of duties to prevent credential leakage, privilege escalation, and unauthorized access:

```mermaid
flowchart TD
    subgraph "Local / Developer Machine"
        A["Git Working Copy"] --> B[".gitignore & .dockerignore<br/>Blocks .env*, *.key, *sa-key*.json"]
        B --> C["GitHub Repository<br/>(Clean: Zero Secret Footprint)"]
    end

    subgraph "CI/CD Pipeline"
        C --> D["GitHub Actions"]
        D -->|Encrypted Secret| E["GCP_SA_KEY (Masked in Logs)"]
        D -->|Least-Privilege Auth| F["GCP Cloud Build"]
    end

    subgraph "Google Cloud Production"
        F --> G["Container Registry (Artifact Registry)"]
        G --> H["Cloud Run Job<br/>(Runs as appuser UID:10001)"]
        H -->|IAM Token| I["GCS Bucket<br/>(Storage Object Admin)"]
        H -->|IAM Token| J["Vertex AI<br/>(aiplatform.user)"]
        H -->|Mounts Secret at Runtime| K["Google Secret Manager<br/>(DATABASE_URL)"]
        K --> L["Cloud SQL PostgreSQL"]
    end
```

---

## 2. Hardened Rules Applied

### 2.1 Git & VCS Hardening (`.gitignore`)
The repository strictly blocks any attempt to commit sensitive files:
- All `.env*` files (except `.env.example`).
- All cryptographic key files (`*.pem`, `*.key`, `*.pfx`, `*.p12`).
- All GCP Service Account JSON keys (`*sa-key*.json`, `*credential*.json`, `*service-account*.json`, `client_secrets*.json`).
- All local database files (`*.db`, `*.sqlite`, `*.sqlite3`).
- Runtime checkpoint logs (`ingestion_state.json`, `*.log`).

### 2.2 Container Isolation (`Dockerfile` & `.dockerignore`)
- **Non-Root Execution**: Runs as an isolated user (`appuser`, UID `10001`, GID `10001`).
- **No Secret Baking**: Neither `.env` nor any credentials exist inside the Docker image layers.
- **Minimal Surface**: Based on `python:3.10-slim-bookworm` with all unnecessary package managers and build tools cleared (`rm -rf /var/lib/apt/lists/*`).

### 2.3 GCP Identity & Least-Privilege IAM
The service account `scj-ingestion-sa@sc-judgments-rag.iam.gserviceaccount.com` has **zero administrative privileges**. It has exactly the minimum 4 roles required:

| Role | Scope | Purpose |
| :--- | :--- | :--- |
| `roles/storage.objectAdmin` | Bucket Level | Read raw PDFs and write chunk JSONs |
| `roles/aiplatform.user` | `asia-south1` | Call Vertex AI `text-embedding-004` |
| `roles/cloudsql.client` | Project Level | Connect securely to Cloud SQL |
| `roles/secretmanager.secretAccessor` | Secret Level | Read `DATABASE_URL` at runtime |

*No `roles/owner`, `roles/editor`, or broad IAM roles are assigned.*

---

## 3. Safe Secret Rotation Checklist

When updating your Cloud SQL password or API keys:
1. **Never edit files in git**.
2. **Never type passwords in CLI command histories**.
3. **Always use Google Secret Manager**:
   ```bash
   echo -n "postgresql://REAL_USER:REAL_PASS@REAL_IP:5432/scj_rag" | \
   gcloud secrets versions add scj-database-url --data-file=-
   ```
4. Cloud Run will automatically fetch the new secret version on the next execution without needing any code changes or redeployments.
