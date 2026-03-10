"""
mcp_server/session_stats.py — Trading Session Performance Statistics
──────────────────────────────────────────────────────────────────────
Tracks and analyses how each currency pair performs during each
trading session — and feeds that intelligence back into confidence scoring.

Why this matters:
  Not all hours are equal in Forex. EUR/USD during the London session
  is a completely different beast from EUR/USD at 3am UTC.

  - London session (08:00–17:00 UTC): highest volume, tightest spreads,
    most reliable trends. Best for most pairs.
  - New York session (13:00–22:00 UTC): second most liquid, volatile
    around US data releases.
  - London/NY overlap (13:00–17:00 UTC): BEST time to trade — volume
    from both sessions combines, spreads are lowest.
  - Tokyo session (00:00–09:00 UTC): lower volume, JPY pairs most active.
  - Sydney session (22:00–07:00 UTC): quietest, highest spreads.

The bot builds up a database of which pairs perform well in which sessions
based on ACTUAL trade results. After a month of data, it knows things like:
  "GBP/USD has a 72% win rate during London session but only 41% during Tokyo"
  "USD/JPY is most profitable in the Tokyo/London overlap"

This is the AI learning from real experience — not just theory.

Sessions (UTC):
  sydney:   22:00 – 07:00
  tokyo:    00:00 – 09:00
  london:   08:00 – 17:00
  new_york: 13:00 – 22:00
  overlap:  13:00 – 17:00  (London + NY overlap — best conditions)
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from loguru import logger
from typing import Optional

STATS_FILE = Path("/app/data/session_stats.json")

# Session definitions (UTC hours)
SESSIONS = {
    "sydney":   {"start": 22, "end": 7},    # Wraps midnight
    "tokyo":    {"start": 0,  "end": 9},
    "london":   {"start": 8,  "end": 17},
    "new_york": {"start": 13, "end": 22},
    "overlap":  {"start": 13, "end": 17},   # London + NY overlap
}

# Default performance scores (0-100) used before enough real data is collected
# Based on well-known Forex trading wisdom about session characteristics
DEFAULT_SCORES = {
    "EUR_USD": {
        "sydney":   30,   # Very quiet for EUR/USD
        "tokyo":    35,   # Moderate activity
        "london":   75,   # Very active — European session drives EUR
        "new_york": 70,   # Active during NY open
        "overlap":  85,   # Best conditions
    },
    "GBP_USD": {
        "sydney":   25,
        "tokyo":    30,
        "london":   80,   # London is home to GBP — very active
        "new_york": 65,
        "overlap":  82,
    },
    "USD_JPY": {
        "sydney":   50,
        "tokyo":    75,   # JPY very active in Tokyo session
        "london":   60,
        "new_york": 65,
        "overlap":  70,
    },
    "AUD_USD": {
        "sydney":   65,   # AUD active in Sydney
        "tokyo":    60,
        "london":   50,
        "new_york": 55,
        "overlap":  55,
    },
    "USD_CAD": {
        "sydney":   30,
        "tokyo":    30,
        "london":   55,
        "new_york": 75,   # CAD most active during NY (North American session)
        "overlap":  72,
    },
    "USD_CHF": {
        "sydney":   30,
        "tokyo":    35,
        "london":   70,   # CHF active in European hours
        "new_york": 60,
        "overlap":  75,
    },
}


async def get_session_performance(pair: str) -> dict:
    """
    Get the performance score for a pair in the current trading session.

    Returns:
        {pair: score}  where score is 0-100

    Higher = historically more profitable in the current session.
    Used by confidence.py to adjust confidence scores.
    """
    current_session = _get_current_session()
    score = _get_score_for_session(pair, current_session)

    logger.debug(f"Session performance for {pair} in {current_session} session: {score}/100")

    return {
        pair: score,
        "current_session": current_session,
        "session_description": _session_description(current_session),
    }


async def get_all_session_stats(pair: str) -> dict:
    """
    Get full session performance breakdown for a pair across all sessions.
    Used in analysis reports.

    Returns:
        {
            "pair": "EUR_USD",
            "best_session": "overlap",
            "worst_session": "sydney",
            "sessions": {
                "london": {"score": 78, "trades": 45, "win_rate": 64.4},
                ...
            }
        }
    """
    all_stats = _load_stats()
    pair_data = all_stats.get(pair, {})

    sessions_output = {}
    for session in SESSIONS:
        session_data = pair_data.get(session, {})
        trades = session_data.get("trades", 0)
        wins = session_data.get("wins", 0)

        # Use real data if we have enough, otherwise use defaults
        if trades >= 10:
            score = int(wins / trades * 100)
            win_rate = round(wins / trades * 100, 1)
        else:
            score = DEFAULT_SCORES.get(pair, {}).get(session, 50)
            win_rate = None  # Not enough data yet

        sessions_output[session] = {
            "score": score,
            "trades": trades,
            "wins": wins,
            "win_rate": win_rate,
            "data_source": "real" if trades >= 10 else "default_estimate",
        }

    best_session = max(sessions_output, key=lambda s: sessions_output[s]["score"])
    worst_session = min(sessions_output, key=lambda s: sessions_output[s]["score"])

    return {
        "pair": pair,
        "best_session": best_session,
        "worst_session": worst_session,
        "sessions": sessions_output,
        "current_session": _get_current_session(),
    }


def record_trade_result(pair: str, direction: str, pl: float, opened_at: str):
    """
    Record a completed trade result against the session it was opened in.
    Called by the main bot when a trade closes — this is how the bot
    learns which sessions are most profitable over time.

    Args:
        pair: e.g. "EUR_USD"
        direction: "BUY" or "SELL"
        pl: Profit or loss amount
        opened_at: ISO datetime string when trade was opened
    """
    try:
        opened_dt = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
        session = _get_session_for_time(opened_dt)

        stats = _load_stats()
        if pair not in stats:
            stats[pair] = {}
        if session not in stats[pair]:
            stats[pair][session] = {"trades": 0, "wins": 0, "losses": 0, "total_pl": 0.0}

        stats[pair][session]["trades"] += 1
        stats[pair][session]["total_pl"] = round(
            stats[pair][session].get("total_pl", 0) + pl, 2
        )

        if pl > 0:
            stats[pair][session]["wins"] += 1
        else:
            stats[pair][session]["losses"] += 1

        _save_stats(stats)
        logger.debug(f"Session stat recorded: {pair} {direction} {session} session P&L: {pl:.2f}")

    except Exception as e:
        logger.warning(f"Failed to record session stat: {e}")


def _get_current_session() -> str:
    """Determine which trading session is currently active."""
    now = datetime.now(timezone.utc)
    return _get_session_for_time(now)


def _get_session_for_time(dt: datetime) -> str:
    """Get the dominant trading session for a given UTC datetime."""
    hour = dt.hour

    # Overlap is most specific — check first
    if 13 <= hour < 17:
        return "overlap"
    elif 8 <= hour < 17:
        return "london"
    elif 13 <= hour < 22:
        return "new_york"
    elif 0 <= hour < 9:
        return "tokyo"
    else:
        return "sydney"


def _get_score_for_session(pair: str, session: str) -> int:
    """
    Get the performance score for a pair/session combination.
    Uses real data if available (10+ trades), otherwise uses defaults.
    """
    stats = _load_stats()
    session_data = stats.get(pair, {}).get(session, {})
    trades = session_data.get("trades", 0)

    if trades >= 10:
        # Enough real data — use it
        wins = session_data.get("wins", 0)
        return int(wins / trades * 100)
    else:
        # Not enough data yet — use expert defaults
        return DEFAULT_SCORES.get(pair, {}).get(session, 50)


def _session_description(session: str) -> str:
    """Human-readable description of a session."""
    descriptions = {
        "sydney":   "Sydney session (22:00–07:00 UTC) — quietest, lowest volume",
        "tokyo":    "Tokyo session (00:00–09:00 UTC) — JPY pairs most active",
        "london":   "London session (08:00–17:00 UTC) — high volume, EUR/GBP pairs peak",
        "new_york": "New York session (13:00–22:00 UTC) — USD pairs most active",
        "overlap":  "London/NY overlap (13:00–17:00 UTC) — BEST conditions, highest volume",
    }
    return descriptions.get(session, "Unknown session")


def _load_stats() -> dict:
    if not STATS_FILE.exists():
        return {}
    try:
        with open(STATS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_stats(stats: dict):
    try:
        STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(STATS_FILE, "w") as f:
            json.dump(stats, f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to save session stats: {e}")
