# Nyaya-Mitra frontend: final technology stack

Versions are the exact ones installed from `package-lock.json` on the
`cleanup-for-backend` branch (React Router 6.30.6). After you merge the
React Router upgrade (section 5), update the two rows marked with `*`.

## 1. Shipped to the browser

| Layer                       | Technology                                                                 | Version                               |
| --------------------------- | -------------------------------------------------------------------------- | ------------------------------------- |
| Language                    | JavaScript (ES modules) with JSX; no TypeScript                            | n/a                                   |
| UI library                  | React and React DOM                                                        | 18.3.1                                |
| Routing *                   | React Router DOM (core: react-router, @remix-run/router)                   | ^7.18.2 (check `npm ls react-router`) |
| Icons                       | lucide-react                                                               | 0.453.0                               |
| Styling                     | One hand-written CSS file, `src/styles/global.css`. No Tailwind, no UI kit | n/a                                   |
| Fonts                       | Inter, Lora, Poppins from Google Fonts (not self-hosted)                   | n/a                                   |
| Network                     | Browser `fetch`, JSON bodies, 60 s timeout. No Axios                       | built in                              |
| State                       | React Context (`AuthContext`) and one hook (`useChat`). No Redux           | n/a                                   |
| Internal React dependencies | scheduler 0.23.2, loose-envify 1.4.0, js-tokens 4.0.0                      |                                       |

The production bundle contains only these nine packages.

## 2. Build and quality tooling (not shipped)

| Tool                      | Purpose                     | Version        |
| ------------------------- | --------------------------- | -------------- |
| Vite                      | Build tool and dev server   | ^7.3.6         |
| esbuild (inside Vite)     | Bundler                     | 0.28.x         |
| @vitejs/plugin-react      | React support               | 4.7.0          |
| ESLint and @eslint/js     | Linting, `--max-warnings 0` | 9.39.5         |
| eslint-plugin-react       | React rules                 | 7.37.5         |
| eslint-plugin-react-hooks | Hooks rules                 | 5.2.0          |
| eslint-config-prettier    | Avoids lint/format clashes  | 9.1.2          |
| globals                   | Lint environment names      | 15.15.0        |
| Prettier                  | Formatting                  | 3.9.10         |
| Node.js                   | Local and CI runtime        | 22 (min 20.19) |

## 3. CI (`.github/workflows/ci.yml`)

One job on every push and pull request: `npm ci`, lint, format check,
`npm audit --omit=dev --audit-level=high`, then `npm run build` with a
placeholder `VITE_API_URL=https://api.example.com/api`.
Actions: `actions/checkout@v4`, `actions/setup-node@v4`
(pinned to tags, not commit IDs).

## 4. Backend contract (not in this repo)

The backend is FastAPI (stated by the team, not verifiable from this repo).
The frontend expects JSON over HTTPS at `VITE_API_URL`, HttpOnly session cookies (CORS with credentials, CSRF header), and
the 17 endpoints in `docs/FRONTEND_GUIDE.md`. There is no database, model,
search index, email service or cloud config in this repo.

Browser storage: no auth data. `nm_wish_day` and `nm_sidebar:<email>`
(localStorage) only.
Only third-party request: Google Fonts.

## 5. Known dependency issues

| Item                                                         | Status                                                                                                                                                                                                  |
| ------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| react-router 6.x advisories (open redirect, SSR hydration) * | Fixed by react-router-dom 7.18.4, not yet applied to the cleaned code. The app uses fixed link targets and no server rendering, so exposure looks low. Upgrade on its own branch and retest every page. |
| esbuild and Vite advisories (dev server only)                | Not shipped to users. Affects only a running `npm run dev`. Needs a Vite major upgrade, so do it separately.                                                                                            |
| `npm audit --omit=dev --audit-level=high` in CI              | Passes (exit 0). Only moderate findings in production dependencies.                                                                                                                                     |

## 6. Still open (not provable from frontend code)

| Priority | Item                                                                                                |
| -------- | --------------------------------------------------------------------------------------------------- |
| P0       | Backend: admin authorization on every `/admin/*` route, conversation ownership, citation provenance |
| P1       | Token expiry, refresh and server-side logout; decide on HttpOnly cookies                            |
| P2       | Pin GitHub Actions to commit IDs; secret scanning; code scanning; automated tests                   |
| P2       | CSP, HSTS and TLS on the deployed host                                                              |
