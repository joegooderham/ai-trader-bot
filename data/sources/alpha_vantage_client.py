"""
data/sources/alpha_vantage_client.py — Alpha Vantage Technical Indicators & Sentiment
──────────────────────────────────────────────────────────────────────────────────────
Fetches pre-computed technical indicators (RSI, MACD, Bollinger Bands, EMA) and
news sentiment scores from Alpha Vantage. These feed directly into the confidence
scoring engine as a cross-validation source for our locally-computed indicators.

Rate Limiting Strategy:
  Free tier: 25 API calls per day. We cache results for 60 minutes and only
  re-fetch when the cache expires. With 5 pairs × 5 endpoints = 25 calls,
  we stay right at the daily limit by fetching once per pair per day and
  relying on cache for the rest.

  The daily call counter resets at midnight UTC. If we've already used 20
  calls today, we stop fetching and return cached/None until tomorrow.

API Docs: https://www.alphavantage.co/documentation/
"""

import time
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from loguru import logger

from bot import config


# ── Pair Mapping ──────────────────────────────────────────────────────────────
# Alpha Vantage uses "EUR/USD" format for forex pairs
AV_SYMBOLS = {
    "EUR_USD": ("EUR", "USD"),
    "GBP_USD": ("GBP", "USD"),
    "USD_JPY": ("USD", "JPY"),
    "AUD_USD": ("AUD", "USD"),
    "USD_CAD": ("USD", "CAD"),
    "USD_CHF": ("USD", "CHF"),
    "GBP_JPY": ("GBP", "JPY"),
    "EUR_GBP": ("EUR", "GBP"),
    "EUR_JPY": ("EUR", "JPY"),
    "NZD_USD": ("NZD", "USD"),
}

# For news sentiment, Alpha Vantage uses ticker format like "FOREX:EUR"
AV_SENTIMENT_TICKERS = {
    "EUR_USD": "FOREX:EUR",
    "GBP_USD": "FOREX:GBP",
    "USD_JPY": "FOREX:JPY",
    "AUD_USD": "FOREX:AUD",
    "USD_CAD": "FOREX:CAD",
    "USD_CHF": "FOREX:CHF",
    "GBP_JPY": "FOREX:GBP",
    "EUR_GBP": "FOREX:EUR",
    "EUR_JPY": "FOREX:EUR",
    "NZD_USD": "FOREX:NZD",
}

BASE_URL = "https://www.alphavantage.co/query"

# Daily call budget — free tier is 25/day, we cap at 20 for safety
MAX_DAILY_CALLS = 20


