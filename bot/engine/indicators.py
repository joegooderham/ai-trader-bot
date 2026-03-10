"""
bot/engine/indicators.py — Technical Indicators
─────────────────────────────────────────────────
Calculates all technical indicators used by the AI to make trade decisions.

Each indicator is explained clearly. These are standard tools used by
professional forex traders worldwide — the AI uses them as inputs,
not as the sole decision maker.

Indicators used:
  - RSI:            Is price overbought or oversold?
  - MACD:           Is momentum shifting direction?
  - Bollinger Bands: Is price at an extreme relative to recent range?
  - EMA Crossover:  Is the short-term trend above the long-term trend?
  - ATR:            How volatile is the market right now?
"""

import pandas as pd
import pandas_ta as ta
from loguru import logger
from dataclasses import dataclass


@dataclass
class IndicatorResult:
    """
    All indicator values for a single currency pair at a single moment.
    Each field is explained so you know exactly what the bot is looking at.
    """

    # RSI (Relative Strength Index) — 0 to 100
    # Below 30 = oversold (price may bounce up)
    # Above 70 = overbought (price may fall)
    # 40–60 = neutral zone
    rsi: float

    # MACD Signal
    # "bullish" = upward momentum detected
    # "bearish" = downward momentum detected
    # "neutral" = no clear momentum
    macd_signal: str

    # MACD Histogram value (positive = bullish, negative = bearish)
    macd_histogram: float

    # Bollinger Band position — where is price relative to the bands?
    # "above_upper" = price very high, likely to fall
    # "below_lower" = price very low, likely to rise
    # "middle_upper" = in upper half of bands (mild bullish)
    # "middle_lower" = in lower half of bands (mild bearish)
    # "middle" = right in the centre (no strong signal)
    bb_position: str

    # EMA Trend direction based on 20-period vs 50-period moving averages
    # "bullish" = short-term EMA above long-term EMA (uptrend)
    # "bearish" = short-term EMA below long-term EMA (downtrend)
    # "neutral" = EMAs very close together (no clear trend)
    ema_trend: str

    # ATR (Average True Range) — measures recent volatility in price units
    # Used to set dynamic stop-loss distances
    atr: float

    # Volume relative to 20-period average
    # > 1.0 means above-average volume (signal is more reliable)
    # < 0.5 means very low volume (signal is less reliable)
    relative_volume: float

    # Current closing price
    current_price: float


def calculate(df: pd.DataFrame) -> IndicatorResult:
    """
    Calculate all technical indicators from a DataFrame of OHLCV candle data.

    Args:
        df: DataFrame with columns: open, high, low, close, volume

    Returns:
        IndicatorResult with all indicator values explained
    """
    if len(df) < 60:
        raise ValueError(f"Need at least 60 candles, got {len(df)}")

    # ── RSI ───────────────────────────────────────────────────────────────────
    # 14-period RSI is the industry standard
    rsi_series = ta.rsi(df["close"], length=14)
    rsi = float(rsi_series.iloc[-1])

    # ── MACD ──────────────────────────────────────────────────────────────────
    # Standard MACD settings: 12-period fast, 26-period slow, 9-period signal
    macd_df = ta.macd(df["close"], fast=12, slow=26, signal=9)
    macd_line = float(macd_df["MACD_12_26_9"].iloc[-1])
    signal_line = float(macd_df["MACDs_12_26_9"].iloc[-1])
    histogram = float(macd_df["MACDh_12_26_9"].iloc[-1])
    prev_histogram = float(macd_df["MACDh_12_26_9"].iloc[-2])

    # MACD is bullish when the histogram crosses from negative to positive
    if histogram > 0 and prev_histogram <= 0:
        macd_signal = "bullish_crossover"    # Strong signal
    elif histogram > 0:
        macd_signal = "bullish"              # Ongoing upward momentum
    elif histogram < 0 and prev_histogram >= 0:
        macd_signal = "bearish_crossover"    # Strong signal
    elif histogram < 0:
        macd_signal = "bearish"              # Ongoing downward momentum
    else:
        macd_signal = "neutral"

    # ── Bollinger Bands ───────────────────────────────────────────────────────
    # 20-period, 2 standard deviations (standard settings)
    bb_df = ta.bbands(df["close"], length=20, std=2)
    upper_band = float(bb_df["BBU_20_2.0"].iloc[-1])
    lower_band = float(bb_df["BBL_20_2.0"].iloc[-1])
    middle_band = float(bb_df["BBM_20_2.0"].iloc[-1])
    current_price = float(df["close"].iloc[-1])

    if current_price > upper_band:
        bb_position = "above_upper"
    elif current_price < lower_band:
        bb_position = "below_lower"
    elif current_price > middle_band:
        bb_position = "middle_upper"
    elif current_price < middle_band:
        bb_position = "middle_lower"
    else:
        bb_position = "middle"

    # ── EMA Crossover ─────────────────────────────────────────────────────────
    # 20-period EMA (short-term trend) vs 50-period EMA (long-term trend)
    ema_20 = float(ta.ema(df["close"], length=20).iloc[-1])
    ema_50 = float(ta.ema(df["close"], length=50).iloc[-1])
    ema_diff_pct = abs(ema_20 - ema_50) / ema_50 * 100

    if ema_20 > ema_50 and ema_diff_pct > 0.05:
        ema_trend = "bullish"
    elif ema_20 < ema_50 and ema_diff_pct > 0.05:
        ema_trend = "bearish"
    else:
        ema_trend = "neutral"  # EMAs too close together — no clear trend

    # ── ATR (Average True Range) ──────────────────────────────────────────────
    # 14-period ATR tells us the average price range per candle
    # Used to set stop-loss distances proportional to current volatility
    atr = float(ta.atr(df["high"], df["low"], df["close"], length=14).iloc[-1])

    # ── Volume Analysis ───────────────────────────────────────────────────────
    # Compare current volume to 20-period average
    avg_volume = df["volume"].tail(20).mean()
    current_volume = float(df["volume"].iloc[-1])
    relative_volume = current_volume / avg_volume if avg_volume > 0 else 1.0

    result = IndicatorResult(
        rsi=rsi,
        macd_signal=macd_signal,
        macd_histogram=histogram,
        bb_position=bb_position,
        ema_trend=ema_trend,
        atr=atr,
        relative_volume=relative_volume,
        current_price=current_price
    )

    logger.debug(
        f"Indicators | RSI: {rsi:.1f} | MACD: {macd_signal} | "
        f"BB: {bb_position} | EMA: {ema_trend} | ATR: {atr:.5f} | "
        f"RelVol: {relative_volume:.2f}"
    )

    return result
