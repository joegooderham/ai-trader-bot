"""
mcp_server/sentiment.py — News Sentiment Analysis
───────────────────────────────────────────────────
Analyses current news sentiment for each currency pair.

Why this matters:
  Even when technical indicators say "buy", if all the news is
  negative about a currency, the market may not move your way.
  Sentiment is the market's emotional temperature — it doesn't
  predict direction perfectly but it confirms or contradicts signals.

How it works:
  1. Fetch recent RSS headlines from free financial news sources
  2. Score each headline as bullish, bearish, or neutral using
     keyword analysis and Claude AI
  3. Aggregate into a per-pair sentiment score

Data sources (all free, no API key needed):
  - Reuters RSS (business/markets section)
  - FX Street RSS (dedicated Forex news)
  - Investing.com RSS

Sentiment values returned:
  "bullish"  — Net positive news flow (boosts BUY confidence)
  "bearish"  — Net negative news flow (boosts SELL confidence)
  "neutral"  — Mixed or no strong signal
"""

import feedparser
import httpx
from datetime import datetime, timezone, timedelta
from loguru import logger
from typing import Optional
import json
from pathlib import Path
import re

CACHE_FILE = Path("/app/data/sentiment_cache.json")
CACHE_DURATION_MINUTES = 30  # Refresh sentiment every 30 minutes

# RSS feeds — free, no auth required
NEWS_FEEDS = [
    {
        "name": "FX Street",
        "url": "https://www.fxstreet.com/rss",
        "currencies": ["USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF"],
    },
    {
        "name": "ForexLive",
        "url": "https://www.forexlive.com/feed/news",
        "currencies": ["USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD"],
    },
    {
        "name": "Investing.com Forex",
        "url": "https://www.investing.com/rss/news_285.rss",
        "currencies": ["USD", "EUR", "GBP", "JPY", "AUD", "CAD"],
    },
    {
        "name": "DailyFX",
        "url": "https://www.dailyfx.com/feeds/all",
        "currencies": ["USD", "EUR", "GBP", "JPY", "AUD", "CAD"],
    },
]

# Keywords that indicate bullish sentiment for a currency
BULLISH_KEYWORDS = [
    "strong", "rise", "rises", "rising", "surge", "surges", "rally",
    "gain", "gains", "positive", "optimism", "boost", "boosted",
    "growth", "beat", "beats", "better than expected", "hawkish",
    "rate hike", "interest rate increase", "outperform", "recovery",
    "higher", "upside", "bullish", "strengthen", "strengthens",
]

# Keywords that indicate bearish sentiment for a currency
BEARISH_KEYWORDS = [
    "weak", "fall", "falls", "falling", "drop", "drops", "decline",
    "loss", "losses", "negative", "pessimism", "cut", "cuts",
    "slowdown", "miss", "misses", "worse than expected", "dovish",
    "rate cut", "interest rate decrease", "underperform", "recession",
    "lower", "downside", "bearish", "weaken", "weakens", "concern",
    "uncertainty", "risk", "caution", "selloff",
]

# Currency mention patterns
CURRENCY_PATTERNS = {
    "USD": ["dollar", "usd", "federal reserve", "fed", "us economy", "american", "greenback"],
    "EUR": ["euro", "eur", "ecb", "european central bank", "eurozone", "europe"],
    "GBP": ["pound", "gbp", "sterling", "bank of england", "boe", "britain", "british", "uk economy"],
    "JPY": ["yen", "jpy", "bank of japan", "boj", "japan", "japanese"],
    "AUD": ["aussie", "aud", "rba", "reserve bank of australia", "australia", "australian"],
    "CAD": ["loonie", "cad", "bank of canada", "boc", "canada", "canadian", "oil prices"],
    "CHF": ["franc", "chf", "swiss national bank", "snb", "switzerland", "swiss"],
}

