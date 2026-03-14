# bot/engine/

Trade decision engine — transforms market data into confidence scores.

| File | Purpose |
|------|---------|
| `indicators.py` | Calculates technical indicators from candle data: RSI, MACD, Bollinger Bands, EMA crossover, ATR, volume analysis. Returns an `IndicatorResult` used by confidence scoring. |
| `confidence.py` | Scores trade signals 0–100%. Weighted: LSTM 50%, MACD/RSI 20%, EMA 15%, Bollinger 10%, Volume 5%. Applies MCP context and multi-timeframe modifiers. |
| `daily_plan.py` | Generates tomorrow's trading plan via Claude AI for Telegram delivery. |

## Subdirectory

| Directory | Purpose |
|-----------|---------|
| `lstm/` | Neural network prediction system — model, training, inference, drift detection, backtesting |
