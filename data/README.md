# data/

Data storage, context generation, and runtime data files.

| File | Purpose |
|------|---------|
| `storage.py` | SQLite database interface. Tables: `trades`, `overnight_holds`, `candles`, `predictions`, `model_metrics`, `analytics_snapshots`. Provides `get_trades_for_date()`, `get_trades_for_week()`, `get_trades_for_date_range()` for historical queries. |
| `context_writer.py` | Generates `LIVE_CONTEXT.md` every 15 minutes — account status, today's trades, weekly performance, all-time stats, config. This file is read by the Claude Projects integration for deep analysis. |

## Runtime Files (not committed)
- `trader.db` — SQLite database (mounted via Docker volume)
- `LIVE_CONTEXT.md` — auto-generated, do not edit manually
- `sentiment_*.json`, `vol_*.json` — cached MCP analysis data
- `calendar_cache.json` — cached economic calendar
- `heartbeat_primary.json` — instance coordination heartbeat
