"""
bot/engine/lstm/trainer.py — LSTM Training Pipeline
─────────────────────────────────────────────────────
Handles data backfill, feature engineering, training, and model saving.

Training flow:
  1. Backfill 3 months of H1 candles from yfinance (adaptive — extends if accuracy poor)
  2. Load all candle data from SQLite
  3. Build features + labels for each pair
  4. Train the LSTM with early stopping
  5. If val accuracy < 50%, add 1 week per 10% deficit and retrain
  6. Save model weights + scaler to disk

Adaptive data window:
  Starts at 3 months (3mo). If validation accuracy is below 50%, the backfill
  window grows by 2 weeks for every 10% below 50%. E.g. 30% accuracy → 3mo + 4 weeks.
  Max cap at 6 months to avoid diminishing returns on stale data.

Runs continuously on a configurable interval via scheduler, or manually via:
  python -m bot.engine.lstm.trainer
"""

import time
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
import joblib
from pathlib import Path
from loguru import logger
from sklearn.preprocessing import StandardScaler
from collections import Counter

import yfinance as yf
import pandas as pd
import ta

from bot.engine.lstm.model import ForexLSTM
from bot.engine.lstm.features import build_features, build_labels, create_sequences, SEQUENCE_LENGTH
from bot import config

# Where to save trained models — on the Docker volume so they survive rebuilds
MODEL_DIR = config.DATA_DIR / "models"
MODEL_PATH = MODEL_DIR / "lstm_v1.pt"
SCALER_PATH = MODEL_DIR / "scaler_v1.pkl"

# yfinance ticker mapping (same as ig_client.py)
YFINANCE_TICKERS = {
    "EUR_USD": "EURUSD=X",
    "GBP_USD": "GBPUSD=X",
    "USD_JPY": "USDJPY=X",
    "AUD_USD": "AUDUSD=X",
    "USD_CAD": "USDCAD=X",
}

# Adaptive backfill settings
BASE_PERIOD = "3mo"         # Start with 3 months
MIN_ACCURACY = 0.50         # 50% threshold — below this we extend the data window
ACCURACY_STEP = 0.10        # Each 10% below threshold adds 2 weeks
MAX_PERIOD_DAYS = 180       # Cap at ~6 months to avoid stale data hurting more than helping

# yfinance period strings mapped to approximate day counts for comparison
PERIOD_DAYS = {"3mo": 90, "6mo": 180}


