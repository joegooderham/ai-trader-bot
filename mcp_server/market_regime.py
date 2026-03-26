"""
mcp_server/market_regime.py — Market Regime Detection (Free Signals)
─────────────────────────────────────────────────────────────────────
Four free market regime signals fetched via yfinance (no API key needed):

  1. Fear & Greed proxy (VIX level) — risk-on vs risk-off
  2. DXY Dollar Index — USD strength/weakness
  3. VIX (Volatility Index) — market fear level
  4. Treasury Yield Spread (10Y-2Y) — recession indicator

All cached for 1 hour since these are slow-moving macro indicators.

Integration:
  - Called by /context/{pair} in server.py
  - Returns regime data that feeds into confidence modifiers
  - Also injected as LSTM features for pattern learning
"""

import time
from loguru import logger

from bot import config

# Cache — macro regime data changes slowly
_cache = {}
_CACHE_TTL = 3600  # 1 hour


async def get_market_regime(pair: str) -> dict:
    """
    Fetch market regime indicators for confidence scoring.

    Returns:
      - vix: current VIX level
      - vix_regime: "low" (<15), "normal" (15-25), "high" (25-35), "extreme" (>35)
      - dxy: current Dollar Index value
      - dxy_trend: "strengthening", "weakening", or "neutral"
      - dxy_bias: "BUY" or "SELL" for the given pair based on USD position
      - yield_spread: 10Y - 2Y treasury spread
      - yield_signal: "normal", "flattening", or "inverted"
      - fear_greed: 0-100 (derived from VIX: 0=extreme fear, 100=extreme greed)
    """
    cache_key = "market_regime"
    if cache_key in _cache:
        cached_at, cached_data = _cache[cache_key]
        if time.time() - cached_at < _CACHE_TTL:
            # Add pair-specific DXY bias to the cached data
            result = dict(cached_data)
            result["dxy_bias"] = _get_dxy_bias(pair, result.get("dxy_trend", "neutral"))
            return result

    try:
        import yfinance as yf

        # Fetch all three tickers in one go
        data = {}

        # VIX — CBOE Volatility Index
        try:
            vix_ticker = yf.Ticker("^VIX")
            vix_hist = vix_ticker.history(period="5d")
            if not vix_hist.empty:
                vix_current = float(vix_hist["Close"].iloc[-1])
                vix_prev = float(vix_hist["Close"].iloc[-2]) if len(vix_hist) > 1 else vix_current
                data["vix"] = round(vix_current, 2)
                data["vix_change"] = round(vix_current - vix_prev, 2)

                # Classify VIX regime
                if vix_current < 15:
                    data["vix_regime"] = "low"
                elif vix_current < 25:
                    data["vix_regime"] = "normal"
                elif vix_current < 35:
                    data["vix_regime"] = "high"
                else:
                    data["vix_regime"] = "extreme"

                # Fear & Greed proxy (inverted VIX scale)
                # VIX 10 = greed 90, VIX 40 = fear 10
                data["fear_greed"] = max(0, min(100, round(100 - (vix_current - 10) * 3)))
        except Exception as e:
            logger.debug(f"VIX fetch failed: {e}")

        # DXY — US Dollar Index
        try:
            dxy_ticker = yf.Ticker("DX-Y.NYB")
            dxy_hist = dxy_ticker.history(period="5d")
            if not dxy_hist.empty:
                dxy_current = float(dxy_hist["Close"].iloc[-1])
                dxy_prev_5d = float(dxy_hist["Close"].iloc[0])
                data["dxy"] = round(dxy_current, 2)
                dxy_change = dxy_current - dxy_prev_5d

                if dxy_change > 0.3:
                    data["dxy_trend"] = "strengthening"
                elif dxy_change < -0.3:
                    data["dxy_trend"] = "weakening"
                else:
                    data["dxy_trend"] = "neutral"
        except Exception as e:
            logger.debug(f"DXY fetch failed: {e}")

        # Treasury Yield Spread (10Y - 2Y) via FRED if available
        try:
            import os
            fred_key = os.getenv("FRED_API_TOKEN", "")
            if fred_key:
                import httpx
                async with httpx.AsyncClient(timeout=10) as client:
                    # 10Y yield
                    r10 = await client.get(
                        f"https://api.stlouisfed.org/fred/series/observations"
                        f"?series_id=DGS10&api_key={fred_key}&sort_order=desc&limit=1&file_type=json"
                    )
                    # 2Y yield
                    r2 = await client.get(
                        f"https://api.stlouisfed.org/fred/series/observations"
                        f"?series_id=DGS2&api_key={fred_key}&sort_order=desc&limit=1&file_type=json"
                    )

                    y10 = r10.json().get("observations", [{}])[0].get("value", ".")
                    y2 = r2.json().get("observations", [{}])[0].get("value", ".")

                    if y10 != "." and y2 != ".":
                        spread = float(y10) - float(y2)
                        data["yield_spread"] = round(spread, 2)
                        data["yield_10y"] = float(y10)
                        data["yield_2y"] = float(y2)

                        if spread < 0:
                            data["yield_signal"] = "inverted"  # Recession warning
                        elif spread < 0.5:
                            data["yield_signal"] = "flattening"
                        else:
                            data["yield_signal"] = "normal"
        except Exception as e:
            logger.debug(f"Treasury yield fetch failed: {e}")

        # Set defaults for any missing data
        data.setdefault("vix", 20)
        data.setdefault("vix_regime", "normal")
        data.setdefault("fear_greed", 50)
        data.setdefault("dxy", 100)
        data.setdefault("dxy_trend", "neutral")
        data.setdefault("yield_spread", 1.0)
        data.setdefault("yield_signal", "normal")

        _cache[cache_key] = (time.time(), data)

        # Add pair-specific bias
        result = dict(data)
        result["dxy_bias"] = _get_dxy_bias(pair, data.get("dxy_trend", "neutral"))
        return result

    except Exception as e:
        logger.warning(f"Market regime fetch failed: {e}")
        return _default_result(pair)


def _get_dxy_bias(pair: str, dxy_trend: str) -> str:
    """Determine directional bias for a pair based on USD strength.

    DXY strengthening = USD getting stronger:
      - USD is quote (EUR/USD, GBP/USD) → pair goes DOWN → bias SELL
      - USD is base (USD/JPY, USD/CAD) → pair goes UP → bias BUY
    DXY weakening = opposite.
    """
    if dxy_trend == "neutral":
        return "NEUTRAL"

    parts = pair.split("_")
    if len(parts) != 2:
        return "NEUTRAL"

    base, quote = parts

    if dxy_trend == "strengthening":
        # USD stronger
        if quote == "USD":
            return "SELL"   # EUR/USD goes down when USD strong
        elif base == "USD":
            return "BUY"    # USD/JPY goes up when USD strong
        else:
            return "NEUTRAL"  # Cross pair — no direct DXY effect
    else:
        # USD weaker
        if quote == "USD":
            return "BUY"
        elif base == "USD":
            return "SELL"
        else:
            return "NEUTRAL"


def _default_result(pair: str) -> dict:
    return {
        "vix": 20, "vix_regime": "normal", "fear_greed": 50,
        "dxy": 100, "dxy_trend": "neutral", "dxy_bias": "NEUTRAL",
        "yield_spread": 1.0, "yield_signal": "normal",
    }
