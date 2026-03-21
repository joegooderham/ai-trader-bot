# config/

Trading configuration files.

| File | Purpose |
|------|---------|
| `config.yaml` | All trading parameters: pairs, timeframes, confidence thresholds, risk settings, LSTM architecture and training hyperparameters, schedule times, MCP module toggles, session adjustments, instance coordination. Loaded once at import by `bot/config.py`. Changes require container restart. |

## Key Sections
- **trading** — pairs, scan interval, max positions, streaming, session adjustments, multi-timeframe
- **confidence** — aggressiveness (min score to trade: currently 85%), component weights
- **risk** — stop-loss/take-profit ATR multipliers, trailing stops, correlation block, circuit breaker, confidence-tiered risk, partial profit-taking
- **lstm** — architecture (hidden_size, num_layers, dropout), training hyperparameters, shadow mode, retrain interval
- **schedule** — EOD times, report times
- **mcp** — 9 analysis modules: economic calendar, news sentiment, correlations, volatility, session stats, IG client sentiment, FRED macro, Myfxbook sentiment, CFTC COT positioning
- **remediation** — auto-pause threshold (-£50 weekly), direction win rate alert threshold (30%), losing streak analysis minimum (5)
