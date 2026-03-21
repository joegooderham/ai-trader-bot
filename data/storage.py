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

            -- ── Analytics Tables (Phase 2) ──────────────────────────────────
            -- Every LSTM prediction is logged here with outcome tracking.
            -- Predictions that lead to trades are linked via trade_id.
            -- The outcome columns (actual_direction, actual_pips, was_correct)
            -- are populated later by the outcome resolver once enough candles exist.
            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pair TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                predicted_direction TEXT NOT NULL,
                predicted_probability REAL NOT NULL,
                confidence_score REAL,
                indicator_only_score REAL,
                actual_direction TEXT,
                actual_pips REAL,
                was_correct INTEGER,
                trade_id TEXT,
                model_version TEXT,
                confidence_breakdown TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_predictions_pair_ts
                ON predictions(pair, timestamp);
            CREATE INDEX IF NOT EXISTS idx_predictions_created
                ON predictions(created_at);

            -- Snapshot of model metrics after each retrain cycle.
            -- Used for drift detection (comparing live accuracy vs training accuracy).
            CREATE TABLE IF NOT EXISTS model_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                model_version TEXT,
                val_accuracy REAL,
                val_loss REAL,
                train_accuracy REAL,
                train_samples INTEGER,
                val_samples INTEGER,
                epochs_trained INTEGER,
                training_duration_seconds REAL,
                feature_count INTEGER,
                hidden_size INTEGER,
                num_layers INTEGER,
                data_period TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            -- Rolling performance snapshots computed periodically.
            -- Stores named metrics (e.g. "rolling_accuracy_24h") with pair
            -- and window context. Queried by API endpoints and Telegram commands.
            CREATE TABLE IF NOT EXISTS analytics_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                metric_name TEXT NOT NULL,
                metric_value REAL NOT NULL,
                pair TEXT,
                window TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_analytics_metric
                ON analytics_snapshots(metric_name, timestamp);

            -- Daily plans generated by Claude AI each evening.
            -- Stored so the dashboard can display the latest plan and history.
            CREATE TABLE IF NOT EXISTS daily_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL UNIQUE,
                plan_text TEXT NOT NULL,
                context_json TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
        """)
        conn.commit()

        # Schema migrations — add columns that may not exist in older databases
        _migrate_add_column(conn, "predictions", "confidence_breakdown", "TEXT")

        conn.commit()
    finally:
        conn.close()


def _migrate_add_column(conn, table: str, column: str, col_type: str):
    """Add a column to a table if it doesn't already exist (safe migration)."""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
    except Exception:
        pass  # Column already exists


# Initialise database on module load
_init_db()


