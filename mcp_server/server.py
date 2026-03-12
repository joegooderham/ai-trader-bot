"""
mcp_server/server.py — MCP Analysis Server
────────────────────────────────────────────
This is the "research desk" that the trading bot consults before making decisions.

It runs as a separate service (its own Docker container) and exposes a simple
HTTP API. The trading bot calls it with questions like:
  "What's the market context for EUR/USD right now?"

The MCP server responds with structured data:
  - Economic calendar events in the next few hours
  - News sentiment for the currency pair
  - Correlation with other pairs the bot might be holding
  - Current volatility regime
  - Historical performance stats for this pair at this time of day

It also uses Claude AI to write a plain-English market summary that
gets included in your daily and weekly Telegram reports.
"""

from fastapi import FastAPI
from fastapi.responses import JSONResponse
import asyncio
from datetime import datetime, timezone
from loguru import logger
import json
from pathlib import Path

from mcp_server import economic_calendar, sentiment, correlations, volatility, session_stats
from bot import config

app = FastAPI(title="AI Trader MCP Server", version="1.0.0")

# In-memory cache to avoid re-fetching data on every single request
_cache = {}
CACHE_DURATION_SECONDS = config.MCP_CONFIG.get("cache_duration_minutes", 30) * 60


@app.get("/health")
async def health_check():
    """Simple health endpoint — used by Docker to verify the server is running."""
    return {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/test-fallback")
async def test_yfinance_fallback():
    """
    Test endpoint to verify yfinance fallback is working.

    Fetches 10 candles from yfinance for all configured pairs and returns
    the results. Use this to confirm the fallback data source is healthy
    without needing to wait for an actual IG failure.

    Example: GET /test-fallback
    """
    import yfinance as yf
    from broker.ig_client import YFINANCE_TICKERS, YFINANCE_INTERVALS

    results = {}
    granularity = config.TIMEFRAME if hasattr(config, "TIMEFRAME") else "H1"
    interval = YFINANCE_INTERVALS.get(granularity, "1h")

    for pair in config.PAIRS:
        ticker_symbol = YFINANCE_TICKERS.get(pair)
        if not ticker_symbol:
            results[pair] = {"status": "error", "reason": "no ticker mapping"}
            continue

        try:
            ticker = yf.Ticker(ticker_symbol)
            df = ticker.history(period="5d", interval=interval)

            if df is None or df.empty:
                results[pair] = {"status": "error", "reason": "no data returned"}
            else:
                # Return the last 3 candles as a sample
                sample = df.tail(3)
                results[pair] = {
                    "status": "ok",
                    "ticker": ticker_symbol,
                    "candles_available": len(df),
                    "latest_close": round(float(sample.iloc[-1]["Close"]), 5),
                    "latest_time": str(sample.index[-1]),
                }
        except Exception as e:
            results[pair] = {"status": "error", "reason": str(e)}

    all_ok = all(r.get("status") == "ok" for r in results.values())

    return JSONResponse(content={
        "test": "yfinance_fallback",
        "overall_status": "healthy" if all_ok else "degraded",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pairs": results,
    })


@app.get("/context/{pair}")
async def get_market_context(pair: str):
    """
    Get full market context for a currency pair.

    This is called by the trading bot before every trade decision.
    Returns all analysis data in one response.

    Example: GET /context/EUR_USD
    """
    cache_key = f"context_{pair}"

    # Return cached result if it's fresh enough
    if cache_key in _cache:
        cached_at, cached_data = _cache[cache_key]
        age = (datetime.now(timezone.utc) - cached_at).total_seconds()
        if age < CACHE_DURATION_SECONDS:
            logger.debug(f"Returning cached context for {pair} (age: {age:.0f}s)")
            return cached_data

    logger.info(f"Fetching fresh market context for {pair}")

    # Run all analysis modules concurrently for speed
    results = await asyncio.gather(
        economic_calendar.get_upcoming_events(pair),
        sentiment.get_sentiment(pair),
        correlations.get_correlation_warning(pair),
        volatility.get_volatility_regime(pair),
        session_stats.get_session_performance(pair),
        return_exceptions=True  # Don't fail if one module errors
    )

    # Safely unpack results (handle any module errors gracefully)
    def safe_result(r, default):
        return r if not isinstance(r, Exception) else default

    context = {
        "pair": pair,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "upcoming_high_impact_events": safe_result(results[0], []),
        "sentiment": safe_result(results[1], {pair: "neutral"}),
        "correlation_warning": safe_result(results[2], {}),
        "volatility_regime": safe_result(results[3], "normal"),
        "session_performance": safe_result(results[4], {pair: 50}),
    }

    # Cache the result
    _cache[cache_key] = (datetime.now(timezone.utc), context)

    return JSONResponse(content=context)


@app.get("/weekly-outlook")
async def get_weekly_outlook():
    """
    Generate a full weekly market outlook using Claude AI.

    Called every Sunday evening at 19:00 UTC.
    This gives the bot a strategic view of the week ahead.
    The output is also sent to you via Telegram as your weekly brief.
    """
    logger.info("Generating weekly market outlook with Claude AI")

    # Gather all available context for the weekly analysis
    economic_events = await economic_calendar.get_week_events()
    sentiment_data = {}
    for pair in config.PAIRS:
        sentiment_data[pair] = await sentiment.get_sentiment(pair)

    # Ask Claude AI for intelligent analysis
    claude_analysis = await _ask_claude_for_weekly_outlook(economic_events, sentiment_data)

    return JSONResponse(content={
        "week_starting": _next_monday(),
        "economic_events": economic_events,
        "pair_sentiment": sentiment_data,
        "claude_analysis": claude_analysis,
        "generated_at": datetime.now(timezone.utc).isoformat()
    })


@app.get("/daily-learning")
async def get_daily_learning(date: str = None):
    """
    After the last trade closes each day, the bot calls this endpoint.
    It returns a summary of what market conditions were like today,
    which gets included in the nightly Telegram report.

    Over time, this builds a rich history the AI can learn from.
    """
    data_dir = Path(config.DATA_DIR)
    trades_file = data_dir / "trades.json"

    if not trades_file.exists():
        return {"message": "No trade data yet"}

    with open(trades_file) as f:
        all_trades = json.load(f)

    today = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_trades = [t for t in all_trades if t.get("opened_at", "").startswith(today)]

    if not today_trades:
        return {"date": today, "message": "No trades today"}

    # What worked today? What didn't?
    wins = [t for t in today_trades if t.get("pl", 0) > 0]
    losses = [t for t in today_trades if t.get("pl", 0) < 0]

    learning = {
        "date": today,
        "total_trades": len(today_trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(today_trades) * 100, 1) if today_trades else 0,
        "total_pl": round(sum(t.get("pl", 0) for t in today_trades), 2),
        "best_trade": max(today_trades, key=lambda t: t.get("pl", 0), default={}),
        "worst_trade": min(today_trades, key=lambda t: t.get("pl", 0), default={}),
        "pairs_traded": list(set(t.get("pair") for t in today_trades)),
    }

    return JSONResponse(content=learning)


async def _ask_claude_for_weekly_outlook(economic_events: list, sentiment_data: dict) -> str:
    """
    Use Claude AI to write an intelligent weekly market outlook.

    This is where Claude's reasoning ability adds real value — it can
    connect dots between events, sentiment, and price expectations in a way
    that simple rule-based code cannot.
    """
    import anthropic

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    prompt = f"""You are a professional Forex market analyst. 
    
Here is the market data for the coming week:

ECONOMIC EVENTS:
{json.dumps(economic_events, indent=2)}

CURRENT SENTIMENT BY PAIR:
{json.dumps(sentiment_data, indent=2)}

PAIRS BEING TRADED: {', '.join(config.PAIRS)}

Please provide:
1. A brief overview of the key themes for the week (2-3 sentences)
2. For each pair, one sentence on what to watch for
3. Any specific risks or opportunities that stand out
4. An overall market mood summary (cautious / neutral / opportunistic)

Keep it concise and practical. This will be sent as a Telegram message."""

    try:
        message = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=config.MCP_CONFIG.get("max_analysis_tokens", 1000),
            messages=[{"role": "user", "content": prompt}]
        )
        return message.content[0].text

    except Exception as e:
        logger.error(f"Claude API error during weekly outlook: {e}")
        return "Weekly outlook unavailable — Claude API error. Check logs for details."


def _next_monday() -> str:
    """Returns the date of next Monday as a string."""
    from datetime import timedelta
    today = datetime.now(timezone.utc).date()
    days_until_monday = (7 - today.weekday()) % 7 or 7
    return (today + timedelta(days=days_until_monday)).isoformat()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8090)
