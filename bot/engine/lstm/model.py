"""
bot/engine/lstm/model.py — PyTorch LSTM Model Definition
─────────────────────────────────────────────────────────
Lightweight single-layer LSTM for forex direction prediction.
~35k parameters — trains in under 2 minutes on CPU.
"""

import torch
import torch.nn as nn


class ForexLSTM(nn.Module):
    """
    LSTM classifier for forex direction prediction.

    Input:  (batch, seq_len=30, features=12)
    Output: (batch, 3) — logits for BUY, SELL, HOLD
    """

    def __init__(self, input_size: int = 12, hidden_size: int = 64,
                 num_layers: int = 1, num_classes: int = 3, dropout: float = 0.2):
        super().__init__()

        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0,
        )
        self.dropout = nn.Dropout(dropout)
        self.fc = nn.Linear(hidden_size, num_classes)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x shape: (batch, seq_len, features)
        lstm_out, (h_n, _) = self.lstm(x)
        # Use final hidden state for classification (many-to-one)
        last_hidden = h_n[-1]  # (batch, hidden_size)
        out = self.dropout(last_hidden)
        logits = self.fc(out)  # (batch, num_classes)
        return logits
