# bot/analytics/

Rolling performance metrics computation. Runs hourly via the scheduler.

| File | Purpose |
|------|---------|
| `metrics.py` | Computes and stores: prediction accuracy (24h/7d/30d), LSTM edge vs indicator-only, per-pair accuracy, week-over-week accuracy trend. Results saved to `analytics_snapshots` SQLite table. |

## Metrics Computed
- **prediction_accuracy** — overall and per-direction (BUY/SELL) at 24h, 7d, 30d windows
- **lstm_edge_avg** — average confidence score difference (LSTM-enhanced minus indicator-only)
- **lstm_indicator_agreement** — how often LSTM agrees with indicator direction
- **pair_accuracy_7d** — per-pair prediction accuracy over 7 days
- **accuracy_trend_weekly** — week-over-week accuracy change

All metrics are queryable via MCP API endpoints (`/analytics/*`) and Telegram commands (`/accuracy`, `/performance`).
