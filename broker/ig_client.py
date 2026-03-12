"""
broker/ig_client.py — IG Group API Client
──────────────────────────────────────────
Handles all communication with the IG Group broker API.
This is the only file in the entire project that talks directly to IG.
Everything else (signals, risk management, reporting) goes through here.

IG API Docs: https://labs.ig.com/rest-trading-api-reference

Authentication:
  IG uses a session-based auth flow:
  1. POST /session with API key, username, password
  2. IG returns CST and X-SECURITY-TOKEN headers
  3. Every request includes both tokens
  4. Tokens expire after 6 hours — auto-refreshed here

Environments:
  Demo: https://demo-api.ig.com/gateway/deal
  Live: https://api.ig.com/gateway/deal

Data Allowance Strategy (IG demo = 10,000 points/week):
  - On first call: fetch full lookback (60 candles)         = 60 points
  - On subsequent calls within same candle period: use cache = 0 points
  - When a new candle period opens: top up with 3 candles   = 3 points
  - 5 pairs × (60 + 3×96 scans/day × 7 days) ≈ 1,320/week  ✅
"""

import httpx
import pandas as pd
import yfinance as yf
from datetime import datetime, timezone, timedelta
from loguru import logger
from typing import Optional
import time

from bot import config

# ── IG Epic Mapping ───────────────────────────────────────────────────────────
IG_EPICS = {
    "EUR_USD": "CS.D.EURUSD.MINI.IP",
    "GBP_USD": "CS.D.GBPUSD.MINI.IP",
    "USD_JPY": "CS.D.USDJPY.MINI.IP",
    "AUD_USD": "CS.D.AUDUSD.MINI.IP",
    "USD_CAD": "CS.D.USDCAD.MINI.IP",
    "USD_CHF": "CS.D.USDCHF.MINI.IP",
    "GBP_JPY": "CS.D.GBPJPY.MINI.IP",
    "EUR_GBP": "CS.D.EURGBP.MINI.IP",
    "EUR_JPY": "CS.D.EURJPY.MINI.IP",
    "NZD_USD": "CS.D.NZDUSD.MINI.IP",
}

# Dealing currency for each pair's IG mini CFD
IG_DEAL_CURRENCY = {
    "EUR_USD": "USD",
    "GBP_USD": "USD",
    "USD_JPY": "JPY",
    "AUD_USD": "USD",
    "USD_CAD": "CAD",
    "USD_CHF": "CHF",
    "GBP_JPY": "JPY",
    "EUR_GBP": "GBP",
    "EUR_JPY": "JPY",
    "NZD_USD": "USD",
}

# ── yfinance Ticker Mapping ──────────────────────────────────────────────────
# yfinance uses "EURUSD=X" format for forex pairs — no underscore, suffix =X
YFINANCE_TICKERS = {
    "EUR_USD": "EURUSD=X",
    "GBP_USD": "GBPUSD=X",
    "USD_JPY": "USDJPY=X",
    "AUD_USD": "AUDUSD=X",
    "USD_CAD": "USDCAD=X",
    "USD_CHF": "USDCHF=X",
    "GBP_JPY": "GBPJPY=X",
    "EUR_GBP": "EURGBP=X",
    "EUR_JPY": "EURJPY=X",
    "NZD_USD": "NZDUSD=X",
}

# Map our timeframe codes to yfinance interval strings
# yfinance supports: 1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h, 1d, 5d, 1wk
YFINANCE_INTERVALS = {
    "M1":  "1m",
    "M5":  "5m",
    "M15": "15m",
    "M30": "30m",
    "H1":  "1h",
    "H4":  "4h",    # yfinance doesn't support 4h — will fall back to 1h
    "D":   "1d",
    "W":   "1wk",
}

# yfinance limits the lookback period based on interval granularity
# e.g. 1m data only available for last 7 days, 1h for last 730 days
YFINANCE_PERIOD_MAP = {
    "1m":  "7d",
    "5m":  "60d",
    "15m": "60d",
    "30m": "60d",
    "1h":  "730d",
    "4h":  "730d",
    "1d":  "max",
    "1wk": "max",
}

