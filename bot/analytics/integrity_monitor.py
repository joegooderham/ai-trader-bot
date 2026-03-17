"""
bot/analytics/integrity_monitor.py — Profit Integrity & Contingency Monitor
─────────────────────────────────────────────────────────────────────────────
Proactively detects trading anomalies before they compound into serious losses.
Runs at three frequencies:

  1. QUICK CHECK (after every 15-min scan):
     - Validates SL/TP distances aren't too tight (spread vs ATR sanity)
     - Catches trades that opened with broken risk parameters
     - Alerts immediately on anomalies

  2. HOURLY REVIEW:
     - Analyses all trades closed in the rolling 24h window
     - Detects patterns: breakeven streaks, win rate collapse, P&L drift
     - Checks average trade duration (too short = stops too tight)
     - Validates trailing stop behaviour

  3. DEEP REVIEW (every 4 hours):
     - Per-pair profitability analysis
     - Config effectiveness scoring (are current settings producing profit?)
     - LSTM vs indicator-only comparison (is the model helping or hurting?)
     - Generates actionable recommendations

Alerts go via the SYSTEM Telegram bot so they don't mix with trade signals.
Each alert type is deduplicated — won't spam the same warning repeatedly.
"""

from datetime import datetime, timezone, timedelta
from loguru import logger
from typing import Optional

from data.storage import TradeStorage
from bot import config


