# Nyaya-Mitra frontend: integration guide

For the person wiring this frontend to the FastAPI backend.
Everything here was read from the code in this repo. Where something is a
recommendation rather than something the frontend already does, it says so.

## 1. What this app is

A React 18 single-page app (Vite, React Router 6, lucide-react icons, one
hand-written CSS file). It has no backend code, no mock data and no state
library. All server calls go through one file: `src/lib/api.js`.

Stack: React 18.3, react-router-dom 6, lucide-react, Vite 5. Nothing else
ships to the browser.

## 2. Setup

    cp .env.example .env     # set VITE_API_URL
    npm ci
    npm run dev              # http://localhost:5173

| Variable       | Meaning                                                                                            |
| -------------- | -------------------------------------------------------------------------------------------------- |
| `VITE_API_URL` | Backend base URL including the prefix, e.g. `http://localhost:8000/api`. No trailing slash needed. |

`npm run build` **fails** if `VITE_API_URL` is not an `https://` URL. This is
deliberate, so a production bundle can never point at localhost.
Vite bakes this value into the bundle at build time: changing it means
rebuilding, not just restarting.

## 3. File map

| Path                              | What it does                                                                                                                  |
| --------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `src/main.jsx`                    | Mounts React, Router and `AuthProvider`.                                                                                      |
| `src/App.jsx`                     | Route table (section 4).                                                                                                      |
| `src/lib/api.js`                  | **The only backend client.** Token storage, fetch wrapper, timeout, error parsing, every endpoint.                            |
| `src/context/AuthContext.jsx`     | `user`, `loading`, `login`, `register`, `verify`, `logout`. Restores the session with `GET /auth/me` on page load.            |
| `src/hooks/useChat.js`            | Chat messages, sidebar history, current conversation id, send/open/reset.                                                     |
| `src/hooks/useCountdown.js`       | 30 s "resend code" timer.                                                                                                     |
| `src/lib/validators.js`           | Browser-side input checks (name, email, password, 6-digit code, question).                                                    |
| `src/lib/names.js`, `greeting.js` | Display name and the time-of-day greeting (computed in IST).                                                                  |
| `src/components/`                 | `AppShell` (sidebar and header), `AuthLayout`, `ProtectedRoute`, `TextField`, `OtpField`, `Buttons`, `Logo`, `ErrorBoundary`. |
| `src/pages/`                      | `Landing`, `Login`, `Register` (includes the email-code step), `ForgotPassword`, `Home` (chat), `NotFound`.                   |
| `src/pages/admin/`                | `AdminPage` (gate), `AdminLogin`, `AdminConsole` (header), `AdminData` (all four admin panels).                               |
| `src/styles/global.css`           | All styling.                                                                                                                  |

## 4. Routes

| URL                                       | Page                      | Access                                                                                                       |
| ----------------------------------------- | ------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `/`                                       | Landing                   | public                                                                                                       |
| `/login`, `/register`, `/forgot-password` | Auth pages                | public (Login and Register send signed-in users to `/home`)                                                  |
| `/home`                                   | Chat                      | needs a user session (`ProtectedRoute`). `/home?c=<conversationId>` opens that conversation (see section 6). |
| `/admin`                                  | Admin login, then console | needs the admin token                                                                                        |

`ProtectedRoute` and the `/admin` gate only decide what the browser shows.
Anyone can bypass them. **Every real check must be on the server.**

## 5. What is dynamic and what is not

Dynamic (comes from the backend or the user): user name and email, chat
answers and sources, sidebar history, all four admin panels.

Computed locally: the greeting ("Good morning, Tanvi") and "Happy Thursday",
from the browser clock in IST.

Static on purpose: Landing page copy, FAQ, auth side-panel text, footer
disclaimers. These are marketing text, not data. If you want them editable
without a deploy, that needs a CMS or a `GET /content` endpoint, which does
not exist yet. Say so if you want it and it can be added.

## 6. How the frontend behaves

- **Token header.** Every request sends `Authorization: Bearer <token>`.
  Paths starting `/admin` use the admin token; all others use the user token.
- **Token storage.** User token: `localStorage` if "Remember me", else
  `sessionStorage` (key `nm_token`). Admin token: `sessionStorage` only
  (`nm_admin_token`). Also stored: `nm_wish_day` (date of last greeting).
- **Timeout.** 60 seconds per request, then the user sees "The server took too
  long to respond."
