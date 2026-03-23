"""
bot/analytics/integrity_monitor.py — Automated Trading Remediation System
─────────────────────────────────────────────────────────────────────────────
Proactively detects trading anomalies, diagnoses root causes, and presents
targeted fixes via Telegram inline buttons. The user taps Approve/Reject —
no restart needed, changes apply immediately at runtime.

Detection → Recommendation → Telegram Inline Buttons → Approve → Apply

  1. QUICK CHECK (after every 15-min scan):
     - Validates SL/TP distances aren't too tight (spread vs ATR sanity)
     - Catches trades that opened with broken risk parameters
     - Alerts immediately on anomalies

  2. HOURLY REVIEW:
     - Analyses all trades closed in the rolling 24h window
     - Smart losing streak analysis (diagnoses WHY — direction, pair, confidence)
     - Direction performance alert (detects one-sided failure)
     - Auto-pause on sustained weekly losses (fires autonomously)
     - ALWAYS sends a report with inline buttons for any recommendations

  3. DEEP REVIEW (every 4 hours):
     - Per-pair profitability analysis
     - Config effectiveness scoring (are current settings producing profit?)
     - LSTM vs indicator-only comparison (is the model helping or hurting?)
     - Generates numbered actionable recommendations with inline buttons

  4. WEEKLY STRATEGY REVIEW (Monday 00:15 UTC):
     - Compares this week vs last week: trade count, win rate, P&L
     - Flags pairs that flipped from profitable to unprofitable
     - Recommends defensive adjustments if P&L declined >20%

  5. DAILY LSTM HEALTH (08:00 UTC):
     - Checks model file age, prediction accuracy (24h/7d)
     - Computes LSTM edge (confidence delta vs indicator-only)
     - Recommends shadow mode toggle based on accuracy

All recommendations come with inline [✅ Approve] [❌ Reject] buttons.
Config changes apply immediately at runtime AND persist to config.yaml.
"""

from datetime import datetime, timezone, timedelta
from loguru import logger
from typing import Optional
from pathlib import Path

from data.storage import TradeStorage
from bot import config


# ── Actionable Recommendations ────────────────────────────────────────────────
# Each recommendation has an ID, description, and the config change it would make.
# When the user taps Approve, the bot applies the change at runtime.

class ActionableRecommendation:
    """A recommendation the user can approve via Telegram inline buttons."""

    def __init__(self, action_id: int, title: str, detail: str,
                 config_key: str = None, config_value=None,
                 action_type: str = "config_change"):
        self.action_id = action_id
        self.title = title
        self.detail = detail
        # What config change to make if approved
        self.config_key = config_key
        self.config_value = config_value
        # Action types: "config_change", "runtime_config_change", "remove_pair",
        # "disable_direction", "enable_direction", "pause_trading"
        self.action_type = action_type


