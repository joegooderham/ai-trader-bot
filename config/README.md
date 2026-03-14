# config/

Trading configuration files.

| File | Purpose |
|------|---------|
| `config.yaml` | All trading parameters: pairs, timeframes, confidence thresholds, risk settings, LSTM architecture and training hyperparameters, schedule times, MCP module toggles, session adjustments, instance coordination. Loaded once at import by `bot/config.py`. Changes require container restart. |

## Key Sections
- **trading** — pairs, scan interval, max positions, streaming, session adjustments, multi-timeframe
- **confidence** — aggressiveness (min score to trade), component weights
- **risk** — stop-loss/take-profit ATR multipliers, trailing stops, correlation block, circuit breaker
- **lstm** — architecture (hidden_size, num_layers, dropout), training hyperparameters, shadow mode, retrain interval
- **schedule** — EOD times, report times
- **mcp** — which analysis modules to enable, cache duration, Claude model
