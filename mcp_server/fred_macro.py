"""
mcp_server/fred_macro.py — FRED Macro Data Module (BACKLOG-014)
────────────────────────────────────────────────────────────────
Fetches key macroeconomic indicators from the Federal Reserve (FRED) API
and provides a directional bias based on interest rate differentials and
economic momentum.

Uses the free FRED API (https://fred.stlouisfed.org/docs/api/). Requires
a FRED_API_KEY environment variable (free, get one at https://fred.stlouisfed.org/docs/api/api_key.html).

How it works:
  - Fetches the latest Fed Funds Rate and ECB/BOJ/BOE/RBA/BOC/SNB policy rates
  - Computes interest rate differential for the given pair
  - Higher rate differential favours the higher-yield currency (carry trade logic)
  - Also fetches latest CPI data for inflation trend assessment

Integration:
  - Called by /context/{pair} in server.py alongside other MCP modules
  - Returns a directional bias signal that the confidence engine uses
  - Cached for 6 hours (macro data changes very slowly)
"""

import os
import time
from loguru import logger

from bot import config

FRED_API_KEY = os.getenv("FRED_API_TOKEN", "")

# FRED series IDs for central bank policy rates
# These are the most commonly used proxies for each currency's benchmark rate
RATE_SERIES = {
    "USD": "DFEDTARU",   # Fed Funds Upper Target (daily)
    "EUR": "ECBMLFR",    # ECB Main Refinancing Rate
    "GBP": "IUDSOIA",    # BOE Official Bank Rate (proxy: SONIA)
    "JPY": "IRSTCB01JPM156N",  # BOJ Policy Rate
    "AUD": "RBATCTR",    # RBA Cash Rate Target (monthly)
    "CAD": "INTDSRCAM193N",  # BOC Bank Rate
    "CHF": "IRSTCI01CHM156N",  # SNB Policy Rate
    "NZD": "IRSTCB01NZM156N",  # RBNZ Official Cash Rate
}

# Cache — macro data doesn't change often
_cache = {}
_CACHE_TTL = 6 * 3600  # 6 hours


async def get_macro_bias(pair: str) -> dict:
    """
    Fetch macro data for a currency pair and return a directional bias.

    Returns:
      - rate_differential: interest rate gap between base and quote currencies
      - bias: "BUY" (base currency has higher rate), "SELL" (quote has higher rate),
              or "NEUTRAL" (rates similar or data unavailable)
      - bias_strength: how significant the rate differential is (0-100)
      - base_rate / quote_rate: the actual interest rates used
    """
    if not config.MCP_CONFIG.get("enable_fred_macro", True):
        return _neutral_result(pair)

    if not FRED_API_KEY:
        logger.debug("FRED_API_KEY not set — macro bias disabled")
        return _neutral_result(pair)

    # Check cache
    cache_key = f"fred_{pair}"
    if cache_key in _cache:
        cached_at, cached_data = _cache[cache_key]
        if time.time() - cached_at < _CACHE_TTL:
            return cached_data

    try:
        parts = pair.split("_")
        if len(parts) != 2:
            return _neutral_result(pair)

        base_ccy, quote_ccy = parts[0], parts[1]

        base_rate = await _fetch_rate(base_ccy)
        quote_rate = await _fetch_rate(quote_ccy)

        if base_rate is None or quote_rate is None:
            return _neutral_result(pair)

        rate_diff = base_rate - quote_rate

        # Determine bias from interest rate differential
        # A significant differential (>0.5%) favours the higher-yield currency
        # This is basic carry trade logic — money flows to higher rates
        if rate_diff > 0.5:
            bias = "BUY"  # Base currency has higher rate → bullish for pair
            bias_strength = min(abs(rate_diff) / 3.0 * 100, 100)  # Scale: 3% diff = 100 strength
        elif rate_diff < -0.5:
            bias = "SELL"  # Quote currency has higher rate → bearish for pair
            bias_strength = min(abs(rate_diff) / 3.0 * 100, 100)
        else:
            bias = "NEUTRAL"
            bias_strength = 0

        result = {
            "pair": pair,
            "base_currency": base_ccy,
            "quote_currency": quote_ccy,
            "base_rate": base_rate,
            "quote_rate": quote_rate,
            "rate_differential": round(rate_diff, 2),
            "bias": bias,
            "bias_strength": round(bias_strength, 1),
        }

        _cache[cache_key] = (time.time(), result)
        return result

    except Exception as e:
        logger.warning(f"FRED macro fetch failed for {pair}: {e}")
        return _neutral_result(pair)


async def _fetch_rate(currency: str) -> float:
    """Fetch the latest interest rate for a currency from FRED."""
    series_id = RATE_SERIES.get(currency)
    if not series_id:
        return None

    # Check cache for individual rate
    cache_key = f"fred_rate_{currency}"
    if cache_key in _cache:
        cached_at, rate = _cache[cache_key]
        if time.time() - cached_at < _CACHE_TTL:
            return rate

    try:
        import httpx
        url = (
            f"https://api.stlouisfed.org/fred/series/observations"
            f"?series_id={series_id}&api_key={FRED_API_KEY}"
            f"&sort_order=desc&limit=1&file_type=json"
        )

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()

        observations = data.get("observations", [])
        if not observations:
            return None

        # FRED returns "." for missing values
        value = observations[0].get("value", ".")
        if value == "." or not value:
            return None

        rate = float(value)
        _cache[cache_key] = (time.time(), rate)
        return rate

    except Exception as e:
        logger.debug(f"FRED rate fetch failed for {currency} ({series_id}): {e}")
        return None


def _neutral_result(pair: str) -> dict:
    """Return a neutral result when FRED data is unavailable."""
    return {
        "pair": pair,
        "bias": "NEUTRAL",
        "bias_strength": 0,
        "rate_differential": 0,
    }
