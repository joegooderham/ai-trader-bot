"""
mcp_server/client_sentiment.py — IG Client Sentiment Module
─────────────────────────────────────────────────────────────
Fetches retail client positioning data from the IG API.

This is a contrarian indicator: when a large majority (>75%) of retail traders
are positioned one direction, the market statistically tends to move against them.
This is well-documented across retail forex (FXCM's SSI, IG's own research).

The sentiment data is fetched via the IG REST API using the same authentication
the bot already has — zero additional cost or API keys required.

Integration:
  - Called by /context/{pair} in server.py alongside other MCP modules
  - Returns a dict with long/short percentages and a contrarian bias signal
  - The confidence engine applies a modifier based on the contrarian signal
"""

from loguru import logger
from bot import config


async def get_client_sentiment(pair: str, ig_client=None) -> dict:
    """
    Fetch IG client sentiment for a currency pair.

    Returns a dict with:
      - long_percentage: % of IG clients with long positions
      - short_percentage: % of IG clients with short positions
      - contrarian_bias: "BUY" if majority are short, "SELL" if majority are long, "NEUTRAL" if balanced
      - bias_strength: how extreme the positioning is (0-100, where 100 = 100% one-sided)

    The contrarian logic: if >65% of retail clients are long, the contrarian signal is SELL,
    because retail traders are historically wrong at extremes. The threshold is intentionally
    lower than the 75% modifier threshold in confidence.py — this gives an early directional
    hint while the confidence modifier only applies a score penalty at stronger extremes.
    """
    # Only fetch if the feature is enabled in config
    if not config.MCP_CONFIG.get("enable_client_sentiment", True):
        return _neutral_result(pair)

    try:
        # Use shared IG client if provided, otherwise create one (fallback)
        if ig_client is None:
            from broker.ig_client import IGClient
            ig_client = IGClient()
        sentiment = ig_client.get_client_sentiment(pair)

        if not sentiment:
            return _neutral_result(pair)

        long_pct = sentiment["long_percentage"]
        short_pct = sentiment["short_percentage"]

        # Determine contrarian bias — the opposite of where the crowd is positioned
        # 65% threshold chosen because IG's own research shows contrarian signals
        # become statistically significant around this level
        contrarian_threshold = 65.0

        if long_pct >= contrarian_threshold:
            # Majority are long → contrarian says SELL
            contrarian_bias = "SELL"
            bias_strength = long_pct  # Higher % = stronger contrarian signal
        elif short_pct >= contrarian_threshold:
            # Majority are short → contrarian says BUY
            contrarian_bias = "BUY"
            bias_strength = short_pct
        else:
            contrarian_bias = "NEUTRAL"
            bias_strength = max(long_pct, short_pct)

        return {
            "pair": pair,
            "long_percentage": long_pct,
            "short_percentage": short_pct,
            "contrarian_bias": contrarian_bias,
            "bias_strength": bias_strength,
        }

    except Exception as e:
        # Fail gracefully — sentiment is a bonus signal, not critical
        logger.warning(f"Client sentiment fetch failed for {pair}: {e}")
        return _neutral_result(pair)


def _neutral_result(pair: str) -> dict:
    """Return a neutral result when sentiment data is unavailable."""
    return {
        "pair": pair,
        "long_percentage": 50.0,
        "short_percentage": 50.0,
        "contrarian_bias": "NEUTRAL",
        "bias_strength": 50.0,
    }
