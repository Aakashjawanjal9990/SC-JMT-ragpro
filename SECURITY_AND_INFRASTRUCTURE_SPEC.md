# Supreme Court Judgment RAG System
## Cloud Infrastructure, Security & IAM Specification (Handoff Document)
**From**: Kedar (Data Ingestion Engineer)  
**To**: Prathmesh (Cloud Infrastructure & Security Engineer)  
**Coordinated With**: Anuradha (Database Engineer), Aakash (CI/CD & DevOps), Nilesh (System Architect)  
**Workspace**: `E:\Dummy`

---

## 1. Executive Summary & Purpose

This specification outlines the **cloud infrastructure, security boundaries, IAM policies, secrets management, network rules, and cost-control mechanisms** required to deploy and run the **Data Ingestion Pipeline** on Google Cloud Platform (GCP).

The ingestion engine processes ~43,000 Indian Supreme Court judgment PDFs (spanning 1950 to the present) and delivers structured chunk and document records into Cloud SQL and Cloud Storage.

---

## 2. Secrets Management & API Keys

### 2.1 The Sensitive Secret: LlamaParse Cloud API Key
* **Environment Variable**: `LLAMA_CLOUD_API_KEY`
* **Format**: `llx-...` (e.g. `llx-ee2ydvz36sSllFhN6VTypVU8fgK86bilUIgKlFe8uCZuOkpW`)
* **Security Controls Applied in Code**:
  * `.gitignore` explicitly excludes `.env` to prevent accidental commits to GitHub.
  * A sanitized template [`.env.example`](.env.example) is provided in the repository with placeholder values.
  * Secrets are loaded via `python-dotenv` at runtime and never logged in plain text.

### 2.2 GCP Secret Manager Configuration
To deploy on GCP, Prathmesh should store this key in **Google Cloud Secret Manager**:
```bash
# 1. Create the secret in Secret Manager
gcloud secrets create llama-cloud-api-key \
    --replication-policy="automatic" \
    --project="<GCP_PROJECT_ID>"

# 2. Add the secret version
echo -n "llx-ee2ydvz36sSllFhN6VTypVU8fgK86bilUIgKlFe8uCZuOkpW" | \
gcloud secrets versions add llama-cloud-api-key \
    --data-file=- \
    --project="<GCP_PROJECT_ID>"
```

### 2.3 Runtime Secret Injection
* **For Cloud Run Jobs / GKE / Compute Engine**:
  Inject the secret as an environment variable `LLAMA_CLOUD_API_KEY` directly from Secret Manager at container launch:
  ```bash
  --set-secrets="LLAMA_CLOUD_API_KEY=llama-cloud-api-key:latest"
  ```

---

## 3. Identity & Access Management (IAM) — Least Privilege Principle

The ingestion pipeline should run under a dedicated, least-privilege Service Account.

### 3.1 Dedicated Service Account
* **Account Name**: `sa-scj-data-ingestion@<GCP_PROJECT_ID>.iam.gserviceaccount.com`
* **Display Name**: `SCJ RAG Data Ingestion Worker`

### 3.2 Required IAM Roles

| GCP Role | Resource Scope | Purpose |
| :--- | :--- | :--- |
| **`roles/secretmanager.secretAccessor`** | `projects/.../secrets/llama-cloud-api-key` | Read-only access to retrieve `LLAMA_CLOUD_API_KEY` at runtime. |
| **`roles/storage.objectUser`** | `gs://<scj-rag-data-bucket>/*` | Read raw PDFs from bucket; write chunk/document JSON deliverables. |
| **`roles/cloudsql.client`** | `projects/.../instances/<scj-postgres-instance>` | Secure connectivity through Cloud SQL Auth Proxy to Anuradha's PostgreSQL instance. |
| **`roles/logging.logWriter`** | Project level | Write ingestion progress and failure logs to Google Cloud Logging. |

### 3.3 Explicit Denials & Protections
* **No Public Access**: The service account must NOT possess project-wide Editor/Owner roles.
* **No KMS Admin**: No permissions to alter encryption keys.
* **No Ingress Access**: Ingestion workers have zero public ingress endpoints; they pull jobs from GCS or queue triggers.

---

## 4. Network & Egress Security

### 4.1 Required Outbound Egress
The ingestion pipeline requires outbound HTTPS traffic on port **443** for:

| Destination Host | Port / Protocol | Purpose |
| :--- | :--- | :--- |
| **`api.cloud.llamaindex.ai`** | `443` (HTTPS) | LlamaParse v2 document parsing API (`/api/v2/parse/upload` and status polling). |
| **`pypi.org` & `files.pythonhosted.org`** | `443` (HTTPS) | Package dependency retrieval during container builds (CI/CD). |
| **`storage.googleapis.com`** | `443` (HTTPS) | Google Cloud Storage API for downloading PDFs and archiving JSON outputs. |

### 4.2 Inbound Traffic (Ingress)
* **Zero Public Ingress**: Ingestion workers run as background workers or batch jobs (Cloud Run Jobs or GCE batch instances). No public IP ports (80/443/8080) need to be open to the internet.

### 4.3 Internal VPC & Database Connectivity
* **Cloud SQL Connection**:
  * Connect to Anuradha’s PostgreSQL instance via **Private Service Access (Private IP)** within the VPC or via the **Cloud SQL Auth Proxy** on `127.0.0.1:5432`.
  * Enforce SSL/TLS for all database transit (`sslmode=verify-full`).

