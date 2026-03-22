# dashboard/frontend/

React SPA built with Vite + Tailwind CSS. Dark theme, responsive (mobile + desktop).

## Tech Stack
- **React 18** — component framework
- **React Router 6** — client-side routing
- **Recharts** — charts and data visualisation
- **Tailwind CSS 3** — utility-first styling
- **Vite 5** — build tool and dev server

## Structure

| Path | Purpose |
|------|---------|
| `src/pages/` | Page components (14 pages — see below) |
| `src/components/` | Reusable UI components (9 components) |
| `src/hooks/` | Custom hooks: `useApi` (GET + auto-refresh), `useCommand` (POST) |
| `src/App.jsx` | Router — maps URL paths to page components |
| `public/` | Static assets (favicon, PWA icons, manifest) |

## Building

```bash
npm install
npm run dev      # Dev server on localhost:5173 (proxies /api to :8050)
npm run build    # Production build → dist/
```

The Dockerfile handles this automatically in a multi-stage build.
