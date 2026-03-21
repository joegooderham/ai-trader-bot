"""
bot/engine/lstm/predictor.py — LSTM Inference Wrapper
──────────────────────────────────────────────────────
Loads the trained LSTM model + scaler and provides predictions
for the confidence scoring engine.

This is the "production" side of the LSTM — trainer.py handles
training, this handles inference during live trading.

Usage in the confidence pipeline:
    predictor = LSTMPredictor()
    prediction = predictor.predict("EUR_USD", candle_df)
    # Returns: {"direction": "BUY", "probability": 0.73} or None
"""

import numpy as np
import torch
import joblib
from datetime import datetime
from pathlib import Path
from loguru import logger
from typing import Optional

from bot.engine.lstm.model import ForexLSTM
from bot.engine.lstm.features import build_features, create_sequences, SEQUENCE_LENGTH, NUM_FEATURES
from bot import config

# Model paths — same location the trainer saves to
MODEL_DIR = config.DATA_DIR / "models"
MODEL_PATH = MODEL_DIR / "lstm_v1.pt"
SCALER_PATH = MODEL_DIR / "scaler_v1.pkl"

# Direction labels matching trainer.py's label encoding
DIRECTION_MAP = {0: "BUY", 1: "SELL", 2: "HOLD"}


class LSTMPredictor:
    """
    Loads the trained LSTM model and provides predictions for live trading.

    The predictor is designed to fail gracefully — if no model exists yet
    (first run before any training), it returns None and the confidence
    engine falls back to indicator-only scoring.
    """

    def __init__(self):
        self.model = None
        self.scaler = None
        self._loaded = False
        self.model_version = None  # Track which model file is loaded
        self._load_model()

    def _load_model(self):
        """
        Load saved model weights and scaler from disk.
        Called once at startup. If files don't exist yet (no training
        has been done), the predictor gracefully returns None for all
        predictions until the first training run completes.
        """
        if not MODEL_PATH.exists() or not SCALER_PATH.exists():
            logger.warning(
                "LSTM model not found — predictions disabled until first training run. "
                f"Expected: {MODEL_PATH}"
            )
            return

        try:
            # Rebuild model architecture matching the trainer's config
            hidden_size = config._cfg.get("lstm", {}).get("hidden_size", 96)
            num_layers = config._cfg.get("lstm", {}).get("num_layers", 2)
            dropout = config._cfg.get("lstm", {}).get("dropout", 0.3)

            self.model = ForexLSTM(
                input_size=NUM_FEATURES,
                hidden_size=hidden_size,
                num_layers=num_layers,
                dropout=dropout,
            )
            state_dict = torch.load(str(MODEL_PATH), map_location="cpu", weights_only=True)
            self.model.load_state_dict(state_dict)
            self.model.eval()  # Set to inference mode (disables dropout)

            self.scaler = joblib.load(str(SCALER_PATH))
            self._loaded = True
            # Track model version from file modification time for prediction logging
            self.model_version = f"lstm_{datetime.fromtimestamp(MODEL_PATH.stat().st_mtime).strftime('%Y%m%d_%H%M%S')}"

            logger.info(f"LSTM model loaded from {MODEL_PATH} (version: {self.model_version})")
        except Exception as e:
            logger.error(f"Failed to load LSTM model: {e}")
            self.model = None
            self.scaler = None
            self._loaded = False

    def reload(self):
        """
        Reload the model after a new training run completes.
        Called by the scheduler after the weekly retrain job finishes.
        """
        logger.info("Reloading LSTM model after retrain...")
        self._loaded = False
        self._load_model()

    def predict(self, pair: str, df) -> Optional[dict]:
        """
        Generate a direction prediction for a currency pair.

        Args:
            pair: Currency pair identifier e.g. "EUR_USD"
            df: DataFrame with OHLCV columns and DatetimeIndex,
                must have at least SEQUENCE_LENGTH + 50 rows
                (50 for indicator warm-up, 30 for the sequence window)

        Returns:
            dict with:
                - "direction": "BUY" or "SELL" (never returns HOLD as a prediction)
                - "probability": float 0.0–1.0 (softmax probability of predicted direction)
            Returns None if:
                - Model not loaded (no training done yet)
                - Insufficient candle data
                - Any prediction error
        """
        if not self._loaded:
            return None

        try:
            # Step 1: Build features from raw candle data
            features = build_features(df)
            if len(features) < SEQUENCE_LENGTH:
                logger.debug(f"{pair}: Not enough feature rows ({len(features)}) for LSTM prediction")
                return None

            # Step 2: Take the most recent SEQUENCE_LENGTH candles as input
            # We only need the last sequence for live prediction
            recent_features = features[-SEQUENCE_LENGTH:]

            # Step 3: Scale using the same scaler fitted during training
            recent_scaled = self.scaler.transform(recent_features).astype(np.float32)

            # Step 4: Reshape to (1, seq_len, num_features) for single-sample batch
            X = torch.from_numpy(recent_scaled.reshape(1, SEQUENCE_LENGTH, NUM_FEATURES))

            # Step 5: Run inference (no gradient computation needed)
            with torch.no_grad():
                logits = self.model(X)
                probabilities = torch.softmax(logits, dim=1).squeeze()

            # Step 6: Extract prediction
            predicted_class = probabilities.argmax().item()
            direction = DIRECTION_MAP[predicted_class]
            probability = probabilities[predicted_class].item()

            # HOLD predictions aren't useful for the confidence engine —
            # it needs a directional signal. If the model predicts HOLD,
            # return None so the confidence engine uses indicator fallback.
            if direction == "HOLD":
                logger.debug(f"{pair}: LSTM predicts HOLD ({probability:.1%}) — no directional signal")
                return None

            logger.info(f"{pair}: LSTM predicts {direction} ({probability:.1%})")
            return {
                "direction": direction,
                "probability": probability,
            }

        except Exception as e:
            logger.error(f"LSTM prediction failed for {pair}: {e}")
            return None
