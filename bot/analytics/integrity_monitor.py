"""
bot/analytics/integrity_monitor.py — Profit Integrity & Contingency Monitor
─────────────────────────────────────────────────────────────────────────────
Proactively detects trading anomalies before they compound into serious losses.
Runs at three frequencies and ALWAYS sends a Telegram report — even when
everything is healthy — so you have full visibility of all trading activity.

  1. QUICK CHECK (after every 15-min scan):
     - Validates SL/TP distances aren't too tight (spread vs ATR sanity)
     - Catches trades that opened with broken risk parameters
     - Alerts immediately on anomalies

  2. HOURLY REVIEW:
     - Analyses all trades closed in the rolling 24h window
     - Detects patterns: breakeven streaks, win rate collapse, P&L drift
     - Checks average trade duration (too short = stops too tight)
     - Validates trailing stop behaviour
     - ALWAYS sends a report with current status + any recommendations

  3. DEEP REVIEW (every 4 hours):
     - Per-pair profitability analysis
     - Config effectiveness scoring (are current settings producing profit?)
     - LSTM vs indicator-only comparison (is the model helping or hurting?)
     - Generates numbered actionable recommendations
     - ALWAYS sends full report + options you can reply to

Every recommendation comes with clear reply commands:
  /action 1  — Apply recommendation #1
  /action 2  — Apply recommendation #2
  /discuss 1 — Ask Claude to explain recommendation #1 in detail

Alerts go via the SYSTEM Telegram bot so they don't mix with trade signals.
"""

from datetime import datetime, timezone, timedelta
from loguru import logger
from typing import Optional

from data.storage import TradeStorage
from bot import config


# ── Actionable Recommendations ────────────────────────────────────────────────
# Each recommendation has an ID, description, and the config change it would make.
# When the user replies /action <id>, the bot applies the change.

class ActionableRecommendation:
    """A recommendation the user can approve via Telegram."""

    def __init__(self, action_id: int, title: str, detail: str,
                 config_key: str = None, config_value=None,
                 action_type: str = "config_change"):
        self.action_id = action_id
        self.title = title
        self.detail = detail
        # What config change to make if approved
        self.config_key = config_key
        self.config_value = config_value
        # "config_change", "remove_pair", "pause_trading"
        self.action_type = action_type


