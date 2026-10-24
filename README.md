# Nyaya-Mitra frontend

React 18 + Vite + React Router. Talks to a FastAPI backend over JSON.
No mock data: every screen needs a running backend.

## Run

    cp .env.example .env      # Windows PowerShell: Copy-Item .env.example .env
    # then set VITE_API_URL
    npm ci
    npm run dev

## Scripts

- `npm run dev` / `npm run build` / `npm run preview`
- `npm run lint`, `npm run format`, `npm run format:check`

A production build fails unless `VITE_API_URL` is an `https://` URL.

**Security note:** never put passwords, API secrets or private keys in
`VITE_*` variables. Everything in them ends up in the browser bundle.
