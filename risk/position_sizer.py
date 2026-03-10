"""
risk/position_sizer.py — Position Sizing
──────────────────────────────────────────
Calculates exactly how many units to trade on each position.

The goal: risk only a small, fixed percentage of your capital on each trade.
This is the most important risk management principle in trading.

Example with £500 capital, 2% risk per trade:
  - Max loss per trade: £10
  - If stop-loss is 20 pips away, trade size = £10 / (20 pips × pip value)
  - This ensures every losing trade only costs you £10, no matter what.
"""

from loguru import logger
from bot import config


def calculate_position_size(
    pair: str,
    direction: str,
    entry_price: float,
    atr: float,
    available_capital: float
) -> tuple[int, float, float]:
    """
    Calculate safe position size and set stop-loss / take-profit levels.

    The stop-loss distance is based on ATR (Average True Range).
    ATR is the average daily price movement — using it ensures our stop-loss
    is wide enough not to be hit by normal market noise, but tight enough
    to limit losses when the trade genuinely goes wrong.

    Args:
        pair: Currency pair e.g. "EUR_USD"
        direction: "BUY" or "SELL"
        entry_price: Current market price
        atr: Average True Range (price volatility measure)
        available_capital: Maximum we can deploy on this trade

    Returns:
        Tuple of (units, stop_loss_price, take_profit_price)
    """
    # How much money we're willing to lose on this one trade
    max_loss_amount = (config.MAX_CAPITAL * config.PER_TRADE_RISK_PCT / 100)
    max_loss_amount = min(max_loss_amount, available_capital * 0.2)  # Never risk more than 20% of available

    # Stop-loss distance in price units (based on ATR)
    stop_distance = atr * config.STOP_LOSS_ATR_MULTIPLIER

    # Take-profit distance (multiple of stop-loss for positive risk:reward)
    tp_distance = stop_distance * config.TAKE_PROFIT_RATIO

    # Calculate stop-loss and take-profit prices
    if direction == "BUY":
        stop_loss_price = entry_price - stop_distance
        take_profit_price = entry_price + tp_distance
    else:  # SELL
        stop_loss_price = entry_price + stop_distance
        take_profit_price = entry_price - tp_distance

    # Calculate pip value for this pair
    pip_value = _get_pip_value(pair, entry_price)

    # How many pips to our stop-loss?
    stop_distance_pips = stop_distance / pip_value if pip_value > 0 else stop_distance * 10000

    # How many units can we trade given our max loss amount?
    # units × pip_value_per_unit × stop_pips = max_loss
    if stop_distance_pips > 0 and pip_value > 0:
        pip_value_per_unit = pip_value / 10000  # Value per unit per pip
        units = int(max_loss_amount / (pip_value_per_unit * stop_distance_pips))
    else:
        units = 1000  # Fallback to micro lot

    # Cap at a reasonable maximum (1 standard lot = 100,000 units)
    units = min(units, 100000)

    # OANDA minimum is 1 unit, but sensible minimum is a micro lot (1,000)
    units = max(units, 1000)

    logger.debug(
        f"Position size for {pair} {direction}: {units:,} units | "
        f"SL: {stop_loss_price:.5f} | TP: {take_profit_price:.5f} | "
        f"Max loss: £{max_loss_amount:.2f}"
    )

    return units, round(stop_loss_price, 5), round(take_profit_price, 5)


def _get_pip_value(pair: str, price: float) -> float:
    """
    Get the pip size for a currency pair.

    Most pairs: 1 pip = 0.0001 (4th decimal place)
    JPY pairs:  1 pip = 0.01   (2nd decimal place)
    """
    if "JPY" in pair:
        return 0.01
    return 0.0001
