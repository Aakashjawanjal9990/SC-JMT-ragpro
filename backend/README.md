# Query backend

FastAPI backend for the Supreme Court RAG project. It keeps the versioned `/v1/query` API and provides `/api` routes for the current frontend: email/password auth, email verification and reset, chat, conversation history, and admin reporting. It uses PostgreSQL/pgvector, Vertex AI, Redis, SMTP, and Cloud Run.

## Layout

- `app/api/routes.py`: health, readiness, and versioned query endpoints
- `app/api/compat.py`: frontend-compatible auth, chat, conversation, and admin endpoints
- `app/auth_service.py`: Argon2id passwords, email codes, hashed opaque sessions
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

Set Google Application Default Credentials and Vertex values in `.env` for live query generation. The frontend auth and conversation APIs require the `users`, `user_sessions`, `user_activity_logs`, and `query_runs` schema.

## Database migration status

The Alembic baseline and schema-only SQL snapshot now reflect the live Cloud SQL export, including `chunks`, `judgments`, `source_files`, and auth/history tables. The export did not contain an `alembic_version` table. **Do not run `alembic upgrade head` against the existing Cloud SQL instance:** that would try to create tables that already exist.

After reviewing the snapshot against the live schema, the existing instance must be stamped once at `0001_live_schema_baseline`. Stamping records the baseline without executing its table-creation SQL. Cloud Build migration execution remains disabled until that one-time bootstrap is complete. Fresh databases can use `alembic upgrade head` to create the schema.

The database currently has zero chunks with embeddings. Query integration can be exercised once ingestion populates and embeds the corpus.

## Production configuration

Production requires `DATABASE_URL` using `postgresql+asyncpg`, `REDIS_URL`, `AUTH_CHALLENGE_SECRET` (at least 32 bytes), SMTP host/from address, Google Cloud project/location, Vertex model IDs, and exact HTTPS `CORS_ORIGINS`. Store database, Redis, SMTP credentials, and challenge secret in Secret Manager. Set Cloud Run's Cloud SQL attachment, VPC connectivity for Redis, and a least-privilege runtime service account.

Cloud Run provides HTTPS and manages its serving certificate. The container itself listens on HTTP behind the managed proxy. The Cloud Build pipeline runs Ruff, Bandit, and pip-audit, builds/pushes the image, and deploys the service. Database migrations remain excluded until the existing database has been stamped at the verified baseline.

## API and ownership

See `FRONTEND_INTEGRATION.md` for request/response shapes. `/v1/query` remains available to direct clients and can use OIDC when configured. Current `/api` routes use database-backed bearer sessions. A first admin must be provisioned securely in the database; public registration always assigns the regular-user role.

The adapter verifies citation identity and source/page provenance. It does not yet provide claim-level legal entailment verification. Production launch also requires a populated corpus, baseline stamping, configured production email, and deployment review.
