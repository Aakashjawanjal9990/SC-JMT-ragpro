# Frontend integration contract

The backend now provides compatibility routes for Tanvi's current frontend and keeps the versioned `/v1/query` endpoint available for direct API clients.

## Local setup

1. Copy `backend/.env.example` to `backend/.env`, set `GROQ_API_KEY`, and configure local database credentials as needed.
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

Chat uses the existing query orchestrator and verified citations. The frontend receives source objects with case/title, year, page(s), citation, excerpt, and judgment ID. The database adapter uses AlloyDB's native BM25 operator and `idx_chunks_bm25` on `chunks.content`, then verifies passages against `judgments`, `source_files`, and `source_pages`. Case-name filtering is rejected because the schema does not expose a trusted case-name field.

The API can be connected end to end, but it will return an insufficient-evidence response until ingestion populates searchable chunks and source-page records.

## Retrieval adapter integration status

The default backend path performs AlloyDB native BM25 retrieval from `chunks.content` and passes verified source passages to Groq. It does not require query embeddings, an embedding model row, a separate reranker, or the separate BM25 builder. Keep `RETRIEVAL_PORT_ADAPTER_ENABLED=false` for this path.

## Deployment configuration

Production requires `DATABASE_URL` pointing to AlloyDB, `REDIS_URL`, `AUTH_CHALLENGE_SECRET` (at least 32 bytes), `GROQ_API_KEY`, SMTP host/from address, and HTTPS `CORS_ORIGINS`. Supply passwords, the Groq key, and the challenge secret through Secret Manager. Configure Cloud Run networking to reach AlloyDB securely. The API Cloud Run service is private; the public BFF invokes it through a dedicated service account. Configure the frontend host to proxy same-origin `/api` requests to the BFF and set production `VITE_API_URL` to `https://YOUR_FRONTEND_ORIGIN/api`. Configure CORS to the exact frontend origin without `/api`; credentialed CORS is enabled, so wildcard origins are not allowed. The Cloud Build deployment uses `SameSite=None` and `Secure`; local HTTP development uses `SameSite=Lax`.

## Known integration blockers

- The supplied AlloyDB schema dump contains the source and auth/history tables expected by the API, plus the BM25 index on `chunks.content`.
- Do not run `alembic upgrade head` against the existing AlloyDB database; migrations would attempt to create tables already present. Cloud Build intentionally does not run migrations.
- The BM25 query change must be exercised against the reachable AlloyDB instance before release.
- Email verification/reset needs a real SMTP provider in production. Mailpit is for local development only.
- `POST /api/chat/interpret` uses basic local normalization and keyword extraction; no separate intent model is wired yet.
- A first administrator account must be provisioned by an authorized database operator; public registration always creates the `user` role.

## Existing direct query API

`POST /v1/query` remains available and returns `request_id`, `status`, `answer`, `citations`, and `limitations`. It supports the existing configured OIDC verifier; the frontend compatibility routes use database-backed sessions.