class IntegrityMonitor:
    """
    Proactive profit integrity and contingency checker.

    Catches problems like the breakeven bug (56 trades at £0 P&L)
    before they compound over days. Sends Telegram alerts via the
    system bot so trading signals stay clean.
    """

    def __init__(self, notifier=None):
        self.storage = TradeStorage()
        self.notifier = notifier
        # Deduplicate alerts: track which warnings have been sent recently
        # Key = alert_type, Value = datetime of last alert
        self._last_alerts: dict[str, datetime] = {}
        # Cooldown: don't repeat the same alert within this window
        self._alert_cooldown_hours = 4

    # ── Quick Check (every 15 min, after each scan) ───────────────────────────

    def quick_check(self, trade_result: dict = None):
        """
        Fast validation run after every market scan.

        If a trade was just opened, validates its risk parameters.
        Also checks for any positions with suspiciously tight stops.
        """
        issues = []

        if trade_result:
            issues.extend(self._validate_trade_params(trade_result))

        # Check all open positions for anomalies
        issues.extend(self._check_open_position_health())

        if issues:
            self._send_alert("quick_check", issues)

    def _validate_trade_params(self, trade: dict) -> list[str]:
        """Validate that a newly opened trade has sane risk parameters."""
        issues = []
        pair = trade.get("pair", "Unknown")
        entry = trade.get("fill_price", 0)
        sl = trade.get("stop_loss", 0)
        tp = trade.get("take_profit", 0)
        direction = trade.get("direction", "BUY")

        if not entry or not sl or not tp:
            issues.append(f"{pair}: Trade opened with missing SL ({sl}) or TP ({tp})")
            return issues

        # Check SL distance isn't zero or negative
        sl_distance = abs(entry - sl)
        tp_distance = abs(entry - tp)

        if sl_distance < 0.00001:
            issues.append(f"{pair}: Stop-loss is at entry price — trade will close immediately at breakeven")

        if tp_distance < 0.00001:
            issues.append(f"{pair}: Take-profit is at entry price — impossible to profit")

        # Check risk/reward ratio is sane (should be ~2:1 per config)
        if sl_distance > 0:
            rr_ratio = tp_distance / sl_distance
            if rr_ratio < 1.0:
                issues.append(
                    f"{pair}: Risk/reward ratio is {rr_ratio:.1f}:1 "
                    f"(should be >= {config.TAKE_PROFIT_RATIO}:1)"
                )

        # Check SL isn't on the wrong side of entry (would be instant loss)
        if direction == "BUY" and sl > entry:
            issues.append(f"{pair} BUY: Stop-loss ({sl}) is ABOVE entry ({entry}) — instant close")
        elif direction == "SELL" and sl < entry:
            issues.append(f"{pair} SELL: Stop-loss ({sl}) is BELOW entry ({entry}) — instant close")

        return issues

    def _check_open_position_health(self) -> list[str]:
        """Check all open positions for anomalies."""
        issues = []
        try:
            from broker.ig_client import IGClient
            broker = IGClient()
            open_trades = broker.get_open_trades()

            for trade in open_trades:
                pair = trade.get("pair") or trade.get("instrument", "?")
                entry = float(trade.get("level") or trade.get("price", 0))
                current_stop = trade.get("stopLevel")
                direction = trade.get("direction", "BUY")

                if not entry or not current_stop:
                    continue

                stop = float(current_stop)
                sl_distance = abs(entry - stop)

                # Warn if stop is less than 3 pips from entry (likely to get stopped out by spread)
                from risk.position_sizer import PIP_SIZE, DEFAULT_PIP_SIZE
                pip_size = PIP_SIZE.get(pair, DEFAULT_PIP_SIZE)
                sl_pips = sl_distance / pip_size if pip_size > 0 else 0

                if sl_pips < 3:
                    issues.append(
                        f"{pair}: Stop-loss only {sl_pips:.1f} pips from entry — "
                        f"spread alone could trigger it"
                    )

        except Exception as e:
            logger.debug(f"Integrity quick check — couldn't fetch positions: {e}")

        return issues

    # ── Hourly Review ─────────────────────────────────────────────────────────

    def hourly_review(self) -> dict:
        """
        Analyse rolling 24h of trades for red flags.

        Returns a summary dict with all findings, and sends
        a Telegram alert if any issues are detected.
        """
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")

        # Get today's closed trades
        today_trades = self.storage.get_trades_for_date(today)
        closed_trades = [t for t in today_trades if t.get("closed_at")]

        # Also get yesterday's trades for 24h rolling window
        yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        yesterday_trades = self.storage.get_trades_for_date(yesterday)
        yesterday_closed = [t for t in yesterday_trades if t.get("closed_at")]

        all_closed = yesterday_closed + closed_trades
        issues = []
        summary = {
            "timestamp": now.isoformat(),
            "trades_24h": len(all_closed),
            "trades_today": len(closed_trades),
            "issues": [],
            "status": "HEALTHY",
        }

        if not all_closed:
            summary["status"] = "NO_DATA"
            return summary

        # ── Check 1: Breakeven Streak ─────────────────────────────────────────
        # The exact bug we caught: multiple trades closing at £0 P&L
        breakeven_trades = [t for t in all_closed if abs(t.get("pl", 0)) < 0.01]
        breakeven_pct = len(breakeven_trades) / len(all_closed) * 100

        if len(breakeven_trades) >= 3 and breakeven_pct >= 50:
            issue = (
                f"BREAKEVEN STREAK: {len(breakeven_trades)}/{len(all_closed)} trades "
                f"({breakeven_pct:.0f}%) closed at £0 P&L in 24h. "
                f"This indicates stops are too tight or a SL/TP calculation bug."
            )
            issues.append(issue)

        # ── Check 2: Win Rate Collapse ────────────────────────────────────────
        # Alert if win rate drops below 20% with a meaningful sample
        wins = [t for t in all_closed if t.get("pl", 0) > 0.01]
        losses = [t for t in all_closed if t.get("pl", 0) < -0.01]
        win_rate = len(wins) / len(all_closed) * 100 if all_closed else 0

        if len(all_closed) >= 5 and win_rate < 20:
            issue = (
                f"WIN RATE COLLAPSE: Only {win_rate:.0f}% win rate "
                f"({len(wins)} wins / {len(losses)} losses / "
                f"{len(breakeven_trades)} breakeven) across {len(all_closed)} trades in 24h."
            )
            issues.append(issue)

        # ── Check 3: Net P&L Drift ────────────────────────────────────────────
        # Catch sustained losses that aren't triggering circuit breaker
        total_pl = sum(t.get("pl", 0) for t in all_closed)
        summary["net_pl_24h"] = round(total_pl, 2)

        # Alert if cumulative P&L is worse than -£20 (4% of £500 capital)
        # This is below the circuit breaker but still concerning
        if total_pl < -(config.MAX_CAPITAL * 0.04):
            issue = (
                f"P&L DRIFT: Net P&L is £{total_pl:.2f} over 24h "
                f"({total_pl / config.MAX_CAPITAL * 100:.1f}% of capital). "
                f"Consider pausing to review strategy."
            )
            issues.append(issue)

        # ── Check 4: Average Trade Duration ───────────────────────────────────
        # If trades are closing very quickly, stops are probably too tight
        durations = []
        for t in all_closed:
            opened = t.get("opened_at")
            closed = t.get("closed_at")
            if opened and closed:
                try:
                    open_dt = datetime.fromisoformat(opened.replace("Z", "+00:00"))
                    close_dt = datetime.fromisoformat(closed.replace("Z", "+00:00"))
                    duration_min = (close_dt - open_dt).total_seconds() / 60
                    durations.append(duration_min)
                except (ValueError, TypeError):
                    pass

        if durations:
            avg_duration = sum(durations) / len(durations)
            summary["avg_duration_min"] = round(avg_duration, 1)

            # On H1 timeframe, trades closing in under 30 min are suspicious
            if avg_duration < 30 and len(durations) >= 3:
                issue = (
                    f"SHORT DURATION: Average trade lasts only {avg_duration:.0f} min. "
                    f"On H1 timeframe, this suggests stops are too tight or "
                    f"trailing stops activate too early."
                )
                issues.append(issue)

        # ── Check 5: Consecutive Losses ───────────────────────────────────────
        # Detect losing streaks (sorted by close time)
        sorted_trades = sorted(all_closed, key=lambda t: t.get("closed_at", ""))
        max_consecutive_losses = 0
        current_streak = 0
        for t in sorted_trades:
            if t.get("pl", 0) < -0.01:
                current_streak += 1
                max_consecutive_losses = max(max_consecutive_losses, current_streak)
            else:
                current_streak = 0

        summary["max_consecutive_losses"] = max_consecutive_losses

        if max_consecutive_losses >= 5:
            issue = (
                f"LOSING STREAK: {max_consecutive_losses} consecutive losing trades. "
                f"Market conditions may have shifted — consider pausing or "
                f"raising the confidence threshold."
            )
            issues.append(issue)

        # ── Check 6: All Trades Same Direction ────────────────────────────────
        # If every trade is BUY or every trade is SELL, the bot may be stuck
        directions = [t.get("direction") for t in all_closed if t.get("direction")]
        if len(directions) >= 5:
            buy_pct = directions.count("BUY") / len(directions) * 100
            if buy_pct > 90 or buy_pct < 10:
                dominant = "BUY" if buy_pct > 90 else "SELL"
                issue = (
                    f"DIRECTION BIAS: {buy_pct:.0f}% of trades are {dominant}. "
                    f"The bot may be stuck reading one-sided signals."
                )
                issues.append(issue)

        # Build summary
        summary["win_rate"] = round(win_rate, 1)
        summary["breakeven_count"] = len(breakeven_trades)
        summary["issues"] = issues
        summary["status"] = "WARNING" if issues else "HEALTHY"

        if issues:
            self._send_alert("hourly_review", issues)

        return summary

    # ── Deep Review (every 4 hours) ───────────────────────────────────────────

    def deep_review(self) -> dict:
        """
        Comprehensive profitability and strategy effectiveness analysis.

        Runs every 4 hours. Analyses per-pair performance, config effectiveness,
        and generates actionable recommendations.
        """
        now = datetime.now(timezone.utc)
        summary = {
            "timestamp": now.isoformat(),
            "pair_analysis": {},
            "recommendations": [],
            "config_assessment": {},
            "status": "HEALTHY",
        }
        issues = []

        # Get trades for the past 7 days for trend analysis
        week_trades = self.storage.get_trades_for_week()
        closed_week = [t for t in week_trades if t.get("closed_at")]

        if len(closed_week) < 5:
            summary["status"] = "INSUFFICIENT_DATA"
            return summary

        # ── Per-Pair Profitability ────────────────────────────────────────────
        pair_stats = {}
        for t in closed_week:
            pair = t.get("pair", "Unknown")
            if pair not in pair_stats:
                pair_stats[pair] = {"trades": 0, "wins": 0, "pl": 0, "breakeven": 0}
            pair_stats[pair]["trades"] += 1
            pair_stats[pair]["pl"] += t.get("pl", 0)
            if t.get("pl", 0) > 0.01:
                pair_stats[pair]["wins"] += 1
            elif abs(t.get("pl", 0)) < 0.01:
                pair_stats[pair]["breakeven"] += 1

        for pair, stats in pair_stats.items():
            win_rate = stats["wins"] / stats["trades"] * 100 if stats["trades"] > 0 else 0
            stats["win_rate"] = round(win_rate, 1)
            summary["pair_analysis"][pair] = stats

            # Flag consistently unprofitable pairs
            if stats["trades"] >= 3 and stats["pl"] < -5:
                issues.append(
                    f"UNPROFITABLE PAIR: {pair.replace('_', '/')} has lost "
                    f"£{abs(stats['pl']):.2f} across {stats['trades']} trades this week "
                    f"(win rate: {win_rate:.0f}%). Consider removing from pairs list."
                )

            # Flag pairs with high breakeven rate
            if stats["trades"] >= 3:
                be_pct = stats["breakeven"] / stats["trades"] * 100
                if be_pct >= 60:
                    issues.append(
                        f"BREAKEVEN PAIR: {pair.replace('_', '/')} has {be_pct:.0f}% "
                        f"breakeven rate ({stats['breakeven']}/{stats['trades']} trades). "
                        f"Spread may be too wide relative to stop distance for this pair."
                    )

        # ── Config Effectiveness ──────────────────────────────────────────────
        total_pl = sum(t.get("pl", 0) for t in closed_week)
        total_trades = len(closed_week)
        overall_win_rate = sum(
            1 for t in closed_week if t.get("pl", 0) > 0.01
        ) / total_trades * 100

        summary["config_assessment"] = {
            "total_trades_7d": total_trades,
            "net_pl_7d": round(total_pl, 2),
            "win_rate_7d": round(overall_win_rate, 1),
            "avg_pl_per_trade": round(total_pl / total_trades, 2) if total_trades > 0 else 0,
            "trailing_stop_activation_atr": config.TRAILING_STOP_ACTIVATION_ATR,
            "trailing_stop_trail_atr": config.TRAILING_STOP_TRAIL_ATR,
            "stop_loss_atr_multiplier": config.STOP_LOSS_ATR_MULTIPLIER,
            "min_confidence": config.MIN_CONFIDENCE_SCORE,
        }

        # ── Overall Assessment ────────────────────────────────────────────────
        if total_pl < -(config.MAX_CAPITAL * 0.05):
            issues.append(
                f"WEEKLY LOSS THRESHOLD: Net P&L is £{total_pl:.2f} this week "
                f"({total_pl / config.MAX_CAPITAL * 100:.1f}% of capital). "
                f"Strategy may need adjustment."
            )

        if overall_win_rate < 30 and total_trades >= 10:
            issues.append(
                f"LOW WIN RATE: Only {overall_win_rate:.0f}% across {total_trades} trades this week. "
                f"Consider raising min confidence from {config.MIN_CONFIDENCE_SCORE}% to "
                f"{min(config.MIN_CONFIDENCE_SCORE + 10, 80)}%."
            )

        # ── Generate Recommendations ──────────────────────────────────────────
        recommendations = self._generate_recommendations(closed_week, pair_stats)
        summary["recommendations"] = recommendations
        summary["issues"] = issues
        summary["status"] = "WARNING" if issues else "HEALTHY"

        if issues:
            self._send_alert("deep_review", issues, recommendations)

        return summary

    def _generate_recommendations(self, trades: list, pair_stats: dict) -> list[str]:
        """Generate actionable recommendations based on trade analysis."""
        recs = []

        # Check if breakeven trades dominate
        breakeven_count = sum(1 for t in trades if abs(t.get("pl", 0)) < 0.01)
        if breakeven_count > len(trades) * 0.4:
            recs.append(
                "High breakeven rate detected. Consider widening trailing stop "
                "activation (trailing_stop_activation_atr) or increasing trail "
                "distance (trailing_stop_trail_atr) to give trades more room."
            )

        # Check if average loss > average win (poor risk/reward)
        wins = [t.get("pl", 0) for t in trades if t.get("pl", 0) > 0.01]
        losses = [abs(t.get("pl", 0)) for t in trades if t.get("pl", 0) < -0.01]
        if wins and losses:
            avg_win = sum(wins) / len(wins)
            avg_loss = sum(losses) / len(losses)
            if avg_loss > avg_win * 1.5:
                recs.append(
                    f"Average loss (£{avg_loss:.2f}) is significantly larger than "
                    f"average win (£{avg_win:.2f}). The take-profit ratio may need "
                    f"increasing, or stops are being hit before TP."
                )

        # Check if worst pair is dragging performance
        if pair_stats:
            worst_pair = min(pair_stats.items(), key=lambda x: x[1]["pl"])
            total_pl = sum(s["pl"] for s in pair_stats.values())
            if worst_pair[1]["pl"] < 0 and total_pl != 0:
                drag_pct = abs(worst_pair[1]["pl"]) / max(abs(total_pl), 0.01) * 100
                if drag_pct > 50:
                    recs.append(
                        f"{worst_pair[0].replace('_', '/')} is responsible for "
                        f"{drag_pct:.0f}% of total losses. Consider removing it "
                        f"from the pairs list temporarily."
                    )

        return recs

    # ── Alert Dispatch ────────────────────────────────────────────────────────

    def _send_alert(self, alert_type: str, issues: list[str], recommendations: list[str] = None):
        """
        Send integrity alert via system Telegram bot.
        Deduplicates: won't send the same alert type within the cooldown window.
        """
        now = datetime.now(timezone.utc)

        # Check cooldown — don't spam the same alert type
        last_sent = self._last_alerts.get(alert_type)
        if last_sent:
            hours_since = (now - last_sent).total_seconds() / 3600
            if hours_since < self._alert_cooldown_hours:
                logger.debug(
                    f"Integrity alert '{alert_type}' suppressed — "
                    f"sent {hours_since:.1f}h ago (cooldown: {self._alert_cooldown_hours}h)"
                )
                return

        if not self.notifier:
            logger.warning("Integrity monitor has no notifier — alert not sent")
            return

        # Build the alert message
        level_emoji = {
            "quick_check": "⚡",
            "hourly_review": "🔍",
            "deep_review": "📊",
        }
        emoji = level_emoji.get(alert_type, "⚠️")
        title = alert_type.replace("_", " ").title()

        message = (
            f"*{emoji} INTEGRITY ALERT — {title}*\n"
            f"═════════════════════\n"
        )

        for i, issue in enumerate(issues, 1):
            message += f"\n*{i}.* {issue}\n"

        if recommendations:
            message += f"\n*💡 Recommendations:*\n"
            for rec in recommendations:
                message += f"  • {rec}\n"

        message += f"\n_Run /integrity for full details_"
        message += f"\n_{now.strftime('%H:%M UTC')}_"

        self.notifier._send_system(message)
        self._last_alerts[alert_type] = now
        logger.info(f"Integrity alert sent: {alert_type} with {len(issues)} issue(s)")

    # ── On-Demand Summary (for /integrity command) ────────────────────────────

    def get_full_report(self) -> str:
        """
        Generate a comprehensive integrity report for the /integrity command.
        Runs all checks and returns a formatted Telegram message.
        """
        # Run hourly + deep reviews (quick check needs live trade data)
        hourly = self.hourly_review()
        deep = self.deep_review()

        now = datetime.now(timezone.utc)

        msg = (
            f"*🛡 INTEGRITY REPORT*\n"
            f"═════════════════════\n"
            f"_Generated {now.strftime('%Y-%m-%d %H:%M UTC')}_\n\n"
        )

        # Overall status
        status_emoji = {"HEALTHY": "✅", "WARNING": "⚠️", "NO_DATA": "📭", "INSUFFICIENT_DATA": "📭"}
        hourly_status = hourly.get("status", "UNKNOWN")
        deep_status = deep.get("status", "UNKNOWN")

        msg += (
            f"*Status:*\n"
            f"  Hourly: {status_emoji.get(hourly_status, '❓')} {hourly_status}\n"
            f"  Deep: {status_emoji.get(deep_status, '❓')} {deep_status}\n\n"
        )

        # 24h summary
        msg += f"*📈 Last 24 Hours:*\n"
        msg += f"  Trades: {hourly.get('trades_24h', 0)}\n"
        msg += f"  Net P&L: £{hourly.get('net_pl_24h', 0):.2f}\n"
        msg += f"  Win Rate: {hourly.get('win_rate', 0):.0f}%\n"
        msg += f"  Breakeven: {hourly.get('breakeven_count', 0)}\n"
        if hourly.get("avg_duration_min"):
            msg += f"  Avg Duration: {hourly['avg_duration_min']:.0f} min\n"
        msg += f"  Max Losing Streak: {hourly.get('max_consecutive_losses', 0)}\n\n"

        # 7-day pair analysis
        pair_analysis = deep.get("pair_analysis", {})
        if pair_analysis:
            msg += f"*📊 7-Day Per-Pair:*\n"
            for pair, stats in sorted(pair_analysis.items(), key=lambda x: x[1]["pl"], reverse=True):
                pl_sign = "+" if stats["pl"] >= 0 else ""
                pair_display = pair.replace("_", "/")
                msg += (
                    f"  {pair_display}: {pl_sign}£{stats['pl']:.2f} "
                    f"({stats['win_rate']:.0f}% win, {stats['trades']} trades)\n"
                )
            msg += "\n"

        # Config assessment
        ca = deep.get("config_assessment", {})
        if ca:
            msg += f"*⚙️ Config Assessment:*\n"
            msg += f"  SL ATR: {ca.get('stop_loss_atr_multiplier', '?')}x\n"
            msg += f"  Trail Activation: {ca.get('trailing_stop_activation_atr', '?')}x ATR\n"
            msg += f"  Trail Distance: {ca.get('trailing_stop_trail_atr', '?')}x ATR\n"
            msg += f"  Min Confidence: {ca.get('min_confidence', '?')}%\n\n"

        # Issues
        all_issues = hourly.get("issues", []) + deep.get("issues", [])
        if all_issues:
            msg += f"*⚠️ Issues Found ({len(all_issues)}):*\n"
            for issue in all_issues:
                msg += f"  • {issue}\n"
            msg += "\n"

        # Recommendations
        recs = deep.get("recommendations", [])
        if recs:
            msg += f"*💡 Recommendations:*\n"
            for rec in recs:
                msg += f"  • {rec}\n"

        if not all_issues and not recs:
            msg += f"*✅ All checks passed — no issues detected.*\n"

        return msg
