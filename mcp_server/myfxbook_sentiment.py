"""
mcp_server/myfxbook_sentiment.py — Myfxbook Community Sentiment (BACKLOG-015)
──────────────────────────────────────────────────────────────────────────────
Fetches community sentiment from Myfxbook — the largest forex social trading
platform with ~100k connected accounts. Shows what percentage of real retail
traders are long vs short on each pair.

This cross-validates the IG Client Sentiment (BACKLOG-013). When both IG and
Myfxbook agree on extreme positioning, the contrarian signal is stronger.

Uses the free Myfxbook community outlook endpoint — no API key required.
Data is scraped from their public outlook page.

Integration:
  - Called by /context/{pair} in server.py alongside other MCP modules
  - Returns community long/short percentages and contrarian bias
  - Used by confidence engine as a secondary contrarian signal
  - Cached for 30 minutes (community sentiment updates periodically)
"""

import time
from loguru import logger

from bot import config

# Myfxbook pair name mapping — their format uses different conventions
MYFXBOOK_PAIR_MAP = {
    "EUR_USD": "EURUSD",
    "GBP_USD": "GBPUSD",
    "USD_JPY": "USDJPY",
    "AUD_USD": "AUDUSD",
    "USD_CAD": "USDCAD",
    "USD_CHF": "USDCHF",
    "GBP_JPY": "GBPJPY",
    "EUR_GBP": "EURGBP",
    "EUR_JPY": "EURJPY",
    "NZD_USD": "NZDUSD",
}

# Cache
_cache = {}
_CACHE_TTL = 30 * 60  # 30 minutes
_outlook_cache = None  # Full outlook page cache (shared across pairs)
_outlook_cache_time = 0


async def get_community_sentiment(pair: str) -> dict:
    """
    Fetch Myfxbook community sentiment for a currency pair.

    Returns:
      - long_percentage: % of Myfxbook community with long positions
      - short_percentage: % of community with short positions
      - contrarian_bias: "BUY" if majority short, "SELL" if majority long, "NEUTRAL"
      - bias_strength: how extreme the positioning is (50-100)
      - source: "myfxbook"
    """
    if not config.MCP_CONFIG.get("enable_myfxbook_sentiment", True):
        return _neutral_result(pair)

    # Check cache
    cache_key = f"myfxbook_{pair}"
    if cache_key in _cache:
        cached_at, cached_data = _cache[cache_key]
        if time.time() - cached_at < _CACHE_TTL:
            return cached_data

    try:
        outlook = await _fetch_community_outlook()
        if not outlook:
            return _neutral_result(pair)

        myfxbook_name = MYFXBOOK_PAIR_MAP.get(pair)
        if not myfxbook_name or myfxbook_name not in outlook:
            return _neutral_result(pair)

        data = outlook[myfxbook_name]
        long_pct = data.get("long", 50.0)
        short_pct = data.get("short", 50.0)

        # Contrarian bias — same logic as IG sentiment
        contrarian_threshold = 65.0
        if long_pct >= contrarian_threshold:
            contrarian_bias = "SELL"
            bias_strength = long_pct
        elif short_pct >= contrarian_threshold:
            contrarian_bias = "BUY"
            bias_strength = short_pct
        else:
            contrarian_bias = "NEUTRAL"
            bias_strength = max(long_pct, short_pct)

        result = {
            "pair": pair,
            "long_percentage": round(long_pct, 1),
            "short_percentage": round(short_pct, 1),
            "contrarian_bias": contrarian_bias,
            "bias_strength": round(bias_strength, 1),
            "source": "myfxbook",
        }

        _cache[cache_key] = (time.time(), result)
        return result

    except Exception as e:
        logger.warning(f"Myfxbook sentiment fetch failed for {pair}: {e}")
        return _neutral_result(pair)


async def _fetch_community_outlook() -> dict:
    """Fetch the full Myfxbook community outlook. Cached across all pairs."""
    global _outlook_cache, _outlook_cache_time

    if _outlook_cache and time.time() - _outlook_cache_time < _CACHE_TTL:
        return _outlook_cache

    try:
        import httpx

        # Myfxbook community outlook — use the API-style endpoint with a
        # realistic browser User-Agent to avoid 403 blocks
        url = "https://www.myfxbook.com/community/outlook"
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Referer": "https://www.myfxbook.com/",
            })

            if resp.status_code != 200:
                logger.debug(f"Myfxbook returned HTTP {resp.status_code}")
                return _outlook_cache or {}

            # Try JSON response first
            try:
                data = resp.json()
                if isinstance(data, list):
                    # Format: [{symbol: "EURUSD", longPercentage: 68.5, shortPercentage: 31.5}, ...]
                    outlook = {}
                    for item in data:
                        symbol = item.get("symbol", "")
                        outlook[symbol] = {
                            "long": item.get("longPercentage", 50),
                            "short": item.get("shortPercentage", 50),
                        }
                    _outlook_cache = outlook
                    _outlook_cache_time = time.time()
                    return outlook
            except Exception:
                pass

            # Fallback: parse HTML page for sentiment data
            text = resp.text
            outlook = _parse_outlook_html(text)
            if outlook:
                _outlook_cache = outlook
                _outlook_cache_time = time.time()
            return outlook or _outlook_cache or {}

    except Exception as e:
        logger.debug(f"Myfxbook outlook fetch failed: {e}")
        return _outlook_cache or {}


def _parse_outlook_html(html: str) -> dict:
    """Parse Myfxbook community outlook page HTML for sentiment data."""
    import re
    outlook = {}

    # Look for patterns like: "EURUSD" ... "68.5%" ... "31.5%"
    # Myfxbook uses various formats, so we try multiple patterns
    for pair_name in MYFXBOOK_PAIR_MAP.values():
        # Pattern: pair name followed by two percentages
        pattern = rf'{pair_name}.*?(\d+\.?\d*)%.*?(\d+\.?\d*)%'
        match = re.search(pattern, html, re.DOTALL)
        if match:
            try:
                long_pct = float(match.group(1))
                short_pct = float(match.group(2))
                # Ensure they sum to ~100%
                if 90 < long_pct + short_pct < 110:
                    outlook[pair_name] = {"long": long_pct, "short": short_pct}
            except (ValueError, IndexError):
                continue

    return outlook


def _neutral_result(pair: str) -> dict:
    """Return a neutral result when Myfxbook data is unavailable."""
    return {
        "pair": pair,
        "long_percentage": 50.0,
        "short_percentage": 50.0,
        "contrarian_bias": "NEUTRAL",
        "bias_strength": 50.0,
        "source": "myfxbook",
    }
