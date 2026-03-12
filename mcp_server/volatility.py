"""
mcp_server/volatility.py — Volatility Regime Detection
─────────────────────────────────────────────────────────
Classifies the current market into one of four volatility regimes.
The bot adjusts its behaviour significantly based on which regime we're in.

Why this matters:
  A strategy that works perfectly in calm, trending markets can lose
  money consistently in chaotic, high-volatility conditions.
  Knowing the regime lets the bot either:
    - Be more selective (fewer trades, higher confidence required)
    - Avoid trading altogether (during extreme volatility)
    - Be more aggressive (during predictable low-volatility trends)

Volatility Regimes:
  LOW      — Market is calm and trending. Signals are more reliable.
             Bot can slightly increase aggressiveness.

  NORMAL   — Standard conditions. Bot operates at configured settings.

  HIGH     — Increased uncertainty. Confidence threshold raised by 5pts.
             Stop-losses widened to avoid being shaken out.

  EXTREME  — Crisis conditions (flash crash, major news shock, etc.)
             Confidence threshold raised by 15pts.
             Consider pausing new trades entirely.

Detection method:
  Compares current ATR (Average True Range) to its 20-period average.
  ATR measures the typical daily price range — if it's much higher than
  usual, the market is in a volatile regime.

  Current ATR / Average ATR = Volatility Ratio
  < 0.7  = LOW
  0.7–1.3 = NORMAL
  1.3–2.0 = HIGH
  > 2.0  = EXTREME
"""

import numpy as np
import json
from pathlib import Path
from datetime import datetime, timezone
from loguru import logger
from typing import Optional

CACHE_FILE = Path("/app/data/volatility_cache.json")
CACHE_DURATION_MINUTES = 15  # Volatility can change quickly — refresh often

# ATR ratio thresholds for each regime
REGIME_THRESHOLDS = {
    "low": 0.70,
    "normal_upper": 1.30,
    "high_upper": 2.00,
    # Above 2.0 = extreme
}


async def get_volatility_regime(pair: str) -> str:
    """
    Get the current volatility regime for a currency pair.

    Returns one of: "low", "normal", "high", "extreme"
    """
    cached = _load_cache(pair)
    if cached is not None:
        return cached

    regime = await _calculate_regime(pair)
    _save_cache(pair, regime)
    return regime


async def get_volatility_details(pair: str) -> dict:
    """
    Get detailed volatility information for reporting and analysis.

    Returns:
        {
            "regime": "high",
            "current_atr": 0.00142,
            "average_atr": 0.00089,
            "volatility_ratio": 1.59,
            "interpretation": "Market is 59% more volatile than usual...",
            "recommended_action": "Raise confidence threshold by 5 points"
        }
    """
    try:
        from broker.ig_client import IGClient
        import pandas_ta as ta

        client = IGClient()
        df = client.get_candles(pair, count=50, granularity="H1")

        if df is None or len(df) < 20:
            return {"regime": "normal", "error": "Insufficient data"}

        atr_series = ta.atr(df["high"], df["low"], df["close"], length=14)
        current_atr = float(atr_series.iloc[-1])
        average_atr = float(atr_series.tail(20).mean())

        if average_atr == 0:
            return {"regime": "normal"}

        ratio = current_atr / average_atr
        regime = _classify_regime(ratio)

        interpretations = {
            "low": (
                f"Market is {(1-ratio)*100:.0f}% calmer than usual. "
                f"Conditions are favourable — signals tend to be more reliable. "
                f"Bot may slightly lower its confidence threshold."
            ),
            "normal": (
                f"Volatility is within normal range ({ratio:.2f}x average). "
                f"Bot operating at standard settings."
            ),
            "high": (
                f"Market is {(ratio-1)*100:.0f}% more volatile than usual. "
                f"Price swings are larger and less predictable. "
                f"Bot raising confidence threshold by 5 points."
            ),
            "extreme": (
                f"Extreme volatility detected — {ratio:.1f}x the normal range. "
                f"Likely caused by a major news event or market shock. "
                f"Bot is being very selective — confidence threshold raised by 15 points."
            ),
        }

        actions = {
            "low": "Slightly lower confidence threshold (more trades allowed)",
            "normal": "No adjustment — standard settings apply",
            "high": "Raise confidence threshold by 5 points, widen stop-losses",
            "extreme": "Raise confidence threshold by 15 points, consider pausing new trades",
        }

        return {
            "pair": pair,
            "regime": regime,
            "current_atr": round(current_atr, 6),
            "average_atr": round(average_atr, 6),
            "volatility_ratio": round(ratio, 3),
            "interpretation": interpretations.get(regime, ""),
            "recommended_action": actions.get(regime, ""),
            "calculated_at": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        logger.warning(f"Volatility details calculation failed for {pair}: {e}")
        return {"regime": "normal", "error": str(e)}


async def _calculate_regime(pair: str) -> str:
    """Calculate the volatility regime from live price data."""
    try:
        from broker.ig_client import IGClient
        import pandas_ta as ta

        client = IGClient()
        df = client.get_candles(pair, count=50, granularity="H1")

        if df is None or len(df) < 20:
            logger.warning(f"Insufficient data for volatility calc on {pair}")
            return "normal"

        # Calculate 14-period ATR
        atr_series = ta.atr(df["high"], df["low"], df["close"], length=14)
        current_atr = float(atr_series.iloc[-1])

        # Compare to 20-period average ATR
        average_atr = float(atr_series.tail(20).mean())

        if average_atr == 0:
            return "normal"

        ratio = current_atr / average_atr
        regime = _classify_regime(ratio)

        logger.debug(f"Volatility regime for {pair}: {regime} (ATR ratio: {ratio:.2f})")
        return regime

    except Exception as e:
        logger.warning(f"Volatility calculation failed for {pair}: {e} — defaulting to normal")
        return "normal"


def _classify_regime(atr_ratio: float) -> str:
    """Map an ATR ratio to a regime name."""
    if atr_ratio < REGIME_THRESHOLDS["low"]:
        return "low"
    elif atr_ratio <= REGIME_THRESHOLDS["normal_upper"]:
        return "normal"
    elif atr_ratio <= REGIME_THRESHOLDS["high_upper"]:
        return "high"
    else:
        return "extreme"


def _load_cache(pair: str) -> Optional[str]:
    cache_path = CACHE_FILE.parent / f"vol_{pair}.json"
    if not cache_path.exists():
        return None
    try:
        with open(cache_path) as f:
            data = json.load(f)
        cached_at = datetime.fromisoformat(data.get("cached_at", "2000-01-01T00:00:00+00:00"))
        age = (datetime.now(timezone.utc) - cached_at).total_seconds() / 60
        if age < CACHE_DURATION_MINUTES:
            return data.get("regime")
    except Exception:
        pass
    return None


def _save_cache(pair: str, regime: str):
    cache_path = CACHE_FILE.parent / f"vol_{pair}.json"
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump({
                "regime": regime,
                "cached_at": datetime.now(timezone.utc).isoformat()
            }, f)
    except Exception:
        pass
