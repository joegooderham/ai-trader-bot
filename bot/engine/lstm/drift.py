"""
bot/engine/lstm/drift.py — Model Drift Detection
──────────────────────────────────────────────────
Monitors LSTM prediction accuracy over time and detects when the model
has drifted from its training accuracy — i.e. market conditions have
changed enough that the model's learned patterns no longer apply.

Drift detection works by comparing:
  - Rolling live accuracy (from the predictions table) over 24h, 7d, 30d
  - Training accuracy (from the model_metrics table) at last retrain

If rolling accuracy drops more than 15% below training accuracy, the model
is flagged as "drifted" and an early retrain is recommended.

This runs as a scheduled job (every 30 minutes) alongside the main scanner.
"""

from datetime import datetime, timezone
from loguru import logger
from data.storage import TradeStorage


# If live accuracy drops this far below training accuracy, flag drift
DRIFT_THRESHOLD_PCT = 15.0

# Minimum number of resolved predictions before we can meaningfully
# assess drift — with fewer samples, random noise dominates
MIN_PREDICTIONS_FOR_DRIFT = 20


class DriftDetector:
    """Detects when LSTM model accuracy has degraded beyond acceptable levels."""

    def __init__(self):
        self.storage = TradeStorage()
        self._last_drift_status = None

    def check(self) -> dict:
        """
        Run a drift check. Returns a status dict:
          - status: "ok", "drift", or "insufficient_data"
          - rolling_accuracy_24h: live accuracy over last 24 hours
          - rolling_accuracy_7d: live accuracy over last 7 days
          - training_accuracy: accuracy at last retrain
          - drift_delta: difference between training and live accuracy
          - should_retrain: True if early retrain is recommended
        """
        # Get the most recent training metrics for comparison baseline
        model_metrics = self.storage.get_latest_model_metrics()
        training_accuracy = model_metrics.get("val_accuracy", 0) * 100 if model_metrics else 0

        # Get rolling live accuracy at different windows
        acc_24h = self.storage.get_prediction_accuracy(hours=24)
        acc_7d = self.storage.get_prediction_accuracy(hours=168)  # 7 * 24

        # Need enough resolved predictions for a meaningful comparison
        total_resolved = acc_24h.get("total", 0)

        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "training_accuracy": round(training_accuracy, 1),
            "rolling_accuracy_24h": acc_24h.get("accuracy", 0),
            "rolling_accuracy_7d": acc_7d.get("accuracy", 0),
            "predictions_resolved_24h": acc_24h.get("total", 0),
            "predictions_resolved_7d": acc_7d.get("total", 0),
        }

        if total_resolved < MIN_PREDICTIONS_FOR_DRIFT:
            result["status"] = "insufficient_data"
            result["drift_delta"] = 0
            result["should_retrain"] = False
            result["message"] = (
                f"Only {total_resolved} resolved predictions — need {MIN_PREDICTIONS_FOR_DRIFT} "
                f"before drift detection is meaningful"
            )
            self._last_drift_status = result
            return result

        # Compare 24h rolling accuracy against training accuracy
        live_accuracy = acc_24h["accuracy"]
        drift_delta = training_accuracy - live_accuracy

        result["drift_delta"] = round(drift_delta, 1)

        if drift_delta > DRIFT_THRESHOLD_PCT:
            result["status"] = "drift"
            result["should_retrain"] = True
            result["message"] = (
                f"Model drift detected: live accuracy {live_accuracy:.1f}% vs "
                f"training {training_accuracy:.1f}% (delta: {drift_delta:.1f}%). "
                f"Early retrain recommended."
            )
            logger.warning(result["message"])

            # Save drift event to analytics for dashboard visibility
            self.storage.save_analytics_snapshot(
                "drift_detected", drift_delta, window="24h"
            )
        else:
            result["status"] = "ok"
            result["should_retrain"] = False
            result["message"] = (
                f"Model healthy: live accuracy {live_accuracy:.1f}% vs "
                f"training {training_accuracy:.1f}% (delta: {drift_delta:.1f}%)"
            )

        self._last_drift_status = result
        return result

    def get_last_status(self) -> dict:
        """Return the most recent drift check result without re-running."""
        return self._last_drift_status or {"status": "not_checked", "message": "No drift check run yet"}
