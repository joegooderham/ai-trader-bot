# broker/

IG Group API integration — all broker communication goes through here.

| File | Purpose |
|------|---------|
| `ig_client.py` | REST API client for IG Group. Handles authentication (CST + X-SECURITY-TOKEN with 6h auto-refresh), candle fetching with in-memory cache, trade placement, stop-loss updates, position queries, and yfinance fallback when IG returns 403. Epic mapping (e.g. `EUR_USD` → `CS.D.EURUSD.MINI.IP`) is defined here. |
| `ig_streaming.py` | Real-time position streaming via IG Lightstreamer (WebSocket). Provides sub-second P&L updates and trade confirmations. Falls back to 5-minute REST polling if connection fails. |

## Key Behaviours
- **Candle caching**: Avoids re-fetching all 60 candles every scan. Tops up with only new candles since last fetch. Keeps within IG demo's 10k points/week allowance.
- **yfinance fallback**: When IG returns 403 (data allowance exhausted), automatically falls back to Yahoo Finance. Sends one Telegram alert, not per-pair spam. Recovery notification when IG returns.
- **Demo mode**: Currently connected to IG demo account. Do not switch to live without explicit owner instruction.
