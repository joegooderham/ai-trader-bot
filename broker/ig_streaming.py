"""
broker/ig_streaming.py — IG Lightstreamer Real-Time Position Monitor
─────────────────────────────────────────────────────────────────────
Connects to IG's Lightstreamer streaming API for real-time position updates.

Instead of polling /positions every 5 minutes, this streams OPU (Open Position
Update) events as they happen — giving sub-second visibility into P&L changes,
stop-loss triggers, and position closures.

How it works:
  1. Reuses CST/X-SECURITY-TOKEN from the REST client (ig_client.py)
  2. Connects to IG's Lightstreamer endpoint (returned by /session)
  3. Subscribes to TRADE:{account_id} in DISTINCT mode
  4. Fires callbacks on position updates, confirmations, and order changes

The streaming client runs in a background thread so it never blocks the
scheduler or Telegram polling loop.

IG Streaming API docs: https://labs.ig.com/streaming-api-guide
"""

import threading
from loguru import logger
from typing import Callable, Optional

try:
    from lightstreamer.client import (
        LightstreamerClient,
        Subscription,
        ClientListener,
        SubscriptionListener,
    )
    LIGHTSTREAMER_AVAILABLE = True
except ImportError:
    LIGHTSTREAMER_AVAILABLE = False
    logger.warning(
        "lightstreamer-client-lib not installed — streaming disabled. "
        "Install with: pip install lightstreamer-client-lib"
    )


class IGPositionListener:
    """
    Listener for IG TRADE subscription events.

    Parses three types of streaming messages:
      - OPU (Open Position Update): position P&L, stop changes, closures
      - CONFIRMS: trade execution confirmations
      - WOU (Working Order Update): pending order changes

    Each update fires the on_position_update callback with a parsed dict.
    """

    def __init__(self, on_position_update: Callable, on_confirmation: Callable = None):
        self.on_position_update = on_position_update
        self.on_confirmation = on_confirmation or (lambda x: None)

    def onItemUpdate(self, update):
        """Called by Lightstreamer when a TRADE subscription event arrives."""
        try:
            # Extract all available fields from the update
            fields = {}
            for i, field_name in enumerate(update.getFields()):
                fields[field_name] = update.getValue(field_name)

            # Determine message type from the update
            # IG streams OPU, CONFIRMS, and WOU on the same TRADE subscription
            raw = fields.get("OPU") or fields.get("CONFIRMS") or fields.get("WOU")

            if fields.get("OPU"):
                self._handle_opu(fields)
            elif fields.get("CONFIRMS"):
                self._handle_confirms(fields)
            else:
                logger.debug(f"Streaming update (unhandled type): {fields}")

        except Exception as e:
            logger.error(f"Error processing streaming update: {e}")

    def _handle_opu(self, fields: dict):
        """Parse an Open Position Update message."""
        try:
            import json
            opu_data = json.loads(fields.get("OPU", "{}"))

            # Resolve IG epic (e.g. CS.D.EURUSD.MINI.IP) back to pair name (EUR_USD)
            from broker.ig_client import IG_EPICS
            epic = opu_data.get("epic", "")
            reverse_epics = {v: k for k, v in IG_EPICS.items()}
            pair = reverse_epics.get(epic, epic)

            position = {
                "dealId": opu_data.get("dealId"),
                "pair": pair,
                "epic": epic,
                "direction": opu_data.get("direction"),
                "dealSize": _safe_float(opu_data.get("size")),
                "level": _safe_float(opu_data.get("level")),
                "stopLevel": _safe_float(opu_data.get("stopLevel")),
                "limitLevel": _safe_float(opu_data.get("limitLevel")),
                "currentPrice": _safe_float(opu_data.get("level")),
                "unrealizedPL": _safe_float(opu_data.get("profit")),
                "status": opu_data.get("status"),
                "channel": opu_data.get("channel"),
                "timestamp": opu_data.get("timestamp"),
            }

            logger.debug(
                f"OPU: {position['pair']} {position['direction']} | "
                f"P&L: {position['unrealizedPL']} | Stop: {position['stopLevel']}"
            )

            self.on_position_update(position)

        except Exception as e:
            logger.error(f"Failed to parse OPU: {e}")

    def _handle_confirms(self, fields: dict):
        """Parse a trade confirmation message."""
        try:
            import json
            confirm_data = json.loads(fields.get("CONFIRMS", "{}"))

            confirmation = {
                "dealId": confirm_data.get("dealId"),
                "dealReference": confirm_data.get("dealReference"),
                "dealStatus": confirm_data.get("dealStatus"),
                "direction": confirm_data.get("direction"),
                "epic": confirm_data.get("epic"),
                "level": _safe_float(confirm_data.get("level")),
                "size": _safe_float(confirm_data.get("size")),
                "profit": _safe_float(confirm_data.get("profit")),
                "status": confirm_data.get("status"),
                "reason": confirm_data.get("reason"),
            }

            logger.info(
                f"Trade confirmed: {confirmation['epic']} {confirmation['direction']} "
                f"@ {confirmation['level']} | Status: {confirmation['dealStatus']}"
            )

            self.on_confirmation(confirmation)

        except Exception as e:
            logger.error(f"Failed to parse CONFIRMS: {e}")

    def onSubscription(self):
        logger.info("TRADE subscription active — receiving real-time position updates")

    def onUnsubscription(self):
        logger.warning("TRADE subscription ended")

    def onSubscriptionError(self, code, message):
        logger.error(f"TRADE subscription error {code}: {message}")

    def onClearSnapshot(self, item_name, item_pos):
        pass

    def onEndOfSnapshot(self, item_name, item_pos):
        logger.debug(f"End of snapshot for {item_name}")

    def onRealMaxFrequency(self, frequency):
        pass

    def onCommandSecondLevelSubscriptionError(self, code, message, key):
        pass

    def onCommandSecondLevelItemLostUpdates(self, lostUpdates, key):
        pass

    def onItemLostUpdates(self, item_name, item_pos, lost_updates):
        logger.warning(f"Lost {lost_updates} updates for {item_name}")


