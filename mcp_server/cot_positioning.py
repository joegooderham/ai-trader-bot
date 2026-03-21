"""
mcp_server/cot_positioning.py — CFTC Commitment of Traders Data (BACKLOG-016)
──────────────────────────────────────────────────────────────────────────────
Fetches weekly CFTC Commitment of Traders (COT) report data for institutional
positioning bias. Shows how large speculators (hedge funds, banks) and
commercial hedgers are positioned in each currency.

Why this matters:
  - Large speculators are generally trend-followers — if they're net long, the
    trend is likely up. If they're adding to positions, momentum is building.
  - Commercial hedgers are generally contrarian — their positioning is less
    useful for direction but extreme levels signal potential reversals.
  - COT is published every Friday (data from Tuesday). It's backward-looking
    but still moves markets because institutional positioning is sticky.

Uses the free CFTC data via the Quandl/Nasdaq API. No API key needed for
basic access. Falls back to cached data if fetch fails.

Integration:
  - Called by /context/{pair} in server.py alongside other MCP modules
  - Returns a directional bias based on large speculator positioning
  - Cached for 24 hours (weekly data, only updates on Fridays)
"""

import time
from loguru import logger

from bot import config

# CFTC commodity codes for forex futures — these are the CME futures contracts
# that the COT report covers. Maps from our pair format to the CFTC code.
# COT data is per-currency (not per-pair), so we track base/quote separately.
CFTC_CURRENCY_CODES = {
    "EUR": "099741",  # Euro FX
    "GBP": "096742",  # British Pound
    "JPY": "097741",  # Japanese Yen
    "AUD": "232741",  # Australian Dollar
    "CAD": "090741",  # Canadian Dollar
    "CHF": "092741",  # Swiss Franc
    "NZD": "112741",  # New Zealand Dollar
    # USD is the base for all — we infer USD positioning from the other currencies
}

# Cache — COT data updates weekly (Friday afternoon for Tuesday's data)
_cache = {}
_CACHE_TTL = 24 * 3600  # 24 hours


async def get_cot_positioning(pair: str) -> dict:
    """
    Fetch COT positioning data for a currency pair.

    Returns:
      - bias: "BUY" if large speculators favour the base currency,
              "SELL" if they favour the quote currency, "NEUTRAL" if balanced
      - bias_strength: how significant the net positioning is (0-100)
      - net_positions_base: net long/short contracts for base currency
      - net_positions_quote: net long/short contracts for quote currency
      - report_date: date of the COT report used
      - source: "cftc_cot"
    """
    if not config.MCP_CONFIG.get("enable_cot_positioning", True):
        return _neutral_result(pair)

    cache_key = f"cot_{pair}"
    if cache_key in _cache:
        cached_at, cached_data = _cache[cache_key]
        if time.time() - cached_at < _CACHE_TTL:
            return cached_data

    try:
        parts = pair.split("_")
        if len(parts) != 2:
            return _neutral_result(pair)

        base_ccy, quote_ccy = parts[0], parts[1]

        # Fetch net positions for both currencies
        base_data = await _fetch_cot_data(base_ccy)
        quote_data = await _fetch_cot_data(quote_ccy)

        # For USD-based pairs where USD is the quote, we only need the base
        # For USD-based pairs where USD is the base (USD/JPY, USD/CAD, USD/CHF),
        # we need to invert — net long JPY futures = net short USD/JPY
        base_net = base_data.get("net_speculative", 0) if base_data else 0
        quote_net = quote_data.get("net_speculative", 0) if quote_data else 0

        # The pair direction: if base has stronger net long positioning, bias is BUY
        # If quote has stronger net long positioning, bias is SELL
        # For USD pairs: long EUR futures = bullish EUR/USD, long JPY futures = bearish USD/JPY

        # Normalise: positive = bullish for the pair
        if base_ccy == "USD":
            # USD is base (USD/JPY etc.) — net long quote = bearish for pair
            pair_bias_score = -quote_net
        elif quote_ccy == "USD":
            # USD is quote (EUR/USD etc.) — net long base = bullish for pair
            pair_bias_score = base_net
        else:
            # Cross pair (GBP/JPY etc.) — compare net positions
            pair_bias_score = base_net - quote_net

        # Determine bias from the net positioning
        # Thresholds: >10k contracts = meaningful, >30k = strong
        if pair_bias_score > 10000:
            bias = "BUY"
            bias_strength = min(abs(pair_bias_score) / 50000 * 100, 100)
        elif pair_bias_score < -10000:
            bias = "SELL"
            bias_strength = min(abs(pair_bias_score) / 50000 * 100, 100)
        else:
            bias = "NEUTRAL"
            bias_strength = 0

        report_date = (base_data or quote_data or {}).get("report_date", "unknown")

        result = {
            "pair": pair,
            "bias": bias,
            "bias_strength": round(bias_strength, 1),
            "net_positions_base": base_net,
            "net_positions_quote": quote_net,
            "report_date": report_date,
            "source": "cftc_cot",
        }

        _cache[cache_key] = (time.time(), result)
        return result

    except Exception as e:
        logger.warning(f"COT positioning fetch failed for {pair}: {e}")
        return _neutral_result(pair)


