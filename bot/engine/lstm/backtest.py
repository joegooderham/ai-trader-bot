"""
bot/engine/lstm/backtest.py — LSTM Backtesting Engine
──────────────────────────────────────────────────────
Walk-forward simulation that replays the bot's full decision pipeline
against historical candle data stored in SQLite.

Compares two strategies side by side:
  1. LSTM-enhanced confidence scoring (how the bot will trade with the model)
  2. Indicator-only scoring (how the bot trades without the model)

This tells you whether the LSTM is actually adding value before you
let it drive real trades.

Usage:
  python -m bot.engine.lstm.backtest          # Run from CLI
  /backtest                                   # Run from Telegram

Output:
  - Per-pair trade count, win rate, P&L, max drawdown
  - Side-by-side LSTM vs indicator-only comparison
  - Total LSTM edge (how much extra £ the model generated)
"""

import time
from dataclasses import dataclass, field
from typing import Optional
from loguru import logger

from bot import config
from bot.engine import indicators, confidence
from bot.engine.lstm.predictor import LSTMPredictor
from data.storage import TradeStorage
from risk.position_sizer import (
    calculate_position_size, IG_PIP_VALUE_GBP, PIP_SIZE, DEFAULT_PIP_SIZE
)


# ── Data Classes ──────────────────────────────────────────────────────────────

@dataclass
class SimPosition:
    """A simulated open position tracked during the backtest."""
    pair: str
    direction: str          # "BUY" or "SELL"
    entry_price: float
    entry_idx: int          # candle index at entry
    stop_loss: float
    take_profit: float
    contracts: float
    confidence_score: float


@dataclass
class ClosedTrade:
    """A completed simulated trade with P&L."""
    pair: str
    direction: str
    entry_price: float
    exit_price: float
    contracts: float
    pl: float               # in GBP
    won: bool
    exit_reason: str        # "stop_loss", "take_profit"


@dataclass
class BacktestResult:
    """Results for one pair, one strategy."""
    pair: str
    candles_tested: int
    date_range: str

    # LSTM-enhanced
    lstm_trades: int = 0
    lstm_wins: int = 0
    lstm_total_pl: float = 0.0
    lstm_max_drawdown: float = 0.0
    lstm_gross_profit: float = 0.0
    lstm_gross_loss: float = 0.0

    # Indicator-only
    ind_trades: int = 0
    ind_wins: int = 0
    ind_total_pl: float = 0.0
    ind_max_drawdown: float = 0.0
    ind_gross_profit: float = 0.0
    ind_gross_loss: float = 0.0


# ── Position Tracker ──────────────────────────────────────────────────────────

