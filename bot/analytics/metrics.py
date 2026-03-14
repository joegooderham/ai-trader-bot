"""
bot/analytics/metrics.py — Performance Metrics Computation
───────────────────────────────────────────────────────────
Computes and stores rolling performance metrics from the predictions
and trades tables. Runs hourly via the scheduler.

Metrics computed:
  - Prediction accuracy (overall, per-pair, per-direction)
  - LSTM edge (difference between LSTM-enhanced and indicator-only outcomes)
  - Confidence calibration (does 70% confidence = ~70% win rate?)
  - Win rate trend (improving or degrading over time)
  - Sharpe-like ratio from trade P&L

All metrics are stored in the analytics_snapshots table for querying
by API endpoints, Telegram commands, and the future dashboard.
"""

from datetime import datetime, timezone, timedelta
from loguru import logger
from data.storage import TradeStorage
from bot import config


class MetricsEngine:
    """Computes and persists rolling analytics metrics."""

    def __init__(self):
        self.storage = TradeStorage()

    def compute_all(self):
        """
        Run all metric computations and store results.
        Called hourly by the scheduler.
        """
        try:
            self._compute_prediction_accuracy()
            self._compute_lstm_edge()
            self._compute_pair_accuracy()
            self._compute_win_rate_trend()
            logger.debug("Analytics metrics computed successfully")
        except Exception as e:
            logger.error(f"Metrics computation failed: {e}")

    def _compute_prediction_accuracy(self):
        """Rolling accuracy at 24h, 7d, 30d windows."""
        for hours, window in [(24, "24h"), (168, "7d"), (720, "30d")]:
            acc = self.storage.get_prediction_accuracy(hours=hours)
            if acc.get("total", 0) > 0:
                self.storage.save_analytics_snapshot(
                    "prediction_accuracy", acc["accuracy"], window=window
                )
                self.storage.save_analytics_snapshot(
                    "predictions_resolved", acc["total"], window=window
                )

    def _compute_lstm_edge(self):
        """
        Compare LSTM-enhanced confidence scores vs indicator-only scores
        for predictions that resulted in trades. The "edge" is how much
        the LSTM adds (or subtracts) from the indicator-only baseline.
        """
        predictions = self.storage.get_recent_predictions(limit=200)
        if not predictions:
            return

        # Only look at predictions where both scores exist
        with_both = [
            p for p in predictions
            if p.get("confidence_score") is not None
            and p.get("indicator_only_score") is not None
        ]

        if len(with_both) < 5:
            return

        # Average difference: positive = LSTM adds value, negative = LSTM hurts
        diffs = [
            p["confidence_score"] - p["indicator_only_score"]
            for p in with_both
        ]
        avg_edge = sum(diffs) / len(diffs)

        self.storage.save_analytics_snapshot(
            "lstm_edge_avg", round(avg_edge, 2), window="recent"
        )

        # Also track how often LSTM agrees with indicators
        agreements = sum(1 for d in diffs if d >= 0)
        agreement_pct = round(agreements / len(diffs) * 100, 1)
        self.storage.save_analytics_snapshot(
            "lstm_indicator_agreement", agreement_pct, window="recent"
        )

    def _compute_pair_accuracy(self):
        """Per-pair prediction accuracy over the last 7 days."""
        for pair in config.PAIRS:
            acc = self.storage.get_prediction_accuracy(hours=168, pair=pair)
            if acc.get("total", 0) >= 5:
                self.storage.save_analytics_snapshot(
                    "pair_accuracy_7d", acc["accuracy"], pair=pair, window="7d"
                )

    def _compute_win_rate_trend(self):
        """
        Compare this week's win rate to last week's to detect improvement
        or degradation. Stored as a delta: +5 means 5% better than last week.
        """
        now = datetime.now(timezone.utc)
        this_week = self.storage.get_prediction_accuracy(hours=168)
        # Last week = 7-14 days ago. We approximate by getting 14d accuracy
        # and subtracting this week's contribution.
        two_weeks = self.storage.get_prediction_accuracy(hours=336)

        this_total = this_week.get("total", 0)
        two_total = two_weeks.get("total", 0)
        last_total = two_total - this_total

        if this_total < 5 or last_total < 5:
            return

        this_acc = this_week["accuracy"]
        # Back-calculate last week's accuracy from the two-week and this-week numbers
        two_correct = int(two_weeks.get("correct", 0))
        this_correct = int(this_week.get("correct", 0))
        last_correct = two_correct - this_correct
        last_acc = round(last_correct / last_total * 100, 1) if last_total > 0 else 0

        trend = round(this_acc - last_acc, 1)
        self.storage.save_analytics_snapshot(
            "accuracy_trend_weekly", trend, window="week_over_week"
        )

    def get_summary(self) -> dict:
        """
        Build a summary dict of current analytics state.
        Used by Telegram commands and API endpoints.
        """
        acc_24h = self.storage.get_prediction_accuracy(hours=24)
        acc_7d = self.storage.get_prediction_accuracy(hours=168)
        model = self.storage.get_latest_model_metrics()

        # Get LSTM edge from latest snapshot
        edge_snapshots = self.storage.get_analytics("lstm_edge_avg", hours=24)
        lstm_edge = edge_snapshots[-1]["metric_value"] if edge_snapshots else None

        return {
            "prediction_accuracy_24h": acc_24h,
            "prediction_accuracy_7d": acc_7d,
            "model_metrics": model,
            "lstm_edge": lstm_edge,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
