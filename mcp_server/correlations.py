"""
mcp_server/correlations.py — Currency Pair Correlation Analysis
────────────────────────────────────────────────────────────────
Detects when two open positions are highly correlated — meaning
they tend to move in the same direction at the same time.

Why this matters:
  If you hold EUR/USD long AND GBP/USD long, you're not really
  diversified. Both pairs are heavily influenced by USD strength/weakness.
  When USD rallies, BOTH positions lose simultaneously — doubling your
  real risk exposure even though they look like separate trades.

  This module warns the confidence engine when adding a new trade
  would create hidden double-exposure.

How it works:
  1. Fetch recent price data for all active pairs
  2. Calculate rolling correlation coefficients (Pearson -1 to +1)
  3. Flag any new potential trade that correlates > 0.75 with an
     existing open position in the same direction

Correlation scale:
  > +0.75  = Highly correlated (moving together) — WARN if same direction
  +0.5 to +0.75 = Moderately correlated — caution
  -0.5 to +0.5  = Uncorrelated — no concern
  < -0.75  = Inversely correlated — WARN if opposite direction
             (hedges are ok, but can mask losses)

Data: Uses OANDA API price data (already available, no extra cost)
"""

import numpy as np
import pandas as pd
from loguru import logger
from typing import Optional
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

CACHE_FILE = Path("/app/data/correlation_cache.json")
CACHE_DURATION_MINUTES = 60  # Correlations shift slowly — 1hr cache is fine

# Pairs that are known to be highly correlated
# Used as instant fallback when live data is unavailable
KNOWN_CORRELATIONS = {
    ("EUR_USD", "GBP_USD"): 0.82,   # Both heavily USD-driven
    ("EUR_USD", "AUD_USD"): 0.65,   # Moderate positive
    ("EUR_USD", "USD_CHF"): -0.92,  # Very strong inverse (CHF is EUR safe haven)
    ("USD_JPY", "USD_CHF"): 0.75,   # Both USD safe-haven pairs
    ("GBP_USD", "AUD_USD"): 0.60,   # Moderate positive
    ("USD_CAD", "AUD_USD"): -0.55,  # Moderate inverse (oil influence on CAD)
}

CORRELATION_WARN_THRESHOLD = 0.75   # Flag if correlation above this
CORRELATION_INVERSE_THRESHOLD = -0.75


async def get_correlation_warning(pair: str, open_positions: list = None) -> dict:
    """
    Check if adding a trade on `pair` would create dangerous correlation
    with any currently open positions.

    Args:
        pair: The pair being considered for a new trade
        open_positions: List of currently open trade dicts from OANDA

    Returns:
        Dict of {pair: correlated_pair_name} for any dangerous correlations.
        Empty dict means no correlation warning.
    """
    if not open_positions:
        # Try to load from a shared state file written by the main bot
        open_positions = _load_open_positions()

    if not open_positions:
        return {}

    open_pairs = [t.get("instrument") for t in open_positions if t.get("instrument") != pair]

    if not open_pairs:
        return {}

    warnings = {}
    correlations = await _get_correlations(pair, open_pairs)

    for open_pair, correlation in correlations.items():
        if abs(correlation) >= CORRELATION_WARN_THRESHOLD:
            # Same direction correlation = doubled risk
            warnings[pair] = open_pair
            logger.info(
                f"⚠️  Correlation warning: {pair} and {open_pair} have "
                f"correlation of {correlation:.2f} — adding this trade doubles exposure"
            )

    return warnings


async def get_correlation_matrix(pairs: list) -> dict:
    """
    Calculate full correlation matrix for a set of pairs.
    Used in weekly analysis reports to show which pairs move together.

    Returns nested dict: {pair1: {pair2: correlation_score}}
    """
    matrix = {}

    for i, pair1 in enumerate(pairs):
        matrix[pair1] = {}
        for pair2 in pairs:
            if pair1 == pair2:
                matrix[pair1][pair2] = 1.0
            elif (pair1, pair2) in KNOWN_CORRELATIONS:
                matrix[pair1][pair2] = KNOWN_CORRELATIONS[(pair1, pair2)]
            elif (pair2, pair1) in KNOWN_CORRELATIONS:
                matrix[pair1][pair2] = KNOWN_CORRELATIONS[(pair2, pair1)]
            else:
                # Try to calculate from live data
                try:
                    corr_data = await _calculate_live_correlation(pair1, pair2)
                    matrix[pair1][pair2] = corr_data
                except Exception:
                    matrix[pair1][pair2] = 0.0  # Unknown = treat as uncorrelated

    return matrix


