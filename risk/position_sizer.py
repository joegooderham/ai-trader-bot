"""
risk/position_sizer.py — Position Sizing for IG Mini CFD Contracts
────────────────────────────────────────────────────────────────────
Calculates how many IG contracts to trade on each position.

IG Mini CFDs use CONTRACT SIZE:
  - Minimum size = 1 contract
  - 1 contract = 1 mini lot = 10,000 currency units
  - Pip value per contract ≈ £0.80–£1.00 depending on pair

Confidence-tiered risk: parameters scale with confidence score so
high-conviction trades lean in while low-conviction trades play safe.
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


def _get_tier(confidence_score: float) -> str:
    """Determine confidence tier: low (50-65), medium (66-80), high (81+)."""
    if confidence_score >= 81:
        return "high"
    elif confidence_score >= 66:
        return "medium"
    return "low"


def _get_tiered_params(confidence_score: float) -> dict:
    """Get risk parameters for the given confidence score.
    Falls back to base config values if tiers aren't configured."""
    tier_name = _get_tier(confidence_score)
    tier = config.CONFIDENCE_TIERS.get(tier_name, {})

    return {
        "risk_pct": tier.get("risk_pct", config.PER_TRADE_RISK_PCT),
        "stop_loss_atr": tier.get("stop_loss_atr_multiplier", config.STOP_LOSS_ATR_MULTIPLIER),
        "take_profit_ratio": tier.get("take_profit_ratio", config.TAKE_PROFIT_RATIO),
        "trailing_activation_atr": tier.get("trailing_stop_activation_atr", config.TRAILING_STOP_ACTIVATION_ATR),
        "trailing_trail_atr": tier.get("trailing_stop_trail_atr", config.TRAILING_STOP_TRAIL_ATR),
        "tier": tier_name,
    }


def calculate_position_size(
    pair: str,
    direction: str,
    entry_price: float,
    atr: float,
    available_capital: float,
    confidence_score: float = 60.0
) -> tuple[float, float, float]:
    """
    Calculate IG contract size and stop-loss / take-profit levels.

    Risk parameters scale with confidence score:
      - Low (50-65%):  1% risk, 2.0× ATR stop, 1.5:1 TP — defensive
      - Medium (66-80%): 2% risk, 1.5× ATR stop, 2:1 TP — balanced
      - High (81%+):   3% risk, 1.2× ATR stop, 3:1 TP — aggressive

    Args:
        pair:              Currency pair e.g. "EUR_USD"
        direction:         "BUY" or "SELL"
        entry_price:       Current market price
        atr:               Average True Range (price volatility measure)
        available_capital: Maximum capital available to deploy
        confidence_score:  0-100 confidence score (determines risk tier)

    Returns:
        Tuple of (contracts, stop_loss_price, take_profit_price)
        Minimum contracts returned is always 1.0
    """
    # Get tiered parameters based on confidence
    params = _get_tiered_params(confidence_score)
    risk_pct = params["risk_pct"]
    sl_atr_mult = params["stop_loss_atr"]
    tp_ratio = params["take_profit_ratio"]

    # Max loss amount for this trade — scaled by confidence tier
    max_loss_amount = config.MAX_CAPITAL * risk_pct / 100
    # Hard cap: never risk more than max_per_trade_spend per trade
    max_loss_amount = min(max_loss_amount, config.MAX_PER_TRADE_SPEND)
    max_loss_amount = min(max_loss_amount, available_capital * 0.2)
    max_loss_amount = max(max_loss_amount, 1.0)  # At least £1

    # Stop and take-profit distances based on ATR and confidence tier
    pip_size     = PIP_SIZE.get(pair, DEFAULT_PIP_SIZE)
    stop_dist    = atr * sl_atr_mult
    tp_dist      = stop_dist * tp_ratio
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

    logger.info(
        f"Position size {pair} {direction} [{params['tier'].upper()}]: "
        f"{contracts} contracts | Risk: {risk_pct}% (£{max_loss_amount:.2f}) | "
        f"SL: {stop_loss_price:.5f} ({sl_atr_mult}×ATR) | "
        f"TP: {take_profit_price:.5f} ({tp_ratio}:1) | "
        f"Stop: {stop_pips:.1f} pips | Confidence: {confidence_score:.0f}%"
    )

    return contracts, round(stop_loss_price, 5), round(take_profit_price, 5)


def get_trailing_params(confidence_score: float) -> tuple[float, float]:
    """Get trailing stop parameters for a given confidence score.
    Returns (activation_atr_multiplier, trail_atr_multiplier)."""
    params = _get_tiered_params(confidence_score)
    return params["trailing_activation_atr"], params["trailing_trail_atr"]
