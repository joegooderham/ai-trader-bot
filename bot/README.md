# bot/

Core trading bot logic and entry point.

| File | Purpose |
|------|---------|
| `scheduler.py` | **Entry point.** Runs all scheduled jobs via APScheduler: market scans (15min), position monitoring (5min), EOD operations, LSTM retrain, drift checks, analytics. Telegram polling runs on the main thread. |
| `config.py` | Single config source — loads `.env` + `config/config.yaml` at import time. All other modules import from here. Includes GitHub PAT for deploy commands. |
| `instance.py` | Multi-instance heartbeat coordination. Currently single instance ("primary"). Designed for future failover/split-pair setups. |

## Subdirectories

| Directory | Purpose |
|-----------|---------|
| `engine/` | Trade decision engine — indicators, confidence scoring, LSTM, daily plans |
| `analytics/` | Rolling performance metrics computation (accuracy, LSTM edge, trends) |
