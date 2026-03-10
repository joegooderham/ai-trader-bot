"""
data/storage.py — Local Trade Storage
───────────────────────────────────────
Saves all trade history to a local JSON file in the /data directory.
This file is persisted via Docker volume, so it survives container restarts.
It's also committed to GitHub periodically via the sync script.

Why JSON instead of a database?
  - Zero setup required
  - Human-readable — you can open it and see exactly what the bot has done
  - Easy to commit to GitHub for backup
  - Sufficient for the volume of trades this bot will make
"""

import json
from pathlib import Path
from datetime import datetime, timezone
from loguru import logger
from typing import Optional

from bot import config

TRADES_FILE = config.DATA_DIR / "trades.json"
OVERNIGHT_FILE = config.DATA_DIR / "overnight_holds.json"


class TradeStorage:
    """Handles reading and writing trade data to local storage."""

    def __init__(self):
        # Ensure the data directory exists
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)

        # Initialise files if they don't exist
        if not TRADES_FILE.exists():
            self._write(TRADES_FILE, [])
        if not OVERNIGHT_FILE.exists():
            self._write(OVERNIGHT_FILE, [])

    def save_trade(self, trade: dict):
        """
        Save a new trade to the history file.
        Called whenever a trade is opened or closed.
        """
        trades = self._read(TRADES_FILE)
        trades.append(trade)
        self._write(TRADES_FILE, trades)
        logger.debug(f"Trade saved: {trade.get('pair')} {trade.get('direction')}")

    def update_trade(self, trade_id: str, updates: dict):
        """Update an existing trade record (e.g., when it closes with P&L)."""
        trades = self._read(TRADES_FILE)
        for trade in trades:
            if trade.get("trade_id") == trade_id:
                trade.update(updates)
        self._write(TRADES_FILE, trades)

    def get_trades_for_date(self, date: str) -> list:
        """
        Get all trades for a specific date (YYYY-MM-DD format).
        Used for the daily Telegram report.
        """
        trades = self._read(TRADES_FILE)
        return [t for t in trades if t.get("opened_at", "").startswith(date)]

    def get_trades_for_week(self) -> list:
        """
        Get all trades from the past 7 days.
        Used for the weekly Telegram report.
        """
        from datetime import timedelta
        trades = self._read(TRADES_FILE)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        return [t for t in trades if t.get("opened_at", "") >= cutoff]

    def get_all_trades(self) -> list:
        """Return all trade history."""
        return self._read(TRADES_FILE)

    def save_overnight_hold(self, trade_id: str, pair: str, score: float, reasoning: str):
        """Record that a position was held overnight (for reporting)."""
        holds = self._read(OVERNIGHT_FILE)
        holds.append({
            "trade_id": trade_id,
            "pair": pair,
            "score": score,
            "reasoning": reasoning,
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d")
        })
        self._write(OVERNIGHT_FILE, holds)

    def get_overnight_holds(self) -> list:
        """Get pairs held overnight last night."""
        holds = self._read(OVERNIGHT_FILE)
        yesterday = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return [h["pair"] for h in holds if h.get("date") == yesterday]

    def get_summary_stats(self) -> dict:
        """
        Calculate overall performance statistics from all trade history.
        Used to track progress over time.
        """
        trades = [t for t in self._read(TRADES_FILE) if "pl" in t]

        if not trades:
            return {"message": "No completed trades yet"}

        wins = [t for t in trades if t["pl"] > 0]
        losses = [t for t in trades if t["pl"] <= 0]
        total_pl = sum(t["pl"] for t in trades)
        gross_profit = sum(t["pl"] for t in wins)
        gross_loss = abs(sum(t["pl"] for t in losses))

        return {
            "total_trades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(trades) * 100, 1),
            "total_pl": round(total_pl, 2),
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else 999,
            "avg_win": round(gross_profit / len(wins), 2) if wins else 0,
            "avg_loss": round(gross_loss / len(losses), 2) if losses else 0,
        }

    def _read(self, filepath: Path) -> list:
        """Read JSON file safely."""
        try:
            with open(filepath) as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    def _write(self, filepath: Path, data: list):
        """Write JSON file with pretty formatting (human-readable)."""
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2, default=str)
