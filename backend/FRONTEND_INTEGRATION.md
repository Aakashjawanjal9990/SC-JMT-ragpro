# Frontend integration contract

The backend now provides compatibility routes for Tanvi's current frontend and keeps the versioned `/v1/query` endpoint available for direct API clients.

## Local setup

1. Copy `backend/.env.example` to `backend/.env` and set the Google Cloud project, Vertex models, and local database credentials as needed.
2. Start the backend services with `docker compose up --build` from `backend/`.
3. The API listens on `http://localhost:8080`; set the frontend `VITE_API_URL` to `http://localhost:8080/api`.
4. Mailpit's inbox is at `http://localhost:8025`. Verification and password-reset messages are captured there during local development.

## Frontend routes

- `POST /api/auth/register` → `{verificationRequired: true}`; sends a six-digit email code.
- `POST /api/auth/verify` → `{user, csrfToken}` and sets the HttpOnly session cookie.
- `POST /api/auth/resend`, `POST /api/auth/login`, `POST /api/auth/forgot`, and `POST /api/auth/reset` implement the expected email/password flows. Login accepts an optional `remember` boolean; false creates a browser-session cookie.
- `GET /api/auth/me` returns the current user and a session-bound `csrfToken`. `POST /api/auth/logout` revokes the cookie session and clears the cookie.
- `POST /api/chat/interpret` returns the input as the normalized query; it does not yet perform LLM-based intent clarification.
- `POST /api/chat/interpret` accepts `{question}` and returns normalized query, keywords, alternatives, and clarification fields.
- `POST /api/chat/query` accepts the frontend's camelCase chat body (unknown fields are ignored) and returns `{conversationId, answer, sources, status, limitations}`. Sources are grouped by case and expose string `year` and `page` fields.
- Extra frontend chat fields such as `think`, `normalizedQuery`, and `searchTerms` are ignored by the current backend. They do not change retrieval or generation.
- `GET /api/chat/conversations` returns a direct array of `{id, title}` for the signed-in user's active conversations. Add `?archived=true` to list archived conversations. `GET /api/chat/conversations/{id}` returns a direct array of user/assistant messages.
- `POST /api/chat/conversations/{id}/archive` hides a conversation from the active list while retaining its `query_runs` rows. `POST /api/chat/conversations/{id}/restore` returns it to the active list. Both routes are idempotent, scoped to the authenticated owner, and require the normal CSRF header and trusted origin.
- `POST /api/admin/login` requires an existing active user with the `admin` database role. Admin reporting routes are `GET /api/admin/users`, `/api/admin/auth-activity`, `/api/admin/sessions`, and `/api/admin/analytics`, with the frontend's expected field names, pagination, filters, and date range.

The compatibility changes use the existing `query_runs`, `users`, `user_sessions`, and `user_activity_logs` tables; they do not add or migrate database tables. Archive state is stored in each conversation turn's `query_runs.metadata` JSONB field. Archiving does not delete chat history or change the judgment corpus.

The `/api` login, email-verification, and admin-login responses set an HttpOnly session cookie and return `{user, csrfToken}`; they do not return the session token in JSON. The cookie is `Secure` outside local development and uses the configured `SESSION_COOKIE_SAMESITE` value. Only a SHA-256 hash of each token is stored in `user_sessions`; passwords are Argon2id hashes. Browser writes must include the returned `X-CSRF-Token` header and an allowed `Origin`. The frontend must use `credentials: 'include'` and must not store a session token in browser storage. Admin authorization is checked from the stored role on each request.

## Query behavior and current data readiness

Chat uses the existing query orchestrator and verified citations. The frontend receives source objects with case/title, year, page(s), citation, excerpt, and judgment ID. The database adapter reads the live Cloud SQL layout (`chunks.embedding vector(768)`, `chunks.search_vector`, `judgments`, and `source_files`). Case-name filtering is rejected because the live schema does not expose a trusted case-name field.

The Cloud SQL checks shared so far showed zero rows in `chunks` with embeddings. The API can be connected end to end, but it will return an insufficient-evidence response until ingestion populates chunks and vectors.

## Retrieval adapter integration status

Pratik's `app/retrieval` package is wired behind the backend `RetrievalPort`. At startup it can be enabled with `RETRIEVAL_PORT_ADAPTER_ENABLED=true`; it uses `DATABASE_URL` for the Cloud SQL source connection unless the retrieval-specific `DB_*` variables are provided. Its filters and dependency errors are translated by both `/v1/query` and `/api/chat/query` into client-safe 422/503 responses. Readiness checks probe the selected retrieval adapter.

The switch is deliberately off in the checked-in local and Cloud Run defaults. Before enabling it, the source database needs exactly one active `embedding_models` row whose `model_id` is a locally loadable Sentence Transformers model, and `chunks.embedding` must contain vectors generated by that exact model. The latest checks shared for this database showed no active model row and zero embedded chunks. Pratik's current query embedder does not call Vertex AI, so do not set the adapter on for a Vertex-generated embedding model. Once the data/model contract is confirmed, enable it through the Cloud Run environment and keep `RETRIEVAL_BM25_BACKEND=none` unless a separately configured BM25 backend is ready. The existing backend Vertex + Cloud SQL adapter remains the fallback while this switch is off or adapter startup fails.

Sentence Transformers and PyTorch are not backend dependencies because no local embedding model has been approved. This keeps the default API image free of PyTorch and CUDA libraries. If the team later selects a local model for retrieval, add a deliberate CPU-only dependency setup then. Before enabling the adapter on Cloud Run, confirm the selected model is available at startup, the image/startup time and memory limits accommodate it, and the runtime service account can access Cloud SQL. No model name or embedding compatibility should be guessed from vector dimensions alone.

## Deployment configuration

Production requires `DATABASE_URL`, `REDIS_URL`, `AUTH_CHALLENGE_SECRET` (at least 32 bytes), SMTP host/from address, Vertex settings, and HTTPS `CORS_ORIGINS`. Supply passwords and the challenge secret through Secret Manager. Use the Cloud Run runtime identity and Cloud SQL connector/authorized connectivity for the database URL. Configure CORS to the exact frontend origin; credentialed CORS is enabled, so wildcard origins are not allowed. The Cloud Build deployment uses `SameSite=None` for separate frontend/API sites and `Secure`; if hosting changes to a same-site origin, review this setting. Local HTTP development uses `SameSite=Lax`.

## Known integration blockers

- The Alembic baseline and SQL snapshot now reflect the schema-only export from the live Cloud SQL database (`chunks`, `judgments`, `source_files`, auth/history tables and indexes). The auth/history columns and constraints shared in Cloud SQL Studio match the compatibility API's assumptions.
- The export contains no `alembic_version` table. Do not run `alembic upgrade head` against the existing Cloud SQL database: it would try to create tables that already exist. After reviewing the checked-in snapshot against the current live schema, stamp the existing database at `0001_live_schema_baseline` once. The Cloud Build pipeline intentionally does not run migrations until that bootstrap is completed.
- The retrieval adapter now uses the live `judgments.judgment_date` column and `source_pages.page_number` records. This code change has not yet been tested against Cloud SQL.
- Email verification/reset needs a real SMTP provider in production. Mailpit is for local development only.
- `POST /api/chat/interpret` is a simple compatibility response; no separate intent model is wired yet.
- A first administrator account must be provisioned by an authorized database operator; public registration always creates the `user` role.

## Existing direct query API

`POST /v1/query` remains available and returns `request_id`, `status`, `answer`, `citations`, and `limitations`. It supports the existing configured OIDC verifier; the frontend compatibility routes use database-backed sessions.