class AlphaVantageClient:
    """
    Fetches technical indicators and news sentiment from Alpha Vantage.

    Results are cached in memory for 60 minutes (configurable via config.yaml).
    The daily call counter prevents exceeding the free tier's 25-call limit.
    """

    def __init__(self):
        self.api_key = config.ALPHA_VANTAGE_API_KEY
        self._cache: dict = {}  # key → (timestamp, data)
        self._cache_ttl = config.INDICATOR_CACHE_MINUTES * 60  # seconds
        self._daily_calls = 0
        self._daily_reset_date: str = ""
        self._lock = threading.Lock()

        if not self.api_key:
            logger.warning("ALPHA_VANTAGE_API_KEY not set — Alpha Vantage client disabled")

    @property
    def is_available(self) -> bool:
        return bool(self.api_key)

    def get_indicators(self, pair: str) -> Optional[dict]:
        """
        Fetch RSI, MACD, Bollinger Bands, and EMA for a pair.
        Returns a dict with all indicator values, or None on failure.

        The result is cached for 60 minutes — subsequent calls within
        that window return instantly from cache without an API call.
        """
        if not self.is_available:
            return None

        cache_key = f"indicators:{pair}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        from_symbol, to_symbol = AV_SYMBOLS.get(pair, (None, None))
        if not from_symbol:
            return None

        indicators = {}

        # Fetch each indicator — bail early if daily limit reached
        rsi = self._fetch_rsi(from_symbol, to_symbol)
        if rsi is not None:
            indicators["rsi"] = rsi

        macd = self._fetch_macd(from_symbol, to_symbol)
        if macd is not None:
            indicators["macd"] = macd

        bbands = self._fetch_bbands(from_symbol, to_symbol)
        if bbands is not None:
            indicators["bollinger"] = bbands

        ema = self._fetch_ema(from_symbol, to_symbol)
        if ema is not None:
            indicators["ema"] = ema

        if indicators:
            self._set_cached(cache_key, indicators)
            logger.debug(f"Alpha Vantage indicators for {pair}: {list(indicators.keys())}")
            return indicators

        return None

    def get_sentiment(self, pair: str) -> Optional[dict]:
        """
        Fetch news sentiment score for a pair from Alpha Vantage.

        Returns:
            {
                "score": float,       # -1.0 (bearish) to +1.0 (bullish)
                "label": str,         # "Bullish", "Bearish", "Neutral"
                "article_count": int  # Number of articles analysed
            }
        """
        if not self.is_available:
            return None

        cache_key = f"sentiment:{pair}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        ticker = AV_SENTIMENT_TICKERS.get(pair)
        if not ticker:
            return None

        if not self._can_make_call():
            logger.debug("Alpha Vantage daily limit reached — returning cached sentiment")
            return None

        params = {
            "function": "NEWS_SENTIMENT",
            "tickers": ticker,
            "apikey": self.api_key,
        }

        data = self._request(params)
        if data is None or "feed" not in data:
            return None

        # Average the sentiment scores across recent articles
        articles = data.get("feed", [])
        if not articles:
            return {"score": 0.0, "label": "Neutral", "article_count": 0}

        scores = []
        for article in articles[:20]:  # Cap at 20 most recent
            for ticker_data in article.get("ticker_sentiment", []):
                if ticker_data.get("ticker") == ticker:
                    try:
                        scores.append(float(ticker_data.get("ticker_sentiment_score", 0)))
                    except (ValueError, TypeError):
                        pass

        if not scores:
            result = {"score": 0.0, "label": "Neutral", "article_count": len(articles)}
        else:
            avg_score = sum(scores) / len(scores)
            # Classify: > 0.15 = bullish, < -0.15 = bearish, otherwise neutral
            if avg_score > 0.15:
                label = "Bullish"
            elif avg_score < -0.15:
                label = "Bearish"
            else:
                label = "Neutral"

            result = {
                "score": round(avg_score, 4),
                "label": label,
                "article_count": len(articles),
            }

        self._set_cached(cache_key, result)
        logger.debug(f"Alpha Vantage sentiment for {pair}: {result['label']} ({result['score']})")
        return result

    # ── Individual Indicator Fetchers ────────────────────────────────────────

    def _fetch_rsi(self, from_sym: str, to_sym: str) -> Optional[dict]:
        """Fetch RSI (14-period, 60-min interval)."""
        if not self._can_make_call():
            return None

        data = self._request({
            "function": "RSI",
            "symbol": f"{from_sym}{to_sym}",
            "interval": "60min",
            "time_period": 14,
            "series_type": "close",
            "apikey": self.api_key,
        })
        if not data:
            return None

        # Extract the most recent RSI value
        analysis = data.get("Technical Analysis: RSI", {})
        if not analysis:
            return None

        latest_key = next(iter(analysis), None)
        if latest_key:
            return {"value": float(analysis[latest_key].get("RSI", 50)), "timestamp": latest_key}
        return None

    def _fetch_macd(self, from_sym: str, to_sym: str) -> Optional[dict]:
        """Fetch MACD (12, 26, 9 — standard settings, 60-min interval)."""
        if not self._can_make_call():
            return None

        data = self._request({
            "function": "MACD",
            "symbol": f"{from_sym}{to_sym}",
            "interval": "60min",
            "series_type": "close",
            "apikey": self.api_key,
        })
        if not data:
            return None

        analysis = data.get("Technical Analysis: MACD", {})
        if not analysis:
            return None

        latest_key = next(iter(analysis), None)
        if latest_key:
            entry = analysis[latest_key]
            return {
                "macd": float(entry.get("MACD", 0)),
                "signal": float(entry.get("MACD_Signal", 0)),
                "histogram": float(entry.get("MACD_Hist", 0)),
                "timestamp": latest_key,
            }
        return None

    def _fetch_bbands(self, from_sym: str, to_sym: str) -> Optional[dict]:
        """Fetch Bollinger Bands (20-period, 2 std dev, 60-min interval)."""
        if not self._can_make_call():
            return None

        data = self._request({
            "function": "BBANDS",
            "symbol": f"{from_sym}{to_sym}",
            "interval": "60min",
            "time_period": 20,
            "series_type": "close",
            "nbdevup": 2,
            "nbdevdn": 2,
            "apikey": self.api_key,
        })
        if not data:
            return None

        analysis = data.get("Technical Analysis: BBANDS", {})
        if not analysis:
            return None

        latest_key = next(iter(analysis), None)
        if latest_key:
            entry = analysis[latest_key]
            return {
                "upper": float(entry.get("Real Upper Band", 0)),
                "middle": float(entry.get("Real Middle Band", 0)),
                "lower": float(entry.get("Real Lower Band", 0)),
                "timestamp": latest_key,
            }
        return None

    def _fetch_ema(self, from_sym: str, to_sym: str) -> Optional[dict]:
        """Fetch EMA (20-period and 50-period for crossover detection)."""
        if not self._can_make_call():
            return None

        # We only count this as one API call — fetch EMA 20
        data = self._request({
            "function": "EMA",
            "symbol": f"{from_sym}{to_sym}",
            "interval": "60min",
            "time_period": 20,
            "series_type": "close",
            "apikey": self.api_key,
        })
        if not data:
            return None

        analysis = data.get("Technical Analysis: EMA", {})
        if not analysis:
            return None

        latest_key = next(iter(analysis), None)
        if latest_key:
            return {
                "ema_20": float(analysis[latest_key].get("EMA", 0)),
                "timestamp": latest_key,
            }
        return None

    # ── HTTP & Caching ────────────────────────────────────────────────────────

    def _request(self, params: dict) -> Optional[dict]:
        """Make an API request and increment the daily call counter."""
        try:
            with httpx.Client(timeout=15) as client:
                resp = client.get(BASE_URL, params=params)

            self._increment_daily_calls()

            if resp.status_code == 200:
                data = resp.json()
                # Alpha Vantage returns errors as JSON with "Error Message" or "Note" keys
                if "Error Message" in data:
                    logger.warning(f"Alpha Vantage error: {data['Error Message']}")
                    return None
                if "Note" in data:
                    # Rate limit note — stop making calls
                    logger.warning(f"Alpha Vantage rate limit note: {data['Note']}")
                    return None
                return data

            logger.warning(f"Alpha Vantage HTTP {resp.status_code}")
            return None

        except (httpx.ConnectError, httpx.TimeoutException) as e:
            logger.warning(f"Alpha Vantage network error: {e}")
            return None

    def _can_make_call(self) -> bool:
        """Check if we haven't exceeded the daily call limit."""
        with self._lock:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if self._daily_reset_date != today:
                # New day — reset counter
                self._daily_calls = 0
                self._daily_reset_date = today

            return self._daily_calls < MAX_DAILY_CALLS

    def _increment_daily_calls(self):
        """Thread-safe increment of the daily call counter."""
        with self._lock:
            self._daily_calls += 1
            logger.debug(f"Alpha Vantage daily calls: {self._daily_calls}/{MAX_DAILY_CALLS}")

    def _get_cached(self, key: str) -> Optional[dict]:
        """Return cached data if it exists and hasn't expired."""
        if key in self._cache:
            cached_time, data = self._cache[key]
            if (time.time() - cached_time) < self._cache_ttl:
                logger.debug(f"Alpha Vantage cache hit: {key}")
                return data
            # Expired — remove stale entry
            del self._cache[key]
        return None

    def _set_cached(self, key: str, data: dict):
        """Store data in the in-memory cache."""
        self._cache[key] = (time.time(), data)
