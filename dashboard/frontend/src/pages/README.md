# dashboard/frontend/src/pages/

Page components — one per route in the dashboard.

| File | Route | Purpose |
|------|-------|---------|
| `Overview.jsx` | `/` | Today's stats, 30-day + intraday P&L charts, open positions table |
| `Positions.jsx` | `/positions` | Open positions with close buttons, TradeControls bar (pause/resume/close all) |
| `TradeHistory.jsx` | `/trades` | Closed trade history with pagination and pair filter |
| `TradeJournal.jsx` | `/journal` | Expandable trade cards with full context: reasoning, breakdown, R:R, duration |
| `Analytics.jsx` | `/analytics` | LSTM model info, prediction accuracy, drift detection, performance metrics |
| `Heatmap.jsx` | `/heatmap` | Pair x hour win rate grid (colour-coded, 24h) |
| `SessionAnalysis.jsx` | `/sessions` | Performance by forex session (Sydney, Tokyo, London, New York) |
| `Chat.jsx` | `/chat` | AI messenger with Claude — full trading history context, session persistence |
| `WhatIf.jsx` | `/what-if` | Mystic-Wolf simulator — replay trades with hypothetical settings |
| `Remediation.jsx` | `/remediation` | Pending integrity recommendations with approve/reject cards |
| `ConfigEditor.jsx` | `/config` | Live config editor (8 params with sliders) + read-only YAML view |
| `Summary.jsx` | `/summary` | 7d/30d/all-time stats, per-pair breakdown, Claude daily plan |
| `Wiki.jsx` | `/wiki` | Project wiki pages from GitHub (markdown rendered to HTML) |
| `Backlog.jsx` | `/backlog` | Project roadmap |
| `Config.jsx` | `/config-readonly` | Legacy read-only config view |
| `WikiPage.jsx` | `/wiki/:pageName` | Individual wiki page renderer |
