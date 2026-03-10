"""
bot/instance.py — Multi-Instance Coordination
───────────────────────────────────────────────
Handles everything needed for running multiple bot instances safely.

RIGHT NOW (single instance):
  This module does very little — just identifies which instance this is
  and writes a heartbeat file. You won't notice it's here.

IN FUTURE (multiple instances):
  This is what prevents two instances from double-trading the same pair.
  Each instance checks in here before placing any trade.

How it works:
  1. Each instance has a unique ID set in config.yaml (e.g. "primary")
  2. Each instance only trades the pairs assigned to it in config.yaml
     — this is already how the bot works, so no change needed
  3. Each instance writes a heartbeat file every 30 seconds:
        data/heartbeat_primary.json   ← "I'm alive, last seen: 14:32 UTC"
  4. In failover mode, the secondary watches the primary's heartbeat file
     If it goes stale (> 120 seconds), secondary activates and sends you
     a Telegram alert: "Primary offline — secondary has taken over"
  5. When primary comes back online, it checks if secondary is active,
     notifies you, and waits for you to manually decide who resumes

Pair ownership:
  The simplest and most robust coordination method. Each instance is
  configured with a specific list of pairs in config.yaml. There is
  zero overlap — Instance 1 never looks at Instance 2's pairs and
  vice versa. No locking, no race conditions, no complexity.

  Instance 1 config.yaml:   pairs: [EUR_USD, GBP_USD, USD_CHF]
  Instance 2 config.yaml:   pairs: [USD_JPY, AUD_USD, USD_CAD]

  That's it. Both instances share the same OANDA account safely.
"""

import json
import time
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from loguru import logger

from bot import config

# Heartbeat file location — one per instance
HEARTBEAT_FILE = Path(config.DATA_DIR) / f"heartbeat_{config.INSTANCE_ID}.json"
HEARTBEAT_DIR = Path(config.DATA_DIR)


