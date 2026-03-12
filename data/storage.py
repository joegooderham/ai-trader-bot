"""
data/storage.py — SQLite Trade & Candle Storage
─────────────────────────────────────────────────
Stores all trade history and candle data in a local SQLite database.
This file is persisted via Docker volume, so it survives container restarts.

Why SQLite instead of JSON?
  - Candle history grows large — JSON becomes slow to read/write
  - SQL queries are faster for date filtering and aggregation
  - Required for LSTM training later (BACKLOG-007)
  - Still zero setup, single file, no database server needed
  - Can still be committed to GitHub for backup

Why SQLite instead of Postgres/MySQL?
  - Zero infrastructure — just a file on disk
  - No Docker container needed for the database
  - Sufficient for the volume of data this bot generates
  - Easy to copy/backup/migrate

Tables:
  - trades: All opened/closed trade records
  - overnight_holds: Positions held past EOD close
  - candles: Historical OHLCV data keyed by pair, timeframe, and timestamp
"""

import sqlite3
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from loguru import logger
from typing import Optional
import pandas as pd

from bot import config

DB_PATH = config.DATA_DIR / "trader.db"


def _get_connection() -> sqlite3.Connection:
    """
    Create a new SQLite connection with WAL mode for better concurrent access.
    Each call creates a fresh connection — SQLite handles file locking internally.
    """
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # Better performance for concurrent reads
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _init_db():
    """Create tables if they don't exist. Called once at import time."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = _get_connection()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT UNIQUE,
                pair TEXT NOT NULL,
                direction TEXT NOT NULL,
                size REAL,
                fill_price REAL,
                close_price REAL,
                stop_loss REAL,
                take_profit REAL,
                pl REAL,
                confidence_score REAL,
                reasoning TEXT,
                status TEXT,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                close_reason TEXT,
                deal_id TEXT,
                deal_reference TEXT,
                breakdown TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_trades_opened_at ON trades(opened_at);
            CREATE INDEX IF NOT EXISTS idx_trades_pair ON trades(pair);

            CREATE TABLE IF NOT EXISTS overnight_holds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT NOT NULL,
                pair TEXT NOT NULL,
                score REAL NOT NULL,
                reasoning TEXT,
                date TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_overnight_date ON overnight_holds(date);

            CREATE TABLE IF NOT EXISTS candles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pair TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL DEFAULT 0,
                source TEXT DEFAULT 'ig',
                UNIQUE(pair, timeframe, timestamp)
            );

            CREATE INDEX IF NOT EXISTS idx_candles_lookup
                ON candles(pair, timeframe, timestamp);
        """)
        conn.commit()
    finally:
        conn.close()


# Initialise database on module load
_init_db()