class IGConnectionListener:
    """Monitors the Lightstreamer connection lifecycle."""

    def __init__(self):
        self.connected = False

    def onStatusChange(self, status):
        """Called when connection status changes."""
        self.connected = "CONNECTED" in status
        if self.connected:
            logger.info(f"Lightstreamer connected: {status}")
        else:
            logger.warning(f"Lightstreamer status: {status}")

    def onServerError(self, code, message):
        logger.error(f"Lightstreamer server error {code}: {message}")

    def onPropertyChange(self, property_name):
        pass


class IGStreamingClient:
    """
    Manages the Lightstreamer connection to IG for real-time position streaming.

    Usage:
        streaming = IGStreamingClient(broker)
        streaming.start(
            on_position_update=my_callback,
            on_confirmation=my_confirm_callback
        )
        # ... runs in background thread ...
        streaming.stop()
    """

    def __init__(self, broker):
        """
        Args:
            broker: IGClient instance — we reuse its auth tokens and config
        """
        self.broker = broker
        self._client: Optional[LightstreamerClient] = None
        self._subscription: Optional[Subscription] = None
        self._connection_listener = None
        self._running = False

    @property
    def is_available(self) -> bool:
        """Check if Lightstreamer library is installed."""
        return LIGHTSTREAMER_AVAILABLE

    @property
    def is_connected(self) -> bool:
        return (
            self._connection_listener is not None
            and self._connection_listener.connected
        )

    def start(
        self,
        on_position_update: Callable,
        on_confirmation: Callable = None,
    ):
        """
        Connect to IG Lightstreamer and subscribe to TRADE updates.

        Runs in a background thread — never blocks the main loop.
        Falls back to REST polling if connection fails.
        """
        if not LIGHTSTREAMER_AVAILABLE:
            logger.warning("Lightstreamer not available — falling back to REST polling")
            return False

        if self._running:
            logger.debug("Streaming client already running")
            return True

        try:
            # Get the Lightstreamer endpoint from IG
            ls_endpoint = self._get_lightstreamer_endpoint()
            if not ls_endpoint:
                logger.error("Could not obtain Lightstreamer endpoint from IG")
                return False

            # Build credentials: CST-{token}|XST-{token}
            password = f"CST-{self.broker._cst}|XST-{self.broker._security_token}"

            # Create and configure the Lightstreamer client
            self._client = LightstreamerClient(ls_endpoint, "DEFAULT")
            self._client.connectionDetails.setUser(self.broker.account_id)
            self._client.connectionDetails.setPassword(password)

            # Add connection status listener
            self._connection_listener = IGConnectionListener()
            self._client.addListener(self._connection_listener)

            # Subscribe to TRADE updates for this account
            # DISTINCT mode ensures every position change fires a separate event
            self._subscription = Subscription(
                mode="DISTINCT",
                items=[f"TRADE:{self.broker.account_id}"],
                fields=["OPU", "CONFIRMS", "WOU"],
            )
            self._subscription.setRequestedSnapshot("yes")

            # Attach the position listener
            position_listener = IGPositionListener(
                on_position_update=on_position_update,
                on_confirmation=on_confirmation,
            )
            self._subscription.addListener(position_listener)

            # Connect (runs in its own internal thread)
            self._client.connect()
            self._client.subscribe(self._subscription)
            self._running = True

            logger.info(
                f"Lightstreamer streaming started — "
                f"subscribed to TRADE:{self.broker.account_id}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to start Lightstreamer streaming: {e}")
            self._running = False
            return False

    def stop(self):
        """Disconnect from Lightstreamer and clean up."""
        if self._client:
            try:
                if self._subscription:
                    self._client.unsubscribe(self._subscription)
                self._client.disconnect()
                logger.info("Lightstreamer streaming stopped")
            except Exception as e:
                logger.error(f"Error stopping Lightstreamer: {e}")
            finally:
                self._running = False
                self._client = None
                self._subscription = None

    def reconnect(self):
        """
        Reconnect with fresh auth tokens.
        Called when the REST client re-authenticates (every ~6h).
        """
        if not self._running:
            return

        logger.info("Refreshing Lightstreamer connection with new auth tokens")
        callbacks = None

        # Preserve existing listeners before stopping
        if self._subscription:
            listeners = self._subscription.getListeners()
            if listeners:
                callbacks = listeners[0]  # IGPositionListener

        self.stop()

        if callbacks:
            self.start(
                on_position_update=callbacks.on_position_update,
                on_confirmation=callbacks.on_confirmation,
            )

    def _get_lightstreamer_endpoint(self) -> Optional[str]:
        """
        Fetch the Lightstreamer server URL from IG's /session response.
        The endpoint is returned in the session response body as 'lightstreamerEndpoint'.
        """
        try:
            import httpx
            url = f"{self.broker.base_url}/session"
            headers = {
                "Content-Type": "application/json; charset=UTF-8",
                "Accept": "application/json; charset=UTF-8",
                "X-IG-API-KEY": self.broker.api_key,
                "CST": self.broker._cst,
                "X-SECURITY-TOKEN": self.broker._security_token,
                "Version": "1",
            }
            response = httpx.get(url, headers=headers, timeout=15)
            if response.status_code == 200:
                data = response.json()
                endpoint = data.get("lightstreamerEndpoint")
                if endpoint:
                    logger.debug(f"Lightstreamer endpoint: {endpoint}")
                    return endpoint
                else:
                    logger.error("No lightstreamerEndpoint in /session response")
            else:
                logger.error(
                    f"Failed to get Lightstreamer endpoint: "
                    f"{response.status_code} {response.text}"
                )
        except Exception as e:
            logger.error(f"Error fetching Lightstreamer endpoint: {e}")
        return None


def _safe_float(value) -> Optional[float]:
    """Safely convert a value to float, returning None if not possible."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None