class TradeStorage:
    """Handles reading and writing trade data to SQLite."""

    def __init__(self):
        # Migrate any existing JSON data into SQLite on first run
        self._migrate_json_if_needed()

    def save_trade(self, trade: dict) -> int:
        """
        Save a new trade to the database.
        Called whenever a trade is opened or closed.
        Returns the auto-incremented trade number (id) for use in notifications.
        """
        conn = _get_connection()
        try:
            # Serialise breakdown dict to JSON string for storage
            breakdown = trade.get("breakdown")
            if isinstance(breakdown, dict):
                breakdown = json.dumps(breakdown)

            cursor = conn.execute("""
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
            trade_number = cursor.lastrowid
            logger.debug(f"Trade #{trade_number} saved: {trade.get('pair')} {trade.get('direction')}")
            return trade_number
        finally:
            conn.close()

    def update_trade(self, trade_id: str, updates: dict):
        """
        Update an existing trade record (e.g., when it closes with P&L).
        Matches on trade_id OR deal_id to handle both open and close paths,
        since save_trade() stores deal_id in both columns.
        """
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
            # Match on either trade_id or deal_id column for robustness
            values.extend([trade_id, trade_id])

            if columns:
                rows_changed = conn.execute(
                    f"UPDATE trades SET {', '.join(columns)} WHERE trade_id = ? OR deal_id = ?",
                    values
                ).rowcount
                conn.commit()
                if rows_changed == 0:
                    logger.warning(f"update_trade: no matching row for trade_id/deal_id={trade_id}")
                else:
                    logger.debug(f"Trade {trade_id} updated: {list(updates.keys())}")
        finally:
            conn.close()

    def get_open_trades_from_db(self) -> list:
        """Return all trades that the DB thinks are still open (closed_at IS NULL)."""
        conn = _get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM trades WHERE closed_at IS NULL ORDER BY opened_at DESC"
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]
        finally:
            conn.close()

    def get_trade_by_deal_id(self, deal_id: str) -> dict:
        """Fetch a trade record by IG deal_id. Returns dict or None."""
        conn = _get_connection()
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM trades WHERE deal_id = ?", (deal_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def update_trade_field(self, deal_id: str, field: str, value):
        """Update a single field on a trade identified by deal_id."""
        conn = _get_connection()
        try:
            conn.execute(
                f"UPDATE trades SET {field} = ? WHERE deal_id = ?",
                (value, deal_id)
            )
            conn.commit()
        finally:
            conn.close()

    def get_trade_number(self, deal_id: str) -> int:
        """Look up the auto-incremented trade number for a deal_id."""
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT id FROM trades WHERE deal_id = ? OR trade_id = ?",
                (deal_id, deal_id)
            ).fetchone()
            return row[0] if row else None
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

    # ── Prediction Tracking (Phase 2) ────────────────────────────────────────

    def save_prediction(self, prediction: dict) -> int:
        """
        Log an LSTM prediction for later accuracy tracking.
        Called every scan for every pair that gets an LSTM prediction.
        Returns the prediction row id.
        """
        conn = _get_connection()
        try:
            # Store confidence breakdown as JSON string if provided
            breakdown = prediction.get("confidence_breakdown")
            if isinstance(breakdown, dict):
                import json as _json
                breakdown = _json.dumps(breakdown)

            cursor = conn.execute("""
                INSERT INTO predictions
                (pair, timestamp, predicted_direction, predicted_probability,
                 confidence_score, indicator_only_score, trade_id, model_version,
                 confidence_breakdown)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                prediction.get("pair"),
                prediction.get("timestamp"),
                prediction.get("predicted_direction"),
                prediction.get("predicted_probability"),
                prediction.get("confidence_score"),
                prediction.get("indicator_only_score"),
                prediction.get("trade_id"),
                prediction.get("model_version"),
                breakdown,
            ))
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def update_prediction_outcome(self, prediction_id: int, actual_direction: str,
                                   actual_pips: float, was_correct: bool):
        """Update a prediction with the actual outcome once we have enough future candles."""
        conn = _get_connection()
        try:
            conn.execute("""
                UPDATE predictions
                SET actual_direction = ?, actual_pips = ?, was_correct = ?
                WHERE id = ?
            """, (actual_direction, actual_pips, 1 if was_correct else 0, prediction_id))
            conn.commit()
        finally:
            conn.close()

    def get_unresolved_predictions(self, max_age_hours: int = 24) -> list:
        """
        Get predictions that haven't had their outcome resolved yet.
        Only looks back max_age_hours to avoid processing ancient predictions.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
        conn = _get_connection()
        try:
            rows = conn.execute("""
                SELECT id, pair, timestamp, predicted_direction
                FROM predictions
                WHERE was_correct IS NULL AND created_at >= ?
                ORDER BY created_at
            """, (cutoff,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_prediction_accuracy(self, hours: int = None, pair: str = None) -> dict:
        """
        Calculate prediction accuracy over a time window.
        Returns overall and per-direction accuracy.
        """
        conn = _get_connection()
        try:
            where = ["was_correct IS NOT NULL"]
            params = []
            if hours:
                cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
                where.append("created_at >= ?")
                params.append(cutoff)
            if pair:
                where.append("pair = ?")
                params.append(pair)

            where_clause = " AND ".join(where)

            row = conn.execute(f"""
                SELECT
                    COUNT(*) as total,
                    SUM(was_correct) as correct,
                    SUM(CASE WHEN predicted_direction = 'BUY' THEN 1 ELSE 0 END) as buy_total,
                    SUM(CASE WHEN predicted_direction = 'BUY' AND was_correct = 1 THEN 1 ELSE 0 END) as buy_correct,
                    SUM(CASE WHEN predicted_direction = 'SELL' THEN 1 ELSE 0 END) as sell_total,
                    SUM(CASE WHEN predicted_direction = 'SELL' AND was_correct = 1 THEN 1 ELSE 0 END) as sell_correct
                FROM predictions WHERE {where_clause}
            """, params).fetchone()

            if not row or row["total"] == 0:
                return {"total": 0, "accuracy": 0, "message": "No resolved predictions"}

            total = row["total"]
            correct = row["correct"] or 0
            return {
                "total": total,
                "correct": correct,
                "accuracy": round(correct / total * 100, 1),
                "buy_accuracy": round((row["buy_correct"] or 0) / row["buy_total"] * 100, 1) if row["buy_total"] else 0,
                "sell_accuracy": round((row["sell_correct"] or 0) / row["sell_total"] * 100, 1) if row["sell_total"] else 0,
            }
        finally:
            conn.close()

    def get_recent_predictions(self, limit: int = 50) -> list:
        """Get the most recent predictions with outcomes."""
        conn = _get_connection()
        try:
            rows = conn.execute("""
                SELECT * FROM predictions
                ORDER BY created_at DESC LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── Model Metrics (Phase 2) ──────────────────────────────────────────────

    def save_model_metrics(self, metrics: dict):
        """Save a snapshot of model training metrics after each retrain."""
        conn = _get_connection()
        try:
            conn.execute("""
                INSERT INTO model_metrics
                (timestamp, model_version, val_accuracy, val_loss, train_accuracy,
                 train_samples, val_samples, epochs_trained, training_duration_seconds,
                 feature_count, hidden_size, num_layers, data_period)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                datetime.now(timezone.utc).isoformat(),
                metrics.get("model_version"),
                metrics.get("val_accuracy"),
                metrics.get("best_val_loss"),
                metrics.get("train_accuracy"),
                metrics.get("train_samples"),
                metrics.get("val_samples"),
                metrics.get("epochs_trained"),
                metrics.get("training_duration_seconds"),
                metrics.get("num_features"),
                metrics.get("hidden_size"),
                metrics.get("num_layers"),
                metrics.get("extended_period"),
            ))
            conn.commit()
        finally:
            conn.close()

    def get_latest_model_metrics(self) -> dict:
        """Get metrics from the most recent training run."""
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM model_metrics ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else {}
        finally:
            conn.close()

    def get_model_history(self, limit: int = 10) -> list:
        """Get training history — used for drift detection and dashboards."""
        conn = _get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM model_metrics ORDER BY timestamp DESC LIMIT ?",
                (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── Analytics Snapshots (Phase 2) ─────────────────────────────────────────

    def save_analytics_snapshot(self, metric_name: str, value: float,
                                 pair: str = None, window: str = None):
        """Save a computed analytics metric."""
        conn = _get_connection()
        try:
            conn.execute("""
                INSERT INTO analytics_snapshots
                (timestamp, metric_name, metric_value, pair, window)
                VALUES (?, ?, ?, ?, ?)
            """, (
                datetime.now(timezone.utc).isoformat(),
                metric_name, value, pair, window,
            ))
            conn.commit()
        finally:
            conn.close()

    def get_analytics(self, metric_name: str, hours: int = 24,
                       pair: str = None) -> list:
        """Get time series of a specific metric."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        conn = _get_connection()
        try:
            where = ["metric_name = ?", "timestamp >= ?"]
            params = [metric_name, cutoff]
            if pair:
                where.append("pair = ?")
                params.append(pair)

            rows = conn.execute(f"""
                SELECT timestamp, metric_value, pair, window
                FROM analytics_snapshots
                WHERE {" AND ".join(where)}
                ORDER BY timestamp
            """, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── Daily Plans ────────────────────────────────────────────────────────────

    def save_daily_plan(self, date: str, plan_text: str, context: dict = None):
        """Persist a daily plan so the dashboard and Telegram can serve it."""
        conn = _get_connection()
        try:
            conn.execute("""
                INSERT OR REPLACE INTO daily_plans (date, plan_text, context_json)
                VALUES (?, ?, ?)
            """, (
                date,
                plan_text,
                json.dumps(context) if context else None,
            ))
            conn.commit()
            logger.debug(f"Daily plan saved for {date}")
        finally:
            conn.close()

    def get_latest_daily_plan(self) -> Optional[dict]:
        """Get the most recent daily plan."""
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM daily_plans ORDER BY date DESC LIMIT 1"
            ).fetchone()
            if not row:
                return None
            result = dict(row)
            if result.get("context_json"):
                try:
                    result["context"] = json.loads(result["context_json"])
                except json.JSONDecodeError:
                    pass
            return result
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