class PositionTracker:
    """
    Tracks simulated positions and P&L for one strategy (LSTM or indicator-only).
    Maintains a running equity curve to calculate max drawdown.
    """

    def __init__(self, starting_capital: float = 500.0):
        self.capital = starting_capital
        self.position: Optional[SimPosition] = None
        self.closed_trades: list[ClosedTrade] = []

        # Drawdown tracking
        self._peak_equity = starting_capital
        self._max_drawdown = 0.0
        self._cumulative_pl = 0.0

    def has_position(self) -> bool:
        return self.position is not None

    def open_position(self, pos: SimPosition):
        self.position = pos

    def check_exits(self, candle_high: float, candle_low: float):
        """
        Check if the current candle's high/low triggered stop-loss or take-profit.
        Conservative: if both are hit in the same candle, assume stop-loss first.
        """
        if self.position is None:
            return

        pos = self.position
        hit_sl = False
        hit_tp = False

        if pos.direction == "BUY":
            hit_sl = candle_low <= pos.stop_loss
            hit_tp = candle_high >= pos.take_profit
        else:
            hit_sl = candle_high >= pos.stop_loss
            hit_tp = candle_low <= pos.take_profit

        if hit_sl or hit_tp:
            # Conservative: SL takes priority if both hit same candle
            if hit_sl:
                exit_price = pos.stop_loss
                reason = "stop_loss"
            else:
                exit_price = pos.take_profit
                reason = "take_profit"

            pl = self._calculate_pl(pos, exit_price)
            won = pl > 0

            self.closed_trades.append(ClosedTrade(
                pair=pos.pair,
                direction=pos.direction,
                entry_price=pos.entry_price,
                exit_price=exit_price,
                contracts=pos.contracts,
                pl=round(pl, 2),
                won=won,
                exit_reason=reason,
            ))

            self._cumulative_pl += pl
            self.capital += pl

            # Update drawdown
            equity = self.capital
            if equity > self._peak_equity:
                self._peak_equity = equity
            drawdown = self._peak_equity - equity
            if drawdown > self._max_drawdown:
                self._max_drawdown = drawdown

            self.position = None

    def _calculate_pl(self, pos: SimPosition, exit_price: float) -> float:
        """Calculate P&L in GBP for a closed position."""
        pip_size = PIP_SIZE.get(pos.pair, DEFAULT_PIP_SIZE)
        pip_value = IG_PIP_VALUE_GBP.get(pos.pair, 0.80)

        if pos.direction == "BUY":
            pips = (exit_price - pos.entry_price) / pip_size
        else:
            pips = (pos.entry_price - exit_price) / pip_size

        return pips * pip_value * pos.contracts

    @property
    def wins(self) -> int:
        return sum(1 for t in self.closed_trades if t.won)

    @property
    def total_pl(self) -> float:
        return round(sum(t.pl for t in self.closed_trades), 2)

    @property
    def gross_profit(self) -> float:
        return round(sum(t.pl for t in self.closed_trades if t.pl > 0), 2)

    @property
    def gross_loss(self) -> float:
        return round(abs(sum(t.pl for t in self.closed_trades if t.pl <= 0)), 2)

    @property
    def max_drawdown(self) -> float:
        return round(self._max_drawdown, 2)


# ── Backtest Engine ───────────────────────────────────────────────────────────

