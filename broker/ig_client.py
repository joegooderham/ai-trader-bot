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
"""

import httpx
import pandas as pd
from datetime import datetime, timezone, timedelta
from loguru import logger
from typing import Optional
import time

from bot import config

# ── IG Epic Mapping ───────────────────────────────────────────────────────────
# Maps standard pair names to IG's internal epic identifiers (mini CFD contracts)
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


class IGClient:
    """
    Wrapper around the IG Group REST API.

    Provides the same interface as the original OandaClient so the
    rest of the bot works without any changes. Only this file knows
    about IG — everything else uses standard pair names and methods.

    Usage:
        client = IGClient()
        price = client.get_price("EUR_USD")
        client.place_trade("EUR_USD", "BUY", size=1.0)
    """

    def __init__(self):
        self.base_url = config.IG_BASE_URL
        self.api_key  = config.IG_API_KEY
        self.username = config.IG_USERNAME
        self.password = config.IG_PASSWORD
        self.account_id = config.IG_ACCOUNT_ID

        # Session tokens — refreshed automatically every 5.5 hours
        self._cst = None
        self._security_token = None
        self._session_expires = None

        self._authenticate()
        env = "DEMO" if "demo" in self.base_url else "LIVE ⚠️"
        logger.info(f"Connected to IG Group ({env} account: {self.account_id})")

    # ── Authentication ────────────────────────────────────────────────────────

    def _authenticate(self):
        """Log in to IG and obtain session tokens."""
        url = f"{self.base_url}/session"
        headers = {
            "Content-Type":  "application/json; charset=UTF-8",
            "Accept":        "application/json; charset=UTF-8",
            "X-IG-API-KEY":  self.api_key,
            "Version":       "2",
        }
        payload = {
            "identifier": self.username,
            "password":   self.password,
        }

        response = httpx.post(url, json=payload, headers=headers, timeout=15)

        if response.status_code != 200:
            logger.error(f"IG auth failed {response.status_code}: {response.text}")
            response.raise_for_status()

        self._cst            = response.headers.get("CST")
        self._security_token = response.headers.get("X-SECURITY-TOKEN")
        self._session_expires = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)

        if not self._cst or not self._security_token:
            raise ValueError(
                "IG authentication failed — no session tokens returned. "
                "Check IG_USERNAME, IG_PASSWORD and IG_API_KEY in your secrets."
            )
        logger.debug("IG session authenticated successfully")

    def _headers(self, version: str = "1") -> dict:
        """Build authenticated headers, auto-refreshing session if needed."""
        if self._session_expires and datetime.now(timezone.utc) >= self._session_expires:
            logger.info("IG session expiring — re-authenticating")
            self._authenticate()

        return {
            "Content-Type":      "application/json; charset=UTF-8",
            "Accept":            "application/json; charset=UTF-8",
            "X-IG-API-KEY":      self.api_key,
            "CST":               self._cst,
            "X-SECURITY-TOKEN":  self._security_token,
            "Version":           version,
        }

    def _get(self, endpoint: str, version: str = "1") -> dict:
        url = f"{self.base_url}{endpoint}"
        r = httpx.get(url, headers=self._headers(version), timeout=15)
        r.raise_for_status()
        return r.json()

    def _post(self, endpoint: str, payload: dict, version: str = "1") -> dict:
        url = f"{self.base_url}{endpoint}"
        r = httpx.post(url, json=payload, headers=self._headers(version), timeout=15)
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

    def get_account_balance(self) -> float:
        """Returns current account balance."""
        data = self._get(f"/accounts/{self.account_id}")
        return float(data.get("balance", {}).get("balance", 0))

    def get_account_summary(self) -> dict:
        """Returns full account summary — balance, P&L, margin, available funds."""
        data = self._get(f"/accounts/{self.account_id}")
        b = data.get("balance", {})
        return {
            "balance":     float(b.get("balance", 0)),
            "deposit":     float(b.get("deposit", 0)),
            "profit_loss": float(b.get("profitLoss", 0)),
            "available":   float(b.get("available", 0)),
            "currency":    data.get("currency", "GBP"),
        }

    def get_open_positions_value(self) -> float:
        """Returns approximate total value deployed in open positions."""
        positions = self.get_open_trades()
        return sum(
            float(p.get("dealSize", 0)) * float(p.get("level", 0))
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
        count: int = 100,
        granularity: str = "H1"
    ) -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV candlestick data for a pair.

        Returns a DataFrame with columns: open, high, low, close, volume
        with a UTC datetime index. Returns None if data unavailable.
        """
        epic = self._pair_to_epic(pair)
        if not epic:
            return None

        resolution = IG_RESOLUTIONS.get(granularity, "HOUR")

        try:
            data = self._get(
                f"/prices/{epic}?resolution={resolution}&max={count}&pageSize=0",
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

    # ── Trading ───────────────────────────────────────────────────────────────

    def place_trade(
        self,
        pair: str,
        direction: str,
        size: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> Optional[dict]:
        """
        Open a new CFD position on IG.
        """
        epic = self._pair_to_epic(pair)
        if not epic:
            logger.error(f"No epic found for pair: {pair}")
            return None

        payload = {
            "epic":             epic,
            "direction":        direction.upper(),
            "size":             str(size),
            "orderType":        "MARKET",
            "timeInForce":      "FILL_OR_KILL",
            "guaranteedStop":   False,
            "forceOpen":        True,
            "currencyCode":     "GBP",
        }

        if stop_loss:
            payload["stopLevel"] = stop_loss
        if take_profit:
            payload["limitLevel"] = take_profit

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
                logger.error(f"Trade rejected: {pair} {direction} — {reason}")
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
            "size":        str(size),
            "orderType":   "MARKET",
            "timeInForce": "FILL_OR_KILL",
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
            data = self._get("/positions/otc")
            positions = data.get("positions", [])

            result = []
            for pos in positions:
                position = pos.get("position", {})
                market   = pos.get("market",   {})
                epic     = market.get("epic",  "")
                pair     = self._epic_to_pair(epic)

                result.append({
                    "dealId":        position.get("dealId"),
                    "pair":          pair,
                    "epic":          epic,
                    "instrument":    pair,
                    "direction":     position.get("direction"),
                    "dealSize":      position.get("dealSize"),
                    "level":         position.get("level"),
                    "currentUnits":  position.get("dealSize"),
                    "unrealizedPL":  position.get("upl"),
                    "stopLevel":     position.get("stopLevel"),
                    "limitLevel":    position.get("limitLevel"),
                    "openTime":      position.get("createdDateUTC"),
                    "price":         position.get("level"),
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