class TradeStorage:
    """Handles reading and writing trade data to SQLite."""

    def __init__(self):
        # Migrate any existing JSON data into SQLite on first run
        self._migrate_json_if_needed()

    def save_trade(self, trade: dict):
        """
        Save a new trade to the database.
        Called whenever a trade is opened or closed.
        """
        conn = _get_connection()
        try:
            # Serialise breakdown dict to JSON string for storage
            breakdown = trade.get("breakdown")
            if isinstance(breakdown, dict):
                breakdown = json.dumps(breakdown)

            conn.execute("""
                INSERT OR REPLACE INTO trades
                (trade_id, pair, direction, size, fill_price, close_price,
                 stop_loss, take_profit, pl, confidence_score, reasoning,
                 status, opened_at, closed_at, close_reason, deal_id,
                 deal_reference, breakdown)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade.get("trade_id") or trade.get("deal_id"),
                trade.get("pair"),
                trade.get("direction"),
                trade.get("size"),
                trade.get("fill_price"),
                trade.get("close_price"),
                trade.get("stop_loss"),
                trade.get("take_profit"),
                trade.get("pl"),
                trade.get("confidence_score"),
                trade.get("reasoning"),
                trade.get("status"),
                trade.get("opened_at"),
                trade.get("closed_at"),
                trade.get("close_reason"),
                trade.get("deal_id"),
                trade.get("deal_reference"),
                breakdown,
            ))
            conn.commit()
            logger.debug(f"Trade saved: {trade.get('pair')} {trade.get('direction')}")
        finally:
            conn.close()

    def update_trade(self, trade_id: str, updates: dict):
        """Update an existing trade record (e.g., when it closes with P&L)."""
        conn = _get_connection()
        try:
            # Build SET clause dynamically from the updates dict
            columns = []
            values = []
            for key, value in updates.items():
                if isinstance(value, dict):
                    value = json.dumps(value)
                columns.append(f"{key} = ?")
                values.append(value)
            values.append(trade_id)

            if columns:
                conn.execute(
                    f"UPDATE trades SET {', '.join(columns)} WHERE trade_id = ?",
                    values
                )
                conn.commit()
        finally:
            conn.close()

    def get_trades_for_date(self, date: str) -> list:
        """
        Get all trades for a specific date (YYYY-MM-DD format).
        Used for the daily Telegram report.
        """
        conn = _get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM trades WHERE opened_at LIKE ? ORDER BY opened_at",
                (f"{date}%",)
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]
        finally:
            conn.close()

    def get_trades_for_week(self) -> list:
        """
        Get all trades from the past 7 days.
        Used for the weekly Telegram report.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        conn = _get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM trades WHERE opened_at >= ? ORDER BY opened_at",
                (cutoff,)
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]
        finally:
            conn.close()

    def get_all_trades(self) -> list:
        """Return all trade history."""
        conn = _get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM trades ORDER BY opened_at"
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]
        finally:
            conn.close()

    def save_overnight_hold(self, trade_id: str, pair: str, score: float, reasoning: str):
        """Record that a position was held overnight (for reporting)."""
        conn = _get_connection()
        try:
            conn.execute(
                "INSERT INTO overnight_holds (trade_id, pair, score, reasoning, date) VALUES (?, ?, ?, ?, ?)",
                (trade_id, pair, score, reasoning, datetime.now(timezone.utc).strftime("%Y-%m-%d"))
            )
            conn.commit()
        finally:
            conn.close()

    def get_overnight_holds(self) -> list:
        """Get pairs held overnight last night."""
        yesterday = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        conn = _get_connection()
        try:
            rows = conn.execute(
                "SELECT pair FROM overnight_holds WHERE date = ?",
                (yesterday,)
            ).fetchall()
            return [r["pair"] for r in rows]
        finally:
            conn.close()

    def get_summary_stats(self) -> dict:
        """
        Calculate overall performance statistics from all trade history.
        Uses SQL aggregation instead of loading all trades into memory.
        """
        conn = _get_connection()
        try:
            row = conn.execute("""
                SELECT
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN pl > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN pl <= 0 THEN 1 ELSE 0 END) as losses,
                    SUM(pl) as total_pl,
                    SUM(CASE WHEN pl > 0 THEN pl ELSE 0 END) as gross_profit,
                    SUM(CASE WHEN pl <= 0 THEN ABS(pl) ELSE 0 END) as gross_loss
                FROM trades WHERE pl IS NOT NULL
            """).fetchone()

            if not row or row["total_trades"] == 0:
                return {"message": "No completed trades yet"}

            total = row["total_trades"]
            wins = row["wins"]
            losses = row["losses"]
            total_pl = row["total_pl"]
            gross_profit = row["gross_profit"] or 0
            gross_loss = row["gross_loss"] or 0

            return {
                "total_trades": total,
                "wins": wins,
                "losses": losses,
                "win_rate": round(wins / total * 100, 1),
                "total_pl": round(total_pl, 2),
                "gross_profit": round(gross_profit, 2),
                "gross_loss": round(gross_loss, 2),
                "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else 999,
                "avg_win": round(gross_profit / wins, 2) if wins else 0,
                "avg_loss": round(gross_loss / losses, 2) if losses else 0,
            }
        finally:
            conn.close()

    # ── Candle Storage ─────────────────────────────────────────────────────────

    def save_candles(self, pair: str, timeframe: str, df: pd.DataFrame, source: str = "ig"):
        """
        Store candle data in SQLite. Uses INSERT OR IGNORE to skip duplicates,
        so it's safe to call repeatedly with overlapping data.
        """
        if df is None or df.empty:
            return

        conn = _get_connection()
        try:
            rows = []
            for idx, row in df.iterrows():
                timestamp = str(idx)
                rows.append((
                    pair, timeframe, timestamp,
                    float(row["open"]), float(row["high"]),
                    float(row["low"]), float(row["close"]),
                    float(row.get("volume", 0)),
                    source,
                ))

            conn.executemany("""
                INSERT OR IGNORE INTO candles
                (pair, timeframe, timestamp, open, high, low, close, volume, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, rows)
            conn.commit()
            logger.debug(f"Saved {len(rows)} candles for {pair} {timeframe} (source: {source})")
        finally:
            conn.close()

    def get_candles(self, pair: str, timeframe: str, count: int = 60) -> Optional[pd.DataFrame]:
        """
        Retrieve candle data from SQLite. Returns a DataFrame matching the
        format used by ig_client.py (columns: open, high, low, close, volume;
        index: datetime with UTC timezone).

        Returns None if no data is found.
        """
        conn = _get_connection()
        try:
            rows = conn.execute("""
                SELECT timestamp, open, high, low, close, volume
                FROM candles
                WHERE pair = ? AND timeframe = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (pair, timeframe, count)).fetchall()

            if not rows:
                return None

            data = [{
                "datetime": pd.to_datetime(r["timestamp"], utc=True),
                "open": r["open"],
                "high": r["high"],
                "low": r["low"],
                "close": r["close"],
                "volume": r["volume"],
            } for r in rows]

            df = pd.DataFrame(data).set_index("datetime").sort_index()
            return df
        finally:
            conn.close()

    def get_candle_count(self, pair: str, timeframe: str) -> int:
        """Return how many candles we have stored for a pair/timeframe."""
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM candles WHERE pair = ? AND timeframe = ?",
                (pair, timeframe)
            ).fetchone()
            return row["cnt"] if row else 0
        finally:
            conn.close()

    def get_latest_candle_time(self, pair: str, timeframe: str) -> Optional[datetime]:
        """Return the timestamp of the most recent candle stored."""
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT MAX(timestamp) as latest FROM candles WHERE pair = ? AND timeframe = ?",
                (pair, timeframe)
            ).fetchone()
            if row and row["latest"]:
                return pd.to_datetime(row["latest"], utc=True).to_pydatetime()
            return None
        finally:
            conn.close()

    # ── Migration ──────────────────────────────────────────────────────────────

    def _migrate_json_if_needed(self):
        """
        One-time migration: if trades.json exists with data, import it into
        SQLite and rename the file so we don't re-import on next restart.
        """
        json_trades = config.DATA_DIR / "trades.json"
        json_holds = config.DATA_DIR / "overnight_holds.json"

        if json_trades.exists():
            try:
                with open(json_trades) as f:
                    trades = json.load(f)

                if trades:
                    logger.info(f"Migrating {len(trades)} trades from JSON to SQLite")
                    for trade in trades:
                        self.save_trade(trade)

                # Rename to prevent re-migration
                json_trades.rename(config.DATA_DIR / "trades.json.migrated")
                logger.info("JSON trade data migrated to SQLite successfully")
            except Exception as e:
                logger.warning(f"JSON migration skipped: {e}")

        if json_holds.exists():
            try:
                with open(json_holds) as f:
                    holds = json.load(f)

                if holds:
                    logger.info(f"Migrating {len(holds)} overnight holds from JSON to SQLite")
                    conn = _get_connection()
                    try:
                        for h in holds:
                            conn.execute(
                                "INSERT OR IGNORE INTO overnight_holds (trade_id, pair, score, reasoning, date) VALUES (?, ?, ?, ?, ?)",
                                (h.get("trade_id"), h.get("pair"), h.get("score"), h.get("reasoning"), h.get("date"))
                            )
                        conn.commit()
                    finally:
                        conn.close()

                json_holds.rename(config.DATA_DIR / "overnight_holds.json.migrated")
                logger.info("JSON overnight holds migrated to SQLite successfully")
            except Exception as e:
                logger.warning(f"JSON holds migration skipped: {e}")

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        """Convert a sqlite3.Row to a plain dict, deserialising JSON fields."""
        d = dict(row)
        # Remove internal SQLite id — callers don't need it
        d.pop("id", None)
        d.pop("created_at", None)
        # Deserialise breakdown from JSON string back to dict
        if d.get("breakdown") and isinstance(d["breakdown"], str):
            try:
                d["breakdown"] = json.loads(d["breakdown"])
            except json.JSONDecodeError:
                pass
        return d
