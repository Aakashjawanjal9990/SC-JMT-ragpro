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

4. Set VITE_API_URL in .env to your backend API URL.

5. Start the development server:

   npm run dev

## Available Scripts

- npm run dev - Start development server
- npm run build - Build production assets
- npm run preview - Preview production build
- npm run lint - Run ESLint
- npm run format - Format files with Prettier
- npm run format:check - Check formatting