- **401 handling.** A 401 on a request that carried a token clears that token
  and sends the user to Sign In (admin: back to the admin login). A 401 on a
  request with no token (wrong password at login) just shows the error text.
  A 401 from `/auth/logout` is ignored.
- **Error text.** For 4xx replies the frontend shows the backend's text
  (max 200 characters): `{"detail": "text"}`, FastAPI's 422 list
  `{"detail": [{"msg": "..."}]}` (messages joined), or `{"message": "..."}`.
  So **write 4xx `detail` strings that are safe and readable for end users**
  and never put stack traces, SQL or internal ids in them. For 5xx replies
  the frontend ignores the body and shows a generic message.
- **Conversation link.** When a conversation is open, the address bar is
  `/home?c=<conversationId>`. Opening or reloading that address calls
  `GET /chat/conversations/{id}` and shows it. New Research clears it. If the
  user is not signed in, Login sends them back to the same address (including
  `?c=`) after sign-in. The **Share** button copies the current address, so
  the link opens for the conversation's **owner only**: the backend must
  return 404 for a conversation that belongs to someone else. Sharing with
  another person needs a backend endpoint that does not exist yet (for
  example `POST /chat/conversations/{id}/share`).
- **Question checks.** Before sending: not empty, at most 2000 characters, no
  control characters, no HTML tags. Repeat these on the server.

## 7. Endpoint contract

All bodies and replies are JSON. Dates are ISO 8601 strings.
`user = {id, name, email}`.

### Auth (public unless noted)

| #   | Request                      | Body                      | Success reply                                         | Notes                                                                                                                                                                            |
| --- | ---------------------------- | ------------------------- | ----------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | `POST /auth/register`        | `{name, email, password}` | `{verificationRequired: true}` **or** `{token, user}` | Email is already lower-cased and trimmed by the frontend. 409 if the email exists.                                                                                               |
| 2   | `POST /auth/verify`          | `{email, code}`           | `{token, user}`                                       | `code` is exactly 6 digits. 400/422 with a clear message if wrong or expired.                                                                                                    |
| 3   | `POST /auth/resend`          | `{email}`                 | `{sent: true}`                                        | Rate-limit it.                                                                                                                                                                   |
| 4   | `POST /auth/login`           | `{email, password}`       | `{token, user}`                                       | 401 for wrong credentials. Use the same message for "no such user" and "wrong password". For an unverified account use 403 with a message like "Please verify your email first." |
| 5   | `POST /auth/forgot`          | `{email}`                 | `{sent: true}`                                        | Always reply 200, even if the email is unknown, so it cannot be used to find registered users.                                                                                   |
| 6   | `POST /auth/reset`           | `{email, code, password}` | `{ok: true}`                                          | Invalidate old sessions after a reset.                                                                                                                                           |
| 7   | `GET /auth/me` (Bearer)      | none                      | `user`                                                | Called on every page load that has a saved token, and after login if the reply had no `user`. 401 if the token is bad or expired.                                                |
| 8   | `POST /auth/logout` (Bearer) | `{}`                      | `{ok: true}`                                          | Must revoke the session server-side. The frontend deletes its token regardless of the result.                                                                                    |

The frontend also accepts `access_token` in place of `token`.

Password rule in the UI: at least 8 characters, with a letter and a number.
Name rule: letters, spaces, `.`, `'`, `-` only. Enforce the same on the server.

### Chat (Bearer user token)

| #   | Request                        | Body / query                                                                                                      | Success reply                                                                                   |
| --- | ------------------------------ | ----------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| 9   | `POST /chat/interpret`         | `{question}`                                                                                                      | `{normalizedQuery, keywords: [], alternatives: [], clarificationNeeded, clarificationQuestion}` |
| 10  | `POST /chat/query`             | `{question, conversationId, think, normalizedQuery, searchTerms: [], clarificationNeeded, clarificationQuestion}` | `{conversationId, answer, sources: [{case, year, page}]}`                                       |
| 11  | `GET /chat/conversations`      | none                                                                                                              | `[{id, title}]`                                                                                 |
| 12  | `GET /chat/conversations/{id}` | none                                                                                                              | `[{role, text, sources?}]`, with `role` = `"user"` or `"assistant"`                             |

How a question flows: the frontend calls #9 first. If #9 fails with anything
other than 422, it is skipped and #10 is sent with just the question (so #9 is
**optional**; a 422 from it is shown to the user). If #9 returns
`clarificationNeeded: true`, #10 is **not** called and `clarificationQuestion`
is shown as the reply. Otherwise #10 is sent.