class BacktestEngine:
    """
    Walk-forward backtester that replays the bot's full decision pipeline
    against historical data, comparing LSTM-enhanced vs indicator-only scoring.
    """

    def __init__(self):
        self.storage = TradeStorage()
        self.predictor = LSTMPredictor()

    def run(self, pair: str) -> BacktestResult:
        """
        Run backtest for a single pair.

        Walks forward through all stored H1 candles, at each step:
        1. Check open positions for exit (SL/TP hit)
        2. Calculate indicators from 60-candle window
        3. Get LSTM prediction
        4. Score confidence with and without LSTM
        5. Simulate trade entry if confidence >= threshold
        """
        # Load all stored candles for this pair
        candles = self.storage.get_candles(pair, "H1", count=999999)
        if candles is None or len(candles) < 120:
            logger.warning(f"Backtest {pair}: not enough data ({len(candles) if candles is not None else 0} candles, need 120+)")
            return BacktestResult(pair=pair, candles_tested=0, date_range="insufficient data")

        # Two independent trackers — one for each strategy
        lstm_tracker = PositionTracker(starting_capital=config.MAX_CAPITAL)
        ind_tracker = PositionTracker(starting_capital=config.MAX_CAPITAL)

        start_idx = 90  # Warm-up for EMA50 + buffer
        total_candles = len(candles)

        date_start = str(candles.index[start_idx])[:10]
        date_end = str(candles.index[-1])[:10]

        logger.info(f"Backtest {pair}: {total_candles} candles, walk-forward from idx {start_idx} ({date_start} to {date_end})")

        for i in range(start_idx, total_candles):
            current = candles.iloc[i]
            candle_high = float(current["high"])
            candle_low = float(current["low"])

            # Step 1: Check exits on current candle
            lstm_tracker.check_exits(candle_high, candle_low)
            ind_tracker.check_exits(candle_high, candle_low)

            # Skip if both already in a position (can't open another for same pair)
            if lstm_tracker.has_position() and ind_tracker.has_position():
                continue

            # Step 2: Build 60-candle window (matches live LOOKBACK_CANDLES)
            window_start = max(0, i - 60)
            window = candles.iloc[window_start:i]

            if len(window) < 60:
                continue

            # Step 3: Calculate indicators
            try:
                ind_result = indicators.calculate(window)
            except (ValueError, Exception):
                continue

            # Step 4: Get LSTM prediction
            ml_prediction = None
            if self.predictor._loaded:
                ml_prediction = self.predictor.predict(pair, window)

            # Step 5: Score with LSTM
            if not lstm_tracker.has_position():
                lstm_conf = confidence.calculate_confidence(
                    pair=pair, indicators=ind_result,
                    mcp_context={}, ml_prediction=ml_prediction
                )
                if lstm_conf.should_trade and lstm_tracker.capital > 0:
                    try:
                        contracts, sl, tp = calculate_position_size(
                            pair=pair, direction=lstm_conf.direction,
                            entry_price=ind_result.current_price,
                            atr=ind_result.atr,
                            available_capital=lstm_tracker.capital,
                        )
                        if contracts > 0:
                            lstm_tracker.open_position(SimPosition(
                                pair=pair, direction=lstm_conf.direction,
                                entry_price=ind_result.current_price, entry_idx=i,
                                stop_loss=sl, take_profit=tp,
                                contracts=contracts, confidence_score=lstm_conf.score,
                            ))
                    except Exception:
                        pass

            # Step 6: Score without LSTM
            if not ind_tracker.has_position():
                ind_conf = confidence.calculate_confidence(
                    pair=pair, indicators=ind_result,
                    mcp_context={}, ml_prediction=None
                )
                if ind_conf.should_trade and ind_tracker.capital > 0:
                    try:
                        contracts, sl, tp = calculate_position_size(
                            pair=pair, direction=ind_conf.direction,
                            entry_price=ind_result.current_price,
                            atr=ind_result.atr,
                            available_capital=ind_tracker.capital,
                        )
                        if contracts > 0:
                            ind_tracker.open_position(SimPosition(
                                pair=pair, direction=ind_conf.direction,
                                entry_price=ind_result.current_price, entry_idx=i,
                                stop_loss=sl, take_profit=tp,
                                contracts=contracts, confidence_score=ind_conf.score,
                            ))
                    except Exception:
                        pass

        return BacktestResult(
            pair=pair,
            candles_tested=total_candles - start_idx,
            date_range=f"{date_start} to {date_end}",
            lstm_trades=len(lstm_tracker.closed_trades),
            lstm_wins=lstm_tracker.wins,
            lstm_total_pl=lstm_tracker.total_pl,
            lstm_max_drawdown=lstm_tracker.max_drawdown,
            lstm_gross_profit=lstm_tracker.gross_profit,
            lstm_gross_loss=lstm_tracker.gross_loss,
            ind_trades=len(ind_tracker.closed_trades),
            ind_wins=ind_tracker.wins,
            ind_total_pl=ind_tracker.total_pl,
            ind_max_drawdown=ind_tracker.max_drawdown,
            ind_gross_profit=ind_tracker.gross_profit,
            ind_gross_loss=ind_tracker.gross_loss,
        )

    def run_all_pairs(self) -> list[BacktestResult]:
        """Run backtest across all configured pairs."""
        results = []
        for pair in config.PAIRS:
            try:
                result = self.run(pair)
                results.append(result)
            except Exception as e:
                logger.error(f"Backtest failed for {pair}: {e}")
        return results

    def format_report(self, results: list[BacktestResult]) -> str:
        """
        Format backtest results into a Telegram-friendly report.
        Shows LSTM vs indicator-only comparison with clear totals.
        """
        if not results:
            return "No backtest results — check that candle data exists in SQLite."

        # Aggregate totals
        lstm_trades = sum(r.lstm_trades for r in results)
        lstm_wins = sum(r.lstm_wins for r in results)
        lstm_pl = sum(r.lstm_total_pl for r in results)
        lstm_dd = max((r.lstm_max_drawdown for r in results), default=0)
        lstm_gp = sum(r.lstm_gross_profit for r in results)
        lstm_gl = sum(r.lstm_gross_loss for r in results)

        ind_trades = sum(r.ind_trades for r in results)
        ind_wins = sum(r.ind_wins for r in results)
        ind_pl = sum(r.ind_total_pl for r in results)
        ind_dd = max((r.ind_max_drawdown for r in results), default=0)
        ind_gp = sum(r.ind_gross_profit for r in results)
        ind_gl = sum(r.ind_gross_loss for r in results)

        lstm_wr = f"{lstm_wins/lstm_trades*100:.0f}%" if lstm_trades else "N/A"
        ind_wr = f"{ind_wins/ind_trades*100:.0f}%" if ind_trades else "N/A"
        lstm_pf = f"{lstm_gp/lstm_gl:.1f}" if lstm_gl > 0 else "inf"
        ind_pf = f"{ind_gp/ind_gl:.1f}" if ind_gl > 0 else "inf"

        edge = lstm_pl - ind_pl
        edge_str = f"+£{edge:.2f}" if edge >= 0 else f"-£{abs(edge):.2f}"

        # Date range from first result that has data
        date_range = "N/A"
        total_candles = 0
        for r in results:
            if r.candles_tested > 0:
                date_range = r.date_range
                total_candles += r.candles_tested

        model_status = "Trained" if any(r.lstm_trades != r.ind_trades for r in results) else "No model loaded — both columns show indicator-only"

        lines = [
            "*BACKTEST RESULTS*",
            "═══════════════════════",
            f"Period: {date_range}",
            f"Candles tested: {total_candles} (H1)",
            f"Model: {model_status}",
            "",
            "*LSTM vs Indicator-Only:*",
            "─────────────────────────",
            f"{'':12s}{'LSTM':>8s}{'IND':>8s}",
            f"{'Trades':12s}{lstm_trades:>8d}{ind_trades:>8d}",
            f"{'Win Rate':12s}{lstm_wr:>8s}{ind_wr:>8s}",
            f"{'Net P&L':12s}{'£'+f'{lstm_pl:.2f}':>8s}{'£'+f'{ind_pl:.2f}':>8s}",
            f"{'Max DD':12s}{'£'+f'{lstm_dd:.2f}':>8s}{'£'+f'{ind_dd:.2f}':>8s}",
            f"{'Profit F':12s}{lstm_pf:>8s}{ind_pf:>8s}",
            "─────────────────────────",
            f"*LSTM Edge:* {edge_str}",
            "",
            "*Per Pair:*",
        ]

        for r in results:
            if r.candles_tested == 0:
                lines.append(f"  {r.pair}: insufficient data")
                continue
            lstm_str = f"+£{r.lstm_total_pl:.2f}" if r.lstm_total_pl >= 0 else f"-£{abs(r.lstm_total_pl):.2f}"
            ind_str = f"+£{r.ind_total_pl:.2f}" if r.ind_total_pl >= 0 else f"-£{abs(r.ind_total_pl):.2f}"
            lines.append(f"  {r.pair}: LSTM {lstm_str} ({r.lstm_trades}t) | IND {ind_str} ({r.ind_trades}t)")

        return "\n".join(lines)


# ── CLI Entry Point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    start = time.time()
    engine = BacktestEngine()

    print("Running backtest across all pairs...")
    results = engine.run_all_pairs()

    duration = time.time() - start
    report = engine.format_report(results)

    print()
    print(report)
    print(f"\nCompleted in {duration:.1f}s")
