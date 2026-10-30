# Nyaya-Mitra Frontend

React-based frontend for Nyaya-Mitra, a legal research application
that communicates with a FastAPI backend.

## Tech Stack

- React 18
- Vite
- React Router
- JavaScript and CSS
- Nginx for production serving
- Docker for containerization

## Prerequisites

- Node.js 22.x
- npm
- Access to a running backend API

## Local Development

1. Clone the repository.
2. Install dependencies:

   npm ci

3. Create your local environment file:

   Copy-Item .env.example .env

4. Keep `VITE_API_URL=http://localhost:8080/api` in `.env` for local development.

5. Start the development server:

   npm run dev

## Production

Set `VITE_API_URL=/api` when building the production frontend. Set the Docker
build argument `BFF_URL` to the HTTPS Cloud Run origin for the BFF, for example
`https://your-bff-xxxxx-uc.a.run.app`. Nginx forwards `/api/*` to that origin
while preserving the request path, query string, cookies, and CSRF header.

## Available Scripts

- npm run dev - Start development server
- npm run build - Build production assets
- npm run preview - Preview production build
- npm run lint - Run ESLint
- npm run format - Format files with Prettier
- npm run format:check - Check formatting