- `conversationId` is `null` on the first question of a new research. Create
  the conversation on the server and return its id. Ids may be ints or
  strings.
- `think: true` means the person wants a slower, deeper answer. The
  60-second timeout still applies, so keep "think" answers under that or tell
  the frontend team to raise `TIMEOUT_MS` in `api.js`.
- `searchTerms` is `keywords + alternatives` from #9, or empty.
- Sidebar title: the frontend shows the first 48 characters of the first
  question until the list is reloaded; after that it shows whatever
  `GET /chat/conversations` returns as `title`. **Always return a string**;
  an empty or null title is shown as "Untitled research".
- `sources` render as `case (year), p. page`. Each `case` must be unique within
  one answer (it is used as the React key).
- Only return conversations that belong to the calling user. Check ownership
  on #12; return 404 (not 403) for someone else's id.

### Admin (separate token)

| #   | Request                    | Body / query                             | Success reply                                                                                    |
| --- | -------------------------- | ---------------------------------------- | ------------------------------------------------------------------------------------------------ |
| 13  | `POST /admin/login`        | `{username, password}`                   | `{token}`                                                                                        |
| 14  | `GET /admin/users`         | `?page=1&pageSize=10&status=all&search=` | `{items: [{id, name, email, registered_at, status}], page, pageSize, total}`                     |
| 15  | `GET /admin/auth-activity` | none                                     | `[{id, user_id, session_id, event_type, occurred_at, logout_at, expires_at, event_status}]`      |
| 16  | `GET /admin/sessions`      | none                                     | `[{session_id, user_id, name, email, created_at, last_activity, expires_at, logout_at, status}]` |
| 17  | `GET /admin/analytics`     | `?start=YYYY-MM-DD&end=YYYY-MM-DD`       | `{totalQueries, source, daily: [{date, queries}]}`                                               |

- `status` filter values: `all` (means no filter), `active`, `pending`,
  `disabled`. `status`, `event_type` and `event_status` are shown with
  underscores and hyphens replaced by spaces and the first letter capitalised
  (`login_failed` becomes "Login failed"); no other translation is done.
- `search` matches name or email. `page` starts at 1.
- #15 and #16 are not paginated by the frontend: it renders everything it
  receives. **Cap them server-side (for example the latest 100 or 200 rows)**
  or the page will get slow. Adding pagination is a small frontend change if
  you need it.
- #17 `daily` should contain **every date** in the range, with `0` for quiet
  days, in ascending order. `source` is optional free text (for example
  "query_logs").
- `logout_at` may be `null`; the UI then shows "Not recorded".
- The admin date pickers use the browser's local date. `start` and `end` are
  sent as `YYYY-MM-DD`; decide on the server which time zone a "day" means
  and keep it consistent.

## 8. FastAPI-specific points (read before you write the backend)

These are the places a FastAPI backend most often disagrees with this
frontend.

1. **JSON login, not OAuth2 form.** The frontend sends
   `Content-Type: application/json`. Do not use `OAuth2PasswordRequestForm`
   for `/auth/login`; use a Pydantic model.
2. **camelCase vs snake_case.** The frontend uses camelCase for chat and
   analytics fields (`conversationId`, `normalizedQuery`, `searchTerms`,
   `clarificationNeeded`, `verificationRequired`, `totalQueries`,
   `pageSize`) but snake_case for the admin rows (`registered_at`,
   `user_id`, `session_id`...). Use `alias_generator=to_camel` with
   `populate_by_name=True` on the chat models, and keep the admin row models
   in snake_case. Return with `response_model_by_alias=True` (the default).
3. **422 errors.** Request-validation failures return a list in `detail`. The
   frontend handles this and shows the joined `msg` values, but Pydantic's
   default messages read like "String should have at least 8 characters".
   Consider a custom `RequestValidationError` handler that returns friendlier
   text.
4. **CORS.** Add `CORSMiddleware` with the exact frontend origin(s) (not `*`),
   `allow_methods=["GET","POST"]`, and `allow_headers=["Authorization",
"Content-Type"]`. Because the frontend sends `Authorization`, every call is
   preflighted with `OPTIONS`.
5. **Trailing slashes.** Declare routes exactly as listed (no trailing slash),
   or set `redirect_slashes=False`. A 307 redirect on a cross-origin request
   can drop the `Authorization` header.
6. **Two token types.** Issue admin tokens with a different secret or an
   `aud`/`role` claim, and reject user tokens on `/admin/*` and admin tokens
   on user routes. Use a dependency (`Depends`) on the admin router, not
   per-route checks you might forget.
