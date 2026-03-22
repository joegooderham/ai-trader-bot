# dashboard/frontend/src/hooks/

Custom React hooks for data fetching and bot commands.

| File | Purpose |
|------|---------|
| `useApi.js` | **GET data hook.** Fetches from an API endpoint on mount with optional auto-refresh interval. Returns `{ data, loading, error }`. Used by every page for read-only data. |
| `useCommand.js` | **POST command hook.** Sends commands to the bot via `/api/cmd/*` endpoints. Returns `{ execute, loading, error, result }`. Used by TradeControls, ConfigEditor, Remediation for write operations. |
