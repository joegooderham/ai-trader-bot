# notifications/

All Telegram communication — outbound messages and inbound commands.

| File | Purpose |
|------|---------|
| `telegram_bot.py` | **Outbound messages.** Sends trade notifications (opened/closed), daily and weekly reports, overnight hold alerts, health alerts, fallback alerts, dev activity updates, startup message. Uses a fresh Bot instance per send with 1s rate limiting to avoid pool exhaustion. |
| `telegram_chat.py` | **Inbound commands + AI chat.** Handles all `/commands` and free-text questions. Free-text questions are sent to Claude AI with full trade context for intelligent answers. |

## Available Commands
| Command | Action |
|---------|--------|
| `/today` | Today's trades and P&L |
| `/positions` | Currently open positions |
| `/trades` | Recent trades with index numbers |
| `/close <#>` | Close a specific trade |
| `/closeall` | Close all open positions |
| `/pause` / `/resume` | Pause/resume trading |
| `/health` | System health check |
| `/stats` | All-time performance stats |
| `/accuracy` | LSTM prediction accuracy |
| `/model` | LSTM model info |
| `/drift` | Model drift status |
| `/performance` | LSTM performance metrics |
| `/datastatus` | IG vs yfinance status per pair |
| `/plan` | Tomorrow's trading plan |
| `/query <question>` | Natural language SQL query |
| `/devops` | Recent code changes |
| `/backtest` | LSTM vs indicator-only simulation |
| `/fallbacktest` | Test yfinance data source |
