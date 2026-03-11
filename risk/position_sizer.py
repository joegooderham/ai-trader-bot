"""
risk/position_sizer.py — Position Sizing for IG Mini CFD Contracts
────────────────────────────────────────────────────────────────────
Calculates how many IG contracts to trade on each position.

IG Mini CFDs use CONTRACT SIZE (not units like OANDA):
  - Minimum size = 1 contract
  - 1 contract = 1 mini lot = 10,000 currency units
  - Pip value per contract ≈ £0.80–£1.00 depending on pair

Example with £500 capital, 2% risk per trade:
  - Max loss per trade: £10
  - If stop-loss is 20 pips away: contracts = £10 / (20 × £0.90) = 0.55 → 1 contract
  - Minimum is always 1 contract
"""
from loguru import logger
from bot import config


# Approximate pip value per IG mini contract in GBP
# These are estimates — actual value shifts slightly with exchange rates
IG_PIP_VALUE_GBP = {
    "EUR_USD": 0.79,
    "GBP_USD": 1.00,
    "USD_JPY": 0.67,
    "AUD_USD": 0.63,
    "USD_CAD": 0.58,
    "USD_CHF": 0.79,
    "GBP_JPY": 0.67,
    "EUR_GBP": 1.00,
    "EUR_JPY": 0.67,
    "NZD_USD": 0.63,
}

# Pip size per pair
PIP_SIZE = {
    "USD_JPY": 0.01,
    "GBP_JPY": 0.01,
    "EUR_JPY": 0.01,
}
DEFAULT_PIP_SIZE = 0.0001


def calculate_position_size(
    pair: str,
    direction: str,
    entry_price: float,
    atr: float,
    available_capital: float
) -> tuple[float, float, float]:
    """
    Calculate IG contract size and stop-loss / take-profit levels.

    Args:
        pair:              Currency pair e.g. "EUR_USD"
        direction:         "BUY" or "SELL"
        entry_price:       Current market price
        atr:               Average True Range (price volatility measure)
        available_capital: Maximum capital available to deploy

    Returns:
        Tuple of (contracts, stop_loss_price, take_profit_price)
        Minimum contracts returned is always 1.0
    """
    # Max loss amount for this trade
    max_loss_amount = config.MAX_CAPITAL * config.PER_TRADE_RISK_PCT / 100
    max_loss_amount = min(max_loss_amount, available_capital * 0.2)
    max_loss_amount = max(max_loss_amount, 1.0)  # At least £1

    # Stop and take-profit distances based on ATR
    pip_size     = PIP_SIZE.get(pair, DEFAULT_PIP_SIZE)
    stop_dist    = atr * config.STOP_LOSS_ATR_MULTIPLIER
    tp_dist      = stop_dist * config.TAKE_PROFIT_RATIO
    stop_pips    = stop_dist / pip_size if pip_size > 0 else 20.0
    stop_pips    = max(stop_pips, 5.0)   # Never less than 5 pips stop

    # Stop-loss and take-profit price levels
    if direction == "BUY":
        stop_loss_price  = entry_price - stop_dist
        take_profit_price = entry_price + tp_dist
    else:
        stop_loss_price  = entry_price + stop_dist
        take_profit_price = entry_price - tp_dist

    # IG contract sizing
    # contracts = max_loss / (stop_pips × pip_value_per_contract)
    pip_val_per_contract = IG_PIP_VALUE_GBP.get(pair, 0.80)
    contracts = max_loss_amount / (stop_pips * pip_val_per_contract)

    # Round to 1 decimal place, enforce minimum of 1
    contracts = max(1.0, round(contracts, 1))

    # Cap at a sensible maximum for a £500 demo account
    contracts = min(contracts, 5.0)

    logger.debug(
        f"Position size {pair} {direction}: {contracts} contracts | "
        f"SL: {stop_loss_price:.5f} | TP: {take_profit_price:.5f} | "
        f"Stop: {stop_pips:.1f} pips | Max loss: £{max_loss_amount:.2f}"
    )

    return contracts, round(stop_loss_price, 5), round(take_profit_price, 5)