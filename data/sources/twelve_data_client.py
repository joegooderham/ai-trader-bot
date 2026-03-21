"""
data/sources/twelve_data_client.py — Twelve Data Historical Candle Client
──────────────────────────────────────────────────────────────────────────
Provides deep historical candle data for LSTM training and backtesting.
Fetches a full year of daily candles on bot startup, then tops up nightly
at 23:00 UTC with the current day's data.

This is the secondary candle source in the fallback chain:
  Finnhub (live) → Twelve Data (historical) → IG API → pause + alert

Rate Limiting:
  Free tier: 8 API calls per minute, 800 per day.
  We only make bulk calls at startup and once nightly, so usage stays
  well under both limits.

API Docs: https://twelvedata.com/docs
"""

import time
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
import pandas as pd
from loguru import logger

from bot import config


# ── Pair Mapping ──────────────────────────────────────────────────────────────
# Twelve Data uses "EUR/USD" format for forex pairs
TWELVE_DATA_SYMBOLS = {
    "EUR_USD": "EUR/USD",
    "GBP_USD": "GBP/USD",
    "USD_JPY": "USD/JPY",
    "AUD_USD": "AUD/USD",
    "USD_CAD": "USD/CAD",
    "USD_CHF": "USD/CHF",
    "GBP_JPY": "GBP/JPY",
    "EUR_GBP": "EUR/GBP",
    "EUR_JPY": "EUR/JPY",
    "NZD_USD": "NZD/USD",
}

# Resolution mapping — our internal names to Twelve Data interval strings
RESOLUTION_MAP = {
    "1":   "1min",
    "5":   "5min",
    "15":  "15min",
    "60":  "1h",
    "M1":  "1min",
    "M5":  "5min",
    "M15": "15min",
    "H1":  "1h",
    "H4":  "4h",
    "D":   "1day",
}

BASE_URL = "https://api.twelvedata.com"


class TwelveDataClient:
    """
    Fetches historical forex candle data from Twelve Data.

    Primary use cases:
    1. Startup backfill — fetch up to 365 days of historical candles for LSTM training
    2. Nightly top-up — add the current day's candles at 23:00 UTC
    3. Fallback candle source — when Finnhub is unavailable during live scans
    """

    def __init__(self):
        self.api_key = config.TWELVE_DATA_API_KEY
        self._lock = threading.Lock()

        if not self.api_key:
            logger.warning("TWELVE_DATA_API_KEY not set — Twelve Data client disabled")

    @property
    def is_available(self) -> bool:
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
            resolution: Candle resolution e.g. "60", "H1", "D"
            count: Number of candles to return

        Returns:
            DataFrame with [open, high, low, close, volume] indexed by UTC datetime,
            or None on failure.
        """
        if not self.is_available:
            return None

        symbol = TWELVE_DATA_SYMBOLS.get(pair)
        if not symbol:
            logger.warning(f"Twelve Data: no symbol mapping for {pair}")
            return None

        interval = RESOLUTION_MAP.get(resolution, "1h")

        params = {
            "symbol": symbol,
            "interval": interval,
            "outputsize": count,
            "apikey": self.api_key,
            "timezone": "UTC",
        }

        data = self._request("/time_series", params)
        if data is None:
            return None

        return self._parse_candles(data, count)

    def get_historical(
        self,
        pair: str,
        days: int = 365,
        resolution: str = "H1",
    ) -> Optional[pd.DataFrame]:
        """
        Fetch deep historical data for LSTM training.

        Uses date range parameters instead of outputsize to get a specific
        window of historical data. Twelve Data free tier supports up to
        5000 rows per request.

        Args:
            pair: Internal pair name
            days: Number of days of history to fetch
            resolution: Candle resolution (default H1 for LSTM training)

        Returns:
            DataFrame of historical candles, or None on failure.
        """
        if not self.is_available:
            return None

        symbol = TWELVE_DATA_SYMBOLS.get(pair)
        if not symbol:
            return None

        interval = RESOLUTION_MAP.get(resolution, "1h")
        end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        start_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

        params = {
            "symbol": symbol,
            "interval": interval,
            "start_date": start_date,
            "end_date": end_date,
            "apikey": self.api_key,
            "timezone": "UTC",
            "outputsize": 5000,  # Max rows per request on free tier
        }

        data = self._request("/time_series", params)
        if data is None:
            return None

        df = self._parse_candles(data, count=None)
        if df is not None:
            logger.info(f"Twelve Data: fetched {len(df)} historical candles for {pair} ({days} days)")
        return df

    def _parse_candles(self, data: dict, count: Optional[int]) -> Optional[pd.DataFrame]:
        """Convert Twelve Data JSON response to a pandas DataFrame."""
        try:
            values = data.get("values", [])
            if not values:
                return None

            rows = []
            for v in values:
                rows.append({
                    "datetime": pd.to_datetime(v["datetime"], utc=True),
                    "open": float(v["open"]),
                    "high": float(v["high"]),
                    "low": float(v["low"]),
                    "close": float(v["close"]),
                    # Forex volume from Twelve Data is often 0 — that's expected
                    "volume": float(v.get("volume", 0)),
                })

            df = pd.DataFrame(rows).set_index("datetime").sort_index()

            if count is not None:
                df = df.tail(count)

            return df

        except (KeyError, ValueError, TypeError) as e:
            logger.error(f"Twelve Data: failed to parse candle data: {e}")
            return None

    def _request(self, endpoint: str, params: dict) -> Optional[dict]:
        """
        Make an API request to Twelve Data.

        Retries up to 3 times with exponential backoff on network errors.
        Twelve Data returns errors in a structured JSON format with "code" and "message".
        """
        for attempt in range(3):
            try:
                with httpx.Client(timeout=30) as client:
                    resp = client.get(f"{BASE_URL}{endpoint}", params=params)

                if resp.status_code == 200:
                    data = resp.json()

                    # Check for API-level errors in the response body
                    if "code" in data and data["code"] != 200:
                        logger.warning(
                            f"Twelve Data API error {data.get('code')}: {data.get('message', 'unknown')}"
                        )
                        return None

                    return data

                if resp.status_code == 429:
                    wait = 2 ** (attempt + 1)
                    logger.warning(f"Twelve Data 429 rate limit — backing off {wait}s")
                    time.sleep(wait)
                    continue

                logger.warning(f"Twelve Data HTTP {resp.status_code}: {resp.text[:200]}")
                return None

            except (httpx.ConnectError, httpx.TimeoutException) as e:
                wait = 2 ** (attempt + 1)
                logger.warning(f"Twelve Data network error: {e} — retrying in {wait}s")
                time.sleep(wait)

        logger.error(f"Twelve Data: all retries exhausted for {endpoint}")
        return None
