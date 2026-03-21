# notifications/

All Telegram communication — outbound messages and inbound commands.

| File | Purpose |
|------|---------|
| `telegram_bot.py` | **Outbound messages.** Sends trade notifications (opened/closed), daily and weekly reports, overnight hold alerts, health alerts, fallback alerts, dev activity updates, startup message. Supports inline keyboard buttons (`send_action_buttons`) for remediation approve/reject. Uses a fresh Bot instance per send with 1s rate limiting. |
| `telegram_chat.py` | **Inbound commands + AI chat + inline button callbacks.** Handles all `/commands`, free-text questions (sent to Claude AI with context), and `CallbackQueryHandler` for remediation inline button presses (approve/reject actions from integrity monitor). |

## Available Commands

### Positions & Account
| Command | Action |
|---------|--------|
| `/positions` | Currently open positions with P&L |
| `/balance` | Account funds, equity, margin used, available |
| `/pltoday` | Today's realised + unrealised P&L |
| `/plweek` | This week's running total P&L by pair |
| `/history` | Last 10 closed trades with outcome |
| `/trades` | Recent trades with index numbers |

### Close Commands
| Command | Action |
|---------|--------|
| `/close <#>` | Close a specific trade by number |
| `/closeall` | Close all open positions |
| `/closepair EURUSD` | Close a specific pair's position |
| `/closeprofitable` | Close all winning positions |
| `/closelosing` | Close all losing positions |

### Bot Control
| Command | Action |
|---------|--------|
| `/pause` | Stop opening new trades |
| `/resume` | Re-enable trading after a pause |
| `/status` | Bot health, services, open positions |
| `/report` | Trigger daily report on demand |

### Strategy
| Command | Action |
|---------|--------|
| `/setconfidence 50` | Adjust min confidence threshold % |
| `/setrisk 2` | Adjust risk per trade % |
| `/settings` | Show all current bot settings |

### Deploy
| Command | Action |
|---------|--------|
| `/deploy` | Trigger CI/CD deployment via GitHub Actions |
| `/deploystatus` | Show last 5 deployment runs |

### Analytics & Integrity
| Command | Action |
|---------|--------|
| `/accuracy` | LSTM prediction accuracy (7d) |
| `/model` | LSTM model info and last retrain |
| `/drift` | Model drift detection status |
| `/performance` | LSTM performance metrics |
| `/integrity` | Full profit integrity check with recommendations |
| `/action <#>` | Apply an integrity recommendation |
| `/discuss <#>` | Discuss a recommendation in detail |

### Tools
| Command | Action |
|---------|--------|
| `/today` | Today's trades and P&L summary |
| `/health` | System health check |
| `/plan` | Tomorrow's trading plan |
| `/stats` | All-time performance stats |
| `/datastatus` | IG vs yfinance status per pair |
| `/query <question>` | Natural language SQL query |
| `/devops` | Recent code changes |
| `/backtest` | LSTM vs indicator-only simulation |
| `/fallbacktest` | Test yfinance data source |