7. **Long answers.** `/chat/query` runs retrieval and an LLM call. Make it
   `async`, set an upstream timeout, and return a readable `detail` when it
   fails. Streaming is **not** supported by this frontend; it expects one JSON
   reply.

Minimal shape for the camelCase point:

    from pydantic import BaseModel, ConfigDict
    from pydantic.alias_generators import to_camel

    class CamelModel(BaseModel):
        model_config = ConfigDict(alias_generator=to_camel,
                                  populate_by_name=True)

    class QueryIn(CamelModel):
        question: str
        conversation_id: int | None = None
        think: bool = False
        normalized_query: str | None = None
        search_terms: list[str] = []

## 9. Security: what is done and what is yours

Done in the frontend:

- No `dangerouslySetInnerHTML`, `innerHTML` or `eval`. React escapes all
  answers and admin data.
- No secrets, no demo credentials and no mock code in the bundle.
- Production build refuses a non-HTTPS API address.
- Request timeout; 401 forces sign-out; admin token lives only in the tab.
- Admin and user tokens are separate and each is sent only to its own routes.

Needs doing (backend or deployment). None of this is implemented here:

- Hash passwords with argon2 or bcrypt. Never log passwords, codes or tokens.
- Store OTP and reset codes hashed, with a 5 to 10 minute expiry and an
  attempt limit.
- Rate-limit and lock out `/auth/*` and `/admin/login`.
- Short-lived access tokens (15 to 30 minutes) plus refresh. **Note:** this
  frontend has no refresh-token logic. A 401 signs the user out. Adding
  silent refresh is a code change in `api.js`.
- Check the admin role on every `/admin/*` route on the server.
- **Tokens in browser storage** can be read by any script on the page. The
  safer setup is an `HttpOnly; Secure; SameSite` cookie. Moving to it means
  changing `api.js` (`credentials: 'include'`, stop storing tokens), the
  token store, `AuthContext`, and adding CSRF protection on the backend. It
  is a real change, not a setting.
- Serve over HTTPS and send security headers from your host or proxy:
  `Content-Security-Policy`, `X-Content-Type-Options`, `Referrer-Policy`,
  `frame-ancestors`. A CSP is not set by this repo.
- The page loads fonts from `fonts.googleapis.com`. It is the only third-party
  request. Self-host the fonts if your policy forbids that (and then tighten
  the CSP).
- Repeat every validation on the server.

## 10. Testing the integration

Check the backend by hand first:

    API=http://localhost:8000/api
    curl -s -X POST $API/auth/login -H 'Content-Type: application/json' \
         -d '{"email":"a@b.com","password":"Passw0rd1"}'
    curl -s $API/auth/me -H "Authorization: Bearer <token>"

Then in the browser, open DevTools, Network tab, and walk this list:

1. Register, with and without email verification.
2. Wrong password (shows the error, does not loop or sign out).
3. Reload while signed in (calls `/auth/me`, stays signed in).
4. Expire or delete the token (next call returns 401, lands on Sign In).
5. Ask a question: you should see `/chat/interpret` then `/chat/query`.
6. Make `/chat/interpret` return 404: the question should still work.
7. Reload and click an old conversation in the sidebar.
8. Open `/admin`, sign in, check all four panels, search, filters, paging,
   date range.
9. Stop the backend: the user sees "Cannot reach the server."

## 11. Removed in this cleanup

For reference, so nobody looks for them: `src/lib/mock.js`, the demo-mode
notices, `TraceView.jsx`, the admin KPI cards / query list / trace panel
(they only ever appeared in demo mode), the unused `GET /meta` and
`/admin/metrics|queries` client functions, the stale security-review PDF,
`env.txt`, the Python lint config (`pyproject.toml`, `.pre-commit-config.yaml`,
the CI `python` job and the `lint:py` script) and the unused CSS for those
screens. The Python tooling belongs in the backend repo.

If you later want the query-trace view in the admin console, it needs new
backend endpoints and a new screen; it is not a switch to turn on.

## 12. Known limits

- **Share** copies a link that only the conversation's owner can open (see
  section 6).
- No refresh tokens, no streaming answers, no pagination on admin
  sessions/activity, no i18n.
- Answers and source labels are plain text. Markdown in answers will show as
  raw text until a renderer is added.
- Admin data is fetched together in one `Promise.all`; if one of the four
  endpoints fails, the whole admin screen shows an error.