# IG resolution strings for candle data
IG_RESOLUTIONS = {
    "M1":  "MINUTE",
    "M5":  "MINUTE_5",
    "M15": "MINUTE_15",
    "M30": "MINUTE_30",
    "H1":  "HOUR",
    "H4":  "HOUR_4",
    "D":   "DAY",
    "W":   "WEEK",
}

# How many minutes each timeframe candle covers — used for cache expiry logic
TIMEFRAME_MINUTES = {
    "M1":  1,
    "M5":  5,
    "M15": 15,
    "M30": 30,
    "H1":  60,
    "H4":  240,
    "D":   1440,
    "W":   10080,
}


class IGClient:
    """
    Wrapper around the IG Group REST API.

    Usage:
        client = IGClient()
        price = client.get_price("EUR_USD")
        client.place_trade("EUR_USD", "BUY", size=1.0)
    """

    def __init__(self):
        self.base_url   = config.IG_BASE_URL
        self.api_key    = config.IG_API_KEY
        self.username   = config.IG_USERNAME
        self.password   = config.IG_PASSWORD
        self.account_id = config.IG_ACCOUNT_ID

        self._cst = None
        self._security_token = None
        self._session_expires = None

        # ── Candle cache ─────────────────────────────────────────────────────
        # Stores { (pair, granularity): {"df": DataFrame, "last_candle_time": datetime} }
        # Avoids re-fetching all 60 candles on every scan — only tops up new ones.
        self._candle_cache: dict = {}

        # Optional Telegram notifier — set via set_notifier() after construction
        # to avoid circular imports (scheduler creates both IGClient and TelegramNotifier)
        self._notifier = None

        # SQLite candle storage — persists candle data across restarts so we
        # never re-fetch historical data we already have
        self._storage = None
        try:
            from data.storage import TradeStorage
            self._storage = TradeStorage()
        except Exception as e:
            logger.warning(f"Could not init candle storage: {e}")

        self._authenticate()
        env = "DEMO" if "demo" in self.base_url else "LIVE ⚠️"
        logger.info(f"Connected to IG Group ({env} account: {self.account_id})")

    def set_notifier(self, notifier):
        """
        Attach a TelegramNotifier instance so the client can send alerts
        when falling back to yfinance. Called by scheduler after both
        IGClient and TelegramNotifier are initialised.
        """
        self._notifier = notifier

    # ── Authentication ────────────────────────────────────────────────────────

    def _authenticate(self):
        """Log in to IG and obtain session tokens."""
        url = f"{self.base_url}/session"
        headers = {
            "Content-Type": "application/json; charset=UTF-8",
            "Accept":       "application/json; charset=UTF-8",
            "X-IG-API-KEY": self.api_key,
            "Version":      "2",
        }
        payload = {
            "identifier": self.username,
            "password":   self.password,
        }

        response = httpx.post(url, json=payload, headers=headers, timeout=15)

        if response.status_code != 200:
            logger.error(f"IG auth failed {response.status_code}: {response.text}")
            response.raise_for_status()

        self._cst             = response.headers.get("CST")
        self._security_token  = response.headers.get("X-SECURITY-TOKEN")
        self._session_expires = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)

        if not self._cst or not self._security_token:
            raise ValueError(
                "IG authentication failed — no session tokens returned. "
                "Check IG_USERNAME, IG_PASSWORD and IG_API_KEY in your secrets."
            )

        # Switch to the correct account explicitly
        switch_headers = {
            "Content-Type":     "application/json; charset=UTF-8",
            "Accept":           "application/json; charset=UTF-8",
            "X-IG-API-KEY":     self.api_key,
            "CST":              self._cst,
            "X-SECURITY-TOKEN": self._security_token,
            "Version":          "1",
        }
        switch_payload = {"accountId": self.account_id, "defaultAccount": True}
        switch_response = httpx.put(url, json=switch_payload, headers=switch_headers, timeout=15)
        if switch_response.status_code == 200:
            logger.debug(f"Switched to account {self.account_id}")
        else:
            logger.warning(f"Account switch returned {switch_response.status_code}: {switch_response.text}")

        logger.debug("IG session authenticated successfully")

    def _headers(self, version: str = "1") -> dict:
        """Build authenticated headers, auto-refreshing session if needed."""
        if self._session_expires and datetime.now(timezone.utc) >= self._session_expires:
            logger.info("IG session expiring — re-authenticating")
            self._authenticate()

        return {
            "Content-Type":     "application/json; charset=UTF-8",
            "Accept":           "application/json; charset=UTF-8",
            "X-IG-API-KEY":     self.api_key,
            "CST":              self._cst,
            "X-SECURITY-TOKEN": self._security_token,
            "IG-ACCOUNT-ID":    self.account_id,
            "Version":          version,
        }

    def _get(self, endpoint: str, version: str = "1") -> dict:
        url = f"{self.base_url}{endpoint}"
        r = httpx.get(url, headers=self._headers(version), timeout=15)
        r.raise_for_status()
        return r.json()

    def _post(self, endpoint: str, payload: dict, version: str = "1") -> dict:
        url = f"{self.base_url}{endpoint}"
        r = httpx.post(url, json=payload, headers=self._headers(version), timeout=15)
        if r.status_code != 200:
            logger.error(f"POST {endpoint} failed {r.status_code}: {r.text}")
        r.raise_for_status()
        return r.json()

    def _delete(self, endpoint: str, payload: dict = None, version: str = "1") -> dict:
        """IG uses POST with _method=DELETE override for some endpoints."""
        url = f"{self.base_url}{endpoint}"
        headers = self._headers(version)
        headers["_method"] = "DELETE"
        r = httpx.post(url, json=payload or {}, headers=headers, timeout=15)
        r.raise_for_status()
        return r.json()

    # ── Account ───────────────────────────────────────────────────────────────

    def _get_account_data(self) -> dict:
        """Fetch the matching account from /accounts list."""
        data = self._get("/accounts")
        accounts = data.get("accounts", [])
        for account in accounts:
            if account.get("accountId") == self.account_id:
                return account
        if accounts:
            logger.warning(
                f"Account {self.account_id} not found — using first account: "
                f"{accounts[0].get('accountId')}"
            )
            return accounts[0]
        return {}

    def get_account_balance(self) -> float:
        """Returns current account balance."""
        account = self._get_account_data()
        return float(account.get("balance", {}).get("balance", 0))

    def get_account_summary(self) -> dict:
        """Returns full account summary — balance, P&L, margin, available funds."""
        account = self._get_account_data()
        b = account.get("balance", {})
        return {
            "balance":     float(b.get("balance", 0)),
            "deposit":     float(b.get("deposit", 0)),
            "profit_loss": float(b.get("profitLoss", 0)),
            "available":   float(b.get("available", 0)),
            "currency":    account.get("currency", "GBP"),
        }

    def get_open_positions_value(self) -> float:
        """Returns approximate total value deployed in open positions."""
        positions = self.get_open_trades()
        return sum(
            float(p.get("dealSize") or 0) * float(p.get("level") or 0)
            for p in positions
        )

    # ── Prices ────────────────────────────────────────────────────────────────

    def get_price(self, pair: str) -> Optional[float]:
        """Get current mid price for a pair."""
        epic = self._pair_to_epic(pair)
        if not epic:
            return None
        try:
            data = self._get(f"/markets/{epic}")
            snap = data.get("snapshot", {})
            bid  = float(snap.get("bid",   0))
            ask  = float(snap.get("offer", 0))
            if bid > 0 and ask > 0:
                return round((bid + ask) / 2, 5)
        except Exception as e:
            logger.error(f"get_price({pair}) failed: {e}")
        return None

    def get_bid_ask(self, pair: str) -> Optional[tuple]:
        """Returns (bid, ask) tuple."""
        epic = self._pair_to_epic(pair)
        if not epic:
            return None
        try:
            data = self._get(f"/markets/{epic}")
            snap = data.get("snapshot", {})
            return float(snap.get("bid", 0)), float(snap.get("offer", 0))
        except Exception as e:
            logger.error(f"get_bid_ask({pair}) failed: {e}")
        return None

    def get_candles(
        self,
        pair: str,
        count: int = 60,
        granularity: str = "H1"
    ) -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV candlestick data for a pair, using a cache to minimise
        IG API data point consumption.

        Strategy:
          - First call: fetch full `count` candles from IG, store in cache.
          - Subsequent calls within the same candle period: return cache as-is.
          - When a new candle period has opened: fetch only 3 candles (top-up),
            append to cache, trim to `count` rows, update cache.

        This reduces IG data usage from ~28,800 points/day to ~1,320/week.
        """
        epic = IG_RESOLUTIONS.get(granularity, "HOUR")
        cache_key = (pair, granularity)
        candle_minutes = TIMEFRAME_MINUTES.get(granularity, 60)
        now = datetime.now(timezone.utc)

        cached = self._candle_cache.get(cache_key)

        if cached is not None:
            last_candle_time = cached["last_candle_time"]
            next_candle_time = last_candle_time + timedelta(minutes=candle_minutes)

            if now < next_candle_time:
                # Still within the same candle period — return cache unchanged
                logger.debug(f"Cache hit for {pair} {granularity} — no API call needed")
                return cached["df"]

            # New candle period has opened — top up with just 3 candles
            logger.debug(f"Cache top-up for {pair} {granularity} — fetching 3 candles")
            new_df = self._fetch_candles_with_fallback(pair, count=3, granularity=granularity)
            if new_df is not None and not new_df.empty:
                combined = pd.concat([cached["df"], new_df])
                combined = combined[~combined.index.duplicated(keep="last")]
                combined = combined.sort_index().tail(count)
                self._candle_cache[cache_key] = {
                    "df":               combined,
                    "last_candle_time": combined.index[-1].to_pydatetime(),
                }
                return combined
            else:
                # Top-up failed — return stale cache rather than nothing
                logger.warning(f"Cache top-up failed for {pair} — returning stale cache")
                return cached["df"]

        # No cache yet — check SQLite for stored candle history before hitting APIs
        if self._storage:
            stored_df = self._storage.get_candles(pair, granularity, count)
            if stored_df is not None and len(stored_df) >= count:
                logger.debug(f"SQLite hit for {pair} {granularity} — {len(stored_df)} candles from disk")
                self._candle_cache[cache_key] = {
                    "df":               stored_df,
                    "last_candle_time": stored_df.index[-1].to_pydatetime(),
                }
                return stored_df

        # SQLite didn't have enough data — do the full fetch
        logger.debug(f"Cache miss for {pair} {granularity} — fetching {count} candles")
        df = self._fetch_candles_with_fallback(pair, count=count, granularity=granularity)
        if df is not None and not df.empty:
            self._candle_cache[cache_key] = {
                "df":               df,
                "last_candle_time": df.index[-1].to_pydatetime(),
            }
            # Persist to SQLite so we never need to re-fetch this data
            if self._storage:
                self._storage.save_candles(pair, granularity, df, source="ig")
        return df

    def _fetch_candles_with_fallback(
        self,
        pair: str,
        count: int,
        granularity: str,
    ) -> Optional[pd.DataFrame]:
        """
        Try IG first, fall back to yfinance on failure (e.g. 403 rate limit).

        yfinance is free with no rate limits, making it a reliable backup when
        IG's demo data allowance is exhausted or the API is temporarily unavailable.
        """
        df = self._fetch_candles_from_ig(pair, count=count, granularity=granularity)
        if df is not None and not df.empty:
            # Persist IG candles to SQLite for historical record
            if self._storage:
                self._storage.save_candles(pair, granularity, df, source="ig")
            return df

        # IG failed — try yfinance as fallback
        logger.warning(f"IG candle fetch failed for {pair} — falling back to yfinance")
        fallback_df = self._fetch_candles_from_yfinance(pair, count=count, granularity=granularity)

        # Notify via Telegram so Joseph knows the bot switched data sources
        if fallback_df is not None and not fallback_df.empty and self._notifier:
            try:
                self._notifier.data_source_fallback(
                    pair=pair,
                    reason="IG API returned no data (possible 403 rate limit or outage)"
                )
            except Exception as e:
                logger.error(f"Failed to send fallback Telegram alert: {e}")

        # Persist yfinance candles to SQLite too
        if fallback_df is not None and not fallback_df.empty and self._storage:
            self._storage.save_candles(pair, granularity, fallback_df, source="yfinance")

        return fallback_df

    def _fetch_candles_from_yfinance(
        self,
        pair: str,
        count: int,
        granularity: str,
    ) -> Optional[pd.DataFrame]:
        """
        Fetch candle data from Yahoo Finance as a free fallback source.

        yfinance has no API key, no rate limits, and no cost. The trade-off is
        slightly delayed data (~15 min) and no volume for some forex pairs,
        but it's good enough to keep the bot scanning when IG is unavailable.
        """
        ticker_symbol = YFINANCE_TICKERS.get(pair)
        if not ticker_symbol:
            logger.error(f"No yfinance ticker mapping for pair: {pair}")
            return None

        interval = YFINANCE_INTERVALS.get(granularity, "1h")
        period = YFINANCE_PERIOD_MAP.get(interval, "730d")

        try:
            ticker = yf.Ticker(ticker_symbol)
            # Fetch more rows than needed so we can trim to exact count,
            # because yfinance doesn't support an exact "max rows" parameter
            df = ticker.history(period=period, interval=interval)

            if df is None or df.empty:
                logger.error(f"yfinance returned no data for {ticker_symbol}")
                return None

            # Normalise column names to match our IG format (lowercase OHLCV)
            df = df.rename(columns={
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
            })

            # Keep only the columns our indicators expect
            df = df[["open", "high", "low", "close", "volume"]]

            # Ensure timezone-aware UTC index to match IG candle format
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            else:
                df.index = df.index.tz_convert("UTC")
            df.index.name = "datetime"

            # Trim to requested count
            df = df.tail(count)

            logger.info(
                f"yfinance fallback: got {len(df)} candles for {pair} "
                f"({interval}, {ticker_symbol})"
            )
            return df

        except Exception as e:
            logger.error(f"yfinance fallback failed for {pair}: {e}")
            return None

    def _fetch_candles_from_ig(
        self,
        pair: str,
        count: int,
        granularity: str,
    ) -> Optional[pd.DataFrame]:
        """Raw candle fetch from IG API — always hits the network."""
        ig_epic = self._pair_to_epic(pair)
        if not ig_epic:
            return None

        resolution = IG_RESOLUTIONS.get(granularity, "HOUR")

        try:
            data = self._get(
                f"/prices/{ig_epic}?resolution={resolution}&max={count}&pageSize=0",
                version="3"
            )
            prices = data.get("prices", [])
            if not prices:
                return None

            rows = []
            for p in prices:
                snap_time = p.get("snapshotTimeUTC", "")
                mid = p.get("openPrice", {})
                rows.append({
                    "datetime": pd.to_datetime(snap_time, utc=True),
                    "open":     float(mid.get("mid") or mid.get("ask", 0)),
                    "high":     float(p.get("highPrice",  {}).get("mid") or
                                      p.get("highPrice",  {}).get("ask", 0)),
                    "low":      float(p.get("lowPrice",   {}).get("mid") or
                                      p.get("lowPrice",   {}).get("ask", 0)),
                    "close":    float(p.get("closePrice", {}).get("mid") or
                                      p.get("closePrice", {}).get("ask", 0)),
                    "volume":   float(p.get("lastTradedVolume", 0)),
                })

            df = pd.DataFrame(rows).set_index("datetime").sort_index()
            return df

        except Exception as e:
            logger.error(f"get_candles({pair}, {granularity}) failed: {e}")
            return None

    def clear_candle_cache(self, pair: Optional[str] = None):
        """
        Clear the candle cache. Pass a pair to clear just that pair,
        or call with no args to wipe everything (e.g. at day rollover).
        """
        if pair:
            keys = [k for k in self._candle_cache if k[0] == pair]
            for k in keys:
                del self._candle_cache[k]
            logger.debug(f"Candle cache cleared for {pair}")
        else:
            self._candle_cache.clear()
            logger.debug("Candle cache fully cleared")

    # ── Trading ───────────────────────────────────────────────────────────────

    def place_trade(
        self,
        pair: str,
        direction: str,
        size: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> Optional[dict]:
        """Open a new CFD position on IG."""
        epic = self._pair_to_epic(pair)
        if not epic:
            logger.error(f"No epic found for pair: {pair}")
            return None

        # IG mini contracts: minimum size 1, rounded to 1 decimal place
        size = max(1.0, round(float(size), 1))

        # Each instrument has its own dealing currency (not always GBP)
        currency_code = IG_DEAL_CURRENCY.get(pair, "USD")

        payload = {
            "epic":           epic,
            "expiry":         "-",
            "direction":      direction.upper(),
            "size":           size,
            "orderType":      "MARKET",
            "timeInForce":    "EXECUTE_AND_ELIMINATE",
            "guaranteedStop": False,
            "forceOpen":      True,
            "currencyCode":   currency_code,
        }

        # SL/TP temporarily disabled for bare order test
        # if stop_loss:
        #     payload["stopLevel"] = round(stop_loss, 5)
        # if take_profit:
        #     payload["limitLevel"] = round(take_profit, 5)

        logger.info(f"Submitting order: {pair} {direction} size={size} | payload={payload}")

        try:
            response = self._post("/positions/otc", payload, version="2")
            deal_ref = response.get("dealReference")

            if not deal_ref:
                logger.error(f"No deal reference returned for {pair} {direction}")
                return None

            time.sleep(0.5)
            confirmation = self._get(f"/confirms/{deal_ref}")
            status = confirmation.get("dealStatus", "UNKNOWN")

            if status == "ACCEPTED":
                logger.info(
                    f"✅ Trade placed: {pair} {direction} size={size} "
                    f"@ {confirmation.get('level')} | Deal: {deal_ref}"
                )
                return {
                    "deal_reference": deal_ref,
                    "deal_id":        confirmation.get("dealId"),
                    "pair":           pair,
                    "direction":      direction,
                    "size":           size,
                    "fill_price":     float(confirmation.get("level", 0)),
                    "stop_loss":      stop_loss,
                    "take_profit":    take_profit,
                    "status":         "ACCEPTED",
                    "opened_at":      datetime.now(timezone.utc).isoformat(),
                }
            else:
                reason = confirmation.get("reason", "Unknown reason")
                logger.error(
                    f"Trade rejected: {pair} {direction} — {reason} "
                    f"| Full response: {confirmation}"
                )
                return None

        except Exception as e:
            logger.error(f"place_trade({pair}, {direction}) failed: {e}")
            return None

    def close_trade(self, deal_id: str, size: float, direction: str) -> Optional[dict]:
        """Close an open CFD position."""
        close_direction = "SELL" if direction.upper() == "BUY" else "BUY"

        payload = {
            "dealId":      deal_id,
            "direction":   close_direction,
            "size":        max(1.0, round(float(size), 1)),
            "orderType":   "MARKET",
            "timeInForce": "EXECUTE_AND_ELIMINATE",
        }

        try:
            response = self._delete("/positions/otc", payload, version="1")
            deal_ref = response.get("dealReference")

            if not deal_ref:
                logger.error(f"No deal reference returned when closing {deal_id}")
                return None

            time.sleep(0.5)
            confirmation = self._get(f"/confirms/{deal_ref}")
            status = confirmation.get("dealStatus", "UNKNOWN")

            if status == "ACCEPTED":
                pl = float(confirmation.get("profit", 0))
                logger.info(f"✅ Position closed: {deal_id} | P&L: {pl:+.2f}")
                return {
                    "deal_reference": deal_ref,
                    "deal_id":        deal_id,
                    "close_price":    float(confirmation.get("level", 0)),
                    "profit_loss":    pl,
                    "status":         "ACCEPTED",
                    "closed_at":      datetime.now(timezone.utc).isoformat(),
                }
            else:
                reason = confirmation.get("reason", "Unknown")
                logger.error(f"Close rejected for {deal_id}: {reason}")
                return None

        except Exception as e:
            logger.error(f"close_trade({deal_id}) failed: {e}")
            return None

    def close_all_positions(self) -> list:
        """Close every open position. Called at EOD."""
        open_trades = self.get_open_trades()
        results = []

        if not open_trades:
            logger.info("EOD close: no open positions to close")
            return results

        logger.info(f"EOD close: closing {len(open_trades)} position(s)")

        for trade in open_trades:
            deal_id   = trade.get("dealId")
            size      = float(trade.get("dealSize", 0))
            direction = trade.get("direction", "BUY")

            result = self.close_trade(deal_id, size, direction)
            if result:
                results.append(result)
            else:
                logger.error(f"Failed to close position {deal_id} during EOD close")

        return results

    # ── Open Positions ────────────────────────────────────────────────────────

    def get_open_trades(self) -> list:
        """Returns all currently open CFD positions."""
        try:
            data = self._get("/positions", version="2")
            positions = data.get("positions", [])

            result = []
            for pos in positions:
                position = pos.get("position", {})
                market   = pos.get("market",   {})
                epic     = market.get("epic",  "")
                pair     = self._epic_to_pair(epic)

                result.append({
                    "dealId":       position.get("dealId"),
                    "pair":         pair,
                    "epic":         epic,
                    "instrument":   pair,
                    "direction":    position.get("direction"),
                    "dealSize":     position.get("dealSize"),
                    "level":        position.get("level"),
                    "currentUnits": position.get("dealSize"),
                    "unrealizedPL": position.get("upl"),
                    "stopLevel":    position.get("stopLevel"),
                    "limitLevel":   position.get("limitLevel"),
                    "openTime":     position.get("createdDateUTC"),
                    "price":        position.get("level"),
                })
            return result

        except Exception as e:
            logger.error(f"get_open_trades() failed: {e}")
            return []

    def get_trade_by_id(self, deal_id: str) -> Optional[dict]:
        """Get details of a specific open trade by deal ID."""
        trades = self.get_open_trades()
        for t in trades:
            if t.get("dealId") == deal_id:
                return t
        return None

    # ── Utilities ─────────────────────────────────────────────────────────────

    def _pair_to_epic(self, pair: str) -> Optional[str]:
        """Convert standard pair name to IG epic."""
        epic = IG_EPICS.get(pair)
        if not epic:
            logger.warning(f"No IG epic mapping found for pair: {pair}")
        return epic

    def _epic_to_pair(self, epic: str) -> str:
        """Convert IG epic back to standard pair name."""
        reverse = {v: k for k, v in IG_EPICS.items()}
        return reverse.get(epic, epic)

    def test_connection(self) -> bool:
        """Quick connection test."""
        try:
            balance = self.get_account_balance()
            logger.info(f"IG connection test passed — Balance: £{balance:.2f}")
            return True
        except Exception as e:
            logger.error(f"IG connection test failed: {e}")
            return False