class IntegrityMonitor:
    """
    Automated trading remediation system.

    Detects problems (losing streaks, direction failures, weekly P&L decline),
    diagnoses root causes, and presents targeted fixes via Telegram inline buttons.
    Changes apply immediately at runtime — no restart needed.
    """

    def __init__(self, notifier=None):
        self.storage = TradeStorage()
        self.notifier = notifier
        # Store pending recommendations so approve/reject can reference them
        self.pending_actions: list[ActionableRecommendation] = []
        # Counter for unique action IDs across the session
        self._next_action_id = 1
        # Auto-approve mode: when True, the bot applies recommendations automatically
        # instead of waiting for user approval. Sends a notification of what changed.
        self.auto_approve = config._remediation_cfg.get("auto_approve", True)

        # ── Optimisation Feedback Loop ────────────────────────────────────────
        # Tracks applied actions and their pre-fix metrics so we can measure
        # whether each fix actually improved things. If not, we escalate.
        #
        # Format: [{
        #   "action_title": str, "action_type": str, "config_key": str,
        #   "config_value": any, "applied_at": datetime,
        #   "pre_fix_metrics": {"win_rate": float, "pl": float, "trades": int},
        #   "review_after": datetime,  # When to check if it helped
        #   "escalation_level": int,   # 0 = first fix, 1 = escalated, etc.
        # }]
        self._applied_fixes: list[dict] = []
        # How long to wait before reviewing a fix's impact (hours)
        self._review_delay_hours = config._remediation_cfg.get("review_delay_hours", 4)

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

    # ── Smart Losing Streak Analysis ──────────────────────────────────────────
    # Replaces simple "5 losses → pause" with root-cause diagnosis

    def _analyse_losing_streak(self, losses: list[dict]) -> list[ActionableRecommendation]:
        """Diagnose WHY a losing streak is happening and recommend targeted fixes.

        Instead of blindly pausing, looks at the pattern:
        - >70% one direction → disable that direction
        - >60% one pair → remove that pair
        - Low avg confidence → raise min confidence
        - >60% stopped out → widen SL ATR multiplier
        - >60% EOD closed → lower overnight threshold
        """
        actions = []
        if len(losses) < config.LOSING_STREAK_SMART_ANALYSIS_MIN:
            return actions

        total = len(losses)

        # ── Direction analysis: are losses concentrated in BUY or SELL?
        buy_losses = [t for t in losses if t.get("direction") == "BUY"]
        sell_losses = [t for t in losses if t.get("direction") == "SELL"]

        if len(buy_losses) / total > 0.70:
            actions.append(ActionableRecommendation(
                action_id=self._next_id(),
                title="Disable BUY trades",
                detail=(
                    f"{len(buy_losses)}/{total} recent losses are BUY trades ({len(buy_losses)/total*100:.0f}%). "
                    f"Disabling BUY prevents further losses while you investigate the root cause."
                ),
                config_key="BUY",
                action_type="disable_direction",
            ))
        elif len(sell_losses) / total > 0.70:
            actions.append(ActionableRecommendation(
                action_id=self._next_id(),
                title="Disable SELL trades",
                detail=(
                    f"{len(sell_losses)}/{total} recent losses are SELL trades ({len(sell_losses)/total*100:.0f}%). "
                    f"Disabling SELL prevents further losses while you investigate the root cause."
                ),
                config_key="SELL",
                action_type="disable_direction",
            ))

        # ── Pair analysis: are losses concentrated in one pair?
        pair_counts = {}
        for t in losses:
            pair = t.get("pair", "Unknown")
            pair_counts[pair] = pair_counts.get(pair, 0) + 1

        for pair, count in pair_counts.items():
            if count / total > 0.60 and count >= 3:
                actions.append(ActionableRecommendation(
                    action_id=self._next_id(),
                    title=f"Remove {pair.replace('_', '/')} from pairs list",
                    detail=(
                        f"{count}/{total} losses are on {pair.replace('_', '/')} ({count/total*100:.0f}%). "
                        f"Removing it stops the bleeding while market conditions are unfavourable."
                    ),
                    config_key="pairs",
                    config_value=pair,
                    action_type="remove_pair",
                ))

        # ── Confidence analysis: are we trading on weak signals?
        confidences = [t.get("confidence_score", 0) for t in losses if t.get("confidence_score")]
        if confidences:
            avg_conf = sum(confidences) / len(confidences)
            if avg_conf < config.MIN_CONFIDENCE_SCORE + 10:
                new_conf = min(config.MIN_CONFIDENCE_SCORE + 10, 80)
                actions.append(ActionableRecommendation(
                    action_id=self._next_id(),
                    title=f"Raise min confidence to {new_conf}%",
                    detail=(
                        f"Average confidence on losing trades: {avg_conf:.0f}%. "
                        f"Raising from {config.MIN_CONFIDENCE_SCORE}% to {new_conf}% "
                        f"filters out weaker signals."
                    ),
                    config_key="min_to_trade",
                    config_value=new_conf,
                    action_type="runtime_config_change",
                ))

        # ── Stop-loss analysis: are most losses from stops being hit?
        stopped = [t for t in losses if "stop" in (t.get("close_reason") or "").lower()]
        if len(stopped) / total > 0.60:
            new_sl = config.STOP_LOSS_ATR_MULTIPLIER + 0.5
            actions.append(ActionableRecommendation(
                action_id=self._next_id(),
                title=f"Widen stop-loss to {new_sl}x ATR",
                detail=(
                    f"{len(stopped)}/{total} losses hit stop-loss ({len(stopped)/total*100:.0f}%). "
                    f"Widening from {config.STOP_LOSS_ATR_MULTIPLIER}x to {new_sl}x ATR "
                    f"gives trades more room to develop."
                ),
                config_key="stop_loss_atr_multiplier",
                config_value=new_sl,
                action_type="runtime_config_change",
            ))

        # ── EOD analysis: are most losses from end-of-day closures?
        eod_closed = [t for t in losses if "eod" in (t.get("close_reason") or "").lower()
                      or "end of day" in (t.get("close_reason") or "").lower()
                      or "force close" in (t.get("close_reason") or "").lower()]
        if len(eod_closed) / total > 0.60:
            new_threshold = max(config.HOLD_OVERNIGHT_THRESHOLD - 15, 60)
            actions.append(ActionableRecommendation(
                action_id=self._next_id(),
                title=f"Lower overnight threshold to {new_threshold}%",
                detail=(
                    f"{len(eod_closed)}/{total} losses were EOD forced closures ({len(eod_closed)/total*100:.0f}%). "
                    f"Lowering overnight hold threshold from {config.HOLD_OVERNIGHT_THRESHOLD}% to "
                    f"{new_threshold}% lets profitable trades survive overnight."
                ),
                config_key="hold_overnight_threshold",
                config_value=new_threshold,
                action_type="runtime_config_change",
            ))

        return actions

    # ── Direction Performance Alert ───────────────────────────────────────────

    def _check_direction_performance(self, trades_7d: list[dict]) -> tuple[list[str], list[ActionableRecommendation]]:
        """Check if BUY or SELL has a dangerously low win rate over 7 days.

        If either direction has <30% win rate with >=5 trades, recommend disabling it.
        """
        issues = []
        actions = []

        for direction in ("BUY", "SELL"):
            dir_trades = [t for t in trades_7d if t.get("direction") == direction and t.get("closed_at")]
            if len(dir_trades) < 5:
                continue

            wins = sum(1 for t in dir_trades if t.get("pl", 0) > 0.01)
            win_rate = wins / len(dir_trades) * 100

            if win_rate < config.DIRECTION_WINRATE_ALERT_THRESHOLD:
                losses_count = len(dir_trades) - wins
                issues.append(
                    f"{direction} win rate: {win_rate:.0f}% ({wins}W/{losses_count}L in 7d)"
                )
                # Only recommend disabling if not already disabled
                if direction not in config.DISABLED_DIRECTIONS:
                    actions.append(ActionableRecommendation(
                        action_id=self._next_id(),
                        title=f"Disable {direction} trades",
                        detail=(
                            f"{direction} has {win_rate:.0f}% win rate over 7d ({wins}W/{losses_count}L). "
                            f"Disabling prevents further losses while you investigate the root cause."
                        ),
                        config_key=direction,
                        action_type="disable_direction",
                    ))

        return issues, actions

    # ── Weekly P&L Auto-Pause ─────────────────────────────────────────────────

    # ── No-Trade Threshold Adjustment ───────────────────────────────────────

    def _check_no_trade_threshold(self, now: datetime):
        """Lower confidence threshold if no trades have fired in 24+ hours.

        If the confidence threshold is too high, the bot sits idle while markets
        move. This detects that situation and drops confidence by 5% to let
        more trades through. Won't go below the configured floor (default 70%).

        Only triggers:
          - During weekday market hours (Mon-Fri, before 22:00 UTC)
          - If no trades at all in the last 24 hours
          - If current confidence > floor (70%)
          - Maximum once per 24 hours
        """
        floor = config._remediation_cfg.get("min_confidence_floor", 70)

        if config.MIN_CONFIDENCE_SCORE <= floor:
            return  # Already at floor, don't lower further

        try:
            # Check if ANY trades (open or closed) happened in last 24h
            yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
            today = now.strftime("%Y-%m-%d")
            yesterday_trades = self.storage.get_trades_for_date(yesterday)
            today_trades = self.storage.get_trades_for_date(today)
            all_recent = yesterday_trades + today_trades

            if len(all_recent) > 0:
                return  # Trades happened, threshold is fine

            # No trades in 24h during market hours — threshold is too restrictive
            old_conf = config.MIN_CONFIDENCE_SCORE
            new_conf = max(old_conf - 5, floor)

            if new_conf == old_conf:
                return  # Already at floor

            config.apply_runtime_config("min_to_trade", new_conf)

            logger.warning(
                f"NO-TRADE ADJUSTMENT: {old_conf}% → {new_conf}% "
                f"(no trades in 24h, floor: {floor}%)"
            )

            if self.notifier:
                self.notifier._send_system(
                    f"*📉 CONFIDENCE AUTO-LOWERED*\n"
                    f"─────────────────────\n"
                    f"*Was:* {old_conf}% → *Now:* {new_conf}%\n"
                    f"*Reason:* Zero trades in 24 hours during market hours\n"
                    f"*Floor:* {floor}% (won't go lower)\n\n"
                    f"_The threshold was too restrictive. Lowering by 5% "
                    f"to let stronger signals through._\n"
                    f"\n_{now.strftime('%H:%M UTC')}_"
                )

        except Exception as e:
            logger.error(f"No-trade threshold check failed: {e}")

    # ── Weekly P&L Auto-Pause ─────────────────────────────────────────────────

    def _check_weekly_pl_autopause(self) -> bool:
        """Auto-pause trading if weekly P&L exceeds the loss threshold.

        This is the ONLY action that fires autonomously (no approval needed).
        Returns True if trading was paused.
        """
        try:
            week_trades = self.storage.get_trades_for_week()
            closed = [t for t in week_trades if t.get("closed_at")]
            if not closed:
                return False

            weekly_pl = sum(t.get("pl", 0) for t in closed)

            if weekly_pl < config.AUTOPAUSE_WEEKLY_LOSS_THRESHOLD:
                # Auto-pause immediately — no approval needed for capital protection
                import bot.scheduler as scheduler
                if not scheduler._trading_paused:
                    scheduler._trading_paused = True
                    logger.warning(
                        f"AUTO-PAUSE: Weekly P&L £{weekly_pl:.2f} breached threshold "
                        f"£{config.AUTOPAUSE_WEEKLY_LOSS_THRESHOLD}"
                    )
                    if self.notifier:
                        msg = (
                            f"*🚨 AUTO-PAUSE TRIGGERED*\n"
                            f"═════════════════════\n"
                            f"*Weekly P&L:* £{weekly_pl:.2f}\n"
                            f"*Threshold:* £{config.AUTOPAUSE_WEEKLY_LOSS_THRESHOLD}\n"
                            f"─────────────────────\n"
                            f"Trading has been *automatically paused* to protect capital.\n"
                            f"Open positions remain active (stops still protect them).\n\n"
                            f"Use /resume when ready to restart trading.\n"
                            f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_"
                        )
                        self.notifier._send_system(msg)
                    return True

        except Exception as e:
            logger.error(f"Weekly P&L auto-pause check failed: {e}")

        return False

    # ── Hourly Review ─────────────────────────────────────────────────────────

    def hourly_review(self) -> dict:
        """
        Analyse rolling 24h of trades for red flags.
        Uses smart analysis to diagnose root causes.
        ALWAYS sends a Telegram report with inline buttons when actions exist.

        Also reviews previously applied fixes to check if they're working.
        If a fix didn't improve things, it escalates automatically.
        """
        # Review any previously applied fixes before generating new recommendations
        if self.auto_approve and self._applied_fixes:
            try:
                self.review_applied_fixes()
            except Exception as e:
                logger.error(f"Fix review failed: {e}")

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
            # Check if the threshold is too restrictive — if markets are open and
            # we've had zero trades for 24h+, lower confidence by 5% automatically.
            # Only triggers during weekday market hours (not weekends).
            if self.auto_approve and now.weekday() < 5 and 0 <= now.hour < 22:
                self._check_no_trade_threshold(now)

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
            # Only recommend widening if below the cap (2.0x ATR).
            # Beyond 2.0x the trail is too wide to protect profits.
            if config.TRAILING_STOP_TRAIL_ATR < 2.0:
                actions.append(ActionableRecommendation(
                    action_id=self._next_id(),
                    title="Widen trailing stop",
                    detail=(
                        f"Increase trailing_stop_trail_atr from "
                        f"{config.TRAILING_STOP_TRAIL_ATR} to "
                        f"{min(config.TRAILING_STOP_TRAIL_ATR + 0.5, 2.0)} to give trades more room"
                    ),
                    config_key="trailing_stop_trail_atr",
                    config_value=min(config.TRAILING_STOP_TRAIL_ATR + 0.5, 2.0),
                    action_type="runtime_config_change",
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
                action_type="runtime_config_change",
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
            issues.append(
                f"SHORT DURATION: Average trade lasts {avg_duration:.0f} min "
                f"(H1 trades should run longer)"
            )
            # Only recommend if below cap (3.0x ATR)
            if config.TRAILING_STOP_ACTIVATION_ATR < 3.0:
                new_activation = min(config.TRAILING_STOP_ACTIVATION_ATR + 0.5, 3.0)
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
                    action_type="runtime_config_change",
                ))

        # ── Check 5: Smart Losing Streak (replaces simple 5-loss → pause) ────
        sorted_trades = sorted(all_closed, key=lambda t: t.get("closed_at", ""))
        max_consecutive_losses = 0
        current_streak = 0
        streak_trades = []
        for t in sorted_trades:
            if t.get("pl", 0) < -0.01:
                current_streak += 1
                streak_trades.append(t)
                max_consecutive_losses = max(max_consecutive_losses, current_streak)
            else:
                if current_streak >= config.LOSING_STREAK_SMART_ANALYSIS_MIN:
                    # Analyse this completed streak
                    streak_actions = self._analyse_losing_streak(streak_trades)
                    actions.extend(streak_actions)
                current_streak = 0
                streak_trades = []

        # Check if the streak is still ongoing at the end
        if current_streak >= config.LOSING_STREAK_SMART_ANALYSIS_MIN:
            streak_actions = self._analyse_losing_streak(streak_trades)
            actions.extend(streak_actions)

        summary["max_consecutive_losses"] = max_consecutive_losses

        if max_consecutive_losses >= config.LOSING_STREAK_SMART_ANALYSIS_MIN:
            issues.append(
                f"LOSING STREAK: {max_consecutive_losses} consecutive losses"
            )

        # ── Check 6: Direction Performance (7d) ───────────────────────────────
        try:
            week_trades = self.storage.get_trades_for_week()
            dir_issues, dir_actions = self._check_direction_performance(week_trades)
            issues.extend(dir_issues)
            # Only add direction actions if not already recommended by streak analysis
            existing_dir_types = {a.config_key for a in actions if a.action_type == "disable_direction"}
            for da in dir_actions:
                if da.config_key not in existing_dir_types:
                    actions.extend([da])
        except Exception as e:
            logger.debug(f"Direction performance check failed: {e}")

        # ── Check 7: Weekly P&L Auto-Pause ────────────────────────────────────
        self._check_weekly_pl_autopause()

        # ── Check 8: Show disabled directions/pairs status ────────────────────
        if config.DISABLED_DIRECTIONS:
            dirs = ", ".join(sorted(config.DISABLED_DIRECTIONS))
            issues.append(f"DISABLED DIRECTIONS: {dirs} (use /action to re-enable)")
            for d in config.DISABLED_DIRECTIONS:
                actions.append(ActionableRecommendation(
                    action_id=self._next_id(),
                    title=f"Re-enable {d} trades",
                    detail=f"Remove {d} from disabled directions and resume trading in that direction.",
                    config_key=d,
                    action_type="enable_direction",
                ))

        if config.DISABLED_PAIRS:
            pairs = ", ".join(p.replace("_", "/") for p in sorted(config.DISABLED_PAIRS))
            issues.append(f"DISABLED PAIRS: {pairs}")

        # Build summary
        summary["win_rate"] = round(win_rate, 1)
        summary["breakeven_count"] = len(breakeven_trades)
        summary["total_pl"] = round(total_pl, 2)
        summary["issues"] = [i for i in issues]
        summary["actions"] = actions
        summary["status"] = "WARNING" if issues else "HEALTHY"

        # Append new actions to pending list (don't overwrite — deep review
        # actions would be lost when the next hourly run fires)
        self.pending_actions.extend(actions)

        # ALWAYS send — with inline buttons when actions exist
        self._send_hourly_report(summary, issues, actions)

        return summary

    # ── Deep Review (every 4 hours) ───────────────────────────────────────────

    def deep_review(self) -> dict:
        """
        Comprehensive profitability and strategy effectiveness analysis.
        ALWAYS sends a full report with per-pair breakdown and inline buttons.
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
                    action_type="runtime_config_change",
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
                    action_type="runtime_config_change",
                ))

        # If everything is healthy, recommend maintaining course
        if not issues:
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
                    action_type="runtime_config_change",
                ))

        summary["recommendations"] = actions
        summary["issues"] = issues
        summary["status"] = "WARNING" if issues else "HEALTHY"

        # Store pending actions
        self.pending_actions.extend(actions)

        # ALWAYS send the deep report with inline buttons
        self._send_deep_report(summary, issues, actions, closed_week)

        return summary

    # ── Weekly Strategy Review (Monday 00:15 UTC) ─────────────────────────────

    def weekly_strategy_review(self):
        """Compare this week vs last week and flag deterioration.

        Runs Monday 00:15 UTC. Sends a Telegram report with comparison table
        and defensive recommendations if P&L declined >20%.
        """
        now = datetime.now(timezone.utc)
        issues = []
        actions = []

        try:
            # This week's trades (Mon-Sun just ended)
            this_week = self.storage.get_trades_for_week()
            this_closed = [t for t in this_week if t.get("closed_at")]

            # Last week's trades — get trades from 7-14 days ago
            last_week_start = (now - timedelta(days=14)).strftime("%Y-%m-%d")
            last_week_end = (now - timedelta(days=7)).strftime("%Y-%m-%d")
            last_week = self.storage.get_trades_for_date_range(last_week_start, last_week_end)
            last_closed = [t for t in last_week if t.get("closed_at")]

            # This week stats
            tw_pl = sum(t.get("pl", 0) for t in this_closed)
            tw_trades = len(this_closed)
            tw_wins = sum(1 for t in this_closed if t.get("pl", 0) > 0.01)
            tw_wr = tw_wins / tw_trades * 100 if tw_trades > 0 else 0

            # Last week stats
            lw_pl = sum(t.get("pl", 0) for t in last_closed)
            lw_trades = len(last_closed)
            lw_wins = sum(1 for t in last_closed if t.get("pl", 0) > 0.01)
            lw_wr = lw_wins / lw_trades * 100 if lw_trades > 0 else 0

            # Per-pair comparison
            tw_pair_pl = {}
            for t in this_closed:
                pair = t.get("pair", "?")
                tw_pair_pl[pair] = tw_pair_pl.get(pair, 0) + t.get("pl", 0)

            lw_pair_pl = {}
            for t in last_closed:
                pair = t.get("pair", "?")
                lw_pair_pl[pair] = lw_pair_pl.get(pair, 0) + t.get("pl", 0)

            # Flag pairs that flipped from profitable to unprofitable
            flipped_pairs = []
            for pair in set(list(tw_pair_pl.keys()) + list(lw_pair_pl.keys())):
                lw_p = lw_pair_pl.get(pair, 0)
                tw_p = tw_pair_pl.get(pair, 0)
                if lw_p > 0 and tw_p < -2:  # Was profitable, now losing > £2
                    flipped_pairs.append(pair)

            if flipped_pairs:
                for pair in flipped_pairs:
                    issues.append(
                        f"FLIPPED: {pair.replace('_', '/')} was profitable last week, "
                        f"now £{tw_pair_pl[pair]:.2f}"
                    )
                    actions.append(ActionableRecommendation(
                        action_id=self._next_id(),
                        title=f"Remove {pair.replace('_', '/')} from pairs list",
                        detail=(
                            f"{pair.replace('_', '/')} flipped from +£{lw_pair_pl.get(pair, 0):.2f} last week "
                            f"to £{tw_pair_pl[pair]:.2f} this week. Consider removing until conditions improve."
                        ),
                        config_key="pairs",
                        config_value=pair,
                        action_type="remove_pair",
                    ))

            # If P&L declined >20%, recommend defensive adjustments
            if lw_pl > 0 and tw_pl < lw_pl * 0.8:
                pl_decline = ((tw_pl - lw_pl) / abs(lw_pl)) * 100 if lw_pl != 0 else 0
                issues.append(f"P&L DECLINE: {pl_decline:.0f}% week-over-week")
                new_conf = min(config.MIN_CONFIDENCE_SCORE + 5, 80)
                actions.append(ActionableRecommendation(
                    action_id=self._next_id(),
                    title=f"Raise min confidence to {new_conf}% (defensive)",
                    detail=(
                        f"P&L dropped {pl_decline:.0f}% week-over-week "
                        f"(£{lw_pl:.2f} → £{tw_pl:.2f}). "
                        f"Tightening confidence filter reduces exposure during unfavourable conditions."
                    ),
                    config_key="min_to_trade",
                    config_value=new_conf,
                    action_type="runtime_config_change",
                ))
            elif lw_pl <= 0 and tw_pl < lw_pl - 5:
                issues.append(f"LOSSES DEEPENING: £{lw_pl:.2f} last week → £{tw_pl:.2f} this week")

            # Build message
            tw_pl_sign = "+" if tw_pl >= 0 else ""
            lw_pl_sign = "+" if lw_pl >= 0 else ""

            msg = (
                f"*📊 WEEKLY STRATEGY REVIEW*\n"
                f"═════════════════════\n"
                f"*This Week:*\n"
                f"  Trades: {tw_trades} | Win rate: {tw_wr:.0f}% | P&L: *{tw_pl_sign}£{tw_pl:.2f}*\n"
                f"*Last Week:*\n"
                f"  Trades: {lw_trades} | Win rate: {lw_wr:.0f}% | P&L: *{lw_pl_sign}£{lw_pl:.2f}*\n"
                f"─────────────────────\n"
            )

            # Per-pair table
            all_pairs = sorted(set(list(tw_pair_pl.keys()) + list(lw_pair_pl.keys())))
            if all_pairs:
                msg += f"*Per-Pair Comparison:*\n"
                for pair in all_pairs:
                    tw_p = tw_pair_pl.get(pair, 0)
                    lw_p = lw_pair_pl.get(pair, 0)
                    emoji = "✅" if tw_p > lw_p else ("❌" if tw_p < lw_p else "➡️")
                    pair_display = pair.replace("_", "/")
                    msg += (
                        f"  {emoji} {pair_display}: "
                        f"{'+'if lw_p >= 0 else ''}£{lw_p:.2f} → "
                        f"{'+'if tw_p >= 0 else ''}£{tw_p:.2f}\n"
                    )
                msg += "\n"

            if issues:
                msg += f"*⚠️ Issues ({len(issues)}):*\n"
                for issue in issues:
                    msg += f"  • {issue}\n"
                msg += "\n"
            else:
                msg += f"✅ No significant deterioration detected\n\n"

            # Recommendations with inline buttons
            if actions:
                msg += f"*💡 Recommendations:*\n"
                for a in actions:
                    msg += (
                        f"\n*#{a.action_id}* — {a.title}\n"
                        f"  _{a.detail}_\n"
                    )

            msg += f"\n_{now.strftime('%H:%M UTC')}_"

            self.pending_actions.extend(actions)

            if actions and self.notifier:
                self.notifier.send_action_buttons(msg, actions)
            else:
                self._send_split(msg)

            logger.info(f"Weekly strategy review sent: {len(issues)} issues, {len(actions)} actions")

        except Exception as e:
            logger.error(f"Weekly strategy review failed: {e}")

    # ── Daily LSTM Health Summary (08:00 UTC) ─────────────────────────────────

    def daily_lstm_health(self):
        """Check LSTM model health and recommend shadow mode toggle if needed.

        Runs daily at 08:00 UTC. Checks:
        - Model file age (last retrain timestamp)
        - Prediction accuracy (24h/7d)
        - LSTM edge (confidence delta vs indicator-only)
        - Recommends shadow mode toggle based on accuracy
        """
        now = datetime.now(timezone.utc)
        actions = []

        try:
            # ── Model file age
            model_path = config.DATA_DIR / "models" / "lstm_v1.pt"
            model_age_str = "Unknown"
            model_age_hours = None
            if model_path.exists():
                import os
                mtime = datetime.fromtimestamp(os.path.getmtime(model_path), tz=timezone.utc)
                model_age_hours = (now - mtime).total_seconds() / 3600
                if model_age_hours < 24:
                    model_age_str = f"{model_age_hours:.1f}h ago"
                else:
                    model_age_str = f"{model_age_hours / 24:.1f}d ago"
            else:
                model_age_str = "No model file found"

            # ── Prediction accuracy from DB
            acc_24h = None
            acc_7d = None
            total_24h = 0
            total_7d = 0
            lstm_edge = None

            try:
                from data.storage import DB_PATH
                import sqlite3
                conn = sqlite3.connect(str(DB_PATH), timeout=10)
                conn.row_factory = sqlite3.Row

                # 24h accuracy
                cutoff_24h = (now - timedelta(hours=24)).isoformat()
                rows_24h = conn.execute(
                    "SELECT outcome FROM predictions WHERE timestamp > ? AND outcome IS NOT NULL",
                    (cutoff_24h,)
                ).fetchall()
                total_24h = len(rows_24h)
                if total_24h > 0:
                    correct_24h = sum(1 for r in rows_24h if r["outcome"] == "correct")
                    acc_24h = correct_24h / total_24h * 100

                # 7d accuracy
                cutoff_7d = (now - timedelta(days=7)).isoformat()
                rows_7d = conn.execute(
                    "SELECT outcome FROM predictions WHERE timestamp > ? AND outcome IS NOT NULL",
                    (cutoff_7d,)
                ).fetchall()
                total_7d = len(rows_7d)
                if total_7d > 0:
                    correct_7d = sum(1 for r in rows_7d if r["outcome"] == "correct")
                    acc_7d = correct_7d / total_7d * 100

                # LSTM edge: average (lstm_score - indicator_score) for recent predictions
                edge_rows = conn.execute(
                    "SELECT confidence_score, indicator_only_score FROM predictions "
                    "WHERE timestamp > ? AND indicator_only_score IS NOT NULL",
                    (cutoff_7d,)
                ).fetchall()
                if edge_rows:
                    edges = [r["confidence_score"] - r["indicator_only_score"] for r in edge_rows
                             if r["confidence_score"] is not None and r["indicator_only_score"] is not None]
                    if edges:
                        lstm_edge = sum(edges) / len(edges)

                conn.close()
            except Exception as e:
                logger.debug(f"LSTM health DB query failed: {e}")

            # ── Recommendations based on accuracy
            if acc_7d is not None and total_7d >= 10:
                if acc_7d < 45 and not config.LSTM_SHADOW_MODE:
                    # Accuracy poor — recommend shadow mode
                    actions.append(ActionableRecommendation(
                        action_id=self._next_id(),
                        title="Enable LSTM shadow mode",
                        detail=(
                            f"7d accuracy is {acc_7d:.0f}% (below 45% threshold). "
                            f"Shadow mode disables LSTM from influencing trade decisions "
                            f"while it continues learning. Re-enable when accuracy improves."
                        ),
                        config_key="lstm_shadow_mode",
                        config_value=True,
                        action_type="runtime_config_change",
                    ))
                elif acc_7d > 55 and config.LSTM_SHADOW_MODE:
                    # Accuracy good — recommend disabling shadow mode
                    actions.append(ActionableRecommendation(
                        action_id=self._next_id(),
                        title="Disable LSTM shadow mode (go live)",
                        detail=(
                            f"7d accuracy is {acc_7d:.0f}% (above 55% threshold). "
                            f"The LSTM is adding value — disabling shadow mode gives it "
                            f"its full 50% weight in confidence scoring."
                        ),
                        config_key="lstm_shadow_mode",
                        config_value=False,
                        action_type="runtime_config_change",
                    ))

            # ── Build message
            shadow_str = "ON (not influencing trades)" if config.LSTM_SHADOW_MODE else "OFF (live, 50% weight)"
            acc_24h_str = f"{acc_24h:.0f}% ({total_24h} predictions)" if acc_24h is not None else "Insufficient data"
            acc_7d_str = f"{acc_7d:.0f}% ({total_7d} predictions)" if acc_7d is not None else "Insufficient data"
            edge_str = f"{lstm_edge:+.1f}pp" if lstm_edge is not None else "N/A"

            msg = (
                f"*🧠 DAILY LSTM HEALTH*\n"
                f"═════════════════════\n"
                f"*Last Retrain:* {model_age_str}\n"
                f"*Shadow Mode:* {shadow_str}\n"
                f"─────────────────────\n"
                f"*Prediction Accuracy:*\n"
                f"  24h: {acc_24h_str}\n"
                f"  7d: {acc_7d_str}\n"
                f"*LSTM Edge:* {edge_str}\n"
            )

            if model_age_hours and model_age_hours > 12:
                msg += f"\n⚠️ Model is {model_age_str} old — check retrain schedule\n"

            if actions:
                msg += f"\n*💡 Recommendations:*\n"
                for a in actions:
                    msg += f"\n*#{a.action_id}* — {a.title}\n  _{a.detail}_\n"

            msg += f"\n_{now.strftime('%H:%M UTC')}_"

            self.pending_actions.extend(actions)

            if actions and self.notifier:
                self.notifier.send_action_buttons(msg, actions)
            else:
                self._send_split(msg)

            logger.info(f"Daily LSTM health summary sent: accuracy_7d={acc_7d_str}, edge={edge_str}")

        except Exception as e:
            logger.error(f"Daily LSTM health check failed: {e}")

    # ── Auto-Approve ────────────────────────────────────────────────────────────

    def _auto_approve_actions(self, actions: list[ActionableRecommendation]):
        """Apply all recommended actions automatically, record pre-fix metrics,
        and schedule a review to check if the fix actually helped.

        The feedback loop:
          1. Snapshot current metrics (win rate, P&L, trade count)
          2. Apply the fix
          3. Schedule a review in N hours
          4. At review time, compare post-fix metrics to pre-fix
          5. If not improved → escalate (try a stronger fix)
          6. If improved → keep the change and log success
        """
        if not actions or not self.auto_approve:
            return

        now = datetime.now(timezone.utc)

        # Snapshot current metrics before applying fixes
        pre_fix_metrics = self._snapshot_metrics()

        applied = []
        for action in list(actions):
            try:
                result = self.apply_action(action.action_id)
                applied.append(f"✅ #{action.action_id} — {action.title}")
                logger.info(f"Auto-approved action #{action.action_id}: {action.title}")

                # Record the fix for later review
                self._applied_fixes.append({
                    "action_title": action.title,
                    "action_type": action.action_type,
                    "config_key": action.config_key,
                    "config_value": action.config_value,
                    "applied_at": now,
                    "pre_fix_metrics": pre_fix_metrics,
                    "review_after": now + timedelta(hours=self._review_delay_hours),
                    "escalation_level": 0,
                    "reviewed": False,
                })
            except Exception as e:
                applied.append(f"⚠️ #{action.action_id} — {action.title} (failed: {e})")
                logger.error(f"Auto-approve failed for #{action.action_id}: {e}")

        if applied and self.notifier:
            msg = (
                f"*🤖 AUTO-OPTIMISE — {len(applied)} actions applied*\n"
                f"═════════════════════\n"
                + "\n".join(applied)
                + f"\n\n_Will review impact in {self._review_delay_hours}h. "
                  f"If no improvement, will escalate automatically._"
                + f"\n_{now.strftime('%H:%M UTC')}_"
            )
            self.notifier._send_system(msg)

    def review_applied_fixes(self):
        """Review previously applied fixes to see if they actually helped.

        Called by the hourly review. For each fix that's past its review time:
        - Compare current metrics to pre-fix metrics
        - If improved: log success, remove from tracking
        - If not improved: escalate with a stronger action

        Escalation ladder per problem type:
          Level 0: Initial fix (e.g., raise confidence by 10%)
          Level 1: Stronger fix (e.g., raise confidence by another 10%)
          Level 2: Aggressive fix (e.g., disable the direction or pair entirely)
          Level 3: Pause trading — something is fundamentally wrong
        """
        now = datetime.now(timezone.utc)
        still_tracking = []

        for fix in self._applied_fixes:
            if fix["reviewed"]:
                continue

            # Not yet time to review
            if now < fix["review_after"]:
                still_tracking.append(fix)
                continue

            # Time to review — compare metrics
            current_metrics = self._snapshot_metrics()
            pre = fix["pre_fix_metrics"]
            improved = self._compare_metrics(pre, current_metrics)

            if improved:
                logger.info(
                    f"Fix review PASSED: '{fix['action_title']}' — "
                    f"win rate {pre['win_rate']:.0f}% → {current_metrics['win_rate']:.0f}%, "
                    f"P&L £{pre['recent_pl']:.2f} → £{current_metrics['recent_pl']:.2f}"
                )
                if self.notifier:
                    self.notifier._send_system(
                        f"*✅ FIX WORKING*\n"
                        f"_{fix['action_title']}_ applied {self._review_delay_hours}h ago\n"
                        f"Win rate: {pre['win_rate']:.0f}% → {current_metrics['win_rate']:.0f}%\n"
                        f"Recent P&L: £{pre['recent_pl']:.2f} → £{current_metrics['recent_pl']:.2f}\n"
                        f"_Keeping this change._"
                    )
                fix["reviewed"] = True
            else:
                # Not improved — escalate
                level = fix["escalation_level"] + 1
                logger.warning(
                    f"Fix review FAILED: '{fix['action_title']}' — "
                    f"escalating to level {level}"
                )
                escalation = self._escalate(fix, level, current_metrics)
                fix["reviewed"] = True

                if escalation:
                    still_tracking.append(escalation)

        self._applied_fixes = [f for f in self._applied_fixes if not f["reviewed"]] + still_tracking

    def _snapshot_metrics(self) -> dict:
        """Snapshot current trading metrics for before/after comparison."""
        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

            today_trades = self.storage.get_trades_for_date(today)
            yesterday_trades = self.storage.get_trades_for_date(yesterday)
            recent = [t for t in (yesterday_trades + today_trades) if t.get("closed_at")]

            if not recent:
                return {"win_rate": 0, "recent_pl": 0, "trade_count": 0}

            wins = sum(1 for t in recent if (t.get("pl") or 0) > 0.01)
            total_pl = sum(t.get("pl", 0) for t in recent)

            return {
                "win_rate": wins / len(recent) * 100 if recent else 0,
                "recent_pl": total_pl,
                "trade_count": len(recent),
            }
        except Exception as e:
            logger.error(f"Metrics snapshot failed: {e}")
            return {"win_rate": 0, "recent_pl": 0, "trade_count": 0}

    def _compare_metrics(self, pre: dict, post: dict) -> bool:
        """Compare pre-fix and post-fix metrics. Returns True if improved.

        Improvement means ANY of:
        - Win rate increased by >= 5 percentage points
        - Recent P&L improved (less negative or more positive)
        - No new trades yet (too early to judge — give benefit of doubt)
        """
        # If no trades happened since the fix, it's too early — don't escalate
        if post["trade_count"] <= pre["trade_count"]:
            return True  # Benefit of the doubt

        # Win rate improved
        if post["win_rate"] >= pre["win_rate"] + 5:
            return True

        # P&L improved
        if post["recent_pl"] > pre["recent_pl"]:
            return True

        # Both got worse
        return False

    def _escalate(self, fix: dict, level: int, current_metrics: dict) -> dict:
        """Escalate a failed fix to a stronger action.

        Escalation ladder:
          Level 1: Tighten the same parameter further
          Level 2: Disable the problematic direction or pair
          Level 3: Pause trading entirely
        """
        now = datetime.now(timezone.utc)
        action_type = fix["action_type"]
        config_key = fix["config_key"]

        escalation_msg = None

        if level >= 3:
            # Maximum escalation — pause trading
            import bot.scheduler as scheduler
            if not scheduler._trading_paused:
                scheduler._trading_paused = True
                escalation_msg = (
                    f"*🚨 ESCALATION LEVEL 3 — TRADING PAUSED*\n"
                    f"═════════════════════\n"
                    f"Previous fix _{fix['action_title']}_ didn't improve results "
                    f"after {self._review_delay_hours}h.\n"
                    f"Win rate: {current_metrics['win_rate']:.0f}% | "
                    f"P&L: £{current_metrics['recent_pl']:.2f}\n\n"
                    f"Trading is PAUSED until you /resume manually.\n"
                    f"_Three escalation levels exhausted — human review needed._"
                )

        elif level == 2:
            # Aggressive: disable direction or pair
            if action_type == "runtime_config_change" and config_key == "min_to_trade":
                # Was raising confidence — now disable the worst direction
                week_trades = self.storage.get_trades_for_week()
                closed = [t for t in week_trades if t.get("closed_at")]
                buy_pl = sum(t.get("pl", 0) for t in closed if t.get("direction") == "BUY")
                sell_pl = sum(t.get("pl", 0) for t in closed if t.get("direction") == "SELL")
                worst_dir = "SELL" if sell_pl < buy_pl else "BUY"

                if worst_dir not in config.DISABLED_DIRECTIONS:
                    config.DISABLED_DIRECTIONS.add(worst_dir)
                    escalation_msg = (
                        f"*⚠️ ESCALATION LEVEL 2 — {worst_dir} DISABLED*\n"
                        f"Raising confidence didn't help. {worst_dir} trades "
                        f"lost £{min(buy_pl, sell_pl):.2f} this week.\n"
                        f"Disabled {worst_dir} until conditions improve.\n"
                        f"_Will review in {self._review_delay_hours}h._"
                    )
            elif action_type in ("disable_direction", "remove_pair"):
                # Already disabled something — try pausing
                return self._escalate(fix, 3, current_metrics)
            else:
                # Generic level 2: raise confidence by another 10%
                new_conf = min(config.MIN_CONFIDENCE_SCORE + 10, 95)
                config.apply_runtime_config("min_to_trade", new_conf)
                escalation_msg = (
                    f"*⚠️ ESCALATION LEVEL 2 — Confidence raised to {new_conf}%*\n"
                    f"Previous fix didn't improve results. Tightening filter further.\n"
                    f"_Will review in {self._review_delay_hours}h._"
                )

        elif level == 1:
            # Moderate: tighten the same parameter
            if action_type == "runtime_config_change" and config_key == "min_to_trade":
                new_conf = min(config.MIN_CONFIDENCE_SCORE + 5, 95)
                config.apply_runtime_config("min_to_trade", new_conf)
                escalation_msg = (
                    f"*⚠️ ESCALATION LEVEL 1 — Confidence raised to {new_conf}%*\n"
                    f"_{fix['action_title']}_ didn't improve results after "
                    f"{self._review_delay_hours}h. Tightening further.\n"
                    f"_Will review in {self._review_delay_hours}h._"
                )
            elif config_key == "stop_loss_atr_multiplier":
                new_sl = config.STOP_LOSS_ATR_MULTIPLIER + 0.5
                config.apply_runtime_config("stop_loss_atr_multiplier", new_sl)
                escalation_msg = (
                    f"*⚠️ ESCALATION LEVEL 1 — SL widened to {new_sl}x ATR*\n"
                    f"Previous SL change didn't help. Widening further.\n"
                    f"_Will review in {self._review_delay_hours}h._"
                )
            elif config_key == "trailing_stop_trail_atr":
                new_trail = config.TRAILING_STOP_TRAIL_ATR + 0.5
                config.apply_runtime_config("trailing_stop_trail_atr", new_trail)
                escalation_msg = (
                    f"*⚠️ ESCALATION LEVEL 1 — Trail widened to {new_trail}x ATR*\n"
                    f"Previous trail change didn't help. Widening further.\n"
                    f"_Will review in {self._review_delay_hours}h._"
                )
            else:
                # Generic: raise confidence
                new_conf = min(config.MIN_CONFIDENCE_SCORE + 5, 95)
                config.apply_runtime_config("min_to_trade", new_conf)
                escalation_msg = (
                    f"*⚠️ ESCALATION LEVEL 1 — Confidence raised to {new_conf}%*\n"
                    f"_{fix['action_title']}_ didn't improve. Raising confidence.\n"
                    f"_Will review in {self._review_delay_hours}h._"
                )

        if escalation_msg:
            logger.warning(f"Escalation level {level}: {escalation_msg[:100]}")
            if self.notifier:
                self.notifier._send_system(escalation_msg)

            # Track the escalation for further review
            return {
                "action_title": f"Escalation L{level} from: {fix['action_title']}",
                "action_type": fix["action_type"],
                "config_key": fix["config_key"],
                "config_value": fix["config_value"],
                "applied_at": now,
                "pre_fix_metrics": current_metrics,
                "review_after": now + timedelta(hours=self._review_delay_hours),
                "escalation_level": level,
                "reviewed": False,
            }

        return None

    # ── Alert Dispatch (always sends) ─────────────────────────────────────────

    def _send_hourly_report(self, summary: dict, issues: list[str],
                            actions: list[ActionableRecommendation]):
        """Send the hourly integrity report. Always sends, with inline buttons when actions exist."""
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

        # Actionable recommendations
        if actions:
            msg += f"\n*💡 Actions Available:*\n"
            for a in actions:
                msg += (
                    f"\n*#{a.action_id}* — {a.title}\n"
                    f"  _{a.detail}_\n"
                )
        else:
            msg += f"\n✅ No changes recommended — current config is working\n"

        msg += f"\n_{now.strftime('%H:%M UTC')}_"

        # Send with inline buttons if there are actions, otherwise plain
        if actions:
            self._send_split_with_buttons(msg, actions)
        else:
            self._send_split(msg)

        logger.info(f"Hourly integrity report sent: {status}, {len(issues)} issues, {len(actions)} actions")

        # Auto-approve: apply actions immediately without waiting for user
        self._auto_approve_actions(actions)

    def _send_deep_report(self, summary: dict, issues: list[str],
                          actions: list[ActionableRecommendation],
                          trades: list):
        """Send the deep integrity report. Always sends, with inline buttons when actions exist."""
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
                )
        else:
            msg += f"*✅ No changes recommended*\n"
            msg += f"Current settings are performing well. Keep monitoring.\n"

        msg += f"\n_Use /integrity for a full on-demand report_"
        msg += f"\n_{now.strftime('%H:%M UTC')}_"

        # Send with inline buttons if there are actions
        if actions:
            self._send_split_with_buttons(msg, actions)
        else:
            self._send_split(msg)

        logger.info(f"Deep integrity report sent: {status}, {len(issues)} issues, {len(actions)} actions")

        # Auto-approve: apply actions immediately without waiting for user
        self._auto_approve_actions(actions)

    def _send_split(self, message: str):
        """Send a message, splitting if it exceeds Telegram's 4096 char limit."""
        if len(message) <= 4000:
            self.notifier._send_system(message)
        else:
            chunks = []
            while message:
                if len(message) <= 4000:
                    chunks.append(message)
                    break
                split_at = message.rfind("\n", 0, 4000)
                if split_at == -1:
                    split_at = 4000
                chunks.append(message[:split_at])
                message = message[split_at:]

            for chunk in chunks:
                if chunk.strip():
                    self.notifier._send_system(chunk)

    def _send_split_with_buttons(self, message: str, actions: list[ActionableRecommendation]):
        """Send a message with inline buttons. If too long, split text and put buttons on last chunk."""
        if len(message) <= 4000:
            self.notifier.send_action_buttons(message, actions)
        else:
            # Split into chunks, put buttons on the last one
            chunks = []
            remaining = message
            while remaining:
                if len(remaining) <= 4000:
                    chunks.append(remaining)
                    break
                split_at = remaining.rfind("\n", 0, 4000)
                if split_at == -1:
                    split_at = 4000
                chunks.append(remaining[:split_at])
                remaining = remaining[split_at:]

            # Send all but last without buttons
            for chunk in chunks[:-1]:
                if chunk.strip():
                    self.notifier._send_system(chunk)

            # Last chunk gets the buttons
            if chunks and chunks[-1].strip():
                self.notifier.send_action_buttons(chunks[-1], actions)

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

        Supports runtime config changes (immediate effect, no restart),
        direction enable/disable, pair removal, and trading pause.
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

            elif action.action_type == "disable_direction":
                # Add direction to disabled set — blocks all trades in that direction
                direction = action.config_key
                config.DISABLED_DIRECTIONS.add(direction)
                logger.info(f"Direction DISABLED at runtime: {direction}")
                result = (
                    f"✅ *Action #{action_id} Applied*\n"
                    f"─────────────────────\n"
                    f"*{action.title}*\n\n"
                    f"{direction} trades are now *disabled*.\n"
                    f"The bot will skip all {direction} signals until re-enabled.\n"
                    f"Existing {direction} positions are unaffected.\n"
                    f"_This is a runtime change — no restart needed._"
                )

            elif action.action_type == "enable_direction":
                # Remove direction from disabled set
                direction = action.config_key
                config.DISABLED_DIRECTIONS.discard(direction)
                logger.info(f"Direction RE-ENABLED at runtime: {direction}")
                result = (
                    f"✅ *Action #{action_id} Applied*\n"
                    f"─────────────────────\n"
                    f"*{action.title}*\n\n"
                    f"{direction} trades are now *re-enabled*.\n"
                    f"The bot will resume opening {direction} positions."
                )

            elif action.action_type == "remove_pair":
                # Remove pair from config.PAIRS at runtime and add to DISABLED_PAIRS
                pair = action.config_value
                if pair in config.PAIRS:
                    config.PAIRS.remove(pair)
                config.DISABLED_PAIRS.add(pair)
                logger.info(f"Pair REMOVED at runtime: {pair}")
                result = (
                    f"✅ *Action #{action_id} Applied*\n"
                    f"─────────────────────\n"
                    f"*{action.title}*\n\n"
                    f"{pair.replace('_', '/')} has been *removed* from the pairs list.\n"
                    f"The bot will stop scanning this pair immediately.\n"
                    f"_This is a runtime change — no restart needed._"
                )

            elif action.action_type == "runtime_config_change":
                # Apply config change at runtime AND persist to YAML
                change_desc = config.apply_runtime_config(action.config_key, action.config_value)
                result = (
                    f"✅ *Action #{action_id} Applied*\n"
                    f"─────────────────────\n"
                    f"*{action.title}*\n\n"
                    f"Changed: `{change_desc}`\n"
                    f"_Effective immediately — also persisted to config.yaml._"
                )

            elif action.action_type == "config_change":
                # Legacy: write to YAML only (requires restart)
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

        type_descriptions = {
            "runtime_config_change": (
                "*Impact:*\n"
                "This changes a trading parameter *immediately at runtime* — "
                "no restart needed. The change is also persisted to config.yaml "
                "so it survives restarts.\n\n"
                f"*To apply:* `/action {action_id}` or tap ✅ Approve\n"
            ),
            "config_change": (
                "*Impact:*\n"
                "This changes a trading parameter in config.yaml. "
                "The change takes effect after a restart.\n\n"
                f"*To apply:* `/action {action_id}` or tap ✅ Approve\n"
            ),
            "pause_trading": (
                "*Impact:*\n"
                "Stops the bot from opening new trades immediately. "
                "Existing positions keep their stops active. "
                "Use /resume to restart.\n\n"
                f"*To apply:* `/action {action_id}` or tap ✅ Approve\n"
            ),
            "remove_pair": (
                "*Impact:*\n"
                "Removes this pair from the scanning list *immediately*. "
                "The bot won't open new positions on this pair. "
                "Existing positions are unaffected. No restart needed.\n\n"
                f"*To apply:* `/action {action_id}` or tap ✅ Approve\n"
            ),
            "disable_direction": (
                "*Impact:*\n"
                "Blocks all trades in this direction *immediately*. "
                "The bot will skip these signals until re-enabled. "
                "Existing positions are unaffected. No restart needed.\n\n"
                f"*To apply:* `/action {action_id}` or tap ✅ Approve\n"
            ),
            "enable_direction": (
                "*Impact:*\n"
                "Re-enables trading in this direction *immediately*. "
                "The bot will resume opening positions in this direction.\n\n"
                f"*To apply:* `/action {action_id}` or tap ✅ Approve\n"
            ),
        }

        msg += type_descriptions.get(action.action_type, f"*To apply:* `/action {action_id}`\n")
        msg += f"*To skip:* Just ignore or tap ❌ Reject — it won't be applied"

        return msg

    def _apply_config_change(self, key: str, value):
        """
        Write a config change to config.yaml (legacy — requires restart).

        For runtime changes, use config.apply_runtime_config() instead.
        """
        import yaml

        config_path = config.CONFIG_PATH

        with open(config_path, "r") as f:
            cfg = yaml.safe_load(f)

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

        Resets pending actions and IDs so on-demand reports start from #1,
        giving clean action numbering for /action and /discuss commands.
        """
        # Reset so the full report starts with clean action IDs (#1, #2, ...)
        self.pending_actions = []
        self._next_action_id = 1

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

        # Disabled directions/pairs status
        if config.DISABLED_DIRECTIONS:
            msg += f"*🚫 Disabled Directions:* {', '.join(sorted(config.DISABLED_DIRECTIONS))}\n"
        if config.DISABLED_PAIRS:
            msg += f"*🚫 Disabled Pairs:* {', '.join(p.replace('_', '/') for p in sorted(config.DISABLED_PAIRS))}\n"
        if config.DISABLED_DIRECTIONS or config.DISABLED_PAIRS:
            msg += "\n"

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
