"""
data/sources/finnhub_client.py — Finnhub Forex Data Client
────────────────────────────────────────────────────────────
Primary real-time candle source for the trading bot.
Finnhub provides free forex candles at 1/5/15/60-minute resolutions.

Rate Limiting Strategy:
  Finnhub free tier allows 60 API calls per minute.
  We cap ourselves at 55/min to leave a safety buffer.
  A sliding-window rate limiter tracks timestamps of recent calls
  and sleeps if we're about to exceed the budget.

Retry Strategy:
  On HTTP 429 (rate limited) or network errors, we retry with
  exponential backoff: 2s, 4s, 8s, 16s — then give up and let
  the unified data manager fall through to the next source.

API Docs: https://finnhub.io/docs/api/forex-candles
"""

import time
import threading
from collections import deque
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
import pandas as pd
from loguru import logger

from bot import config


# ── Pair Mapping ──────────────────────────────────────────────────────────────
# Finnhub uses "OANDA:" prefix with no underscore, e.g. "OANDA:EUR_USD"
FINNHUB_SYMBOLS = {
    "EUR_USD": "OANDA:EUR_USD",
    "GBP_USD": "OANDA:GBP_USD",
    "USD_JPY": "OANDA:USD_JPY",
    "AUD_USD": "OANDA:AUD_USD",
    "USD_CAD": "OANDA:USD_CAD",
    "USD_CHF": "OANDA:USD_CHF",
    "GBP_JPY": "OANDA:GBP_JPY",
    "EUR_GBP": "OANDA:EUR_GBP",
    "EUR_JPY": "OANDA:EUR_JPY",
    "NZD_USD": "OANDA:NZD_USD",
}

# Map our resolution strings to Finnhub resolution codes
RESOLUTION_MAP = {
    "1":  "1",
    "5":  "5",
    "15": "15",
    "60": "60",
    "M1": "1",
    "M5": "5",
    "M15": "15",
    "H1": "60",
    "H4": "240",     # Finnhub doesn't support H4 natively — we'll aggregate from H1
    "D":  "D",
}

# Finnhub free tier: 60 requests per minute, we cap at 55 for safety
MAX_REQUESTS_PER_MINUTE = 55
BASE_URL = "https://finnhub.io/api/v1"


class FinnhubClient:
    """
    Fetches live forex candle data from Finnhub.

    Thread-safe rate limiter ensures we never exceed 55 requests/minute
    even when multiple scan threads are fetching simultaneously.
    """

    def __init__(self):
        self.api_key = config.FINNHUB_API_KEY
        self._request_timestamps: deque = deque()
        self._lock = threading.Lock()

        if not self.api_key:
            logger.warning("FINNHUB_API_KEY not set — Finnhub client disabled")

    @property
    def is_available(self) -> bool:
        """Check if the client is configured and ready to use."""
        return bool(self.api_key)

    def get_candles(
        self,
        pair: str,
        resolution: str = "60",
        count: int = 60,
    ) -> Optional[pd.DataFrame]:
        """
        Fetch candle data for a forex pair.

        Args:
            pair: Internal pair name e.g. "EUR_USD"
            resolution: Candle resolution — "1", "5", "15", "60" minutes
            count: Number of candles to fetch

        Returns:
            DataFrame with columns [open, high, low, close, volume] indexed by UTC datetime,
            or None if the request fails.
        """
        if not self.is_available:
            return None

        symbol = FINNHUB_SYMBOLS.get(pair)
        if not symbol:
            logger.warning(f"Finnhub: no symbol mapping for {pair}")
            return None

        finnhub_resolution = RESOLUTION_MAP.get(resolution, resolution)

        # Calculate time range — fetch slightly more candles than requested
        # to account for gaps (weekends, holidays)
        minutes_per_candle = int(finnhub_resolution) if finnhub_resolution.isdigit() else 1440
        buffer_factor = 1.5  # Fetch 50% extra to handle market gaps
        to_time = int(datetime.now(timezone.utc).timestamp())
        from_time = to_time - int(count * minutes_per_candle * 60 * buffer_factor)

        params = {
            "symbol": symbol,
            "resolution": finnhub_resolution,
            "from": from_time,
            "to": to_time,
            "token": self.api_key,
        }

        data = self._request_with_retry("/forex/candle", params)
        if data is None or data.get("s") == "no_data":
            logger.debug(f"Finnhub: no data for {pair} at resolution {resolution}")
            return None

        return self._parse_candles(data, count)

    def _parse_candles(self, data: dict, count: int) -> Optional[pd.DataFrame]:
        """Convert Finnhub JSON response to a pandas DataFrame."""
        try:
            timestamps = data.get("t", [])
            if not timestamps:
                return None

            df = pd.DataFrame({
                "open": data["o"],
                "high": data["h"],
                "low": data["l"],
                "close": data["c"],
                "volume": data.get("v", [0] * len(timestamps)),
            }, index=pd.to_datetime(timestamps, unit="s", utc=True))

            df.index.name = "datetime"
            # Return the most recent `count` candles
            return df.tail(count)

        except (KeyError, ValueError) as e:
            logger.error(f"Finnhub: failed to parse candle data: {e}")
            return None

    def _request_with_retry(
        self,
        endpoint: str,
        params: dict,
        max_retries: int = 4,
    ) -> Optional[dict]:
        """
        Make an API request with rate limiting and exponential backoff.

        Rate limiter: sleeps if we're about to exceed 55 req/min.
        Retry: on 429 or network error, waits 2s → 4s → 8s → 16s.
        """
        for attempt in range(max_retries):
            self._wait_for_rate_limit()

            try:
                with httpx.Client(timeout=10) as client:
                    resp = client.get(f"{BASE_URL}{endpoint}", params=params)

                self._record_request()

                if resp.status_code == 200:
                    return resp.json()

                if resp.status_code == 429:
                    # Rate limited by Finnhub — back off
                    wait = 2 ** (attempt + 1)
                    logger.warning(f"Finnhub 429 rate limit — backing off {wait}s (attempt {attempt + 1})")
                    time.sleep(wait)
                    continue

                logger.warning(f"Finnhub HTTP {resp.status_code}: {resp.text[:200]}")
                return None

            except (httpx.ConnectError, httpx.TimeoutException, httpx.ReadTimeout) as e:
                wait = 2 ** (attempt + 1)
                logger.warning(f"Finnhub network error: {e} — retrying in {wait}s (attempt {attempt + 1})")
                time.sleep(wait)

        logger.error(f"Finnhub: all {max_retries} retries exhausted for {endpoint}")
        return None

    def _wait_for_rate_limit(self):
        """
        Sliding-window rate limiter. If we've made 55 requests in the last
        60 seconds, sleep until the oldest request falls outside the window.
        """
        with self._lock:
            now = time.monotonic()

            # Purge timestamps older than 60 seconds
            while self._request_timestamps and (now - self._request_timestamps[0]) > 60:
                self._request_timestamps.popleft()

            if len(self._request_timestamps) >= MAX_REQUESTS_PER_MINUTE:
                # Wait until the oldest request in the window expires
                oldest = self._request_timestamps[0]
                sleep_time = 60 - (now - oldest) + 0.1  # +100ms buffer
                if sleep_time > 0:
                    logger.debug(f"Finnhub rate limiter: sleeping {sleep_time:.1f}s")
                    time.sleep(sleep_time)

    def _record_request(self):
        """Record that we just made a request (for rate limiting)."""
        with self._lock:
            self._request_timestamps.append(time.monotonic())
