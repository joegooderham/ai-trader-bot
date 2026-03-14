# risk/

Risk management — position sizing, stop-loss management, end-of-day operations.

| File | Purpose |
|------|---------|
| `position_sizer.py` | Calculates trade size based on ATR-based stop-loss distance and 2% risk per trade. Enforces £5 hard cap per trade. Pip size mapping for all pairs defined here. |
| `eod_manager.py` | End-of-day operations: evaluates positions for 98% overnight hold rule (23:45 UTC), force-closes all remaining positions (23:59 UTC), tightens stops on overnight holds to protect 75% of profit. |

## Key Risk Controls
- **£5 max per-trade spend** — hard cap regardless of risk percentage
- **ATR-based stops** — 1.5× ATR stop-loss, 2:1 reward-to-risk take-profit
- **Trailing stops** — activate at 1.5× ATR profit, trail at 1.0× ATR
- **Correlation block** — blocks new trades if correlation ≥ 0.75 with held position
- **Daily circuit breaker** — pauses trading for 24h if account drops 10% in a day
- **98% overnight rule** — only holds past EOD if confidence ≥ 98% AND profitable
