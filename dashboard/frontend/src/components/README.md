# dashboard/frontend/src/components/

Reusable UI components shared across dashboard pages.

| File | Purpose |
|------|---------|
| `Layout.jsx` | App shell — sidebar navigation (grouped: Trading/Analytics/Tools/Docs), mobile hamburger menu, RunningPL header, remediation badge count, logout button |
| `TradeControls.jsx` | Trading control bar — pause/resume toggle, close all/profitable/losing buttons, disabled direction/pair badges with re-enable |
| `ConfirmModal.jsx` | Reusable confirmation dialog for destructive actions (close all, pause) |
| `Toast.jsx` | Success/error toast notifications with auto-dismiss + `useToast()` hook |
| `LivePLChart.jsx` | Intraday cumulative P&L chart (Recharts LineChart, 30s auto-refresh) |
| `StatCard.jsx` | Reusable metric card (label, value, optional sub-text) |
| `PLBadge.jsx` | Formatted P&L display — green for profit, red for loss |
| `RunningPL.jsx` | Live running P&L total displayed in the header |
| `ErrorBoundary.jsx` | React error boundary — catches rendering crashes |