# Map pairs to their two component currencies
PAIR_CURRENCIES = {
    "EUR_USD": ("EUR", "USD"),
    "GBP_USD": ("GBP", "USD"),
    "USD_JPY": ("USD", "JPY"),
    "AUD_USD": ("AUD", "USD"),
    "USD_CAD": ("USD", "CAD"),
    "USD_CHF": ("USD", "CHF"),
    "GBP_JPY": ("GBP", "JPY"),
}


async def get_sentiment(pair: str) -> dict:
    """
    Get current news sentiment for a currency pair.

    Returns a dict like:
        {
            "EUR_USD": "bullish",   # Net sentiment for this pair
            "EUR": {"score": 0.4, "articles": 6},
            "USD": {"score": -0.2, "articles": 8},
            "reasoning": "EUR news mostly positive (ECB hawkish signals)..."
        }
    """
    # Load from cache if fresh
    cached = _load_cache(pair)
    if cached is not None:
        return cached

    # Fetch and score headlines
    headlines = await _fetch_headlines()
    currency_scores = _score_by_currency(headlines)

    # Calculate pair sentiment
    currencies = PAIR_CURRENCIES.get(pair, ("", ""))
    result = _calculate_pair_sentiment(pair, currencies, currency_scores)

    _save_cache(pair, result)
    return result


def _score_by_currency(headlines: list) -> dict:
    """
    Score news sentiment for each currency based on headlines.

    Returns dict of {currency: {"score": float, "articles": int, "samples": list}}
    Score range: -1.0 (very bearish) to +1.0 (very bullish)
    """
    currency_data = {c: {"bullish": 0, "bearish": 0, "articles": 0, "samples": []} 
                     for c in CURRENCY_PATTERNS}

    for headline in headlines:
        text = (headline.get("title", "") + " " + headline.get("summary", "")).lower()

        # Find which currencies this headline mentions
        for currency, patterns in CURRENCY_PATTERNS.items():
            if any(p in text for p in patterns):
                currency_data[currency]["articles"] += 1

                # Count bullish and bearish keywords
                bullish_hits = sum(1 for kw in BULLISH_KEYWORDS if kw in text)
                bearish_hits = sum(1 for kw in BEARISH_KEYWORDS if kw in text)

                if bullish_hits > bearish_hits:
                    currency_data[currency]["bullish"] += 1
                    if len(currency_data[currency]["samples"]) < 3:
                        currency_data[currency]["samples"].append(headline.get("title", ""))
                elif bearish_hits > bullish_hits:
                    currency_data[currency]["bearish"] += 1
                    if len(currency_data[currency]["samples"]) < 3:
                        currency_data[currency]["samples"].append(headline.get("title", ""))

    # Convert to scores
    scores = {}
    for currency, data in currency_data.items():
        total = data["bullish"] + data["bearish"]
        if total > 0:
            score = (data["bullish"] - data["bearish"]) / total
        else:
            score = 0.0

        scores[currency] = {
            "score": round(score, 3),
            "articles": data["articles"],
            "bullish_count": data["bullish"],
            "bearish_count": data["bearish"],
            "sample_headlines": data["samples"],
        }

    return scores


