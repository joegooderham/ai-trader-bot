# bot/analytics/

Automated trading remediation and rolling performance metrics.

| File | Purpose |
|------|---------|
| `integrity_monitor.py` | **Automated remediation system.** Detects trading anomalies, diagnoses root causes, and presents targeted fixes via Telegram inline buttons. 5 detection methods: smart losing streak analysis, direction performance alerts, weekly strategy review, daily LSTM health, auto-pause on sustained losses. Actions apply at runtime (no restart). |
| `metrics.py` | Computes and stores: prediction accuracy (24h/7d/30d), LSTM edge vs indicator-only, per-pair accuracy, week-over-week accuracy trend. Results saved to `analytics_snapshots` SQLite table. |

## Integrity Monitor — Detection Methods

| Method | Frequency | What it detects |
|--------|-----------|-----------------|
| Smart Losing Streak | Hourly | Diagnoses WHY losses happen: direction bias (>70% one way), pair concentration (>60% one pair), low confidence, stop-loss hits (>60%), EOD closures (>60%) |
| Direction Performance | Hourly | BUY or SELL win rate < 30% over 7 days with >= 5 trades |
| Weekly Strategy Review | Monday 00:15 UTC | Week-over-week P&L decline, pairs that flipped from profitable to unprofitable |
| Daily LSTM Health | Daily 08:00 UTC | Model age, prediction accuracy (24h/7d), LSTM edge, shadow mode toggle recommendations |
| Auto-Pause | Hourly | Weekly P&L < -£50 → immediately pauses trading (no approval needed) |

## Action Types
- `disable_direction` — blocks BUY or SELL at runtime
- `enable_direction` — re-enables a blocked direction
- `remove_pair` — removes a pair from scanning at runtime
- `runtime_config_change` — changes config immediately + persists to YAML
- `pause_trading` — stops all new trades

## Metrics Computed
- **prediction_accuracy** — overall and per-direction (BUY/SELL) at 24h, 7d, 30d windows
- **lstm_edge_avg** — average confidence score difference (LSTM-enhanced minus indicator-only)
- **lstm_indicator_agreement** — how often LSTM agrees with indicator direction
- **pair_accuracy_7d** — per-pair prediction accuracy over 7 days
- **accuracy_trend_weekly** — week-over-week accuracy change

All metrics are queryable via MCP API endpoints (`/analytics/*`) and Telegram commands (`/accuracy`, `/performance`).
