# Data Ingestion: Tech Stack & GCP Access Request
**Role**: Data Ingestion Engineer (Kedar)  
**For**: Prathmesh (Cloud Infrastructure & Security Engineer)  
**Project**: Supreme Court Judgment RAG System (Shared GCP Infrastructure)

---

## 1. Tech Stack & Exact Versions Used

| Technology / Library | Version | Purpose in Ingestion Pipeline |
| :--- | :--- | :--- |
| **Python** | **`3.10.0`** | Primary runtime environment. |
| **`llama-parse`** | **`0.6.94`** | Cloud-based PDF parsing & markdown extraction (v2 API). |
| **`llama-cloud`** | **`0.1.46`** | Underlying LlamaCloud SDK client. |
| **`llama-index-core`** | **`0.14.25`** | Document and node abstraction framework. |
| **`PyMuPDF` (`fitz`)** | **`1.25.3`** | Fast local selectable text extraction & page rasterization. |
| **`pydantic`** | **`2.13.4` (v2)** | Strict schema validation for single-page and chunk contracts. |
| **`httpx`** | **`0.28.1`** | Direct HTTP client for LlamaParse upload & status polling. |
| **`tenacity`** | **`8.5.0`** | Exponential backoff retry engine for fault tolerance. |
| **`python-dotenv`** | **`1.0.1`** | Runtime environment variable management. |
| **`reportlab`** | **`4.4.1`** | Supreme Court sample PDF generation for test validation. |
| **`pytest`** | **`9.1.1`** | Automated unit, contract, and pipeline test runner. |
| **`psycopg2-binary`** | **`2.9.9`** | PostgreSQL client adapter for testing DB inserts. |
| **`tesseract-ocr`** | **`5.x` (Linux)** | System package for local OCR fallback on scanned pages. |

---

## 2. GCP IAM Roles & Access Required

Please assign the following least-privilege IAM roles to Kedar's GCP account / service account:

| GCP Service | IAM Role Name | Exact Role ID | What This Access Allows Me To Do |
| :--- | :--- | :--- | :--- |
| **Google Cloud Storage** | **Storage Object User** | `roles/storage.objectUser` | Read input PDFs from `gs://.../raw_pdfs/` and write chunk JSONs to `gs://.../output/`. |
| **Secret Manager** | **Secret Manager Secret Accessor** | `roles/secretmanager.secretAccessor` | Read the runtime API key `LLAMA_CLOUD_API_KEY` securely without exposing it in code. |
| **Cloud SQL** | **Cloud SQL Client** | `roles/cloudsql.client` | Connect to Anuradha’s PostgreSQL instance via Cloud SQL Auth Proxy to verify data ingestion. |
| **Compute Engine** | **Compute OS Login** | `roles/compute.osLogin` | SSH into the shared GCP Linux VM running the batch ingestion pipeline. |
| **Compute Engine** | **Compute Instance Admin (v1)** | `roles/compute.instanceAdmin.v1` | Start, stop, and restart the dedicated batch ingestion worker VM. |
| **Cloud Run** *(if using Jobs)* | **Cloud Run Developer** | `roles/run.developer` | Deploy and trigger containerized batch ingestion jobs. |
| **Cloud Logging** | **Logs Viewer** | `roles/logging.viewer` | View runtime logs, execution traces, and error outputs in GCP Console. |

---

## 3. Network Egress Requirement

* **Firewall Egress Rule**: Outbound HTTPS on port **443** allowed to:
  * `api.cloud.llamaindex.ai` (LlamaParse v2 document parsing API)
  * `storage.googleapis.com` (Google Cloud Storage)
  * `pypi.org` & `files.pythonhosted.org` (Package installation during build)

