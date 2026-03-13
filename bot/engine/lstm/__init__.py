"""
bot/engine/lstm/ — LSTM Neural Network for Trade Direction Prediction
─────────────────────────────────────────────────────────────────────
Provides the ML component (50% weight) of the confidence scoring system.

The model predicts whether a currency pair will move up (BUY), down (SELL),
or stay flat (HOLD) in the next 3 candles, based on 30 candles of
enriched features (price, indicators, time encoding).
"""

from bot.engine.lstm.predictor import LSTMPredictor

__all__ = ["LSTMPredictor"]
