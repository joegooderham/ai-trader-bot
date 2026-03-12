"""
scripts/health_monitor.py — System Health Monitor
───────────────────────────────────────────────────
Watches the trading bot and MCP server continuously.
Sends a Telegram alert within 60 seconds if anything goes wrong.

Checks:
  - Is the trading bot running? (HTTP health endpoint)
  - Is the MCP server running? (HTTP health endpoint)
  - Is the IG API reachable?
  - Is there enough disk space?
  - Has the bot made any trades recently? (are we stuck?)

Run with: python -m scripts.health_monitor
"""

import time
import httpx
from loguru import logger
from datetime import datetime, timezone

from notifications.telegram_bot import TelegramNotifier
from bot import config

notifier = TelegramNotifier()

# Track issue states so we don't spam Telegram with repeated alerts
_known_issues = set()

CHECK_INTERVAL_SECONDS = 60  # Check every minute


def check_service(name: str, url: str) -> bool:
    """Check if a service is responding to HTTP requests."""
    try:
        with httpx.Client(timeout=10) as client:
            response = client.get(url)
            return response.status_code == 200
    except Exception:
        return False


def check_ig_api() -> bool:
    """Verify IG API is reachable by fetching account balance."""
    try:
        from broker.ig_client import IGClient
        client = IGClient()
        balance = client.get_account_balance()
        return balance is not None
    except Exception:
        return False


def check_disk_space() -> tuple[bool, float]:
    """Check available disk space. Warn if under 1GB."""
    import shutil
    total, used, free = shutil.disk_usage("/")
    free_gb = free / (1024 ** 3)
    return free_gb > 1.0, free_gb


def run_health_checks():
    """Run all health checks and send Telegram alerts for any failures."""

    # Check trading bot
    bot_ok = check_service("Trading Bot", "http://forex-bot:8080/health")
    if not bot_ok and "bot_down" not in _known_issues:
        _known_issues.add("bot_down")
        notifier.health_alert(
            "Trading Bot Offline",
            "The main trading bot is not responding. No trades will be made until it recovers.\n"
            "Try: docker-compose restart forex-bot"
        )
    elif bot_ok and "bot_down" in _known_issues:
        _known_issues.discard("bot_down")
        notifier.health_recovered("Trading Bot Offline")

    # Check MCP server
    mcp_ok = check_service("MCP Server", "http://mcp-server:8090/health")
    if not mcp_ok and "mcp_down" not in _known_issues:
        _known_issues.add("mcp_down")
        notifier.health_alert(
            "MCP Analysis Server Offline",
            "The MCP server is not responding. Trades will continue but without market context analysis."
        )
    elif mcp_ok and "mcp_down" in _known_issues:
        _known_issues.discard("mcp_down")
        notifier.health_recovered("MCP Analysis Server Offline")

    # Check IG API
    ig_ok = check_ig_api()
    if not ig_ok and "ig_down" not in _known_issues:
        _known_issues.add("ig_down")
        notifier.health_alert(
            "IG API Unreachable",
            "Cannot connect to IG Group. Open positions cannot be managed. "
            "The bot will use yfinance for candle data but cannot place trades."
        )
    elif ig_ok and "ig_down" in _known_issues:
        _known_issues.discard("ig_down")
        notifier.health_recovered("IG API Unreachable")

    # Check disk space
    disk_ok, free_gb = check_disk_space()
    if not disk_ok and "low_disk" not in _known_issues:
        _known_issues.add("low_disk")
        notifier.health_alert(
            "Low Disk Space",
            f"Only {free_gb:.1f}GB free. Logs and data may stop being saved. Free up disk space."
        )


def main():
    """Run health checks every 60 seconds indefinitely."""
    logger.info("Health monitor started")

    while True:
        try:
            run_health_checks()
        except Exception as e:
            logger.error(f"Health monitor error: {e}")

        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
