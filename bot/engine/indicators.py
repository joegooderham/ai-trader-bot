"""
bot/engine/indicators.py — Technical Indicators
─────────────────────────────────────────────────
Calculates all technical indicators used by the AI to make trade decisions.
"""

import pandas as pd
import ta
from loguru import logger
from dataclasses import dataclass


@dataclass
class IndicatorResult:
    rsi: float
    macd_signal: str
    macd_histogram: float
    bb_position: str
    ema_trend: str
    atr: float
    relative_volume: float
    current_price: float


def calculate(df: pd.DataFrame) -> IndicatorResult:
    if len(df) < 60:
        raise ValueError(f"Need at least 60 candles, got {len(df)}")

    # ── RSI ───────────────────────────────────────────────────────────────────
    rsi = float(ta.momentum.RSIIndicator(df["close"], window=14).rsi().iloc[-1])

    # ── MACD ──────────────────────────────────────────────────────────────────
    macd_obj = ta.trend.MACD(df["close"], window_fast=12, window_slow=26, window_sign=9)
    histogram = float(macd_obj.macd_diff().iloc[-1])
    prev_histogram = float(macd_obj.macd_diff().iloc[-2])

    if histogram > 0 and prev_histogram <= 0:
        macd_signal = "bullish_crossover"
    elif histogram > 0:
        macd_signal = "bullish"
    elif histogram < 0 and prev_histogram >= 0:
        macd_signal = "bearish_crossover"
    elif histogram < 0:
        macd_signal = "bearish"
    else:
        macd_signal = "neutral"

    # ── Bollinger Bands ───────────────────────────────────────────────────────
    bb_obj = ta.volatility.BollingerBands(df["close"], window=20, window_dev=2)
    upper_band = float(bb_obj.bollinger_hband().iloc[-1])
    lower_band = float(bb_obj.bollinger_lband().iloc[-1])
    middle_band = float(bb_obj.bollinger_mavg().iloc[-1])
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
    ema_20 = float(ta.trend.EMAIndicator(df["close"], window=20).ema_indicator().iloc[-1])
    ema_50 = float(ta.trend.EMAIndicator(df["close"], window=50).ema_indicator().iloc[-1])
    ema_diff_pct = abs(ema_20 - ema_50) / ema_50 * 100

    if ema_20 > ema_50 and ema_diff_pct > 0.05:
        ema_trend = "bullish"
    elif ema_20 < ema_50 and ema_diff_pct > 0.05:
        ema_trend = "bearish"
    else:
        ema_trend = "neutral"

    # ── ATR ───────────────────────────────────────────────────────────────────
    atr = float(ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=14).average_true_range().iloc[-1])

    # ── Volume ────────────────────────────────────────────────────────────────
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


def calculate_trend_summary(df: pd.DataFrame) -> dict:
    """
    Lightweight trend summary for higher-timeframe confirmation (BACKLOG-004).

    Returns a dict with:
      - trend: "bullish", "bearish", or "neutral"
      - strength: 0-100 score indicating how strong the trend is
      - ema_20/ema_50: raw EMA values for reference

    Designed to run on H4 candles to give a macro-trend backdrop for M15/H1 entries.
    Needs only 50 candles minimum (less strict than full calculate()).
    """
    if len(df) < 50:
        return {"trend": "neutral", "strength": 0, "ema_20": 0, "ema_50": 0}

    ema_20 = float(ta.trend.EMAIndicator(df["close"], window=20).ema_indicator().iloc[-1])
    ema_50 = float(ta.trend.EMAIndicator(df["close"], window=50).ema_indicator().iloc[-1])
    rsi = float(ta.momentum.RSIIndicator(df["close"], window=14).rsi().iloc[-1])

    # MACD on higher timeframe for trend strength
    macd_obj = ta.trend.MACD(df["close"], window_fast=12, window_slow=26, window_sign=9)
    histogram = float(macd_obj.macd_diff().iloc[-1])

    ema_diff_pct = abs(ema_20 - ema_50) / ema_50 * 100

    # Tally trend signals
    bullish = 0
    bearish = 0

    if ema_20 > ema_50:
        bullish += 1
    elif ema_20 < ema_50:
        bearish += 1

    if histogram > 0:
        bullish += 1
    elif histogram < 0:
        bearish += 1

    if rsi > 55:
        bullish += 1
    elif rsi < 45:
        bearish += 1

    if bullish >= 2:
        trend = "bullish"
        # Strength based on EMA separation and signal agreement
        strength = min(bullish * 30 + ema_diff_pct * 10, 100)
    elif bearish >= 2:
        trend = "bearish"
        strength = min(bearish * 30 + ema_diff_pct * 10, 100)
    else:
        trend = "neutral"
        strength = 0

    return {
        "trend": trend,
        "strength": round(strength, 1),
        "ema_20": round(ema_20, 5),
        "ema_50": round(ema_50, 5),
    }