async def _fetch_cot_data(currency: str) -> dict:
    """Fetch the latest COT data for a single currency from CFTC."""
    code = CFTC_CURRENCY_CODES.get(currency)
    if not code:
        return None

    cache_key = f"cot_raw_{currency}"
    if cache_key in _cache:
        cached_at, data = _cache[cache_key]
        if time.time() - cached_at < _CACHE_TTL:
            return data

    try:
        import httpx

        # Use the CFTC public data via data.nasdaq.com (formerly Quandl)
        # This endpoint provides COT data without requiring an API key
        url = (
            f"https://data.nasdaq.com/api/v3/datasets/CFTC/{code}_FO_ALL.json"
            f"?rows=1&order=desc"
        )

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)

            if resp.status_code != 200:
                logger.debug(f"CFTC data fetch returned HTTP {resp.status_code} for {currency}")
                return None

            data = resp.json()

        dataset = data.get("dataset", {})
        columns = dataset.get("column_names", [])
        rows = dataset.get("data", [])

        if not rows:
            return None

        row = rows[0]
        col_map = {col: i for i, col in enumerate(columns)}

        # Extract large speculator (non-commercial) positions
        # Column names vary slightly but follow this pattern
        long_idx = col_map.get("Noncommercial Long", col_map.get("Non-Commercial Long"))
        short_idx = col_map.get("Noncommercial Short", col_map.get("Non-Commercial Short"))
        date_idx = col_map.get("Date", 0)

        if long_idx is None or short_idx is None:
            # Try alternative column names
            for col_name, idx in col_map.items():
                if "noncommercial" in col_name.lower() and "long" in col_name.lower():
                    long_idx = idx
                if "noncommercial" in col_name.lower() and "short" in col_name.lower():
                    short_idx = idx

        if long_idx is None or short_idx is None:
            logger.debug(f"COT columns not found for {currency}. Available: {columns}")
            return None

        net_speculative = (row[long_idx] or 0) - (row[short_idx] or 0)
        report_date = row[date_idx] if date_idx < len(row) else "unknown"

        result = {
            "currency": currency,
            "net_speculative": net_speculative,
            "long_contracts": row[long_idx] or 0,
            "short_contracts": row[short_idx] or 0,
            "report_date": report_date,
        }

        _cache[cache_key] = (time.time(), result)
        return result

    except Exception as e:
        logger.debug(f"CFTC COT fetch failed for {currency} ({code}): {e}")
        return None


def _neutral_result(pair: str) -> dict:
    """Return a neutral result when COT data is unavailable."""
    return {
        "pair": pair,
        "bias": "NEUTRAL",
        "bias_strength": 0,
        "net_positions_base": 0,
        "net_positions_quote": 0,
        "report_date": "unavailable",
        "source": "cftc_cot",
    }
