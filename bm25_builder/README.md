# BM25 builder (Cloud Run Job)

Builds the BM25 index that the backend's retrieval module queries
(`app/retrieval/bm25_pg.py`). It reads citable chunks from Cloud SQL (`scrag`) and
writes `bm25.meta`, `bm25.chunks` and `bm25.postings` into the AlloyDB BM25 database,
then swaps the new index in atomically. The previous index is kept as `bm25_previous`.

Run it after every ingestion run. Queries keep working during a build.

## Configuration

Uses the same variable names as the backend.

| Variable | Purpose |
|---|---|
| `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `INSTANCE_CONNECTION_NAME`, `DB_IP_TYPE` | Source DB (Cloud SQL) |
| `BM25_DB_NAME`, `BM25_DB_USER`, `BM25_DB_PASSWORD`, `BM25_INSTANCE_URI`, `BM25_DB_IP_TYPE` | Target DB (AlloyDB) |
| `BM25_SCHEMA` (`bm25`) | Must equal the backend's `RETRIEVAL_BM25_SCHEMA` |
| `BM25_K1` (`1.2`), `BM25_B` (`0.75`) | BM25 parameters |
| `BM25_MIN_KEEP_RATIO` (`0.5`), `BM25_ALLOW_SHRINK` (`false`) | Refuse to replace an index with one less than half its size, unless overridden |
| `DB_HOST` / `BM25_DB_HOST` | Optional direct connection (Auth Proxy) instead of the connectors |

`DB_IP_TYPE` and `BM25_DB_IP_TYPE` default to `private`.

## Deploy

```bash
REGION=asia-south1
IMAGE=$REGION-docker.pkg.dev/sc-judgments-rag/<repo>/bm25-builder

# 1. Build the image from the repository root (it copies app/retrieval/tokenizer.py).
gcloud builds submit . --config bm25_builder/cloudbuild.yaml --substitutions=_IMAGE=$IMAGE

# 2. Create the job (Direct VPC egress so it can reach AlloyDB's private IP).
gcloud run jobs create bm25-builder \
  --image $IMAGE --region $REGION \
  --tasks 1 --max-retries 1 --task-timeout 3600 --memory 4Gi --cpu 2 \
  --service-account bm25-builder@sc-judgments-rag.iam.gserviceaccount.com \
  --network <vpc> --subnet <subnet> --vpc-egress private-ranges-only \
  --set-env-vars DB_NAME=scrag,DB_USER=postgres,INSTANCE_CONNECTION_NAME=sc-judgments-rag:asia-south1:sc-rag-pg,DB_IP_TYPE=private \
  --set-env-vars BM25_DB_NAME=<db>,BM25_DB_USER=<user>,BM25_INSTANCE_URI=projects/sc-judgments-rag/locations/asia-south1/clusters/<cluster>/instances/<instance> \
  --set-secrets DB_PASSWORD=scrag-db-password:latest,BM25_DB_PASSWORD=bm25-db-password:latest

# 3. Run it.
gcloud run jobs execute bm25-builder --region $REGION --wait
```

- **Service account roles:** `roles/cloudsql.client`, `roles/alloydb.client`,
  `roles/secretmanager.secretAccessor`.
- **Cloud SQL IP:** if Cloud SQL has only a public IP, set `DB_IP_TYPE=public` and
  use `--vpc-egress private-ranges-only`, so public traffic still leaves normally.
- **Scheduling:** trigger the job at the end of the ingestion pipeline, or on a
  Cloud Scheduler cron (`gcloud scheduler jobs create http ... /jobs/bm25-builder:run`).

## Operations

- **Empty source with no existing index:** creates a valid empty index, so the
  backend can start.
- **Rollback:** in a single transaction, run
  `ALTER SCHEMA bm25 RENAME TO bm25_bad; ALTER SCHEMA bm25_previous RENAME TO bm25;`
- **Changing the tokenizer** (`TOKENIZER_VERSION`): redeploy this job *and* the backend,
  then run the job. The backend refuses an index built with a different version.
- **Size:** about 600k chunks means tens of millions of `postings` rows. Plan
  AlloyDB storage accordingly; a build takes minutes, not seconds.
