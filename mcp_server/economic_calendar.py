"""
mcp_server/economic_calendar.py — Economic Calendar
──────────────────────────────────────────────────────
Fetches upcoming high-impact economic events from free sources.

Why this matters:
  Economic events (central bank decisions, jobs reports, inflation data)
  cause sudden, large price movements in Forex. A perfectly good technical
  signal can be completely overridden by a surprise news release.

  The bot uses this to REDUCE confidence scores when a high-impact
  event is within 2 hours — protecting against news-driven moves.

Data source: ForexFactory JSON calendar (free, no API key needed)
Fallback: Known recurring event patterns

Impact levels:
  HIGH   — Can move markets 50+ pips (central bank rates, NFP, CPI)
  MEDIUM — Can move markets 20–50 pips (PMI, retail sales, GDP)
  LOW    — Minor effect, usually ignored
"""

import httpx
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from loguru import logger
from typing import Optional

CACHE_FILE = Path("/app/data/calendar_cache.json")
CACHE_DURATION_MINUTES = 60

# Which pairs are affected by each currency's economic events
CURRENCY_TO_PAIRS = {
    "USD": ["EUR_USD", "GBP_USD", "USD_JPY", "USD_CHF", "AUD_USD", "USD_CAD"],
    "EUR": ["EUR_USD"],
    "GBP": ["GBP_USD"],
    "JPY": ["USD_JPY"],
    "AUD": ["AUD_USD"],
    "CAD": ["USD_CAD"],
    "CHF": ["USD_CHF"],
}


async def get_upcoming_events(pair: str, hours_ahead: int = 2) -> list:
    """
    Get high-impact events affecting a currency pair in the next N hours.
    Returns a list of event dicts, empty if none.
    """
    all_events = await _fetch_calendar()
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=hours_ahead)

    upcoming = []
    for event in all_events:
        event_time = _parse_event_time(event.get("datetime"))
        if not event_time:
            continue
        if not (now <= event_time <= cutoff):
            continue

        currency = event.get("currency", "")
        affected_pairs = CURRENCY_TO_PAIRS.get(currency, [])
        if pair in affected_pairs and event.get("impact") in ("HIGH", "MEDIUM"):
            upcoming.append({
                "event": event.get("name", "Unknown"),
                "currency": currency,
                "impact": event.get("impact", "MEDIUM"),
                "time_utc": event_time.strftime("%H:%M UTC"),
                "minutes_away": int((event_time - now).total_seconds() / 60),
                "forecast": event.get("forecast", ""),
                "previous": event.get("previous", ""),
            })

    if upcoming:
        logger.info(f"⚠️  {len(upcoming)} upcoming event(s) affecting {pair}: "
                    f"{[e['event'] for e in upcoming]}")
    return upcoming


async def get_week_events() -> list:
    """All medium/high-impact events for the coming 7 days. Used in weekly reports."""
    all_events = await _fetch_calendar()
    now = datetime.now(timezone.utc)
    week_end = now + timedelta(days=7)

    week = []
    for event in all_events:
        event_time = _parse_event_time(event.get("datetime"))
        if not event_time:
            continue
        if now <= event_time <= week_end and event.get("impact") in ("HIGH", "MEDIUM"):
            currency = event.get("currency", "")
            week.append({
                "event": event.get("name", "Unknown"),
                "currency": currency,
                "impact": event.get("impact", "MEDIUM"),
                "date": event_time.strftime("%A %d %b"),
                "time_utc": event_time.strftime("%H:%M UTC"),
                "affected_pairs": CURRENCY_TO_PAIRS.get(currency, []),
                "forecast": event.get("forecast", ""),
            })

    return sorted(week, key=lambda e: e.get("date", ""))


async def _fetch_calendar() -> list:
    """Fetch calendar with caching. Tries ForexFactory first, then fallback."""
    cached = _load_cache()
    if cached is not None:
        return cached

    # Primary source: ForexFactory JSON (free, widely used, no auth needed)
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
            response = await client.get(
                "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
                headers={"User-Agent": "Mozilla/5.0"}
            )
            response.raise_for_status()
            raw = response.json()

        events = []
        for item in raw:
            impact_map = {"High": "HIGH", "Medium": "MEDIUM", "Low": "LOW"}
            impact = impact_map.get(item.get("impact", "Low"), "LOW")
            if impact == "LOW":
                continue

            events.append({
                "name": item.get("title", "Unknown"),
                "currency": item.get("country", ""),
                "impact": impact,
                "datetime": item.get("date", ""),   # ISO format from FF
                "forecast": item.get("forecast", ""),
                "previous": item.get("previous", ""),
            })

        logger.info(f"Economic calendar: {len(events)} medium/high events loaded from ForexFactory")
        _save_cache(events)
        return events

    except Exception as e:
        logger.warning(f"ForexFactory calendar fetch failed: {e} — using fallback")
        return _fallback_events()


def _fallback_events() -> list:
    """
    Minimal fallback: flag known high-impact windows based on day/time.
    Not perfectly accurate but prevents the bot trading blind during
    typical high-risk windows.
    """
    now = datetime.now(timezone.utc)
    events = []

    # First Friday of month 13:30 UTC → US NFP
    if now.weekday() == 4 and now.day <= 7:
        events.append({
            "name": "US Non-Farm Payrolls (estimated)",
            "currency": "USD",
            "impact": "HIGH",
            "datetime": now.replace(hour=13, minute=30, second=0, microsecond=0).isoformat(),
        })

    # Mid-month Wednesday 13:30 UTC → possible US CPI
    if now.weekday() == 2 and 8 <= now.day <= 15:
        events.append({
            "name": "Potential US CPI Release",
            "currency": "USD",
            "impact": "HIGH",
            "datetime": now.replace(hour=13, minute=30, second=0, microsecond=0).isoformat(),
        })

    return events


def _parse_event_time(dt_str) -> Optional[datetime]:
    """Parse a datetime string into a UTC-aware datetime."""
    if not dt_str:
        return None
    if isinstance(dt_str, datetime):
        return dt_str
    try:
        return datetime.fromisoformat(str(dt_str).replace("Z", "+00:00"))
    except Exception:
        return None


def _load_cache() -> Optional[list]:
    if not CACHE_FILE.exists():
        return None
    try:
        with open(CACHE_FILE) as f:
            data = json.load(f)
        cached_at = datetime.fromisoformat(data["cached_at"])
        age = (datetime.now(timezone.utc) - cached_at).total_seconds() / 60
        if age < CACHE_DURATION_MINUTES:
            return data["events"]
    except Exception:
        pass
    return None


def _save_cache(events: list):
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_FILE, "w") as f:
            json.dump({"cached_at": datetime.now(timezone.utc).isoformat(), "events": events},
                      f, indent=2, default=str)
    except Exception as e:
        logger.warning(f"Calendar cache write failed: {e}")
