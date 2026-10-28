# Query backend

FastAPI backend for the Supreme Court RAG project. It keeps the versioned `/v1/query` API and provides `/api` routes for the current frontend: email/password auth, email verification and reset, chat, conversation history, and admin reporting. It uses PostgreSQL/pgvector, Vertex AI, Redis, SMTP, and Cloud Run.

## Layout

- `app/api/routes.py`: health, readiness, and versioned query endpoints
- `app/api/compat.py`: frontend-compatible auth, chat, conversation, and admin endpoints
- `app/auth_service.py`: Argon2id passwords, email codes, hashed opaque sessions, and auth activity logging
- `app/email.py`: SMTP verification and reset messages
- `app/adapters/`: PostgreSQL retrieval and Vertex AI
- `app/orchestrator.py`: query flow and citation identity checks
- `migrations/versions/0001_live_schema_baseline.py`: baseline migration
- `migrations/versions/0001_live_schema_baseline.sql`: schema-only snapshot from Cloud SQL

## Local development

From this directory:

```powershell
Copy-Item .env.example .env
docker compose up --build -d
```

The API listens at `http://localhost:8080`. Set frontend `VITE_API_URL=http://localhost:8080/api`. Mailpit captures OTP/reset mail at `http://localhost:8025`.

### Ingestion and local end-to-end query

The `../ingestion` directory contains Kedar's offline PCE pipeline. Place permitted sample PDFs under `ingestion/data/sample_pdfs/year=<year>/english/`, then run its batch pipeline from that directory. It writes `ingestion/output/scj_rag_v2.db` without changing the checked-in schema. The upstream fixture PDFs are not included; PDF-dependent ingestion tests now skip until you provide those files, and the batch command needs at least one input PDF.

For a real local query using the SQLite export, set these values in `backend/.env` after the ingestion database exists:

```dotenv
RETRIEVAL_PORT_ADAPTER_ENABLED=true
RETRIEVAL_VECTOR_STORE=sqlite
RETRIEVAL_SQLITE_PATH=/data/scj_rag_v2.db
RETRIEVAL_EMBEDDING_MODEL=text-embedding-004
RETRIEVAL_EMBEDDING_PROVIDER=vertex
RETRIEVAL_BM25_BACKEND=memory
```

The Compose file mounts the host database read-only into the API container. Run `alembic upgrade head` inside the API container once to initialize the separate local PostgreSQL database used for accounts, sessions, and chat history. Configure Vertex Application Default Credentials and matching Google Cloud project/location values before querying. Start the frontend from `../frontend` with `npm ci` and `npm run dev`; its `.env` should set `VITE_API_URL=http://localhost:8080/api`. Verification email appears in Mailpit at `http://localhost:8025`.

The retrieval module also has a test-only `RETRIEVAL_EMBEDDING_PROVIDER=test` option for checking local wiring without Vertex credentials. It produces non-semantic hash vectors; use it only for connectivity tests, not retrieval-quality evaluation or production.

To test the web flow without Google Cloud credentials, explicitly set `LOCAL_TEST_MODE=true`, `RETRIEVAL_EMBEDDING_PROVIDER=test`, and `RETRIEVAL_EMBEDDING_MODEL=test-pseudo-hash-768` in the local Compose environment, along with SQLite mode and `RETRIEVAL_BM25_BACKEND=memory`. The API labels its response as local test output and quotes retrieved text. Ingestion must also run with `ALLOW_MOCK_EMBEDDINGS=true`; those vectors are tagged `TEST_ONLY` and fail the production embedding quality gate. Never use this setup for legal answer quality checks.

Set Google Application Default Credentials and Vertex values in `.env` for live query generation. The frontend auth and conversation APIs require the `users`, `user_sessions`, `user_activity_logs`, and `query_runs` schema.

## Database migration status

The Alembic baseline and schema-only SQL snapshot now reflect the live Cloud SQL export, including `chunks`, `judgments`, `source_files`, and auth/history tables. The export did not contain an `alembic_version` table. **Do not run `alembic upgrade head` against the existing Cloud SQL instance:** that would try to create tables that already exist.

After reviewing the snapshot against the live schema, the existing instance must be stamped once at `0001_live_schema_baseline`. Stamping records the baseline without executing its table-creation SQL. Cloud Build migration execution remains disabled until that one-time bootstrap is complete. Fresh databases can use `alembic upgrade head` to create the schema.

The last recorded Cloud SQL check found zero chunks with embeddings. Recheck the live database before deployment; query integration needs an ingested corpus with embeddings from the configured model.

## Production configuration

Production requires `DATABASE_URL` using `postgresql+asyncpg`, `REDIS_URL`, `AUTH_CHALLENGE_SECRET` (at least 32 bytes), SMTP host/from address, Google Cloud project/location, Vertex model IDs, and exact HTTPS `CORS_ORIGINS`. Store database, Redis, SMTP credentials, and challenge secret in Secret Manager. Set Cloud Run's Cloud SQL attachment, VPC connectivity for Redis, and a least-privilege runtime service account.

Cloud Run provides HTTPS and manages its serving certificate. The container itself listens on HTTP behind the managed proxy. The Cloud Build pipeline runs Ruff, Bandit, and pip-audit, builds/pushes the image, and deploys the service. Database migrations remain excluded until the existing database has been stamped at the verified baseline.

## API and ownership

See `FRONTEND_INTEGRATION.md` for request/response shapes. `/v1/query` remains available to direct clients and can use OIDC when configured. The browser-facing `/api` routes use opaque database-backed sessions carried in HttpOnly cookies, not Bearer tokens. User and admin sessions use separate cookies; admin writes also require the session's CSRF token in `X-CSRF-Token`. Admin routes check the stored role on each request. A first admin must be provisioned securely in the database; public registration always assigns the regular-user role.

The adapter verifies citation identity and source/page provenance. It does not yet provide claim-level legal entailment verification. Production launch also requires a populated corpus, baseline stamping, configured production email, and deployment review.

## Backend security controls

- Authentication endpoints are rate limited by client IP and by a normalized, hashed account identifier. `AUTH_IDENTITY_RATE_LIMIT_REQUESTS` and `AUTH_IDENTITY_RATE_LIMIT_WINDOW_SECONDS` default to 8 attempts per 15 minutes.
- `MAX_REQUEST_BODY_BYTES` defaults to 1 MiB and is enforced before FastAPI parses the body.
- In production, chat rows in `query_runs` older than `CHAT_RETENTION_DAYS` are deleted daily; the default is 365 days. Set this to match the service's approved retention policy. Database backups have their own retention and deletion controls.
- Successful sign-ins and sign-outs, registration, verification, password resets, and failed password/code attempts are recorded in `user_activity_logs`. Failed identities are represented by a hash. Do not log credentials, session tokens, or user query text in auth activity events; chat query text is stored in `query_runs` under the configured retention policy.
- `/ready` returns only an aggregate ready/not-ready status; dependency details are not exposed publicly.

Administrator accounts must use the dedicated admin sign-in endpoint, which issues the separate, shorter eight-hour admin session; a regular login cannot create an admin session. The frontend receives a CSRF token in the login response and keeps it in memory; the opaque session token is never returned to JavaScript. Admin sign-in still uses password authentication without MFA. Adding MFA requires a coordinated admin login and recovery flow in the frontend; the backend-only security changes preserve the current API contract. Tenant isolation is also not implemented because the indexed judgment corpus is shared and public; private tenant documents require tenant ownership filters in retrieval and source resolution before they are introduced.
