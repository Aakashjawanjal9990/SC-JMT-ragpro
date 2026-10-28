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
- `POST /api/chat/interpret` accepts `{question}` and returns whitespace-normalized text, basic extracted keywords, and the expected alternatives/clarification fields. It does not use an LLM for intent clarification.
- `POST /api/chat/query` accepts the frontend's camelCase chat body and returns `{conversationId, answer, sources, status, limitations}`. Sources are grouped by case and expose string `year` and `page` fields.
- `normalizedQuery` and `searchTerms` are included in the retrieval query. `think: true` asks the answer model for a fuller legal synthesis without revealing private chain-of-thought.
- Follow-up queries load up to the previous three exchanges from the same user-owned conversation. History helps resolve references and inform retrieval, but is treated as untrusted context and never as legal evidence.
- `GET /api/chat/conversations` returns a direct array of `{id, title}` for the signed-in user's active conversations. Add `?archived=true` to list archived conversations. `GET /api/chat/conversations/{id}` returns a direct array of user/assistant messages.
- `POST /api/chat/conversations/{id}/archive` hides a conversation from the active list while retaining its `query_runs` rows. `POST /api/chat/conversations/{id}/restore` returns it to the active list. Both routes are idempotent, scoped to the authenticated owner, and require the normal CSRF header and trusted origin.
- `POST /api/admin/login` requires an existing active user with the `admin` database role and sets the separate `ADMIN_SESSION_COOKIE_NAME` cookie. `POST /api/admin/logout` revokes that admin session and clears its cookie. Admin reporting routes are `GET /api/admin/users`, `/api/admin/auth-activity`, `/api/admin/sessions`, and `/api/admin/analytics`, with the frontend's expected field names, pagination, filters, and date range.

The compatibility changes use the existing `query_runs`, `users`, `user_sessions`, and `user_activity_logs` tables; they do not add or migrate database tables. Archive state is stored in each conversation turn's `query_runs.metadata` JSONB field. Archiving does not delete chat history or change the judgment corpus.

The `/api` login, email-verification, and admin-login responses set separate HttpOnly user/admin session cookies and return `{user, csrfToken}`; they do not return session tokens in JSON. The cookies are `Secure` outside local development and use the configured `SESSION_COOKIE_SAMESITE` value. Only a SHA-256 hash of each token is stored in `user_sessions`; passwords are Argon2id hashes. Browser writes must include the returned `X-CSRF-Token` header and an allowed `Origin`. The frontend must use `credentials: 'include'` and must not store a session token in browser storage. Admin authorization is checked from the stored role on each request.

Successful logins, logouts, registrations, email verification, password resets, and failed sign-in/code attempts are recorded in `user_activity_logs`. Failed identities are represented by a hash in event metadata rather than a stored email address. The admin activity endpoint derives `event_status` from the recorded event type and caps its response at 200 rows.

## Query behavior and current data readiness

Chat uses the existing query orchestrator and verified citations. The frontend receives source objects with case/title, year, page(s), citation, excerpt, and judgment ID. The database adapter reads the live Cloud SQL layout (`chunks.embedding vector(768)`, `chunks.search_vector`, `judgments`, and `source_files`). Case-name filtering is rejected because the live schema does not expose a trusted case-name field.

The Cloud SQL checks shared so far showed zero rows in `chunks` with embeddings. The API can be connected end to end, but it will return an insufficient-evidence response until ingestion populates chunks and vectors.

## Retrieval adapter integration status

The `retrieval` branch's `app/retrieval` package is wired behind the backend `RetrievalPort`. It supports PostgreSQL/pgvector and the ingestion team's read-only SQLite export. For local E2E, set `RETRIEVAL_VECTOR_STORE=sqlite`, point `RETRIEVAL_SQLITE_PATH` to the mounted `scj_rag_v2.db`, and enable `RETRIEVAL_PORT_ADAPTER_ENABLED=true`. The same SQLite file is used for source-record verification, so returned chunks, judgment identity, and original pages are validated against ingestion output. `RETRIEVAL_BM25_BACKEND=memory` builds a local lexical index at startup; this can use substantial memory for the full corpus.

For PostgreSQL/pgvector, set `RETRIEVAL_EMBEDDING_MODEL` to the active database model ID and configure `RETRIEVAL_EMBEDDING_PROVIDER` as `vertex` or `local` to match the exact indexing model. The startup check compares the configured model ID to the one active database row and checks that vectors exist. The default backend Vertex + Cloud SQL adapter remains the fallback if the optional adapter is disabled or cannot start. `RETRIEVAL_EMBEDDING_PROVIDER=test` is a local wiring-test option only; its hash vectors are not semantic embeddings and must not be used to evaluate retrieval quality.

The latest shared Cloud SQL check showed no active model row and zero embedded chunks. Use the local ingestion export for an E2E query until the production corpus and embedding model are confirmed. See `README.md` for the local setup sequence.

## Deployment configuration

Production requires `DATABASE_URL`, `REDIS_URL`, `AUTH_CHALLENGE_SECRET` (at least 32 bytes), SMTP host/from address, Vertex settings, and HTTPS `CORS_ORIGINS`. Supply passwords and the challenge secret through Secret Manager. Use the Cloud Run runtime identity and Cloud SQL connector/authorized connectivity for the database URL. Configure CORS to the exact frontend origin; credentialed CORS is enabled, so wildcard origins are not allowed. The Cloud Build deployment uses `SameSite=None` for separate frontend/API sites and `Secure`; if hosting changes to a same-site origin, review this setting. Local HTTP development uses `SameSite=Lax`.

## Known integration blockers

- The Alembic baseline and SQL snapshot now reflect the schema-only export from the live Cloud SQL database (`chunks`, `judgments`, `source_files`, auth/history tables and indexes). The auth/history columns and constraints shared in Cloud SQL Studio match the compatibility API's assumptions.
- The export contains no `alembic_version` table. Do not run `alembic upgrade head` against the existing Cloud SQL database: it would try to create tables that already exist. After reviewing the checked-in snapshot against the current live schema, stamp the existing database at `0001_live_schema_baseline` once. The Cloud Build pipeline intentionally does not run migrations until that bootstrap is completed.
- The retrieval adapter now uses the live `judgments.judgment_date` column and `source_pages.page_number` records. This code change has not yet been tested against Cloud SQL.
- Email verification/reset needs a real SMTP provider in production. Mailpit is for local development only.
- `POST /api/chat/interpret` uses basic local normalization and keyword extraction; no separate intent model is wired yet.
- A first administrator account must be provisioned by an authorized database operator; public registration always creates the `user` role.

## Existing direct query API

`POST /v1/query` remains available and returns `request_id`, `status`, `answer`, `citations`, and `limitations`. It supports the existing configured OIDC verifier; the frontend compatibility routes use database-backed sessions.
