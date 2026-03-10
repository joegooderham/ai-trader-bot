"""
broker/oanda_client.py — OANDA API Client
──────────────────────────────────────────
Handles all communication with the OANDA broker API.
This is the only file that talks directly to OANDA.
Everything else (signals, risk management) goes through this module.

OANDA API Docs: https://developer.oanda.com/rest-live-v20/introduction/
"""

import oandapyV20
import oandapyV20.endpoints.orders as orders
import oandapyV20.endpoints.trades as trades
import oandapyV20.endpoints.pricing as pricing
import oandapyV20.endpoints.instruments as instruments
import oandapyV20.endpoints.accounts as accounts
from oandapyV20.contrib.requests import MarketOrderRequest, TakeProfitDetails, StopLossDetails

from loguru import logger
from datetime import datetime, timezone
from typing import Optional
import pandas as pd

from bot import config


class OandaClient:
    """
    Wrapper around the OANDA REST API.

    Usage:
        client = OandaClient()
        price = client.get_price("EUR_USD")
        client.place_trade("EUR_USD", "BUY", units=1000)
    """

    def __init__(self):
        # Connect to either OANDA's demo or live server
        self.api = oandapyV20.API(
            access_token=config.OANDA_API_TOKEN,
            environment=config.OANDA_ENVIRONMENT
        )
        self.account_id = config.OANDA_ACCOUNT_ID
        logger.info(f"Connected to OANDA ({config.OANDA_ENVIRONMENT.upper()} account)")

    # ── Account Information ───────────────────────────────────────────────────

    def get_account_balance(self) -> float:
        """Returns your current account balance."""
        r = accounts.AccountDetails(self.account_id)
        self.api.request(r)
        balance = float(r.response["account"]["balance"])
        logger.debug(f"Account balance: £{balance:.2f}")
        return balance

    def get_open_positions_value(self) -> float:
        """Returns the total value currently at risk in open positions."""
        r = trades.OpenTrades(self.account_id)
        self.api.request(r)
        open_trades = r.response.get("trades", [])
        total_at_risk = sum(abs(float(t["initialUnits"]) * float(t["price"])) for t in open_trades)
        return total_at_risk

    def get_open_trades(self) -> list:
        """Returns a list of all currently open trades."""
        r = trades.OpenTrades(self.account_id)
        self.api.request(r)
        return r.response.get("trades", [])

    # ── Market Data ───────────────────────────────────────────────────────────

    def get_price(self, pair: str) -> dict:
        """
        Get the current bid/ask price for a currency pair.

        Returns:
            {"bid": 1.08432, "ask": 1.08438, "spread": 0.00006}
        """
        r = pricing.PricingInfo(
            accountID=self.account_id,
            params={"instruments": pair}
        )
        self.api.request(r)
        price_data = r.response["prices"][0]
        bid = float(price_data["bids"][0]["price"])
        ask = float(price_data["asks"][0]["price"])
        return {"bid": bid, "ask": ask, "spread": round(ask - bid, 6)}

    def get_candles(self, pair: str, count: int = 200, granularity: str = "M15") -> pd.DataFrame:
        """
        Fetch historical candlestick (OHLCV) data for a currency pair.

        This is the core data used by the AI to generate signals.

        Args:
            pair: Currency pair e.g. "EUR_USD"
            count: Number of candles to fetch (max 5000)
            granularity: Timeframe — M5, M15, H1, H4, D

        Returns:
            DataFrame with columns: time, open, high, low, close, volume
        """
        params = {"count": count, "granularity": granularity, "price": "M"}
        r = instruments.InstrumentsCandles(instrument=pair, params=params)
        self.api.request(r)

        candles = r.response["candles"]
        data = []
        for candle in candles:
            if candle["complete"]:  # Only use completed (closed) candles
                data.append({
                    "time": pd.to_datetime(candle["time"]),
                    "open": float(candle["mid"]["o"]),
                    "high": float(candle["mid"]["h"]),
                    "low": float(candle["mid"]["l"]),
                    "close": float(candle["mid"]["c"]),
                    "volume": int(candle["volume"])
                })

        df = pd.DataFrame(data).set_index("time")
        logger.debug(f"Fetched {len(df)} candles for {pair} ({granularity})")
        return df

    # ── Trade Execution ───────────────────────────────────────────────────────

    def place_trade(
        self,
        pair: str,
        direction: str,         # "BUY" or "SELL"
        units: int,             # Number of units (positive for buy, negative for sell)
        stop_loss_price: float,
        take_profit_price: float,
        confidence_score: float,
        reasoning: str
    ) -> Optional[dict]:
        """
        Place a trade with OANDA.

        EVERY trade includes:
        - Stop-loss: maximum loss if trade goes wrong
        - Take-profit: target profit level
        These are set on OANDA's servers, so positions are protected
        even if our bot crashes or loses internet connection.

        Args:
            pair: e.g. "EUR_USD"
            direction: "BUY" or "SELL"
            units: How many currency units to trade
            stop_loss_price: Price at which to cut losses
            take_profit_price: Price at which to take profits
            confidence_score: AI confidence (for logging/notifications)
            reasoning: Plain English explanation of why this trade was made

        Returns:
            Trade details dict if successful, None if failed
        """
        # Negative units = SELL in OANDA
        if direction == "SELL":
            units = -abs(units)
        else:
            units = abs(units)

        order_data = MarketOrderRequest(
            instrument=pair,
            units=units,
            takeProfitOnFill=TakeProfitDetails(price=str(round(take_profit_price, 5))),
            stopLossOnFill=StopLossDetails(price=str(round(stop_loss_price, 5)))
        )

        r = orders.Orders(self.account_id, data=order_data.data)

        try:
            self.api.request(r)
            trade_id = r.response["orderFillTransaction"]["tradeOpened"]["tradeID"]
            fill_price = float(r.response["orderFillTransaction"]["price"])

            logger.info(f"✅ Trade opened: {direction} {pair} @ {fill_price} | Confidence: {confidence_score:.1f}%")
            logger.info(f"   Stop-loss: {stop_loss_price} | Take-profit: {take_profit_price}")
            logger.info(f"   Reasoning: {reasoning}")

            return {
                "trade_id": trade_id,
                "pair": pair,
                "direction": direction,
                "fill_price": fill_price,
                "units": abs(units),
                "stop_loss": stop_loss_price,
                "take_profit": take_profit_price,
                "confidence_score": confidence_score,
                "reasoning": reasoning,
                "opened_at": datetime.now(timezone.utc).isoformat()
            }

        except Exception as e:
            logger.error(f"❌ Failed to place trade on {pair}: {e}")
            return None

    def close_trade(self, trade_id: str, reason: str = "Manual close") -> Optional[dict]:
        """
        Close an open trade by its ID.

        Args:
            trade_id: OANDA trade ID
            reason: Why we're closing (for logging and Telegram notification)

        Returns:
            Close details dict including P&L
        """
        r = trades.TradeClose(self.account_id, tradeID=trade_id)
        try:
            self.api.request(r)
            close_price = float(r.response["orderFillTransaction"]["price"])
            pl = float(r.response["orderFillTransaction"]["pl"])

            result = "PROFIT" if pl > 0 else "LOSS"
            logger.info(f"{'✅' if pl > 0 else '❌'} Trade closed: ID {trade_id} | {result}: £{pl:.2f} | Reason: {reason}")

            return {
                "trade_id": trade_id,
                "close_price": close_price,
                "pl": pl,
                "reason": reason,
                "closed_at": datetime.now(timezone.utc).isoformat()
            }

        except Exception as e:
            logger.error(f"Failed to close trade {trade_id}: {e}")
            return None

    def close_all_trades(self, reason: str = "End of day close") -> list:
        """
        Close every open position. Called at end-of-day (23:59 UTC).
        Returns a list of close results.
        """
        open_trades = self.get_open_trades()
        results = []

        if not open_trades:
            logger.info("No open trades to close")
            return results

        logger.info(f"Closing {len(open_trades)} open trade(s) — {reason}")

        for trade in open_trades:
            result = self.close_trade(trade["id"], reason=reason)
            if result:
                results.append(result)

        return results
