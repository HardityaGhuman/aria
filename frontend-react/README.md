# Aria Frontend (React)

Vite + React + TS SPA for the Aria internal assistant. Faithful to the Stitch
"Aria Internal Assistant" design. Talks to the FastAPI backend over HTTP + SSE.

## Dev
1. Backend running on :8000 with `COOKIE_SECURE=false` and
   `FRONTEND_ORIGIN=http://localhost:5173` in `backend/.env`
   (the refresh cookie is `Secure` by default and won't be stored over http).
2. `npm install`
3. `npm run gen:api`   # regenerate types after any backend contract change
4. `npm run dev`       # http://localhost:5173

The backend base URL defaults to `http://localhost:8000`; override with the
`VITE_API_BASE` env var.

## Scripts
- `npm run dev` / `build` / `preview`
- `npm test`            # Vitest (client refresh-on-401, SSE parser, RBAC nav, tier badge)
- `npm run gen:api`     # openapi-typescript ../docs/api/openapi.json -> src/lib/api/types.ts

## Architecture
- `src/lib/api/` — generated `types.ts`, `client.ts` (auth + refresh-on-401),
  `sse.ts` (chat stream reader), `sessions.ts`.
- `src/lib/auth/` — in-memory access token, silent refresh on load, route guard.
- `src/components/` — AppShell (3-col), Sidebar (RBAC nav + history), Button,
  TierBadge, Composer, SuggestionChips, SourcesPanel, HistoryList.
- `src/routes/` — Login, Chat (empty state + streaming answer).

## Scope
Plan 1: auth + streaming chat + history. Preferences and HR Admin = Plan 2.
Deferred (Stitch elements without backend support, rendered disabled): SSO,
Request Time Off, View Full Policy, Preferences toggles (auto-cite / code-highlight).
