# Query backend

FastAPI backend for the Supreme Court RAG project. It keeps the versioned `/v1/query` API and provides `/api` routes for the current frontend: email/password auth, email verification and reset, chat, conversation history, and admin reporting. It uses AlloyDB native BM25 retrieval, Groq answer generation, Redis, SMTP, and Cloud Run.

## Layout

- `app/api/routes.py`: health, readiness, and versioned query endpoints
- `app/api/compat.py`: frontend-compatible auth, chat, conversation, and admin endpoints
- `app/auth_service.py`: Argon2id passwords, email codes, hashed opaque sessions, and auth activity logging
- `app/email.py`: SMTP verification and reset messages
- `app/adapters/`: PostgreSQL BM25 retrieval and Groq answer generation
- `app/orchestrator.py`: query flow and citation identity checks
- `migrations/versions/0001_live_schema_baseline.py`: baseline migration
- `migrations/versions/0001_live_schema_baseline.sql`: schema-only baseline snapshot

## Local development

From this directory:

```powershell
Copy-Item .env.example .env
docker compose up --build -d
```

The API listens at `http://localhost:8080`. Set frontend `VITE_API_URL=http://localhost:8080/api`. Mailpit captures OTP/reset mail at `http://localhost:8025`.

### Query data prerequisites

The query API uses AlloyDB's native `pg_textsearch` BM25 operator against `chunks.content` and sends verified passages to Groq for grounded answer generation. It requires the `pg_textsearch` extension and `idx_chunks_bm25` index shown in the supplied AlloyDB schema, plus valid chunk, judgment, source-file, and source-page records. Ingestion is managed separately from this backend repository. Set `DATABASE_URL` to the AlloyDB connection and `GROQ_API_KEY` to enable live queries. Run `alembic upgrade head` inside the API container only for a fresh database; do not run it on the existing AlloyDB schema. Start the frontend from `../frontend` with `npm ci` and `npm run dev`; its `.env` should set `VITE_API_URL=http://localhost:8080/api`. Verification email appears in Mailpit at `http://localhost:8025`.

## Database migration status

The supplied AlloyDB schema dump includes the live judgment, source, page, authentication, and history tables, as well as the `pg_textsearch` extension and `idx_chunks_bm25`. Cloud Build does not run migrations. **Do not run `alembic upgrade head` against the existing AlloyDB database**; it would try to create tables that already exist. Fresh databases can use migrations after the schema and required extension setup are reviewed.

## Production configuration

Production requires `DATABASE_URL` pointing to AlloyDB with `postgresql+asyncpg`, `REDIS_URL`, `AUTH_CHALLENGE_SECRET` (at least 32 bytes), `GROQ_API_KEY`, SMTP host/from address, and exact HTTPS `CORS_ORIGINS`. Store database, Redis, Groq, SMTP credentials, and challenge secret in Secret Manager. Configure Cloud Run networking so it can securely reach AlloyDB and Redis, and use a least-privilege runtime service account. The local Compose PostgreSQL image does not include AlloyDB's `pg_textsearch`; use the AlloyDB URL for BM25 query testing.

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
