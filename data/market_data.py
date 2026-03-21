"""
data/market_data.py — Unified Market Data Manager
───────────────────────────────────────────────────
Single interface for ALL market data in the trading bot. No other module
should call external data APIs directly — everything goes through here.

This manager:
  1. Exposes a clean API: get_candles(), get_indicators(), get_sentiment(), get_historical()
  2. Implements the fallback chain: Finnhub → Twelve Data → IG API → pause + alert
  3. Logs which source was used for every data fetch (for transparency and debugging)
  4. Initialises all three external data clients on startup

The fallback chain ensures the bot keeps running even if one provider goes down.
If ALL sources fail for a pair, we send a Telegram alert and skip that pair
rather than trading on stale data.

Architecture:
  ┌─────────────┐
  │ scheduler.py │
  └──────┬───────┘
         │ get_candles() / get_indicators() / get_sentiment()
         ▼
  ┌──────────────────┐
  │  market_data.py  │  ← YOU ARE HERE
  │  (unified mgr)   │
  └──┬───┬───┬───┬───┘
     │   │   │   │
     ▼   ▼   ▼   ▼
  Finn  12D  IG  AlphaV
  hub   ata  API  antage
"""

import threading
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
from loguru import logger

from bot import config


class MarketDataManager:
    """
    Unified market data interface for the entire trading bot.

    Initialises lazily — clients are only created when first needed,
    so missing API keys don't crash the bot on import.
    """

    def __init__(self, ig_client=None, notifier=None):
        """
        Args:
            ig_client: The existing IGClient instance (fallback candle source)
            notifier: TelegramNotifier for sending alerts when all sources fail
        """
        self._ig_client = ig_client
        self._notifier = notifier

        # Lazy-initialised clients — created on first call
        self._finnhub = None
        self._alpha_vantage = None
        self._twelve_data = None
        self._initialised = False
        self._lock = threading.Lock()

        # Track which source was used per pair for logging and /datastatus
        self._last_source: dict = {}  # pair → source name

    def _ensure_initialised(self):
        """Lazy-load all data source clients on first use."""
        if self._initialised:
            return

        with self._lock:
            if self._initialised:
                return

            # Import here to avoid circular imports and so missing API keys
            # don't prevent the rest of the bot from starting
            if config.FINNHUB_ENABLED:
                try:
                    from data.sources.finnhub_client import FinnhubClient
                    self._finnhub = FinnhubClient()
                    if self._finnhub.is_available:
                        logger.info("Finnhub client initialised")
                    else:
                        self._finnhub = None
                except Exception as e:
                    logger.warning(f"Failed to initialise Finnhub client: {e}")

            if config.ALPHA_VANTAGE_ENABLED:
                try:
                    from data.sources.alpha_vantage_client import AlphaVantageClient
                    self._alpha_vantage = AlphaVantageClient()
                    if self._alpha_vantage.is_available:
                        logger.info("Alpha Vantage client initialised")
                    else:
                        self._alpha_vantage = None
                except Exception as e:
                    logger.warning(f"Failed to initialise Alpha Vantage client: {e}")

            if config.TWELVE_DATA_ENABLED:
                try:
                    from data.sources.twelve_data_client import TwelveDataClient
                    self._twelve_data = TwelveDataClient()
                    if self._twelve_data.is_available:
                        logger.info("Twelve Data client initialised")
                    else:
                        self._twelve_data = None
                except Exception as e:
                    logger.warning(f"Failed to initialise Twelve Data client: {e}")

            self._initialised = True

    # ── Public API ────────────────────────────────────────────────────────────

    def get_candles(
        self,
        pair: str,
        resolution: str = "H1",
        count: int = 60,
    ) -> Optional[pd.DataFrame]:
        """
        Fetch live candle data using the fallback chain:
          Finnhub → Twelve Data → IG API → None (+ alert)

        Args:
            pair: Internal pair name e.g. "EUR_USD"
            resolution: Candle resolution e.g. "H1", "M15", "60"
            count: Number of candles to return

        Returns:
            DataFrame with [open, high, low, close, volume] indexed by UTC datetime,
            or None if all sources fail.
        """
        self._ensure_initialised()

        # Source 1: Finnhub (primary live candle source)
        if self._finnhub:
            try:
                df = self._finnhub.get_candles(pair, resolution=resolution, count=count)
                if df is not None and len(df) >= 1:
                    self._record_source(pair, "finnhub")
                    return df
            except Exception as e:
                logger.warning(f"Finnhub failed for {pair}: {e}")

        # Source 2: Twelve Data (secondary candle source)
        if self._twelve_data:
            try:
                df = self._twelve_data.get_candles(pair, resolution=resolution, count=count)
                if df is not None and len(df) >= 1:
                    self._record_source(pair, "twelve_data")
                    return df
            except Exception as e:
                logger.warning(f"Twelve Data failed for {pair}: {e}")

        # Source 3: IG API (existing broker client — last resort before failure)
        if self._ig_client:
            try:
                df = self._ig_client.get_candles(pair, count=count, granularity=resolution)
                if df is not None and len(df) >= 1:
                    self._record_source(pair, "ig_api")
                    return df
            except Exception as e:
                logger.warning(f"IG API failed for {pair}: {e}")

        # All sources failed — alert and return None
        self._record_source(pair, "FAILED")
        self._alert_all_sources_failed(pair)
        return None

    def get_indicators(self, pair: str) -> Optional[dict]:
        """
        Fetch pre-computed technical indicators from Alpha Vantage.

        Returns a dict containing RSI, MACD, Bollinger Bands, and EMA values,
        or None if Alpha Vantage is unavailable or the daily limit is reached.

        These are used as a cross-validation source alongside locally-computed
        indicators — they don't replace the bot's own indicator calculations.
        """
        self._ensure_initialised()

        if self._alpha_vantage:
            try:
                return self._alpha_vantage.get_indicators(pair)
            except Exception as e:
                logger.warning(f"Alpha Vantage indicators failed for {pair}: {e}")

        return None

    def get_sentiment(self, pair: str) -> Optional[dict]:
        """
        Fetch news sentiment score for a pair.

        Returns:
            {
                "score": float,       # -1.0 (bearish) to +1.0 (bullish)
                "label": str,         # "Bullish", "Bearish", "Neutral"
                "article_count": int
            }
            or None if unavailable.

        The sentiment score feeds into the confidence engine at 10% weight,
        providing a news-driven directional bias alongside technical signals.
        """
        self._ensure_initialised()

        if self._alpha_vantage:
            try:
                return self._alpha_vantage.get_sentiment(pair)
            except Exception as e:
                logger.warning(f"Alpha Vantage sentiment failed for {pair}: {e}")

        return None

    def get_historical(
        self,
        pair: str,
        days: int = 365,
        resolution: str = "H1",
    ) -> Optional[pd.DataFrame]:
        """
        Fetch deep historical candle data for LSTM training.

        Uses Twelve Data as the primary source (supports up to 5000 rows).
        Falls back to IG API with yfinance if Twelve Data is unavailable.

        Args:
            pair: Internal pair name
            days: Number of days of history (default 365)
            resolution: Candle resolution (default H1)

        Returns:
            DataFrame of historical candles, or None on failure.
        """
        self._ensure_initialised()

        # Primary: Twelve Data (best for deep historical data)
        if self._twelve_data:
            try:
                df = self._twelve_data.get_historical(pair, days=days, resolution=resolution)
                if df is not None and len(df) >= 1:
                    logger.info(f"Historical data for {pair}: {len(df)} candles from Twelve Data")
                    return df
            except Exception as e:
                logger.warning(f"Twelve Data historical failed for {pair}: {e}")

        # Fallback: IG API (limited by data point allowance)
        if self._ig_client:
            try:
                df = self._ig_client.get_candles(pair, count=min(days * 24, 500), granularity=resolution)
                if df is not None and len(df) >= 1:
                    logger.info(f"Historical data for {pair}: {len(df)} candles from IG API (fallback)")
                    return df
            except Exception as e:
                logger.warning(f"IG API historical failed for {pair}: {e}")

        logger.warning(f"No historical data source available for {pair}")
        return None

    def backfill_all_pairs(self):
        """
        Startup task: fetch historical data for all configured pairs and
        store in SQLite for LSTM training. Called once on bot startup.

        Only fetches if Twelve Data is available — otherwise skips silently
        since IG API's data point budget shouldn't be spent on bulk history.
        """
        self._ensure_initialised()

        if not self._twelve_data:
            logger.info("Twelve Data not configured — skipping historical backfill")
            return

        from data.storage import TradeStorage
        storage = TradeStorage()
        days = config.HISTORICAL_BACKFILL_DAYS

        for pair in config.PAIRS:
            try:
                df = self._twelve_data.get_historical(pair, days=days, resolution="H1")
                if df is not None and len(df) > 0:
                    storage.save_candles(pair, "H1", df, source="twelve_data")
                    logger.info(f"Backfilled {len(df)} candles for {pair} from Twelve Data")
                else:
                    logger.warning(f"No historical data returned for {pair}")
            except Exception as e:
                logger.error(f"Failed to backfill {pair}: {e}")

    def nightly_topup(self):
        """
        Nightly job (23:00 UTC): top up SQLite with the current day's candles
        from Twelve Data. Called by the scheduler.
        """
        self._ensure_initialised()

        if not self._twelve_data:
            return

        from data.storage import TradeStorage
        storage = TradeStorage()

        for pair in config.PAIRS:
            try:
                # Fetch today's hourly candles (up to 24 for a full day)
                df = self._twelve_data.get_candles(pair, resolution="H1", count=24)
                if df is not None and len(df) > 0:
                    storage.save_candles(pair, "H1", df, source="twelve_data")
                    logger.debug(f"Nightly top-up: {len(df)} candles for {pair}")
            except Exception as e:
                logger.error(f"Nightly top-up failed for {pair}: {e}")

    def get_source_status(self) -> dict:
        """
        Return the last-used data source per pair.
        Used by the /datastatus Telegram command for visibility.
        """
        return dict(self._last_source)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _record_source(self, pair: str, source: str):
        """Log which data source was used for a pair."""
        self._last_source[pair] = source
        if source != "FAILED":
            logger.debug(f"Data source for {pair}: {source}")

    def _alert_all_sources_failed(self, pair: str):
        """Send a Telegram alert when all data sources fail for a pair."""
        logger.error(f"ALL data sources failed for {pair} — skipping this pair")
        if self._notifier:
            try:
                self._notifier._send_system(
                    f"⚠️ *Data Source Failure*\n"
                    f"─────────────────────────\n"
                    f"All data sources failed for *{pair}*\n"
                    f"Finnhub: {'configured' if self._finnhub else 'not configured'}\n"
                    f"Twelve Data: {'configured' if self._twelve_data else 'not configured'}\n"
                    f"IG API: {'configured' if self._ig_client else 'not configured'}\n\n"
                    f"_This pair will be skipped until a source recovers._"
                )
            except Exception:
                pass  # Don't let notification failure crash the scan