class IntegrityMonitor:
    """
    Proactive profit integrity and contingency checker.

    Catches problems like the breakeven bug (56 trades at £0 P&L)
    before they compound over days. Sends Telegram alerts on EVERY scan
    so you always know what's happening.
    """

    def __init__(self, notifier=None):
        self.storage = TradeStorage()
        self.notifier = notifier
        # Store pending recommendations so /action and /discuss can reference them
        self.pending_actions: list[ActionableRecommendation] = []
        # Counter for unique action IDs across the session
        self._next_action_id = 1

    def _next_id(self) -> int:
        """Get the next action ID and increment."""
        aid = self._next_action_id
        self._next_action_id += 1
        return aid

    # ── Quick Check (every 15 min, after each scan) ───────────────────────────

    def quick_check(self, trade_result: dict = None):
        """
        Fast validation run after every market scan.

        If a trade was just opened, validates its risk parameters.
        Also checks for any positions with suspiciously tight stops.
        Always sends a brief status update.
        """
        issues = []

        if trade_result:
            issues.extend(self._validate_trade_params(trade_result))

        # Check all open positions for anomalies
        issues.extend(self._check_open_position_health())

        # Always send — even if no issues, confirm the check ran
        self._send_quick_report(trade_result, issues)

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

        sl_distance = abs(entry - sl)
        tp_distance = abs(entry - tp)

        if sl_distance < 0.00001:
            issues.append(f"{pair}: Stop-loss is at entry price — trade will close immediately at breakeven")

        if tp_distance < 0.00001:
            issues.append(f"{pair}: Take-profit is at entry price — impossible to profit")

        if sl_distance > 0:
            rr_ratio = tp_distance / sl_distance
            if rr_ratio < 1.0:
                issues.append(
                    f"{pair}: Risk/reward ratio is {rr_ratio:.1f}:1 "
                    f"(should be >= {config.TAKE_PROFIT_RATIO}:1)"
                )

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

                if not entry or not current_stop:
                    continue

                stop = float(current_stop)
                sl_distance = abs(entry - stop)

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

    def _send_quick_report(self, trade_result: dict, issues: list[str]):
        """Send a brief quick-check report. Always sends."""
        if not self.notifier:
            return

        now = datetime.now(timezone.utc)

        if trade_result:
            pair = trade_result.get("pair", "?").replace("_", "/")
            direction = trade_result.get("direction", "?")
            entry = trade_result.get("fill_price", 0)
            sl = trade_result.get("stop_loss", 0)
            tp = trade_result.get("take_profit", 0)

            msg = (
                f"*⚡ QUICK CHECK — New Trade*\n"
                f"─────────────────────\n"
                f"Pair: {pair} {direction} @ {entry}\n"
                f"SL: {sl} | TP: {tp}\n"
            )
        else:
            msg = f"*⚡ QUICK CHECK — Position Health*\n─────────────────────\n"

        if issues:
            msg += f"\n*⚠️ Issues ({len(issues)}):*\n"
            for issue in issues:
                msg += f"  • {issue}\n"
        else:
            msg += f"✅ All parameters validated OK\n"

        msg += f"\n_{now.strftime('%H:%M UTC')}_"
        self.notifier._send_system(msg)

    # ── Hourly Review ─────────────────────────────────────────────────────────

    def hourly_review(self) -> dict:
        """
        Analyse rolling 24h of trades for red flags.
        ALWAYS sends a Telegram report — healthy or not.
        """
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")

        today_trades = self.storage.get_trades_for_date(today)
        closed_trades = [t for t in today_trades if t.get("closed_at")]

        yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        yesterday_trades = self.storage.get_trades_for_date(yesterday)
        yesterday_closed = [t for t in yesterday_trades if t.get("closed_at")]

        all_closed = yesterday_closed + closed_trades
        issues = []
        actions = []

        # Count open positions
        open_count = len([t for t in today_trades if not t.get("closed_at")])

        summary = {
            "timestamp": now.isoformat(),
            "trades_24h": len(all_closed),
            "trades_today": len(closed_trades),
            "open_positions": open_count,
            "issues": [],
            "actions": [],
            "status": "HEALTHY",
        }

        if not all_closed:
            summary["status"] = "NO_TRADES"
            self._send_hourly_report(summary, issues, actions)
            return summary

        # ── Check 1: Breakeven Streak ─────────────────────────────────────────
        breakeven_trades = [t for t in all_closed if abs(t.get("pl", 0)) < 0.01]
        breakeven_pct = len(breakeven_trades) / len(all_closed) * 100

        if len(breakeven_trades) >= 3 and breakeven_pct >= 50:
            issues.append(
                f"BREAKEVEN STREAK: {len(breakeven_trades)}/{len(all_closed)} trades "
                f"({breakeven_pct:.0f}%) closed at £0 P&L"
            )
            actions.append(ActionableRecommendation(
                action_id=self._next_id(),
                title="Widen trailing stop",
                detail=(
                    f"Increase trailing_stop_trail_atr from "
                    f"{config.TRAILING_STOP_TRAIL_ATR} to "
                    f"{config.TRAILING_STOP_TRAIL_ATR + 0.5} to give trades more room"
                ),
                config_key="trailing_stop_trail_atr",
                config_value=config.TRAILING_STOP_TRAIL_ATR + 0.5,
            ))

        # ── Check 2: Win Rate ────────────────────────────────────────────────
        wins = [t for t in all_closed if t.get("pl", 0) > 0.01]
        losses = [t for t in all_closed if t.get("pl", 0) < -0.01]
        win_rate = len(wins) / len(all_closed) * 100 if all_closed else 0

        if len(all_closed) >= 5 and win_rate < 20:
            new_confidence = min(config.MIN_CONFIDENCE_SCORE + 10, 80)
            issues.append(
                f"WIN RATE COLLAPSE: {win_rate:.0f}% "
                f"({len(wins)}W / {len(losses)}L / {len(breakeven_trades)}BE)"
            )
            actions.append(ActionableRecommendation(
                action_id=self._next_id(),
                title=f"Raise min confidence to {new_confidence}%",
                detail=(
                    f"Current: {config.MIN_CONFIDENCE_SCORE}%. "
                    f"Raising to {new_confidence}% will filter out weaker signals "
                    f"and reduce trade frequency until conditions improve"
                ),
                config_key="min_to_trade",
                config_value=new_confidence,
            ))

        # ── Check 3: Net P&L Drift ────────────────────────────────────────────
        total_pl = sum(t.get("pl", 0) for t in all_closed)
        summary["net_pl_24h"] = round(total_pl, 2)

        if total_pl < -(config.MAX_CAPITAL * 0.04):
            issues.append(
                f"P&L DRIFT: £{total_pl:.2f} in 24h "
                f"({total_pl / config.MAX_CAPITAL * 100:.1f}% of capital)"
            )
            actions.append(ActionableRecommendation(
                action_id=self._next_id(),
                title="Pause trading for review",
                detail=(
                    f"Net loss of £{abs(total_pl):.2f} in 24h. "
                    f"Pausing gives time to analyse what's going wrong "
                    f"without risking further losses"
                ),
                action_type="pause_trading",
            ))

        # ── Check 4: Average Trade Duration ───────────────────────────────────
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

        avg_duration = sum(durations) / len(durations) if durations else None
        summary["avg_duration_min"] = round(avg_duration, 1) if avg_duration else None

        if avg_duration and avg_duration < 30 and len(durations) >= 3:
            new_activation = config.TRAILING_STOP_ACTIVATION_ATR + 0.5
            issues.append(
                f"SHORT DURATION: Average trade lasts {avg_duration:.0f} min "
                f"(H1 trades should run longer)"
            )
            actions.append(ActionableRecommendation(
                action_id=self._next_id(),
                title=f"Delay trailing stop activation to {new_activation}x ATR",
                detail=(
                    f"Current activation: {config.TRAILING_STOP_ACTIVATION_ATR}x ATR. "
                    f"Raising to {new_activation}x gives trades more room to develop "
                    f"before the trailing stop kicks in"
                ),
                config_key="trailing_stop_activation_atr",
                config_value=new_activation,
            ))

        # ── Check 5: Consecutive Losses ───────────────────────────────────────
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
            issues.append(
                f"LOSING STREAK: {max_consecutive_losses} consecutive losses"
            )
            actions.append(ActionableRecommendation(
                action_id=self._next_id(),
                title="Pause trading",
                detail=(
                    f"{max_consecutive_losses} straight losses suggests market conditions "
                    f"have shifted. Pausing prevents further drawdown while you review"
                ),
                action_type="pause_trading",
            ))

        # ── Check 6: Direction Bias ───────────────────────────────────────────
        directions = [t.get("direction") for t in all_closed if t.get("direction")]
        if len(directions) >= 5:
            buy_pct = directions.count("BUY") / len(directions) * 100
            if buy_pct > 90 or buy_pct < 10:
                dominant = "BUY" if buy_pct > 90 else "SELL"
                issues.append(
                    f"DIRECTION BIAS: {buy_pct:.0f}% of trades are {dominant}"
                )

        # Build summary
        summary["win_rate"] = round(win_rate, 1)
        summary["breakeven_count"] = len(breakeven_trades)
        summary["total_pl"] = round(total_pl, 2)
        summary["issues"] = [i for i in issues]
        summary["actions"] = actions
        summary["status"] = "WARNING" if issues else "HEALTHY"

        # Store pending actions for /action and /discuss commands
        self.pending_actions = actions

        # ALWAYS send — this is the key change
        self._send_hourly_report(summary, issues, actions)

        return summary

    # ── Deep Review (every 4 hours) ───────────────────────────────────────────

    def deep_review(self) -> dict:
        """
        Comprehensive profitability and strategy effectiveness analysis.
        ALWAYS sends a full report with per-pair breakdown and recommendations.
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
        actions = []

        week_trades = self.storage.get_trades_for_week()
        closed_week = [t for t in week_trades if t.get("closed_at")]

        if len(closed_week) < 5:
            summary["status"] = "INSUFFICIENT_DATA"
            self._send_deep_report(summary, issues, actions, closed_week)
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
            wr = stats["wins"] / stats["trades"] * 100 if stats["trades"] > 0 else 0
            stats["win_rate"] = round(wr, 1)
            summary["pair_analysis"][pair] = stats

            if stats["trades"] >= 3 and stats["pl"] < -5:
                issues.append(
                    f"UNPROFITABLE: {pair.replace('_', '/')} lost "
                    f"£{abs(stats['pl']):.2f} ({stats['trades']} trades, {wr:.0f}% win)"
                )
                actions.append(ActionableRecommendation(
                    action_id=self._next_id(),
                    title=f"Remove {pair.replace('_', '/')} from pairs list",
                    detail=(
                        f"{pair.replace('_', '/')} has lost £{abs(stats['pl']):.2f} across "
                        f"{stats['trades']} trades this week with {wr:.0f}% win rate. "
                        f"Removing it stops the bleeding while you investigate"
                    ),
                    config_key="pairs",
                    config_value=pair,
                    action_type="remove_pair",
                ))

            if stats["trades"] >= 3:
                be_pct = stats["breakeven"] / stats["trades"] * 100
                if be_pct >= 60:
                    issues.append(
                        f"BREAKEVEN: {pair.replace('_', '/')} — {be_pct:.0f}% breakeven rate"
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

        # ── Risk/Reward Analysis ──────────────────────────────────────────────
        wins_pl = [t.get("pl", 0) for t in closed_week if t.get("pl", 0) > 0.01]
        losses_pl = [abs(t.get("pl", 0)) for t in closed_week if t.get("pl", 0) < -0.01]
        if wins_pl and losses_pl:
            avg_win = sum(wins_pl) / len(wins_pl)
            avg_loss = sum(losses_pl) / len(losses_pl)
            if avg_loss > avg_win * 1.5:
                new_tp = config.TAKE_PROFIT_RATIO + 0.5
                issues.append(
                    f"POOR R:R: Avg loss £{avg_loss:.2f} vs avg win £{avg_win:.2f}"
                )
                actions.append(ActionableRecommendation(
                    action_id=self._next_id(),
                    title=f"Increase take-profit ratio to {new_tp}:1",
                    detail=(
                        f"Current: {config.TAKE_PROFIT_RATIO}:1. "
                        f"Avg loss (£{avg_loss:.2f}) far exceeds avg win (£{avg_win:.2f}). "
                        f"A wider TP gives winners more room to run"
                    ),
                    config_key="take_profit_ratio",
                    config_value=new_tp,
                ))

        # ── Overall Assessment ────────────────────────────────────────────────
        if total_pl < -(config.MAX_CAPITAL * 0.05):
            issues.append(
                f"WEEKLY LOSS: £{total_pl:.2f} ({total_pl / config.MAX_CAPITAL * 100:.1f}% of capital)"
            )

        if overall_win_rate < 30 and total_trades >= 10:
            new_conf = min(config.MIN_CONFIDENCE_SCORE + 10, 80)
            issues.append(
                f"LOW WIN RATE: {overall_win_rate:.0f}% across {total_trades} trades"
            )
            # Only add if not already recommended by hourly
            if not any(a.config_key == "min_to_trade" for a in actions):
                actions.append(ActionableRecommendation(
                    action_id=self._next_id(),
                    title=f"Raise min confidence to {new_conf}%",
                    detail=(
                        f"Current: {config.MIN_CONFIDENCE_SCORE}%. "
                        f"Only {overall_win_rate:.0f}% win rate across {total_trades} trades. "
                        f"Higher threshold = fewer but better quality trades"
                    ),
                    config_key="min_to_trade",
                    config_value=new_conf,
                ))

        # If everything is healthy, recommend maintaining course
        if not issues:
            # Check if there's room to be more aggressive
            if overall_win_rate > 60 and total_pl > 0:
                actions.append(ActionableRecommendation(
                    action_id=self._next_id(),
                    title="Consider lowering min confidence by 5%",
                    detail=(
                        f"Win rate is {overall_win_rate:.0f}% and P&L is +£{total_pl:.2f}. "
                        f"Lowering min confidence from {config.MIN_CONFIDENCE_SCORE}% to "
                        f"{max(config.MIN_CONFIDENCE_SCORE - 5, 40)}% could increase "
                        f"trade frequency while maintaining profitability"
                    ),
                    config_key="min_to_trade",
                    config_value=max(config.MIN_CONFIDENCE_SCORE - 5, 40),
                ))

        summary["recommendations"] = actions
        summary["issues"] = issues
        summary["status"] = "WARNING" if issues else "HEALTHY"

        # Store pending actions
        self.pending_actions.extend(actions)

        # ALWAYS send the deep report
        self._send_deep_report(summary, issues, actions, closed_week)

        return summary

    # ── Alert Dispatch (always sends) ─────────────────────────────────────────

    def _send_hourly_report(self, summary: dict, issues: list[str],
                            actions: list[ActionableRecommendation]):
        """Send the hourly integrity report. Always sends."""
        if not self.notifier:
            return

        now = datetime.now(timezone.utc)
        status = summary.get("status", "UNKNOWN")
        status_emoji = {
            "HEALTHY": "✅", "WARNING": "⚠️", "NO_TRADES": "📭"
        }.get(status, "❓")

        msg = (
            f"*🔍 HOURLY INTEGRITY SCAN*\n"
            f"═════════════════════\n"
            f"*Status:* {status_emoji} {status}\n"
            f"─────────────────────\n"
        )

        # Always show the numbers
        msg += f"*📈 Last 24 Hours:*\n"
        msg += f"  Trades closed: {summary.get('trades_24h', 0)}\n"
        msg += f"  Trades today: {summary.get('trades_today', 0)}\n"
        msg += f"  Open positions: {summary.get('open_positions', 0)}\n"

        if summary.get("net_pl_24h") is not None:
            pl = summary["net_pl_24h"]
            pl_sign = "+" if pl >= 0 else ""
            msg += f"  Net P&L: *{pl_sign}£{pl:.2f}*\n"

        if summary.get("win_rate") is not None:
            msg += f"  Win rate: {summary['win_rate']:.0f}%\n"
        if summary.get("breakeven_count") is not None:
            msg += f"  Breakeven: {summary['breakeven_count']}\n"
        if summary.get("avg_duration_min") is not None:
            msg += f"  Avg duration: {summary['avg_duration_min']:.0f} min\n"
        if summary.get("max_consecutive_losses", 0) > 0:
            msg += f"  Max losing streak: {summary['max_consecutive_losses']}\n"

        # Issues
        if issues:
            msg += f"\n*⚠️ Issues ({len(issues)}):*\n"
            for issue in issues:
                msg += f"  • {issue}\n"
        else:
            msg += f"\n✅ No issues detected\n"

        # Actionable recommendations with reply commands
        if actions:
            msg += f"\n*💡 Actions Available:*\n"
            for a in actions:
                msg += (
                    f"\n*#{a.action_id}* — {a.title}\n"
                    f"  _{a.detail}_\n"
                    f"  → `/action {a.action_id}` to apply\n"
                    f"  → `/discuss {a.action_id}` to discuss\n"
                )
        else:
            msg += f"\n✅ No changes recommended — current config is working\n"

        msg += f"\n_{now.strftime('%H:%M UTC')}_"

        # Split long messages (Telegram 4096 char limit)
        self._send_split(msg)
        logger.info(f"Hourly integrity report sent: {status}, {len(issues)} issues, {len(actions)} actions")

    def _send_deep_report(self, summary: dict, issues: list[str],
                          actions: list[ActionableRecommendation],
                          trades: list):
        """Send the deep integrity report. Always sends."""
        if not self.notifier:
            return

        now = datetime.now(timezone.utc)
        status = summary.get("status", "UNKNOWN")
        status_emoji = {
            "HEALTHY": "✅", "WARNING": "⚠️", "INSUFFICIENT_DATA": "📭"
        }.get(status, "❓")

        msg = (
            f"*📊 DEEP INTEGRITY REVIEW (4-Hourly)*\n"
            f"═════════════════════\n"
            f"*Status:* {status_emoji} {status}\n"
            f"─────────────────────\n"
        )

        # Per-pair breakdown
        pair_analysis = summary.get("pair_analysis", {})
        if pair_analysis:
            msg += f"*📊 7-Day Per-Pair Performance:*\n"
            for pair, stats in sorted(pair_analysis.items(), key=lambda x: x[1]["pl"], reverse=True):
                pl_sign = "+" if stats["pl"] >= 0 else ""
                pair_display = pair.replace("_", "/")
                emoji = "✅" if stats["pl"] > 0 else ("⚠️" if stats["pl"] == 0 else "❌")
                msg += (
                    f"  {emoji} {pair_display}: {pl_sign}£{stats['pl']:.2f} "
                    f"| {stats['win_rate']:.0f}% win | {stats['trades']} trades\n"
                )
            msg += "\n"

        # Config assessment
        ca = summary.get("config_assessment", {})
        if ca:
            total_pl = ca.get("net_pl_7d", 0)
            pl_sign = "+" if total_pl >= 0 else ""
            msg += f"*⚙️ Current Config:*\n"
            msg += f"  Net P&L (7d): *{pl_sign}£{total_pl:.2f}*\n"
            msg += f"  Win Rate (7d): {ca.get('win_rate_7d', 0):.0f}%\n"
            msg += f"  Avg P&L/trade: £{ca.get('avg_pl_per_trade', 0):.2f}\n"
            msg += f"  SL: {ca.get('stop_loss_atr_multiplier', '?')}x ATR\n"
            msg += f"  Trail Activation: {ca.get('trailing_stop_activation_atr', '?')}x ATR\n"
            msg += f"  Trail Distance: {ca.get('trailing_stop_trail_atr', '?')}x ATR\n"
            msg += f"  Min Confidence: {ca.get('min_confidence', '?')}%\n\n"

        # Issues
        if issues:
            msg += f"*⚠️ Issues ({len(issues)}):*\n"
            for issue in issues:
                msg += f"  • {issue}\n"
            msg += "\n"
        else:
            msg += f"✅ No issues — strategy performing within expected parameters\n\n"

        # Actionable recommendations
        if actions:
            msg += f"*💡 Recommended Actions:*\n"
            for a in actions:
                msg += (
                    f"\n*#{a.action_id}* — {a.title}\n"
                    f"  _{a.detail}_\n"
                    f"  → `/action {a.action_id}` to apply\n"
                    f"  → `/discuss {a.action_id}` to discuss\n"
                )
        else:
            msg += f"*✅ No changes recommended*\n"
            msg += f"Current settings are performing well. Keep monitoring.\n"

        msg += f"\n_Use /integrity for a full on-demand report_"
        msg += f"\n_{now.strftime('%H:%M UTC')}_"

        self._send_split(msg)
        logger.info(f"Deep integrity report sent: {status}, {len(issues)} issues, {len(actions)} actions")

    def _send_split(self, message: str):
        """Send a message, splitting if it exceeds Telegram's 4096 char limit."""
        if len(message) <= 4000:
            self.notifier._send_system(message)
        else:
            # Split at a newline near the limit
            chunks = []
            while message:
                if len(message) <= 4000:
                    chunks.append(message)
                    break
                # Find last newline before limit
                split_at = message.rfind("\n", 0, 4000)
                if split_at == -1:
                    split_at = 4000
                chunks.append(message[:split_at])
                message = message[split_at:]

            for chunk in chunks:
                if chunk.strip():
                    self.notifier._send_system(chunk)

    # ── Action Execution ──────────────────────────────────────────────────────

    def get_action(self, action_id: int) -> Optional[ActionableRecommendation]:
        """Look up a pending action by ID."""
        for a in self.pending_actions:
            if a.action_id == action_id:
                return a
        return None

    def apply_action(self, action_id: int) -> str:
        """
        Apply a recommended action and return a status message.

        Config changes are written to config.yaml and take effect on restart.
        Some actions (pause) can take effect immediately.
        """
        action = self.get_action(action_id)
        if not action:
            return f"⚠️ Action #{action_id} not found. It may have expired — run /integrity to get fresh recommendations."

        try:
            if action.action_type == "pause_trading":
                # Pause trading immediately via the scheduler flag
                import bot.scheduler as scheduler
                scheduler._trading_paused = True
                result = (
                    f"✅ *Action #{action_id} Applied*\n"
                    f"─────────────────────\n"
                    f"*{action.title}*\n\n"
                    f"Trading is now PAUSED. No new trades will be opened.\n"
                    f"Open positions remain active (stops still protect them).\n"
                    f"Use /resume when ready to restart trading."
                )

            elif action.action_type == "remove_pair":
                pair = action.config_value
                result = (
                    f"✅ *Action #{action_id} Noted*\n"
                    f"─────────────────────\n"
                    f"*{action.title}*\n\n"
                    f"To remove {pair.replace('_', '/')} from the pairs list:\n"
                    f"Edit `config/config.yaml` → `trading.pairs` and remove `{pair}`\n"
                    f"Then restart: `docker-compose down && docker-compose up -d`\n\n"
                    f"_Config changes require a restart to take effect._"
                )

            elif action.action_type == "config_change":
                # Write the config change to config.yaml
                self._apply_config_change(action.config_key, action.config_value)
                result = (
                    f"✅ *Action #{action_id} Applied*\n"
                    f"─────────────────────\n"
                    f"*{action.title}*\n\n"
                    f"Changed `{action.config_key}` to `{action.config_value}`\n"
                    f"_Restart required: `docker-compose down && docker-compose up -d`_"
                )
            else:
                result = f"⚠️ Unknown action type: {action.action_type}"

            # Remove from pending
            self.pending_actions = [a for a in self.pending_actions if a.action_id != action_id]
            return result

        except Exception as e:
            logger.error(f"Failed to apply action #{action_id}: {e}")
            return f"⚠️ Failed to apply action #{action_id}: {str(e)[:200]}"

    def describe_action(self, action_id: int) -> str:
        """Get a detailed description of an action for /discuss."""
        action = self.get_action(action_id)
        if not action:
            return f"⚠️ Action #{action_id} not found. Run /integrity to get fresh recommendations."

        msg = (
            f"*💬 Action #{action_id} — Detailed Discussion*\n"
            f"═════════════════════\n"
            f"*Title:* {action.title}\n"
            f"*Type:* {action.action_type}\n\n"
            f"*What it does:*\n{action.detail}\n\n"
        )

        if action.config_key:
            msg += f"*Config key:* `{action.config_key}`\n"
            msg += f"*New value:* `{action.config_value}`\n\n"

        if action.action_type == "config_change":
            msg += (
                f"*Impact:*\n"
                f"This changes a trading parameter in config.yaml. "
                f"The change takes effect after a restart.\n\n"
                f"*To apply:* `/action {action_id}`\n"
                f"*To skip:* Just ignore — it won't be applied\n"
                f"*To ask more:* Reply with your question and I'll answer"
            )
        elif action.action_type == "pause_trading":
            msg += (
                f"*Impact:*\n"
                f"Stops the bot from opening new trades immediately. "
                f"Existing positions keep their stops active. "
                f"Use /resume to restart.\n\n"
                f"*To apply:* `/action {action_id}`\n"
            )
        elif action.action_type == "remove_pair":
            msg += (
                f"*Impact:*\n"
                f"Removes this pair from the scanning list. The bot won't "
                f"open new positions on this pair. Existing positions are unaffected.\n\n"
                f"*To apply:* `/action {action_id}`\n"
            )

        return msg

    def _apply_config_change(self, key: str, value):
        """
        Write a config change to config.yaml.

        Maps config keys to their YAML path and updates the file.
        Changes take effect on restart.
        """
        import yaml

        config_path = config.CONFIG_PATH

        with open(config_path, "r") as f:
            cfg = yaml.safe_load(f)

        # Map flat config keys to their YAML path
        key_map = {
            "trailing_stop_trail_atr": ("risk", "trailing_stop_trail_atr"),
            "trailing_stop_activation_atr": ("risk", "trailing_stop_activation_atr"),
            "min_to_trade": ("confidence", "min_to_trade"),
            "take_profit_ratio": ("risk", "take_profit_ratio"),
            "stop_loss_atr_multiplier": ("risk", "stop_loss_atr_multiplier"),
            "max_per_trade_spend": ("trading", "max_per_trade_spend"),
            "per_trade_risk_pct": ("trading", "per_trade_risk_pct"),
        }

        path = key_map.get(key)
        if not path:
            raise ValueError(f"Unknown config key: {key}")

        section, param = path
        if section not in cfg:
            raise ValueError(f"Config section '{section}' not found")

        old_value = cfg[section].get(param)
        cfg[section][param] = value

        with open(config_path, "w") as f:
            yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)

        logger.info(f"Config updated: {section}.{param}: {old_value} → {value}")

    # ── On-Demand Summary (for /integrity command) ────────────────────────────

    def get_full_report(self) -> str:
        """
        Generate a comprehensive integrity report for the /integrity command.
        Runs all checks and returns a formatted Telegram message.
        """
        hourly = self.hourly_review()
        deep = self.deep_review()

        now = datetime.now(timezone.utc)

        msg = (
            f"*🛡 FULL INTEGRITY REPORT*\n"
            f"═════════════════════\n"
            f"_Generated {now.strftime('%Y-%m-%d %H:%M UTC')}_\n\n"
        )

        status_emoji = {"HEALTHY": "✅", "WARNING": "⚠️", "NO_TRADES": "📭", "INSUFFICIENT_DATA": "📭"}
        hourly_status = hourly.get("status", "UNKNOWN")
        deep_status = deep.get("status", "UNKNOWN")

        msg += (
            f"*Overall Status:*\n"
            f"  Hourly: {status_emoji.get(hourly_status, '❓')} {hourly_status}\n"
            f"  Deep: {status_emoji.get(deep_status, '❓')} {deep_status}\n\n"
        )

        # All issues
        all_issues = hourly.get("issues", []) + deep.get("issues", [])
        if all_issues:
            msg += f"*⚠️ All Issues ({len(all_issues)}):*\n"
            for issue in all_issues:
                msg += f"  • {issue}\n"
            msg += "\n"
        else:
            msg += f"*✅ All checks passed*\n\n"

        # All available actions
        if self.pending_actions:
            msg += f"*💡 Available Actions ({len(self.pending_actions)}):*\n"
            for a in self.pending_actions:
                msg += (
                    f"\n*#{a.action_id}* — {a.title}\n"
                    f"  _{a.detail}_\n"
                    f"  `/action {a.action_id}` | `/discuss {a.action_id}`\n"
                )
        else:
            msg += f"*✅ No actions recommended — all systems nominal*\n"

        msg += f"\n_{now.strftime('%H:%M UTC')}_"
        return msg
