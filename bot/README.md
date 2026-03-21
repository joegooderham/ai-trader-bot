# bot/

Core trading bot logic and entry point.

| File | Purpose |
|------|---------|
| `scheduler.py` | **Entry point.** Runs all scheduled jobs via APScheduler: market scans (5min), position monitoring (5min), EOD operations, LSTM retrain, drift checks, analytics, integrity reviews (hourly + 4-hourly), weekly strategy review (Mon 00:15), daily LSTM health (08:00). Starts the command API and Telegram polling threads. Includes direction/pair guards from the remediation system. |
| `config.py` | Single config source — loads `.env` + `config/config.yaml` at import time. All other modules import from here. Includes runtime-mutable config: `DISABLED_DIRECTIONS`, `DISABLED_PAIRS` sets, and `apply_runtime_config()` for immediate config changes that also persist to YAML. |
| `command_api.py` | **Dashboard Command API.** FastAPI on port 8060 running as a daemon thread inside the bot process. Provides 15 HTTP endpoints for trade control (pause/resume, close positions), config changes, and remediation approve/reject. Authenticated via `DASHBOARD_CMD_TOKEN`. |
| `instance.py` | Multi-instance heartbeat coordination. Currently single instance ("primary"). Designed for future failover/split-pair setups. |

## Subdirectories

| Directory | Purpose |
|-----------|---------|
| `engine/` | Trade decision engine — indicators, confidence scoring, LSTM, daily plans |
| `analytics/` | Integrity monitoring (automated remediation system) and rolling performance metrics |