async def _get_correlations(pair: str, compare_pairs: list) -> dict:
    """Get correlation scores between `pair` and each of `compare_pairs`."""
    correlations = {}

    for compare_pair in compare_pairs:
        # Check known correlations first (instant, no API call)
        key = (pair, compare_pair)
        reverse_key = (compare_pair, pair)

        if key in KNOWN_CORRELATIONS:
            correlations[compare_pair] = KNOWN_CORRELATIONS[key]
        elif reverse_key in KNOWN_CORRELATIONS:
            correlations[compare_pair] = KNOWN_CORRELATIONS[reverse_key]
        else:
            # Calculate from live price data
            try:
                corr = await _calculate_live_correlation(pair, compare_pair)
                correlations[compare_pair] = corr
            except Exception:
                correlations[compare_pair] = 0.0

    return correlations


async def _calculate_live_correlation(pair1: str, pair2: str, periods: int = 100) -> float:
    """
    Calculate the Pearson correlation coefficient between two pairs
    using recent close price data.

    A value of:
      +1.0 = Perfect positive correlation (always move together)
       0.0 = No correlation (independent)
      -1.0 = Perfect negative correlation (always move opposite)
    """
    # Check cache
    cache_key = f"{pair1}_{pair2}"
    cached = _load_correlation_cache(cache_key)
    if cached is not None:
        return cached

    try:
        # Import here to avoid circular imports
        from broker.oanda_client import OandaClient
        client = OandaClient()

        df1 = client.get_candles(pair1, count=periods, granularity="H1")
        df2 = client.get_candles(pair2, count=periods, granularity="H1")

        if df1 is None or df2 is None or len(df1) < 30 or len(df2) < 30:
            return 0.0

        # Align the two series on time index
        closes1 = df1["close"].pct_change().dropna()
        closes2 = df2["close"].pct_change().dropna()

        # Calculate correlation on the overlapping period
        combined = pd.DataFrame({"p1": closes1, "p2": closes2}).dropna()
        if len(combined) < 20:
            return 0.0

        correlation = float(combined["p1"].corr(combined["p2"]))
        correlation = round(correlation, 3)

        logger.debug(f"Calculated correlation {pair1}/{pair2}: {correlation:.3f}")
        _save_correlation_cache(cache_key, correlation)
        return correlation

    except Exception as e:
        logger.debug(f"Live correlation calculation failed for {pair1}/{pair2}: {e}")
        return 0.0


def _load_open_positions() -> list:
    """
    Load open positions from shared state file.
    The main bot writes this file periodically.
    """
    state_file = Path("/app/data/open_positions.json")
    if not state_file.exists():
        return []
    try:
        with open(state_file) as f:
            return json.load(f)
    except Exception:
        return []


def _load_correlation_cache(key: str) -> Optional[float]:
    cache_path = CACHE_FILE.parent / f"corr_{key}.json"
    if not cache_path.exists():
        return None
    try:
        with open(cache_path) as f:
            data = json.load(f)
        cached_at = datetime.fromisoformat(data.get("cached_at", "2000-01-01T00:00:00+00:00"))
        age = (datetime.now(timezone.utc) - cached_at).total_seconds() / 60
        if age < CACHE_DURATION_MINUTES:
            return data.get("correlation")
    except Exception:
        pass
    return None


def _save_correlation_cache(key: str, correlation: float):
    cache_path = CACHE_FILE.parent / f"corr_{key}.json"
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump({
                "correlation": correlation,
                "cached_at": datetime.now(timezone.utc).isoformat()
            }, f)
    except Exception:
        pass
