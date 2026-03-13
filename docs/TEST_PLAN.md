# Test Plan — March 2026 Feature Batch

All features deployed 2026-03-13. This plan covers 7 changes across PRs #25–#28.

---

## How to Test

Most tests are **log-based observation** — run the bot on the IG demo account and verify expected behaviour in `docker-compose logs -f forex-bot`. Some tests require manual trades on the IG demo platform to trigger specific scenarios.

**Useful commands:**
```bash
docker-compose logs -f forex-bot          # Live bot logs
docker-compose logs -f forex-bot | grep "circuit"   # Filter specific feature
docker exec ai-trader-bot cat /app/logs/forex_bot_$(date +%Y-%m-%d).log  # Full day log
```

---

## 1. Real-Time Position Streaming (GH#6)

**What changed:** Positions are now monitored via IG Lightstreamer WebSocket instead of 5-minute REST polling. Trailing stops react in real-time.

| # | Test | Steps | Expected Result |
|---|------|-------|-----------------|
| 1.1 | Streaming connects on startup | Start bot, check logs | `Lightstreamer connected: CONNECTED:WS-STREAMING` and `TRADE subscription active` |
| 1.2 | Position update streams in real-time | Open a trade on IG demo platform manually | `OPU: EUR_USD BUY | P&L: ...` appears in logs within 1 second |
| 1.3 | P&L alert threshold | Let position P&L swing by <£5 then >£5 | No alert below £5, Telegram alert when change exceeds £5 |
| 1.4 | Trade confirmation streams | Place trade via bot (wait for signal) | `Trade confirmed: CS.D.EURUSD.MINI.IP BUY @ 1.xxxxx | Status: ACCEPTED` |
| 1.5 | Position close detected | Close a position on IG platform | `Position closed via streaming: EUR_USD (deal xxxxx)` |
| 1.6 | Fallback to REST polling | Set `enable_streaming: false` in config, restart | `Streaming disabled — using 5-min REST polling` and position checks every 5 min |
| 1.7 | Graceful reconnection | Wait for IG session refresh (~6h) | Streaming reconnects without manual intervention |

**Config:** `trading.enable_streaming: true`, `trading.streaming_pl_alert_threshold: 5.0`

---

## 2. Circuit Breaker — Daily Loss Limit (BACKLOG-008)

**What changed:** If the account drops by more than 10% in a single day, all trading pauses for 24 hours.

| # | Test | Steps | Expected Result |
|---|------|-------|-----------------|
| 2.1 | Day start balance recorded | Start bot (or wait for first scan of new UTC day) | `Day start balance recorded: £xxxx.xx` |
| 2.2 | Normal operation (no trigger) | Trade normally, stay within 10% loss | No circuit breaker messages, scans continue |
| 2.3 | Breaker activates | Temporarily set threshold to 1% (`daily_loss_circuit_breaker_pct: 1.0`), lose >1% | `CIRCUIT BREAKER ACTIVATED — account down X.X% today` + Telegram alert |
| 2.4 | Scans blocked while active | Wait for next scan cycle after activation | `Circuit breaker active — skipping market scan` |
| 2.5 | EOD reset | Wait for 23:59 UTC force close | State resets, next day first scan records new balance |
| 2.6 | Breaker expires after 24h | Activate breaker, wait 24h (or check logs next day) | `Circuit breaker expired — resuming trading` |

**Config:** `risk.daily_loss_circuit_breaker_pct: 10.0`

**Tip:** To test activation easily, temporarily set threshold to 1% — any small loss will trigger it. Reset to 10% after testing.

---

## 3. Trailing Stop-Loss (BACKLOG-007)

**What changed:** Once a position moves 1.5×ATR into profit, the stop-loss trails behind at 1.0×ATR. Never loosens, only tightens.

