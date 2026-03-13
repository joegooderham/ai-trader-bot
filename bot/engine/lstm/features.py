"""
bot/engine/lstm/features.py — Feature Engineering for LSTM
────────────────────────────────────────────────────────────
Transforms raw OHLCV candle data into 12 normalised features per timestep.

Features (per candle):
  1. close_pct_change  — % change from previous close
  2. range_norm        — (high - low) / ATR
  3. body_norm         — (close - open) / ATR
  4. volume_rel        — volume / 20-period mean volume
  5. rsi_norm          — RSI(14) scaled to 0-1
  6. macd_hist_norm    — MACD histogram / close price
  7. bb_percent_b      — Bollinger %B (0-1 range)
  8. ema20_dist        — % distance of close from EMA(20)
  9. ema50_dist        — % distance of close from EMA(50)
  10. atr_norm         — ATR(14) / close price
  11. hour_sin         — sin(2π * hour / 24) cyclical encoding
  12. hour_cos         — cos(2π * hour / 24) cyclical encoding
"""

import numpy as np
import pandas as pd
import ta
from loguru import logger

NUM_FEATURES = 12
SEQUENCE_LENGTH = 30


def build_features(df: pd.DataFrame) -> np.ndarray:
    """
    Convert a candle DataFrame into a (num_candles, 12) feature array.

    Args:
        df: DataFrame with columns [open, high, low, close, volume]
            and a DatetimeIndex (or datetime-parseable index).

    Returns:
        numpy array of shape (N, 12) where N <= len(df).
        Rows with NaN (from indicator warm-up) are dropped.
    """
    if len(df) < 60:
        logger.warning(f"Need at least 60 candles for features, got {len(df)}")
        return np.array([])

    close = df["close"]
    high = df["high"]
    low = df["low"]
    opn = df["open"]
    volume = df["volume"]

    # Technical indicators
    rsi = ta.momentum.RSIIndicator(close, window=14).rsi()
    macd_hist = ta.trend.MACD(close, window_fast=12, window_slow=26, window_sign=9).macd_diff()
    bb = ta.volatility.BollingerBands(close, window=20, window_dev=2)
    bb_pctb = bb.bollinger_pband()  # %B: (close - lower) / (upper - lower)
    ema20 = ta.trend.EMAIndicator(close, window=20).ema_indicator()
    ema50 = ta.trend.EMAIndicator(close, window=50).ema_indicator()
    atr = ta.volatility.AverageTrueRange(high, low, close, window=14).average_true_range()

    # Rolling volume mean
    vol_mean = volume.rolling(window=20).mean()

    # Hour encoding from index
    try:
        hours = pd.to_datetime(df.index).hour
    except Exception:
        hours = pd.Series(np.zeros(len(df)), index=df.index)

    # Build feature columns
    features = pd.DataFrame(index=df.index)
    features["close_pct"] = close.pct_change()
    features["range_norm"] = (high - low) / atr.replace(0, np.nan)
    features["body_norm"] = (close - opn) / atr.replace(0, np.nan)
    features["vol_rel"] = volume / vol_mean.replace(0, np.nan)
    features["rsi_norm"] = rsi / 100.0
    features["macd_hist_norm"] = macd_hist / close.replace(0, np.nan)
    features["bb_pctb"] = bb_pctb
    features["ema20_dist"] = (close - ema20) / close.replace(0, np.nan)
    features["ema50_dist"] = (close - ema50) / close.replace(0, np.nan)
    features["atr_norm"] = atr / close.replace(0, np.nan)
    features["hour_sin"] = np.sin(2 * np.pi * hours / 24)
    features["hour_cos"] = np.cos(2 * np.pi * hours / 24)

    # Drop NaN rows (from indicator warm-up periods)
    features = features.dropna()

    # Clip extreme values to prevent outlier distortion
    features = features.clip(-5, 5)

    return features.values.astype(np.float32)


def build_labels(df: pd.DataFrame, atr_series: pd.Series,
                 lookahead: int = 3, threshold: float = 1.0) -> np.ndarray:
    """
    Create direction labels based on future price movement.

    For each candle, looks ahead `lookahead` periods:
      - BUY (0):  max high exceeds close + threshold * ATR AND upside > downside
      - SELL (1): min low falls below close - threshold * ATR AND downside > upside
      - HOLD (2): neither threshold reached

    Args:
        df: DataFrame with 'close', 'high', 'low' columns
        atr_series: ATR values aligned with df index
        lookahead: number of future candles to check
        threshold: ATR multiplier for directional threshold

    Returns:
        numpy array of integer labels (0=BUY, 1=SELL, 2=HOLD)
    """
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    atr = atr_series.values

    labels = np.full(len(df), 2, dtype=np.int64)  # Default HOLD

    for i in range(len(df) - lookahead):
        future_high = np.max(high[i + 1: i + 1 + lookahead])
        future_low = np.min(low[i + 1: i + 1 + lookahead])

        upside = future_high - close[i]
        downside = close[i] - future_low
        atr_val = atr[i] if atr[i] > 0 else 1e-10

        if upside / atr_val > threshold and upside > downside:
            labels[i] = 0  # BUY
        elif downside / atr_val > threshold and downside > upside:
            labels[i] = 1  # SELL

    return labels


def create_sequences(features: np.ndarray, labels: np.ndarray = None,
                     seq_len: int = SEQUENCE_LENGTH) -> tuple:
    """
    Window feature array into sequences for LSTM input.

    Args:
        features: (N, 12) feature array
        labels: (N,) label array (optional)
        seq_len: number of timesteps per sequence

    Returns:
        Tuple of (X, y) where:
          X: (num_sequences, seq_len, 12)
          y: (num_sequences,) — label for the LAST timestep in each sequence
             Returns None if labels not provided.
    """
    if len(features) < seq_len:
        return np.array([]), None

    X = []
    y_out = []

    for i in range(len(features) - seq_len):
        X.append(features[i: i + seq_len])
        if labels is not None:
            y_out.append(labels[i + seq_len - 1])

    X = np.array(X, dtype=np.float32)
    y_out = np.array(y_out, dtype=np.int64) if labels is not None else None

    return X, y_out
