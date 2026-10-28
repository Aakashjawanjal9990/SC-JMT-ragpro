# BM25 builder (Cloud Run Job)

Builds the BM25 index that the backend's retrieval module queries
(`app/retrieval/bm25_pg.py`). It reads citable chunks from the vector database
(Postgres + pgvector) and writes `bm25.meta`, `bm25.chunks` (including each chunk's
text and pages, which the backend returns directly) and `bm25.postings` into the
AlloyDB BM25 database, then swaps the new index in atomically. The previous index
is kept as `bm25_previous`.

Run it after every ingestion run. Queries keep working during a build.

## Configuration

Reads the same `config.yaml` as the backend (copied into the image; override the path
with `RETRIEVAL_CONFIG`):

| Key | Purpose |
|---|---|
| `databases.vector` | Source DB (chunks + embeddings) |
| `databases.bm25` | Target DB (AlloyDB) |
| `bm25.schema` | Schema the index is swapped into; the backend reads the same key |
| `bm25_builder.k1`, `bm25_builder.b` | BM25 parameters |
| `bm25_builder.min_keep_ratio`, `bm25_builder.allow_shrink` | Refuse to replace an index with one less than half its size, unless overridden |

Passwords are `${DB_PASSWORD}` / `${BM25_DB_PASSWORD}` references in `config.yaml`,
supplied as Secret Manager secrets.

## Deploy

```bash
REGION=asia-south1
IMAGE=$REGION-docker.pkg.dev/sc-judgments-rag/<repo>/bm25-builder

# 1. Build the image from the repository root (it copies tokenizer.py and config.yaml).
gcloud builds submit . --config bm25_builder/cloudbuild.yaml --substitutions=_IMAGE=$IMAGE

# 2. Create the job (Direct VPC egress so it can reach AlloyDB's private IP).
gcloud run jobs create bm25-builder \
  --image $IMAGE --region $REGION \
  --tasks 1 --max-retries 1 --task-timeout 14400 --memory 4Gi --cpu 2 \
  --service-account bm25-builder@sc-judgments-rag.iam.gserviceaccount.com \
  --network <vpc> --subnet <subnet> --vpc-egress private-ranges-only \
  --set-secrets DB_PASSWORD=scrag-db-password:latest,BM25_DB_PASSWORD=bm25-db-password:latest

# 3. Run it.
gcloud run jobs execute bm25-builder --region $REGION --wait
```

- **Service account roles:** `roles/cloudsql.client`, `roles/alloydb.client`,
  `roles/secretmanager.secretAccessor`.
- **Cloud SQL IP:** if Cloud SQL has only a public IP, set `ip_type: public` and
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
- **Size:** ~43k judgments (1M+ chunks) means roughly 150–250M `postings` rows plus a
  copy of every chunk's text. Plan AlloyDB storage (tens of GB with indexes) and memory
  so the `(term, weight DESC)` index stays mostly cached. A full build can take an hour
  or more, hence `--task-timeout 14400` (4h); check the first run's logs and adjust.
- **Index format** (`INDEX_FORMAT`, now 3): the backend refuses an index without chunk text,
  pages and the `terms` table (used for pseudo-relevance feedback). After upgrading, run
  this job once before deploying the backend.
