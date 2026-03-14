# bot/engine/lstm/

LSTM neural network for forex direction prediction (BUY/SELL/HOLD).

## Architecture
- 2-layer LSTM with self-attention (96 hidden units, ~119k parameters)
- 18 features per candle (price action, technical indicators, time encoding)
- 30-candle input sequences, 3-class output with softmax probabilities
- Trains on CPU in under 3 minutes

| File | Purpose |
|------|---------|
| `model.py` | PyTorch model definition — LSTM layers, self-attention mechanism, batch norm, classifier |
| `features.py` | Feature engineering — transforms OHLCV candles into 18 normalised features. Also generates training labels (3-candle lookahead, ATR-based thresholds). |
| `trainer.py` | Full training pipeline — data backfill from yfinance, adaptive data window (extends if accuracy <50%), early stopping, LR scheduler, model versioning |
| `predictor.py` | Inference wrapper — loads trained model, scales features, returns direction + probability. Hot-reloads after retrain without restart. |
| `drift.py` | Drift detection — compares rolling live accuracy vs training accuracy. Flags drift if >15% degradation, triggers early retrain. |
| `backtest.py` | Walk-forward backtester — simulates LSTM-enhanced vs indicator-only strategies side by side. Available via `/backtest` Telegram command. |

## Training Flow
1. Backfill H1 candles from yfinance (3–6 months adaptive)
2. Build 18 features + labels per candle
3. Window into 30-candle sequences
4. Train with weighted sampling, LR scheduler, gradient clipping
5. Save model + scaler + timestamped version
6. Hot-reload predictor

## Shadow Mode
When `lstm.shadow_mode: true` in config.yaml, predictions are logged but don't influence trades. Both LSTM-enhanced and indicator-only scores are calculated and compared.