def _calculate_pair_sentiment(pair: str, currencies: tuple, scores: dict) -> dict:
    """
    Combine two currency scores into a pair-level sentiment.

    For EUR/USD:
      - Bullish EUR + Bearish USD = strongly bullish pair
      - Bearish EUR + Bullish USD = strongly bearish pair
      - Both similar = neutral
    """
    base_currency, quote_currency = currencies

    base_score = scores.get(base_currency, {}).get("score", 0)
    quote_score = scores.get(quote_currency, {}).get("score", 0)

    # Net score: positive = bullish for the pair (base strengthening vs quote)
    net_score = base_score - quote_score

    # Classify
    if net_score > 0.2:
        sentiment = "bullish"
        reasoning = (f"{base_currency} news is positive (score: {base_score:+.2f}), "
                     f"{quote_currency} news is negative (score: {quote_score:+.2f}) — "
                     f"supports {pair.replace('_', '/')} moving UP")
    elif net_score < -0.2:
        sentiment = "bearish"
        reasoning = (f"{base_currency} news is negative (score: {base_score:+.2f}), "
                     f"{quote_currency} news is positive (score: {quote_score:+.2f}) — "
                     f"supports {pair.replace('_', '/')} moving DOWN")
    else:
        sentiment = "neutral"
        reasoning = f"Mixed or balanced news flow for {base_currency} and {quote_currency}"

    base_articles = scores.get(base_currency, {}).get("articles", 0)
    quote_articles = scores.get(quote_currency, {}).get("articles", 0)

    # Low article count = low confidence in sentiment
    if base_articles + quote_articles < 3:
        sentiment = "neutral"
        reasoning = "Insufficient news articles to determine sentiment"

    result = {
        pair: sentiment,
        "net_score": round(net_score, 3),
        "base_currency": {
            "currency": base_currency,
            "score": base_score,
            "articles": base_articles,
        },
        "quote_currency": {
            "currency": quote_currency,
            "score": quote_score,
            "articles": quote_articles,
        },
        "reasoning": reasoning,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }

    logger.debug(f"Sentiment for {pair}: {sentiment} (net: {net_score:+.3f})")
    return result


async def _fetch_headlines() -> list:
    """
    Fetch recent headlines from all RSS feeds.
    Returns up to 50 recent headlines (last 6 hours).
    """
    all_headlines = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=6)

    for feed_config in NEWS_FEEDS:
        try:
            async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
                response = await client.get(
                    feed_config["url"],
                    headers={"User-Agent": "Mozilla/5.0 (compatible; ForexBot/1.0)"}
                )
                response.raise_for_status()

            feed = feedparser.parse(response.text)

            for entry in feed.entries[:15]:  # Top 15 per feed
                published = _parse_feed_date(entry.get("published", ""))
                if published and published < cutoff:
                    continue  # Skip old headlines

                all_headlines.append({
                    "title": entry.get("title", ""),
                    "summary": entry.get("summary", ""),
                    "source": feed_config["name"],
                    "published": published.isoformat() if published else "",
                })

            logger.debug(f"Sentiment: fetched {len(feed.entries)} headlines from {feed_config['name']}")

        except Exception as e:
            logger.debug(f"News feed {feed_config['name']} unavailable: {e}")

    logger.info(f"Sentiment analysis: {len(all_headlines)} total headlines collected")
    return all_headlines


def _parse_feed_date(date_str: str) -> Optional[datetime]:
    """Parse RSS date strings into UTC-aware datetime.
    Always returns timezone-aware datetime to avoid comparison errors
    with the UTC cutoff in _fetch_headlines."""
    if not date_str:
        return None
    try:
        import email.utils
        parsed = email.utils.parsedate_to_datetime(date_str)
        # Ensure timezone-aware — some feeds return naive datetimes
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        try:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None


def _load_cache(pair: str) -> Optional[dict]:
    cache_key = f"sentiment_{pair}"
    cache_path = CACHE_FILE.parent / f"{cache_key}.json"
    if not cache_path.exists():
        return None
    try:
        with open(cache_path) as f:
            data = json.load(f)
        cached_at = datetime.fromisoformat(data.get("fetched_at", "2000-01-01T00:00:00+00:00"))
        age = (datetime.now(timezone.utc) - cached_at).total_seconds() / 60
        if age < CACHE_DURATION_MINUTES:
            return data
    except Exception:
        pass
    return None


def _save_cache(pair: str, data: dict):
    cache_key = f"sentiment_{pair}"
    cache_path = CACHE_FILE.parent / f"{cache_key}.json"
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        logger.warning(f"Sentiment cache write failed: {e}")
