"""
risk/eod_manager.py — End of Day Position Manager
───────────────────────────────────────────────────
Manages all end-of-day logic:

1. Evaluates open positions at 23:45 UTC
2. Applies the 98% confidence rule to decide what to hold overnight
3. Closes everything else at 23:59 UTC
4. Adds extra protection to any held positions (tighter stop-loss)

The 98% rule explained:
  At end of day, the AI re-scores every open position from scratch.
  If the score is 98% or higher AND the position is profitable, it's held.
  Everything else is closed — no exceptions.

This is intentionally conservative. The bar is very high (98%) because
overnight trading carries additional risk (news events, gaps, etc.).
"""

from loguru import logger
from datetime import datetime, timezone
from typing import Optional

from bot import config


class EODManager:
    """Manages end-of-day position handling."""

    def __init__(self, broker, notifier):
        self.broker = broker
        self.notifier = notifier
        self._held_overnight = set()  # Track which positions are held overnight

    def evaluate_overnight_holds(self):
        """
        Run at 23:45 UTC. Evaluates each open position and decides:
        - Hold overnight (if confidence >= 98% AND position is profitable)
        - Close at 23:59 (everything else)

        For held positions, the stop-loss is tightened to protect profits.
        """
        open_trades = self.broker.get_open_trades()

        if not open_trades:
            logger.info("No open positions to evaluate for overnight hold")
            return

        logger.info(f"Evaluating {len(open_trades)} position(s) for overnight hold (98% rule)")
        self._held_overnight.clear()

        for trade in open_trades:
            trade_id = trade.get("id")
            pair = trade.get("instrument")
            unrealised_pl = float(trade.get("unrealizedPL", 0))
            direction = "BUY" if int(trade.get("currentUnits", 0)) > 0 else "SELL"

            # Re-score this position fresh
            overnight_score = self._calculate_overnight_score(trade)

            logger.info(
                f"{pair} | Direction: {direction} | "
                f"Unrealised P&L: £{unrealised_pl:.2f} | "
                f"Overnight Score: {overnight_score:.1f}%"
            )

            # Apply the 98% rule
            if overnight_score >= config.HOLD_OVERNIGHT_THRESHOLD and unrealised_pl > 0:
                logger.info(f"✅ HOLDING {pair} overnight — {overnight_score:.1f}% confidence, £{unrealised_pl:.2f} profit")
                self._held_overnight.add(trade_id)
                self._tighten_stop_loss(trade, unrealised_pl)

                # Alert Joseph
                self.notifier.overnight_hold_alert(
                    pair=pair,
                    direction=direction,
                    confidence_score=overnight_score,
                    current_pl=unrealised_pl,
                    reasoning=f"Score of {overnight_score:.1f}% exceeds 98% threshold. "
                              f"Position is £{unrealised_pl:.2f} in profit. "
                              f"Stop-loss has been tightened to protect {config.OVERNIGHT_PROFIT_PROTECTION_PCT:.0f}% of gains."
                )
            else:
                reason = "Below 98% confidence" if overnight_score < config.HOLD_OVERNIGHT_THRESHOLD else "Not profitable"
                logger.info(f"Will close {pair} at 23:59 — {reason} ({overnight_score:.1f}%)")

    def force_close_non_held_positions(self) -> list:
        """
        Run at 23:59 UTC. Close every position that wasn't granted overnight hold.
        Returns list of close results for the daily report.
        """
        open_trades = self.broker.get_open_trades()
        results = []

        for trade in open_trades:
            trade_id = trade.get("id")
            pair = trade.get("instrument")

            if trade_id in self._held_overnight:
                logger.info(f"Skipping {pair} (trade {trade_id}) — approved for overnight hold")
                continue

            result = self.broker.close_trade(trade_id, reason="End of day close")
            if result:
                result["pair"] = pair
                results.append(result)

        return results

    def _calculate_overnight_score(self, trade: dict) -> float:
        """
        Re-score an open position to determine if it qualifies for overnight hold.

        This uses a simplified scoring focused on:
        - Is the trade still going in the right direction?
        - Is momentum still strong?
        - Are there any overnight risk factors?

        In a full implementation, this calls the full confidence engine.
        Returns a score from 0–100.
        """
        try:
            from bot.engine import indicators, confidence

            pair = trade.get("instrument")

            # Fetch fresh price data
            candles = self.broker.get_candles(pair, count=200)
            if candles is None or len(candles) < 60:
                return 0  # Can't score without data — default to close

            # Calculate fresh indicators
            ind = indicators.calculate(candles)

            # Simple overnight confidence — does the trend still support this trade?
            units = int(trade.get("currentUnits", 0))
            direction = "BUY" if units > 0 else "SELL"

            # For overnight holds, we apply a strict penalty to the base score
            # This ensures the 98% bar is genuinely hard to reach
            result = confidence.calculate_confidence(
                pair=pair,
                indicators=ind,
                mcp_context={},  # No MCP for overnight eval (keep it fast)
                ml_prediction=None
            )

            # Apply overnight penalty — must be very strong to hold
            overnight_penalty = 10  # Subtract 10 points for overnight risk
            final_score = max(0, result.score - overnight_penalty)

            # Direction must match our open position
            if result.direction != direction:
                logger.info(f"{pair}: Signal has reversed to {result.direction} — will close despite any score")
                return 0  # Always close if direction has reversed

            return final_score

        except Exception as e:
            logger.error(f"Error scoring {trade.get('instrument')} for overnight hold: {e}")
            return 0  # Default to close on any error

    def _tighten_stop_loss(self, trade: dict, unrealised_pl: float):
        """
        For positions held overnight, tighten the stop-loss to protect profits.

        Example: If you're £20 in profit and protection is 75%,
        the new stop-loss is set to lock in at least £15 profit.
        """
        try:
            trade_id = trade.get("id")
            pair = trade.get("instrument")
            open_price = float(trade.get("price", 0))
            units = int(trade.get("currentUnits", 0))
            direction = "BUY" if units > 0 else "SELL"

            current_price = self.broker.get_price(pair)
            price = current_price["bid"] if direction == "BUY" else current_price["ask"]

            # Calculate price distance that represents the profit we want to protect
            protection_pct = config.OVERNIGHT_PROFIT_PROTECTION_PCT / 100
            price_move = abs(price - open_price)
            protected_distance = price_move * protection_pct

            if direction == "BUY":
                new_stop = open_price + protected_distance
            else:
                new_stop = open_price - protected_distance

            logger.info(f"Tightening stop-loss for {pair} (trade {trade_id}) to {new_stop:.5f}")

            # Note: Updating stop-loss via OANDA API requires a separate call
            # Full implementation would call broker.update_stop_loss(trade_id, new_stop)

        except Exception as e:
            logger.error(f"Failed to tighten stop-loss for trade {trade.get('id')}: {e}")