class InstanceManager:
    """
    Manages this instance's identity, heartbeat, and coordination with
    other instances if they exist.
    """

    def __init__(self, notifier=None):
        self.instance_id = config.INSTANCE_ID
        self.active = config.INSTANCE_ACTIVE
        self.coordination_mode = config.COORDINATION_MODE
        self.notifier = notifier
        self._heartbeat_thread = None
        self._running = False

    def start(self):
        """
        Start the instance manager.
        Writes initial heartbeat and starts the background heartbeat thread.
        """
        logger.info(f"Instance: {self.instance_id.upper()} | "
                    f"Mode: {self.coordination_mode} | "
                    f"Active: {self.active}")

        # Write first heartbeat immediately
        self._write_heartbeat()

        # Start background heartbeat writer
        self._running = True
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True,
            name=f"heartbeat-{self.instance_id}"
        )
        self._heartbeat_thread.start()

        # In failover mode, also start monitoring primary
        if self.coordination_mode == "failover" and self.instance_id != "primary":
            self._start_failover_monitor()

        logger.info(f"✅ Instance manager started — heartbeat every "
                    f"{config.HEARTBEAT_INTERVAL_SECONDS}s")

    def stop(self):
        """Stop the heartbeat writer cleanly."""
        self._running = False
        # Mark this instance as offline in the heartbeat file
        self._write_heartbeat(online=False)

    def is_active(self) -> bool:
        """
        Returns True if this instance should be trading.
        In failover mode, secondary returns False unless primary is offline.
        """
        if not self.active:
            return False

        if self.coordination_mode == "failover" and self.instance_id != "primary":
            return self._is_primary_offline()

        return True

    def get_status(self) -> dict:
        """
        Returns a status summary for health checks and Telegram reports.
        Shows all known instances and their last heartbeat time.
        """
        status = {
            "this_instance": self.instance_id,
            "active": self.active,
            "coordination_mode": self.coordination_mode,
            "pairs_assigned": config.PAIRS,
            "known_instances": self._discover_instances(),
        }
        return status

    # ── Heartbeat ─────────────────────────────────────────────────────────────

    def _heartbeat_loop(self):
        """Write a heartbeat file every N seconds while running."""
        while self._running:
            try:
                self._write_heartbeat()
            except Exception as e:
                logger.warning(f"Heartbeat write failed: {e}")
            time.sleep(config.HEARTBEAT_INTERVAL_SECONDS)

    def _write_heartbeat(self, online: bool = True):
        """Write this instance's heartbeat to disk."""
        HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
        heartbeat = {
            "instance_id": self.instance_id,
            "online": online,
            "active": self.active,
            "pairs": config.PAIRS,
            "coordination_mode": self.coordination_mode,
            "last_seen": datetime.now(timezone.utc).isoformat(),
            "version": "1.0",
        }
        with open(HEARTBEAT_FILE, "w") as f:
            json.dump(heartbeat, f, indent=2)

    # ── Failover Monitor ──────────────────────────────────────────────────────

    def _start_failover_monitor(self):
        """
        Start monitoring the primary instance's heartbeat.
        Only runs on secondary instances in failover mode.
        """
        monitor_thread = threading.Thread(
            target=self._failover_monitor_loop,
            daemon=True,
            name="failover-monitor"
        )
        monitor_thread.start()
        logger.info("Failover monitor started — watching primary instance heartbeat")

    def _failover_monitor_loop(self):
        """
        Continuously check if primary is still alive.
        If primary goes offline, activate this secondary instance.
        """
        was_primary_offline = False

        while self._running:
            primary_offline = self._is_primary_offline()

            if primary_offline and not was_primary_offline:
                # Primary just went offline — activate and alert
                logger.warning("⚠️  Primary instance appears offline — secondary activating")
                self.active = True
                was_primary_offline = True

                if self.notifier:
                    self.notifier.health_alert(
                        "Primary Instance Offline — Failover Activated",
                        f"The primary bot instance has not sent a heartbeat for "
                        f"{config.FAILOVER_TIMEOUT_SECONDS} seconds. "
                        f"This secondary instance ({self.instance_id}) is now trading. "
                        f"Check your primary machine."
                    )

            elif not primary_offline and was_primary_offline:
                # Primary came back online
                logger.info("✅ Primary instance is back online")
                was_primary_offline = False

                if self.notifier:
                    self.notifier.health_recovered(
                        f"Primary instance is back online. "
                        f"Secondary ({self.instance_id}) will keep trading until "
                        f"you manually pause it via config."
                    )

            time.sleep(30)  # Check every 30 seconds

    def _is_primary_offline(self) -> bool:
        """
        Check if the primary instance heartbeat has gone stale.
        Returns True if primary appears to be offline.
        """
        primary_heartbeat = HEARTBEAT_DIR / "heartbeat_primary.json"

        if not primary_heartbeat.exists():
            # No heartbeat file — primary never started or is very new
            return False

        try:
            with open(primary_heartbeat) as f:
                data = json.load(f)

            last_seen = datetime.fromisoformat(
                data.get("last_seen", "2000-01-01T00:00:00+00:00")
            )
            age_seconds = (datetime.now(timezone.utc) - last_seen).total_seconds()

            return age_seconds > config.FAILOVER_TIMEOUT_SECONDS

        except Exception:
            return False

    def _discover_instances(self) -> list:
        """
        Find all instance heartbeat files and return their status.
        Used in health checks and Telegram reports.
        """
        instances = []
        for heartbeat_file in HEARTBEAT_DIR.glob("heartbeat_*.json"):
            try:
                with open(heartbeat_file) as f:
                    data = json.load(f)

                last_seen = datetime.fromisoformat(
                    data.get("last_seen", "2000-01-01T00:00:00+00:00")
                )
                age_seconds = (datetime.now(timezone.utc) - last_seen).total_seconds()

                instances.append({
                    "instance_id": data.get("instance_id", "unknown"),
                    "online": age_seconds < config.FAILOVER_TIMEOUT_SECONDS,
                    "last_seen_seconds_ago": int(age_seconds),
                    "pairs": data.get("pairs", []),
                    "active": data.get("active", False),
                })
            except Exception:
                continue

        return instances