class LSTMTrainer:
    """Handles the full training pipeline for the forex LSTM model."""

    def __init__(self):
        from data.storage import TradeStorage
        self.storage = TradeStorage()

    def backfill_history(self, pairs: list = None, period: str = BASE_PERIOD):
        """
        Download H1 candles from yfinance and store in SQLite.
        Only fetches data we don't already have.

        Args:
            pairs: Currency pairs to backfill (defaults to config.PAIRS)
            period: yfinance period string e.g. "3mo", "6mo"
        """
        pairs = pairs or config.PAIRS

        # Calculate minimum candle count we expect for this period
        # H1 candles: ~5 trading days/week × ~13 hours/day of forex trading
        period_days = PERIOD_DAYS.get(period, 90)
        expected_candles = int(period_days * 5 / 7 * 13 * 0.7)  # 70% fill rate is realistic

        for pair in pairs:
            ticker = YFINANCE_TICKERS.get(pair)
            if not ticker:
                logger.warning(f"No yfinance ticker for {pair}, skipping backfill")
                continue

            existing = self.storage.get_candle_count(pair, "H1")
            if existing >= expected_candles:
                logger.debug(f"{pair}: already have {existing} H1 candles (need {expected_candles}), skipping backfill")
                continue

            logger.info(f"Backfilling {pair} H1 candles from yfinance (period={period})...")
            try:
                data = yf.download(ticker, period=period, interval="1h", progress=False)
                if data is None or data.empty:
                    logger.warning(f"No data from yfinance for {pair}")
                    continue

                # Normalise column names (yfinance may return multi-level columns)
                if isinstance(data.columns, pd.MultiIndex):
                    data.columns = data.columns.get_level_values(0)
                data.columns = [c.lower() for c in data.columns]

                # Rename 'adj close' if present
                if "adj close" in data.columns:
                    data = data.drop(columns=["adj close"])

                # Ensure required columns exist
                for col in ["open", "high", "low", "close", "volume"]:
                    if col not in data.columns:
                        logger.warning(f"Missing column {col} for {pair}")
                        continue

                self.storage.save_candles(pair, "H1", data, source="yfinance_backfill")
                logger.info(f"Backfilled {len(data)} H1 candles for {pair}")

            except Exception as e:
                logger.error(f"Backfill failed for {pair}: {e}")

    def refresh_candles(self, pairs: list = None):
        """
        Top up SQLite with the latest candles since our most recent stored data.
        Called every retrain cycle so the model always trains on current market data.

        Uses the latest candle timestamp in SQLite as the start point, then
        downloads everything from there to now. INSERT OR IGNORE in storage
        handles any overlap, so this is safe to call repeatedly.
        """
        pairs = pairs or config.PAIRS
        for pair in pairs:
            ticker = YFINANCE_TICKERS.get(pair)
            if not ticker:
                continue

            latest = self.storage.get_latest_candle_time(pair, "H1")
            if latest is None:
                # No data at all — full backfill will handle it
                logger.debug(f"{pair}: no existing candles, skipping refresh (backfill will run)")
                continue

            # Fetch from the latest candle onwards — yfinance start is inclusive,
            # and INSERT OR IGNORE handles the overlap on the boundary candle
            start_str = latest.strftime("%Y-%m-%d")
            logger.info(f"Refreshing {pair} candles from {start_str} to now...")

            try:
                data = yf.download(ticker, start=start_str, interval="1h", progress=False)
                if data is None or data.empty:
                    logger.debug(f"{pair}: no new candles from yfinance")
                    continue

                if isinstance(data.columns, pd.MultiIndex):
                    data.columns = data.columns.get_level_values(0)
                data.columns = [c.lower() for c in data.columns]

                if "adj close" in data.columns:
                    data = data.drop(columns=["adj close"])

                self.storage.save_candles(pair, "H1", data, source="yfinance_refresh")
                logger.info(f"Refreshed {pair}: {len(data)} candles (new + overlap)")

            except Exception as e:
                logger.error(f"Candle refresh failed for {pair}: {e}")

    def prepare_dataset(self, pairs: list = None) -> tuple:
        """
        Load candles from SQLite, build features + labels, return train/val split.

        Returns:
            (X_train, y_train, X_val, y_val, scaler) — numpy arrays + fitted scaler
        """
        pairs = pairs or config.PAIRS
        all_features = []
        all_labels = []

        for pair in pairs:
            df = self.storage.get_candles(pair, "H1", count=5000)
            if df is None or len(df) < 100:
                logger.warning(f"{pair}: not enough candle data ({len(df) if df is not None else 0}), skipping")
                continue

            # Build features
            features = build_features(df)
            if len(features) == 0:
                continue

            # ATR for labeling (aligned with feature rows)
            atr = ta.volatility.AverageTrueRange(
                df["high"], df["low"], df["close"], window=14
            ).average_true_range()
            # Drop NaN rows to align with features
            atr_clean = atr.dropna()
            # Align: features starts after indicator warm-up
            offset = len(df) - len(features)
            atr_aligned = atr_clean.iloc[offset - len(atr_clean) + len(features):]
            if len(atr_aligned) != len(features):
                # Fallback: use the tail
                atr_aligned = atr.iloc[-len(features):]

            labels = build_labels(df.iloc[-len(features):], atr_aligned)

            # Create sequences
            X, y = create_sequences(features, labels, SEQUENCE_LENGTH)
            if len(X) == 0:
                continue

            all_features.append(X)
            all_labels.append(y)
            logger.info(f"{pair}: {len(X)} training sequences (BUY={np.sum(y==0)}, SELL={np.sum(y==1)}, HOLD={np.sum(y==2)})")

        if not all_features:
            logger.error("No training data available")
            return None, None, None, None, None

        X_all = np.concatenate(all_features)
        y_all = np.concatenate(all_labels)

        # Fit scaler on the training portion
        # Reshape to 2D for scaling, then back to 3D
        n_samples, seq_len, n_features = X_all.shape
        X_flat = X_all.reshape(-1, n_features)

        scaler = StandardScaler()
        X_flat_scaled = scaler.fit_transform(X_flat)
        X_all = X_flat_scaled.reshape(n_samples, seq_len, n_features).astype(np.float32)

        # Chronological 80/20 split — no shuffling for time series
        split_idx = int(len(X_all) * 0.8)
        X_train, X_val = X_all[:split_idx], X_all[split_idx:]
        y_train, y_val = y_all[:split_idx], y_all[split_idx:]

        logger.info(f"Dataset: {len(X_train)} train, {len(X_val)} validation sequences")

        return X_train, y_train, X_val, y_val, scaler

    def _run_training(self, X_train, y_train, X_val, y_val, scaler,
                      epochs: int, batch_size: int, lr: float, patience: int) -> dict:
        """
        Inner training loop — separated so train() can call it multiple times
        when adaptively extending the data window.

        Returns:
            dict with training metrics
        """
        # Class weights for imbalanced data (HOLD typically dominates)
        counts = Counter(y_train)
        total = len(y_train)
        class_weights = torch.tensor([
            total / (3 * counts.get(i, 1)) for i in range(3)
        ], dtype=torch.float32)
        logger.info(f"Class distribution: BUY={counts[0]}, SELL={counts[1]}, HOLD={counts[2]}")
        logger.info(f"Class weights: {class_weights.tolist()}")

        # Create model and data loaders
        model = ForexLSTM(input_size=X_train.shape[2])
        criterion = nn.CrossEntropyLoss(weight=class_weights)
        optimizer = torch.optim.Adam(model.parameters(), lr=lr)

        train_dataset = TensorDataset(torch.from_numpy(X_train), torch.from_numpy(y_train))
        val_dataset = TensorDataset(torch.from_numpy(X_val), torch.from_numpy(y_val))
        train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
        val_loader = DataLoader(val_dataset, batch_size=batch_size)

        # Training loop with early stopping
        best_val_loss = float("inf")
        best_model_state = None
        patience_counter = 0

        for epoch in range(epochs):
            model.train()
            train_loss = 0
            correct = 0
            total_samples = 0

            for X_batch, y_batch in train_loader:
                optimizer.zero_grad()
                outputs = model(X_batch)
                loss = criterion(outputs, y_batch)
                loss.backward()
                optimizer.step()

                train_loss += loss.item() * len(y_batch)
                correct += (outputs.argmax(dim=1) == y_batch).sum().item()
                total_samples += len(y_batch)

            train_loss /= total_samples
            train_acc = correct / total_samples

            # Validation
            model.eval()
            val_loss = 0
            val_correct = 0
            val_total = 0

            with torch.no_grad():
                for X_batch, y_batch in val_loader:
                    outputs = model(X_batch)
                    loss = criterion(outputs, y_batch)
                    val_loss += loss.item() * len(y_batch)
                    val_correct += (outputs.argmax(dim=1) == y_batch).sum().item()
                    val_total += len(y_batch)

            val_loss /= val_total
            val_acc = val_correct / val_total

            if (epoch + 1) % 5 == 0 or epoch == 0:
                logger.info(
                    f"Epoch {epoch+1}/{epochs} | "
                    f"Train Loss: {train_loss:.4f} Acc: {train_acc:.3f} | "
                    f"Val Loss: {val_loss:.4f} Acc: {val_acc:.3f}"
                )

            # Early stopping
            if val_loss < best_val_loss:
                best_val_loss = val_loss
                best_model_state = model.state_dict().copy()
                patience_counter = 0
            else:
                patience_counter += 1
                if patience_counter >= patience:
                    logger.info(f"Early stopping at epoch {epoch+1} (patience={patience})")
                    break

        # Save best model
        if best_model_state:
            model.load_state_dict(best_model_state)

        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        torch.save(model.state_dict(), str(MODEL_PATH))
        joblib.dump(scaler, str(SCALER_PATH))

        metrics = {
            "epochs_trained": epoch + 1,
            "train_samples": len(X_train),
            "val_samples": len(X_val),
            "best_val_loss": round(best_val_loss, 4),
            "val_accuracy": round(val_acc, 3),
            "train_accuracy": round(train_acc, 3),
            "class_distribution": {
                "BUY": int(counts[0]),
                "SELL": int(counts[1]),
                "HOLD": int(counts[2]),
            },
            "model_path": str(MODEL_PATH),
        }

        return metrics

    def _calculate_extended_period(self, val_accuracy: float) -> str:
        """
        Calculate how much extra data to fetch based on accuracy deficit.

        Below 50% accuracy: add 2 weeks per 10% deficit.
          50% → no change (3mo is fine)
          40% → 3mo + 2 weeks
          30% → 3mo + 4 weeks
          20% → 3mo + 6 weeks
          etc.

        Caps at 6 months — beyond that, older data hurts more than helps
        because market regimes change.
        """
        deficit = MIN_ACCURACY - val_accuracy  # e.g. 0.50 - 0.30 = 0.20
        extra_blocks = int(deficit / ACCURACY_STEP)  # e.g. 0.20 / 0.10 = 2 blocks
        extra_weeks = extra_blocks * 2              # 2 weeks per block

        base_days = PERIOD_DAYS[BASE_PERIOD]  # 90 days
        extended_days = base_days + (extra_weeks * 7)

        # Cap at 6 months
        if extended_days >= MAX_PERIOD_DAYS:
            logger.info(f"Extended period capped at 6mo (requested {extended_days} days)")
            return "6mo"

        # yfinance doesn't support arbitrary day counts, so map to nearest valid period
        # Valid yfinance periods: 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, ytd, max
        # For fine-grained control, we use start/end dates instead
        return f"{extended_days}d"

    def train(self, epochs: int = 50, batch_size: int = 64, lr: float = 0.001,
              patience: int = 7) -> dict:
        """
        Full training pipeline with adaptive data window.

        Starts with 3 months of data. If validation accuracy < 50%,
        extends the data window by 1 week per 10% deficit and retrains.

        Returns:
            dict with training metrics (or error message)
        """
        logger.info("═══ LSTM Training Started ═══")
        train_start = time.time()

        # Step 1a: Initial backfill (only runs if we don't have enough history yet)
        self.backfill_history(period=BASE_PERIOD)

        # Step 1b: Top up with latest candles since last refresh
        # This is the key to continuous training — every cycle gets fresh market data
        self.refresh_candles()

        # Step 2: Prepare dataset and train
        X_train, y_train, X_val, y_val, scaler = self.prepare_dataset()
        if X_train is None or len(X_train) < 100:
            msg = f"Not enough training data (need 100+, got {len(X_train) if X_train is not None else 0})"
            logger.error(msg)
            return {"error": msg}

        metrics = self._run_training(X_train, y_train, X_val, y_val, scaler,
                                     epochs, batch_size, lr, patience)

        # Step 3: Check if accuracy is acceptable — if not, extend data and retrain
        val_accuracy = metrics["val_accuracy"]
        if val_accuracy < MIN_ACCURACY:
            extended_period = self._calculate_extended_period(val_accuracy)
            extra_blocks = int((MIN_ACCURACY - val_accuracy) / ACCURACY_STEP)
            extra_weeks = extra_blocks * 2

            logger.warning(
                f"Val accuracy {val_accuracy:.1%} is below {MIN_ACCURACY:.0%} threshold — "
                f"extending data window by {extra_weeks} weeks to {extended_period} and retraining"
            )

            # Backfill with extended period
            # Use yfinance start date for arbitrary day counts
            if extended_period.endswith("d"):
                from datetime import datetime, timedelta
                days = int(extended_period.replace("d", ""))
                start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
                self._backfill_with_dates(start_date=start_date)
            else:
                self.backfill_history(period=extended_period)

            # Re-prepare with all available data and retrain
            X_train, y_train, X_val, y_val, scaler = self.prepare_dataset()
            if X_train is None or len(X_train) < 100:
                logger.error("Still not enough data after extending window")
                return metrics  # Return the original metrics — at least we have a model saved

            metrics = self._run_training(X_train, y_train, X_val, y_val, scaler,
                                         epochs, batch_size, lr, patience)
            metrics["data_extended"] = True
            metrics["extended_period"] = extended_period

            logger.info(
                f"Retrain with extended data: val accuracy {metrics['val_accuracy']:.1%} "
                f"(was {val_accuracy:.1%} with {BASE_PERIOD})"
            )

        # Record total training duration (backfill + prepare + train + any retrain)
        duration_seconds = round(time.time() - train_start, 1)
        metrics["training_duration_seconds"] = duration_seconds
        metrics["training_duration_human"] = (
            f"{int(duration_seconds // 60)}m {int(duration_seconds % 60)}s"
            if duration_seconds >= 60 else f"{duration_seconds}s"
        )

        logger.info(f"═══ LSTM Training Complete ({metrics['training_duration_human']}) ═══")
        logger.info(f"  Val accuracy: {metrics['val_accuracy']:.1%} | Val loss: {metrics['best_val_loss']:.4f}")
        logger.info(f"  Model saved to {MODEL_PATH}")

        return metrics

    def _backfill_with_dates(self, start_date: str, pairs: list = None):
        """
        Backfill using explicit start date instead of a period string.
        Needed when the extended period doesn't map to a standard yfinance period.
        """
        pairs = pairs or config.PAIRS
        for pair in pairs:
            ticker = YFINANCE_TICKERS.get(pair)
            if not ticker:
                continue

            logger.info(f"Backfilling {pair} H1 candles from {start_date}...")
            try:
                data = yf.download(ticker, start=start_date, interval="1h", progress=False)
                if data is None or data.empty:
                    logger.warning(f"No data from yfinance for {pair}")
                    continue

                if isinstance(data.columns, pd.MultiIndex):
                    data.columns = data.columns.get_level_values(0)
                data.columns = [c.lower() for c in data.columns]

                if "adj close" in data.columns:
                    data = data.drop(columns=["adj close"])

                for col in ["open", "high", "low", "close", "volume"]:
                    if col not in data.columns:
                        logger.warning(f"Missing column {col} for {pair}")
                        continue

                self.storage.save_candles(pair, "H1", data, source="yfinance_backfill")
                logger.info(f"Backfilled {len(data)} H1 candles for {pair} (from {start_date})")

            except Exception as e:
                logger.error(f"Extended backfill failed for {pair}: {e}")


# Allow running directly: python -m bot.engine.lstm.trainer
if __name__ == "__main__":
    trainer = LSTMTrainer()
    result = trainer.train()
    print(result)
