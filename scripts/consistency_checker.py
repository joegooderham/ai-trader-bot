"""
scripts/consistency_checker.py — Data Consistency Checker
──────────────────────────────────────────────────────────
Reconciles SQLite trade records against the live IG broker state.

Problem this solves:
  The dashboard reads from SQLite, but Telegram notifications come from
  live broker responses. If a close event fails to persist (crash, timeout,
  missed streaming event), the DB and broker diverge — the dashboard shows
  stale "open" positions that IG has already closed.

What it does:
  1. Gets all trades marked "open" in SQLite (closed_at IS NULL)
  2. Gets all actually-open positions from IG API
  3. Any trade in SQLite that IG says is closed → repairs the DB record
  4. Any position on IG that SQLite doesn't know about → logs a warning
  5. Sends a Telegram alert via the system bot when inconsistencies are found

Run standalone:  python -m scripts.consistency_checker
Or called from:  health_monitor.py (every 5 minutes)
"""

from datetime import datetime, timezone
from loguru import logger

from data.storage import TradeStorage
from broker.ig_client import IGClient
from notifications.telegram_bot import TelegramNotifier


def run_consistency_check(
    storage: TradeStorage = None,
    broker: IGClient = None,
    notifier: TelegramNotifier = None,
    auto_repair: bool = True,
) -> dict:
    """
    Compare SQLite open trades against IG broker state and report/repair gaps.

    Returns a summary dict with counts:
      - db_open: trades SQLite thinks are open
      - broker_open: positions actually open on IG
      - repaired: trades that were stale in DB and got fixed
      - orphaned: positions on IG with no matching DB record
      - clean: True if everything matched
    """
    storage = storage or TradeStorage()
    broker = broker or IGClient()
    notifier = notifier or TelegramNotifier()

    summary = {
        "db_open": 0,
        "broker_open": 0,
        "repaired": 0,
        "orphaned": 0,
        "repair_details": [],
        "orphan_details": [],
        "clean": True,
    }

    try:
        # 1. Get what the DB thinks is open
        db_open_trades = storage.get_open_trades_from_db()
        summary["db_open"] = len(db_open_trades)

        # 2. Get what IG actually has open
        try:
            broker_positions = broker.get_open_trades()
        except Exception as e:
            logger.error(f"Consistency check: cannot reach IG API — {e}")
            return summary
        summary["broker_open"] = len(broker_positions)

        # Build a set of deal IDs that are actually open on IG
        broker_deal_ids = set()
        for pos in broker_positions:
            did = pos.get("dealId")
            if did:
                broker_deal_ids.add(did)

        # 3. Find stale DB records: DB says open, but IG says closed
        for trade in db_open_trades:
            deal_id = trade.get("deal_id") or trade.get("trade_id")
            pair = trade.get("pair", "Unknown")

            if deal_id and deal_id not in broker_deal_ids:
                # This position is no longer open on IG but DB still shows it as open
                logger.warning(
                    f"Consistency: {pair} deal={deal_id} is closed on IG but open in DB"
                )
                summary["clean"] = False

                if auto_repair:
                    # We don't have the exact close price/PL from this check,
                    # but marking it closed with a flag is better than leaving it stale
                    storage.update_trade(deal_id, {
                        "closed_at": datetime.now(timezone.utc).isoformat(),
                        "close_reason": "Auto-repaired: closed on broker but DB was stale",
                        "status": "CLOSED",
                    })
                    summary["repaired"] += 1
                    summary["repair_details"].append(f"{pair} ({deal_id})")
                    logger.info(f"Consistency: repaired stale trade {pair} deal={deal_id}")

        # 4. Find orphaned broker positions: open on IG but no DB record
        db_deal_ids = set()
        for trade in db_open_trades:
            did = trade.get("deal_id") or trade.get("trade_id")
            if did:
                db_deal_ids.add(did)

        for pos in broker_positions:
            did = pos.get("dealId")
            if did and did not in db_deal_ids:
                pair = pos.get("instrument") or pos.get("pair", "Unknown")
                logger.warning(
                    f"Consistency: {pair} deal={did} is open on IG but has no DB record"
                )
                summary["clean"] = False
                summary["orphaned"] += 1
                summary["orphan_details"].append(f"{pair} ({did})")

        # 5. Send Telegram alert if anything was wrong
        if not summary["clean"]:
            _send_consistency_alert(notifier, summary)
        else:
            logger.debug(
                f"Consistency check passed: {summary['db_open']} DB open, "
                f"{summary['broker_open']} broker open — all match"
            )

    except Exception as e:
        logger.error(f"Consistency check failed: {e}")

    return summary


def _send_consistency_alert(notifier: TelegramNotifier, summary: dict):
    """Format and send a Telegram alert about data inconsistencies found."""
    lines = [
        "⚠️ *Data Consistency Alert*",
        "─────────────────────",
    ]

    if summary["repaired"] > 0:
        lines.append(f"🔧 *Repaired:* {summary['repaired']} stale trade(s)")
        for detail in summary["repair_details"]:
            lines.append(f"   • {detail}")
        lines.append("")

    if summary["orphaned"] > 0:
        lines.append(f"❓ *Orphaned:* {summary['orphaned']} broker position(s) with no DB record")
        for detail in summary["orphan_details"]:
            lines.append(f"   • {detail}")
        lines.append("")

    lines.append(
        f"📊 DB open: {summary['db_open']} | Broker open: {summary['broker_open']}"
    )

    # Use health_alert which goes to system bot
    notifier.health_alert(
        "Data Consistency Issue",
        "\n".join(lines),
    )


def main():
    """Run a one-off consistency check (for manual testing or cron)."""
    logger.info("Running data consistency check...")
    result = run_consistency_check()
    logger.info(f"Consistency check result: {result}")


if __name__ == "__main__":
    main()