| # | Test | Steps | Expected Result |
|---|------|-------|-----------------|
| 3.1 | No trailing stop below threshold | Open position, price moves <1.5×ATR in profit | No trailing stop log messages |
| 3.2 | Trailing stop activates | Price moves ≥1.5×ATR in profit | `Trailing stop: EUR_USD BUY | Entry: x.xxxxx | Current: x.xxxxx | Move: x.xxxxx | Old stop: x.xxxxx → New stop: x.xxxxx` |
| 3.3 | Telegram notification sent | Trailing stop updates | `📈 Trailing Stop Updated` message in Telegram |
| 3.4 | Stop only tightens | Price retraces slightly after trailing stop set | No new trailing stop log (stop stays at highest level) |
| 3.5 | Stop tightens further | Price continues in profitable direction | New trailing stop log with higher stop level (BUY) or lower (SELL) |
| 3.6 | Real-time via streaming | With streaming on, price moves in profit | Trailing stop reacts within seconds, not 5 minutes |
| 3.7 | IG API accepts update | Check logs after trailing stop | `Stop-loss updated: {deal_id} → x.xxxxx` (no rejection) |
| 3.8 | EOD overnight tightening | Hold position past 23:45 with 98%+ score | `Tightening stop-loss for {pair}` log + broker.update_stop_loss called |

**Config:** `risk.trailing_stop_activation_atr: 1.5`, `risk.trailing_stop_trail_atr: 1.0`

---

## 4. Correlation Block (BACKLOG-005)

**What changed:** If you hold EUR_USD, the bot won't open GBP_USD (correlation 0.85 > threshold 0.75). Prevents doubling the same directional bet.

| # | Test | Steps | Expected Result |
|---|------|-------|-----------------|
| 4.1 | Block highly correlated pair | Hold EUR_USD, wait for GBP_USD signal | `Skipping GBP_USD — correlation 0.85 with open position EUR_USD exceeds threshold 0.75` |
| 4.2 | Block inverse correlated pair | Hold EUR_USD, wait for USD_CHF signal | `Skipping USD_CHF — correlation 0.90 with open position EUR_USD exceeds threshold 0.75` |
| 4.3 | Allow weakly correlated pair | Hold EUR_USD, wait for USD_JPY signal | No skip message — correlation 0.55 < 0.75, trade proceeds normally |
| 4.4 | No block with no positions | Close all positions, run scan | No correlation skip messages for any pair |
| 4.5 | Threshold adjustment | Set `correlation_block_threshold: 0.90`, hold EUR_USD | GBP_USD (0.85) is now ALLOWED (below 0.90 threshold) |

**Config:** `risk.correlation_block_threshold: 0.75`

**Correlation pairs to watch:**
- EUR_USD ↔ GBP_USD: 0.85 (blocked at 0.75)
- EUR_USD ↔ USD_CHF: 0.90 (blocked)
- AUD_USD ↔ NZD_USD: 0.90 (blocked)
- EUR_USD ↔ USD_JPY: 0.55 (allowed)

---

## 5. Multi-Timeframe Analysis (BACKLOG-004)

**What changed:** Before trading, the bot fetches H4 candles to check the macro trend. Aligned trend = +10 boost, conflicting = -15 penalty.

| # | Test | Steps | Expected Result |
|---|------|-------|-----------------|
| 5.1 | H4 candles fetched | Run scan with `htf_timeframe: H4` | `EUR_USD HTF(H4): {'trend': 'bullish', 'strength': xx.x, ...}` |
| 5.2 | Alignment bonus applied | H4 bullish + H1 BUY signal | `H4 trend is bullish (strength xx%) — aligns with BUY, +10 boost` in reasoning |
| 5.3 | Conflict penalty applied | H4 bearish + H1 BUY signal | `H4 trend is bearish (strength xx%) — conflicts with BUY, -15 penalty` |
| 5.4 | Neutral = no modifier | H4 trend neutral (mixed signals) | No MTF modifier message, `mtf_modifier: 0` in breakdown |
| 5.5 | Breakdown includes MTF | Check confidence breakdown in Telegram | `mtf_modifier: 10.0` or `mtf_modifier: -15.0` in score components |
| 5.6 | HTF disabled | Set `htf_timeframe: none` | No HTF logs, mtf_context = None |

**Config:** `trading.htf_timeframe: H4`, `trading.htf_alignment_bonus: 10`, `trading.htf_conflict_penalty: 15`

---

## 6. Session-Aware Trading (BACKLOG-006)