---

## 5. Compute Sizing & Container Specifications

### 5.1 Workload Profile
* **Input Volume**: ~43,000 PDFs (~38 GB).
* **Workload Type**: I/O and CPU-bound (PDF extraction, text parsing, regex metadata matching, raster image rendering for OCR).

### 5.2 Recommended GCP Compute Options

| Environment | Recommended Machine Type | Specs | Usage |
| :--- | :--- | :--- | :--- |
| **Cloud Run Jobs (Recommended)** | Containerized Job | 2 vCPU, 4 GiB RAM, 60m timeout | Ideal for parallel batch jobs; scales down to zero when done. |
| **Compute Engine (GCE)** | `e2-standard-4` | 4 vCPU, 16 GiB RAM | Continuous batch processing workers. |

### 5.3 Container Dockerfile Specifications
Prathmesh and Aakash can use the hardened, non-root Dockerfile below:

```dockerfile
# Use minimal official Python Debian image
FROM python:3.10-slim-bookworm

# Set environment security flags
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

# Install system dependencies for PyMuPDF & OCR fallback
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    libmupdf-dev \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root application user
RUN groupadd -r appuser && useradd -r -g appuser appuser

WORKDIR /app

# Copy dependency definition and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY --chown=appuser:appuser . .

# Switch to non-root user
USER appuser

# Default entry point
CMD ["python", "main.py", "test-1950", "--limit", "0"]
```

---

## 6. Cloud Storage (GCS) Bucket Architecture & Lifecycle

### 6.1 GCS Bucket Structure
* **Bucket Name**: `gs://scj-rag-data-<env>/` (e.g. `gs://scj-rag-data-prod/`)

```
gs://scj-rag-data-prod/
├── raw_pdfs/                      # Input: ~43,000 PDF files organized by year
│   ├── year=1950/english/*.pdf
│   └── year=2023/english/*.pdf
├── deliverables/
│   ├── chunks/                    # Output: {doc_id}_chunks.json (for DB / Vector ingestion)
│   └── documents/                 # Output: {doc_id}.json (for DB document records)
├── checkpoints/
│   └── ingestion_state.json       # Ingestion status & SHA-256 hash tracking
└── reports/
    └── status_error_report.json   # QA failure logs
```

### 6.2 Bucket Security & Lifecycle Management
1. **Uniform Bucket-Level Access**:
   Enable Uniform Bucket-Level Access to ensure access is governed strictly by IAM, not individual object ACLs.
2. **Object Versioning**:
   Enable Object Versioning on `checkpoints/` to protect `ingestion_state.json` against accidental overwrites.
3. **Storage Lifecycle Rules (Cost Control)**:
   * **`deliverables/`**: Move to **Nearline storage** after 30 days; move to **Coldline storage** after 90 days.
   * **`reports/`**: Retain for 180 days, then delete.
4. **Encryption**:
   Use Google-managed encryption keys (default) or Customer-Managed Encryption Keys (CMEK) via Cloud KMS if compliance mandates.

---

## 7. Cost Controls & Efficiency Safeguards

The pipeline incorporates **software-level cost-control mechanisms** that Prathmesh can rely on to manage cloud bills:

1. **SHA-256 Cryptographic Deduplication**:
   Every PDF is hashed before processing. If a file is uploaded twice, the pipeline detects the identical hash in `ingestion_state.json` and skips processing immediately (`SKIPPED_DUPLICATE`), consuming **0 cloud compute cycles and 0 LlamaParse API credits**.
2. **Resumable State Checkpointing**:
   If an infrastructure instance is preempted or terminated, restarting the worker will skip already-completed documents, preventing duplicate costs.
3. **Dual-Engine Cost Governance**:
   * **Local PyMuPDF (Default / Free)**: Runs on local instance CPU at zero marginal API cost.
   * **LlamaParse Cloud**: Used selectively (`--use-llamaparse`) with tier set to `cost_effective`.
4. **GCP Budget Alerts**:
   Set budget alerts at \$100, \$250, and \$500 on the GCP project to catch runaway compute or storage usage.

---

## 8. Incident Response & Monitoring

1. **Failure Isolation**:
   When a malformed PDF fails, the pipeline isolates the exception, records the full stack trace in `reports/status_error_report.json`, updates `ingestion_state.json` with `"status": "FAILED"`, and proceeds to the next document.
2. **Cloud Monitoring Metric**:
   Log alerts should monitor Cloud Logging for:
   `jsonPayload.message = "Failed processing"`
   to trigger notifications to Kedar and Kusuma for defect triage.

---

## 9. Prathmesh Deployment Checklist

- [ ] **Secret Manager**: Create `llama-cloud-api-key` secret with the provided key.
- [ ] **Service Account**: Create `sa-scj-data-ingestion` with `secretAccessor`, `objectUser`, and `cloudsql.client` roles.
- [ ] **GCS Bucket**: Create `gs://scj-rag-data-<env>` with Uniform Bucket-Level Access and lifecycle rules.
- [ ] **Firewall / Egress**: Verify egress access to `api.cloud.llamaindex.ai` on port 443.
- [ ] **Cloud SQL Proxy**: Ensure private IP / proxy connection to PostgreSQL instance for Anuradha's schema.
- [ ] **Container Build**: Build the container image using the provided hardened Dockerfile and push to Google Artifact Registry.

