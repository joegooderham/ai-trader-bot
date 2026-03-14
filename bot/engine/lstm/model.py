"""
bot/engine/lstm/model.py — PyTorch LSTM Model Definition
─────────────────────────────────────────────────────────
Two-layer LSTM with self-attention for forex direction prediction.
~100k parameters — trains in under 3 minutes on CPU.

Architecture:
  1. 2-layer LSTM (96 hidden, dropout between layers)
  2. Self-attention over the full sequence — learns which candles matter most
  3. Batch normalisation before the final classifier
  4. Dropout for regularisation

The attention mechanism replaces the simple "last hidden state" approach.
Instead of only looking at the final timestep, it computes a weighted
combination of ALL timesteps — so if a key reversal happened 15 candles
ago, the model can still weight it heavily.
"""

import torch
import torch.nn as nn


class ForexLSTM(nn.Module):
    """
    LSTM classifier with self-attention for forex direction prediction.

    Input:  (batch, seq_len=30, features=18)
    Output: (batch, 3) — logits for BUY, SELL, HOLD
    """

    def __init__(self, input_size: int = 18, hidden_size: int = 96,
                 num_layers: int = 2, num_classes: int = 3, dropout: float = 0.3):
        super().__init__()

        self.hidden_size = hidden_size

        # Multi-layer LSTM — dropout applies between layers (not after final layer)
        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0,
        )

        # Self-attention: learns a weight for each timestep in the sequence.
        # A single linear layer maps each hidden state to a scalar "importance"
        # score, then softmax normalises across the sequence. The weighted sum
        # gives us a context vector that captures the most relevant timesteps.
        self.attention = nn.Linear(hidden_size, 1)

        # Batch norm stabilises training and helps the model generalise —
        # normalises the combined representation before classification
        self.batch_norm = nn.BatchNorm1d(hidden_size * 2)

        self.dropout = nn.Dropout(dropout)

        # Final classifier: takes the concatenation of attention context
        # and final hidden state (2 × hidden_size) → 3 classes
        self.fc = nn.Linear(hidden_size * 2, num_classes)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x shape: (batch, seq_len, features)
        lstm_out, (h_n, _) = self.lstm(x)
        # lstm_out: (batch, seq_len, hidden_size) — output at every timestep
        # h_n[-1]:  (batch, hidden_size) — final hidden state from last layer

        # ── Self-Attention ──────────────────────────────────────────────
        # Compute attention weights: which timesteps should the model focus on?
        attn_scores = self.attention(lstm_out).squeeze(-1)  # (batch, seq_len)
        attn_weights = torch.softmax(attn_scores, dim=1)    # (batch, seq_len)

        # Weighted sum of LSTM outputs across all timesteps
        # This is the "context vector" — a single representation that captures
        # the most important patterns across the entire 30-candle window
        context = torch.bmm(
            attn_weights.unsqueeze(1),  # (batch, 1, seq_len)
            lstm_out                     # (batch, seq_len, hidden_size)
        ).squeeze(1)  # (batch, hidden_size)

        # ── Combine attention context with final hidden state ───────────
        # The final hidden state captures recency, the attention context
        # captures importance — together they give a richer representation
        last_hidden = h_n[-1]  # (batch, hidden_size)
        combined = torch.cat([context, last_hidden], dim=1)  # (batch, hidden_size * 2)

        # Batch norm + dropout + classify
        combined = self.batch_norm(combined)
        combined = self.dropout(combined)
        logits = self.fc(combined)  # (batch, num_classes)

        return logits