**What changed:** Minimum confidence threshold adjusts by trading session. Quiet sessions (Sydney +15, Tokyo +10) require stronger signals. Peak sessions (London/NY overlap -5) allow slightly weaker signals.

| # | Test | Steps | Expected Result |
|---|------|-------|-----------------|
| 6.1 | Session logged in scan | Check scan header at any time | `─── Market Scan Started (OVERLAP session) ───` (or LONDON, TOKYO, etc.) |
| 6.2 | Tokyo raises bar | Check scan during 00:00–09:00 UTC | `EUR_USD session adjustment: tokyo session, min confidence 60% + 10 = 70%` |
| 6.3 | Sydney raises bar | Check scan during 21:00–00:00 UTC | Min confidence = 75% (60 + 15) for non-JPY pairs |
| 6.4 | Overlap lowers bar | Check scan during 13:00–17:00 UTC | Min confidence = 55% (60 - 5) |
| 6.5 | London/NY standard | Check scan during 08:00–13:00 UTC (London, not overlap) | Min confidence = 60% (no adjustment) |
| 6.6 | JPY exempt from Tokyo | Check USD_JPY during Tokyo session | No adjustment for JPY pair (boost = 0), min stays 60% |
| 6.7 | Non-JPY not exempt | Check EUR_USD during Tokyo session | Boost = +10, min = 70% |
| 6.8 | Trade blocked by session | Score 65% during Tokyo for EUR_USD | `Confidence: 65.0% | Session: tokyo (min: 70%) | Trade: False` |

**Config:** `trading.session_confidence_boost` (overlap: -5, london: 0, new_york: 0, tokyo: 10, sydney: 15)

**Session times (UTC):**
| Session | Hours | Boost | Effective Min |
|---------|-------|-------|---------------|
| Sydney | 21:00–07:00 | +15 | 75% |
| Tokyo | 00:00–09:00 | +10 | 70% |
| London | 08:00–17:00 | 0 | 60% |
| NY | 13:00–22:00 | 0 | 60% |
| Overlap | 13:00–17:00 | -5 | 55% |

---

## 7. Ghost Position Fix

**What changed:** `get_open_trades()` now skips positions where IG returns None for dealId, size, or level. Prevents phantom positions blocking new trades.

| # | Test | Steps | Expected Result |
|---|------|-------|-----------------|
| 7.1 | Normal positions work | Open trades normally | Positions appear in `/positions` Telegram command |
| 7.2 | Ghost filtered | If IG returns malformed data (rare) | `Skipping position with missing data — dealId=None, size=None, level=None` |
| 7.3 | Max positions not blocked | With ghost positions filtered | Bot can open trades up to true max_open_positions (5) |

**Note:** This bug is intermittent — IG only returns None data occasionally. Monitor logs for the warning message over several days.

---

## Test Schedule

| Day | Focus | Key Tests |
|-----|-------|-----------|
| Day 1 | Streaming + Session | 1.1–1.5, 6.1–6.5 (verify streaming connects, session logged correctly) |
| Day 2 | Correlation + MTF | 4.1–4.4, 5.1–5.4 (need open positions to test correlation block) |
| Day 3 | Trailing Stop | 3.1–3.6 (need positions to move into profit — may take time) |
| Day 4 | Circuit Breaker | 2.1–2.4 (temporarily lower threshold to 1% to trigger, then reset) |
| Day 5 | Edge Cases | 1.6 (fallback), 2.5–2.6 (EOD reset), 5.6 (HTF disabled), 7.1–7.3 |
| Ongoing | Ghost positions | 7.2 (monitor for intermittent IG API issues over 1-2 weeks) |

---

## Quick Smoke Test (Run First)

After any deployment, verify these 5 things in 2 minutes:

```bash
docker-compose logs --tail=30 forex-bot
```

1. `✅ All configuration loaded successfully` — config is valid
2. `Lightstreamer connected: CONNECTED:WS-STREAMING` — streaming works
3. `─── Market Scan Started (XXXX session) ───` — session detection works
4. `Day start balance recorded: £xxxx.xx` — circuit breaker tracking
5. No Python tracebacks or import errors
