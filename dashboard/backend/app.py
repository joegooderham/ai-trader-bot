"""
dashboard/backend/app.py — Dashboard API Server
─────────────────────────────────────────────────
FastAPI backend that serves:
  - REST API for trade data, positions, analytics, wiki, config
  - React static files (built frontend)

Data sources:
  - SQLite database (trade history, candles, predictions, analytics)
  - MCP server (live analytics, market context)
  - GitHub wiki git repo (cloned locally, pulled periodically)

Designed to run in its own container, portable to Oracle Cloud or any host.
The MCP_SERVER_URL env var controls where it finds the MCP server —
works both on the same Docker network and across the internet.
"""

import os
import sqlite3
import subprocess
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
import markdown
import yfinance as yf
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from loguru import logger

# ── Configuration ────────────────────────────────────────────────────────────

# MCP server URL — internal Docker network or remote
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://mcp-server:8090")

# SQLite database path — mounted volume from the trading bot
DB_PATH = os.getenv("DB_PATH", "/app/data_store/trader.db")

# Wiki repo URL and local clone path
WIKI_REPO_URL = os.getenv("WIKI_REPO_URL", "https://github.com/joegooderham/ai-trader-bot.wiki.git")
WIKI_DIR = Path(os.getenv("WIKI_DIR", "/app/wiki"))

# How often to pull wiki updates (seconds)
WIKI_PULL_INTERVAL = int(os.getenv("WIKI_PULL_INTERVAL", "1800"))  # 30 min

# Path to built React frontend static files
STATIC_DIR = Path(os.getenv("STATIC_DIR", "/app/frontend/dist"))

# Bot command API — internal URL for sending trade commands to the bot process
BOT_COMMAND_URL = os.getenv("BOT_COMMAND_URL", "http://forex-bot:8060")
DASHBOARD_CMD_TOKEN = os.getenv("DASHBOARD_CMD_TOKEN", "")

# Anthropic API key for AI chat
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ── App Setup ────────────────────────────────────────────────────────────────

app = FastAPI(
    title="AI Trader Dashboard API",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url=None,
)

# CORS — allow the React dev server during development
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",  # Vite dev server
        "http://localhost:3000",
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ── Database Helper ──────────────────────────────────────────────────────────

# Empty result templates — returned when DB is unavailable so the frontend
# always gets valid JSON instead of a 500 error
EMPTY_OVERVIEW = {
    "today": {"date": "", "trades": 0, "closed": 0, "wins": 0, "losses": 0, "net_pl": 0, "win_rate": 0},
    "open_positions": [],
    "all_time": {"total_trades": 0, "total_wins": 0, "total_pl": 0, "win_rate": 0},
    "system": {},
}


@contextmanager
def get_db():
    """Context manager for read-only SQLite access.
    Uses immutable=1 because the volume is mounted read-only (:ro) —
    SQLite can't create WAL/journal files without directory write access.
    This is safe because the dashboard only reads, never writes."""
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def db_available() -> bool:
    """Quick check if the database file exists and is readable."""
    try:
        with get_db() as db:
            db.execute("SELECT 1").fetchone()
        return True
    except Exception:
        return False


# ── MCP Proxy Helper ────────────────────────────────────────────────────────


async def mcp_get(endpoint: str) -> dict:
    """Fetch data from the MCP server. Returns empty dict on failure."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{MCP_SERVER_URL}{endpoint}")
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.warning(f"MCP request failed ({endpoint}): {e}")
        return {"error": str(e)}


# ── Bot Command Proxy Helper ─────────────────────────────────────────────────


async def bot_cmd(endpoint: str, method: str = "POST", body: dict = None) -> dict:
    """Send a command to the trading bot's command API.
    Returns the JSON response or raises HTTPException on failure."""
    headers = {}
    if DASHBOARD_CMD_TOKEN:
        headers["Authorization"] = f"Bearer {DASHBOARD_CMD_TOKEN}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if method == "GET":
                resp = await client.get(f"{BOT_COMMAND_URL}{endpoint}", headers=headers)
            else:
                resp = await client.post(f"{BOT_COMMAND_URL}{endpoint}", json=body, headers=headers)
            resp.raise_for_status()
            return resp.json()
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Bot command API unreachable")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
    except Exception as e:
        logger.warning(f"Bot command failed ({endpoint}): {e}")
        raise HTTPException(status_code=502, detail=str(e))


# ── Wiki Management ─────────────────────────────────────────────────────────


def clone_or_pull_wiki():
    """Clone the wiki repo if not present, otherwise pull latest."""
    try:
        if (WIKI_DIR / ".git").exists():
            result = subprocess.run(
                ["git", "pull", "--ff-only"],
                cwd=str(WIKI_DIR),
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                logger.debug("Wiki pulled successfully")
            else:
                logger.warning(f"Wiki pull failed: {result.stderr}")
        else:
            WIKI_DIR.parent.mkdir(parents=True, exist_ok=True)
            result = subprocess.run(
                ["git", "clone", WIKI_REPO_URL, str(WIKI_DIR)],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode == 0:
                logger.info("Wiki cloned successfully")
            else:
                logger.warning(f"Wiki clone failed: {result.stderr}")
    except Exception as e:
        logger.warning(f"Wiki sync error: {e}")


def wiki_pull_loop():
    """Background thread that periodically pulls wiki updates."""
    while True:
        time.sleep(WIKI_PULL_INTERVAL)
        clone_or_pull_wiki()


# ── Startup ──────────────────────────────────────────────────────────────────


@app.on_event("startup")
async def startup():
    """Clone wiki and start background pull thread on startup."""
    # Clone wiki in background so startup isn't blocked
    threading.Thread(target=clone_or_pull_wiki, daemon=True).start()

    # Start periodic wiki pull
    threading.Thread(target=wiki_pull_loop, daemon=True).start()

    logger.info(f"Dashboard API started — MCP: {MCP_SERVER_URL}, DB: {DB_PATH}")


# ── API Routes: Overview ────────────────────────────────────────────────────


@app.get("/api/overview")
async def get_overview():
    """Main dashboard overview — account status, today's summary, system health."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Return empty data gracefully if DB is unavailable
    if not db_available():
        result = {**EMPTY_OVERVIEW}
        result["today"]["date"] = today
        result["system"] = await mcp_get("/health")
        result["db_status"] = "unavailable"
        return result

    try:
        with get_db() as db:
            # Today's trades
            trades_today = db.execute(
                "SELECT * FROM trades WHERE date(opened_at) = ? ORDER BY opened_at DESC",
                (today,)
            ).fetchall()

            # Calculate today's P&L — DB column is 'pl' not 'profit_loss'
            closed_today = [t for t in trades_today if t["closed_at"] is not None]
            today_pl = sum(t["pl"] for t in closed_today if t["pl"])
            wins = sum(1 for t in closed_today if t["pl"] and t["pl"] > 0)
            losses = sum(1 for t in closed_today if t["pl"] and t["pl"] <= 0)

            # Open positions
            open_positions = db.execute(
                "SELECT * FROM trades WHERE closed_at IS NULL ORDER BY opened_at DESC"
            ).fetchall()

            # All-time stats
            all_closed = db.execute(
                "SELECT COUNT(*) as total, SUM(CASE WHEN pl > 0 THEN 1 ELSE 0 END) as wins, "
                "SUM(pl) as total_pl FROM trades WHERE closed_at IS NOT NULL"
            ).fetchone()
    except Exception as e:
        logger.error(f"Database error in overview: {e}")
        result = {**EMPTY_OVERVIEW}
        result["today"]["date"] = today
        result["system"] = await mcp_get("/health")
        result["db_status"] = f"error: {e}"
        return result

    # Get system health from MCP
    health = await mcp_get("/health")

    return {
        "today": {
            "date": today,
            "trades": len(trades_today),
            "closed": len(closed_today),
            "wins": wins,
            "losses": losses,
            "net_pl": round(today_pl, 2),
            "win_rate": round(wins / len(closed_today) * 100, 1) if closed_today else 0,
        },
        "open_positions": [dict(p) for p in open_positions],
        "all_time": {
            "total_trades": all_closed["total"] or 0,
            "total_wins": all_closed["wins"] or 0,
            "total_pl": round(all_closed["total_pl"] or 0, 2),
            "win_rate": round((all_closed["wins"] or 0) / all_closed["total"] * 100, 1)
            if all_closed["total"] else 0,
        },
        "system": health,
    }


# ── API Routes: Running P&L ──────────────────────────────────────────────────


@app.get("/api/running-pl")
async def get_running_pl():
    """Running P&L total: realised (closed trades) + unrealised (open positions).
    Polled by the dashboard header to show a live running total."""
    try:
        with get_db() as db:
            # Realised P&L from all closed trades
            realised = db.execute(
                "SELECT COALESCE(SUM(pl), 0) as total FROM trades WHERE closed_at IS NOT NULL"
            ).fetchone()["total"]

            # Unrealised P&L from open positions (estimated from fill_price vs current data)
            # Open trades don't have close_price yet, so unrealised is 0 until
            # the bot updates them with live market prices
            unrealised_row = db.execute(
                "SELECT COALESCE(SUM(pl), 0) as total FROM trades WHERE closed_at IS NULL"
            ).fetchone()
            unrealised = unrealised_row["total"] if unrealised_row else 0

        total = round(realised + unrealised, 2)
        return {
            "total_pl": total,
            "realised": round(realised, 2),
            "unrealised": round(unrealised, 2),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Database error in running-pl: {e}")
        return {"total_pl": 0, "realised": 0, "unrealised": 0, "updated_at": None}


# ── API Routes: Positions ───────────────────────────────────────────────────


@app.get("/api/positions")
async def get_positions():
    """Current open positions with live data."""
    try:
        with get_db() as db:
            positions = db.execute(
                "SELECT * FROM trades WHERE closed_at IS NULL ORDER BY opened_at DESC"
            ).fetchall()
        return {"positions": [dict(p) for p in positions]}
    except Exception as e:
        logger.error(f"Database error in positions: {e}")
        return {"positions": [], "db_status": "unavailable"}


# ── Unrealized P&L ──────────────────────────────────────────────────────────

# Pip-based P&L constants — mirrors risk/position_sizer.py
# Pip value in GBP per pip per 1-lot IG mini contract (10,000 units)
_PIP_VALUE_GBP = {
    "EUR_USD": 0.79, "GBP_USD": 1.00, "USD_JPY": 0.67,
    "AUD_USD": 0.63, "USD_CAD": 0.58, "USD_CHF": 0.79,
    "GBP_JPY": 0.67, "EUR_GBP": 1.00, "EUR_JPY": 0.67, "NZD_USD": 0.63,
}
_PIP_SIZE = {"USD_JPY": 0.01, "GBP_JPY": 0.01, "EUR_JPY": 0.01}
_DEFAULT_PIP_SIZE = 0.0001

# yfinance ticker mapping — same pairs the bot trades
_YF_TICKERS = {
    "EUR_USD": "EURUSD=X", "GBP_USD": "GBPUSD=X", "USD_JPY": "USDJPY=X",
    "AUD_USD": "AUDUSD=X", "USD_CAD": "USDCAD=X", "USD_CHF": "USDCHF=X",
    "GBP_JPY": "GBPJPY=X", "EUR_GBP": "EURGBP=X", "EUR_JPY": "EURJPY=X",
    "NZD_USD": "NZDUSD=X",
}

# Cache current prices for 60 seconds to avoid hammering yfinance
_price_cache: dict = {}
_price_cache_time: float = 0
_PRICE_CACHE_TTL = 60


def _fetch_current_prices(pairs: list[str]) -> dict[str, float]:
    """Fetch current prices for open positions.

    Strategy (in order of preference):
      1. yfinance Ticker.fast_info — one call per pair, reliable
      2. SQLite candle table — last known close from the bot's own data
      3. Give up — return empty (dashboard shows '—' for that pair)

    Uses a 60-second cache so repeated calls don't hammer APIs."""
    global _price_cache, _price_cache_time

    now = time.time()
    if now - _price_cache_time < _PRICE_CACHE_TTL and all(p in _price_cache for p in pairs):
        return _price_cache

    prices = {}

    # Strategy 1: SQLite candle data — the bot saves live IG candles every scan,
    # so this is the freshest and most reliable source we have. No external API needed.
    try:
        with get_db() as db:
            for pair in pairs:
                row = db.execute(
                    "SELECT close FROM candles WHERE pair = ? ORDER BY timestamp DESC LIMIT 1",
                    (pair,)
                ).fetchone()
                if row and row["close"]:
                    prices[pair] = float(row["close"])
    except Exception as e:
        logger.warning(f"SQLite candle price fetch failed: {e}")

    # Strategy 2: yfinance for any pairs missing from SQLite
    missing = [p for p in pairs if p not in prices]
    for pair in missing:
        ticker_symbol = _YF_TICKERS.get(pair)
        if not ticker_symbol:
            continue
        try:
            ticker = yf.Ticker(ticker_symbol)
            price = ticker.fast_info.get("lastPrice") or ticker.fast_info.get("previousClose")
            if price and price > 0:
                prices[pair] = float(price)
        except Exception:
            pass

    if prices:
        _price_cache = prices
        _price_cache_time = now

    return prices


def _calculate_unrealized_pl(positions: list[dict]) -> dict:
    """Calculate unrealized P&L for open positions using pip-based math.
    Same formula as broker/ig_client.py get_open_trades()."""
    pairs = list({p["pair"] for p in positions if p.get("pair")})
    prices = _fetch_current_prices(pairs)

    total_upl = 0.0
    enriched = []

    for pos in positions:
        pair = pos.get("pair", "")
        current_price = prices.get(pair)
        entry_price = pos.get("fill_price")
        direction = pos.get("direction", "")
        size = pos.get("size", 0)

        upl = None
        if current_price and entry_price and size:
            pip_size = _PIP_SIZE.get(pair, _DEFAULT_PIP_SIZE)
            pip_value = _PIP_VALUE_GBP.get(pair, 0.80)

            if direction == "BUY":
                price_diff = current_price - entry_price
            else:
                price_diff = entry_price - current_price

            pips_moved = price_diff / pip_size
            upl = round(pips_moved * pip_value * size, 2)
            total_upl += upl

        enriched.append({
            **pos,
            "current_price": current_price,
            "unrealized_pl": upl,
        })

    return {
        "positions": enriched,
        "total_unrealized_pl": round(total_upl, 2),
        "prices_available": len(prices) > 0,
    }


@app.get("/api/positions/live")
async def get_positions_live():
    """Open positions enriched with current prices and unrealized P&L.
    Uses yfinance for current prices (no IG auth needed)."""
    try:
        with get_db() as db:
            positions = db.execute(
                "SELECT * FROM trades WHERE closed_at IS NULL ORDER BY opened_at DESC"
            ).fetchall()
        pos_list = [dict(p) for p in positions]
        return _calculate_unrealized_pl(pos_list)
    except Exception as e:
        logger.error(f"Error in live positions: {e}")
        return {"positions": [], "total_unrealized_pl": 0, "prices_available": False}


# ── API Routes: Trade History ────────────────────────────────────────────────


@app.get("/api/trades")
async def get_trades(limit: int = 50, offset: int = 0, pair: str = None):
    """Closed trade history, filterable by pair."""
    try:
        with get_db() as db:
            query = "SELECT * FROM trades WHERE closed_at IS NOT NULL"
            params = []

            if pair:
                query += " AND pair = ?"
                params.append(pair)

            query += " ORDER BY closed_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            trades = db.execute(query, params).fetchall()

            # Get total count for pagination
            count_query = "SELECT COUNT(*) as count FROM trades WHERE closed_at IS NOT NULL"
            count_params = []
            if pair:
                count_query += " AND pair = ?"
                count_params.append(pair)

            total = db.execute(count_query, count_params).fetchone()["count"]

        return {
            "trades": [dict(t) for t in trades],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    except Exception as e:
        logger.error(f"Database error in trades: {e}")
        return {"trades": [], "total": 0, "limit": limit, "offset": offset, "db_status": "unavailable"}


# ── API Routes: Analytics ───────────────────────────────────────────────────


@app.get("/api/analytics/summary")
async def get_analytics_summary():
    """Combined analytics overview — proxies to MCP server."""
    return await mcp_get("/analytics/summary")


@app.get("/api/analytics/model")
async def get_analytics_model():
    """LSTM model info — version, params, accuracy."""
    return await mcp_get("/analytics/model")


@app.get("/api/analytics/accuracy")
async def get_analytics_accuracy():
    """Rolling prediction accuracy at 24h/7d/30d."""
    return await mcp_get("/analytics/accuracy")


@app.get("/api/analytics/drift")
async def get_analytics_drift():
    """Drift detection status."""
    return await mcp_get("/analytics/drift")


@app.get("/api/analytics/performance")
async def get_analytics_performance():
    """LSTM performance metrics — edge, agreement, per-pair."""
    return await mcp_get("/analytics/performance")


@app.get("/api/analytics/predictions")
async def get_analytics_predictions():
    """Recent LSTM predictions with outcomes."""
    return await mcp_get("/analytics/predictions")


# ── API Routes: Trading Summary & Next-Day Outlook ─────────────────────────


@app.get("/api/summary")
async def get_summary():
    """High-level trading history summary with performance stats and next-day plan.
    Combines weekly/monthly performance, per-pair breakdown, and the latest
    daily plan generated by Claude AI."""

    if not db_available():
        return {"error": "Database unavailable"}

    try:
        with get_db() as db:
            # ── 7-day performance ────────────────────────────────────────
            week_stats = db.execute("""
                SELECT
                    COUNT(*) as trades,
                    SUM(CASE WHEN pl > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN pl <= 0 THEN 1 ELSE 0 END) as losses,
                    SUM(pl) as net_pl,
                    SUM(CASE WHEN pl > 0 THEN pl ELSE 0 END) as gross_profit,
                    SUM(CASE WHEN pl < 0 THEN ABS(pl) ELSE 0 END) as gross_loss,
                    MAX(pl) as best_trade,
                    MIN(pl) as worst_trade
                FROM trades
                WHERE closed_at IS NOT NULL
                  AND closed_at >= date('now', '-7 days')
            """).fetchone()

            # ── 30-day performance ───────────────────────────────────────
            month_stats = db.execute("""
                SELECT
                    COUNT(*) as trades,
                    SUM(CASE WHEN pl > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(pl) as net_pl
                FROM trades
                WHERE closed_at IS NOT NULL
                  AND closed_at >= date('now', '-30 days')
            """).fetchone()

            # ── Per-pair breakdown (7 days) ──────────────────────────────
            pair_rows = db.execute("""
                SELECT
                    pair,
                    COUNT(*) as trades,
                    SUM(CASE WHEN pl > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(pl) as net_pl
                FROM trades
                WHERE closed_at IS NOT NULL
                  AND closed_at >= date('now', '-7 days')
                GROUP BY pair
                ORDER BY SUM(pl) DESC
            """).fetchall()

            # ── Daily P&L trend (7 days) ─────────────────────────────────
            daily_trend = db.execute("""
                SELECT
                    date(closed_at) as date,
                    SUM(pl) as daily_pl,
                    COUNT(*) as trades
                FROM trades
                WHERE closed_at IS NOT NULL
                  AND closed_at >= date('now', '-7 days')
                GROUP BY date(closed_at)
                ORDER BY date ASC
            """).fetchall()

            # ── All-time stats ───────────────────────────────────────────
            all_time = db.execute("""
                SELECT
                    COUNT(*) as trades,
                    SUM(CASE WHEN pl > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(pl) as net_pl
                FROM trades
                WHERE closed_at IS NOT NULL
            """).fetchone()

            # ── Latest daily plan from Claude AI ─────────────────────────
            plan_row = db.execute(
                "SELECT date, plan_text, created_at FROM daily_plans ORDER BY date DESC LIMIT 1"
            ).fetchone()

    except Exception as e:
        logger.error(f"Database error in summary: {e}")
        return {"error": str(e)}

    # Build per-pair breakdown
    pairs = []
    for row in pair_rows:
        n = row["trades"]
        pairs.append({
            "pair": row["pair"],
            "trades": n,
            "wins": row["wins"] or 0,
            "win_rate": round((row["wins"] or 0) / n * 100, 1) if n else 0,
            "net_pl": round(row["net_pl"] or 0, 2),
        })

    def safe_stats(row):
        """Convert a stats row to a clean dict with safe defaults."""
        n = row["trades"] or 0
        w = row["wins"] or 0
        return {
            "trades": n,
            "wins": w,
            "losses": n - w,
            "win_rate": round(w / n * 100, 1) if n else 0,
            "net_pl": round(row["net_pl"] or 0, 2),
        }

    week = safe_stats(week_stats)
    week["gross_profit"] = round(week_stats["gross_profit"] or 0, 2)
    week["gross_loss"] = round(week_stats["gross_loss"] or 0, 2)
    week["best_trade"] = round(week_stats["best_trade"] or 0, 2)
    week["worst_trade"] = round(week_stats["worst_trade"] or 0, 2)

    return {
        "week": week,
        "month": safe_stats(month_stats),
        "all_time": safe_stats(all_time),
        "pairs": pairs,
        "daily_trend": [
            {"date": r["date"], "pl": round(r["daily_pl"] or 0, 2), "trades": r["trades"]}
            for r in daily_trend
        ],
        "plan": {
            "date": plan_row["date"],
            "text": plan_row["plan_text"],
            "generated_at": plan_row["created_at"],
        } if plan_row else None,
    }


# ── API Routes: P&L Chart Data ──────────────────────────────────────────────


@app.get("/api/charts/pl-history")
async def get_pl_history(days: int = 30):
    """Daily P&L for charting."""
    try:
        with get_db() as db:
            rows = db.execute(
                """SELECT date(closed_at) as date,
                          SUM(pl) as daily_pl,
                          COUNT(*) as trades
                   FROM trades
                   WHERE closed_at IS NOT NULL
                     AND closed_at >= date('now', ?)
                   GROUP BY date(closed_at)
                   ORDER BY date ASC""",
                (f"-{days} days",)
            ).fetchall()

        # Build cumulative P&L
        cumulative = 0
        result = []
        for row in rows:
            cumulative += row["daily_pl"] or 0
            result.append({
                "date": row["date"],
                "daily_pl": round(row["daily_pl"] or 0, 2),
                "cumulative_pl": round(cumulative, 2),
                "trades": row["trades"],
            })

        return {"data": result}
    except Exception as e:
        logger.error(f"Database error in pl-history: {e}")
        return {"data": []}


# ── API Routes: Wiki ────────────────────────────────────────────────────────


@app.get("/api/wiki")
async def list_wiki_pages():
    """List all wiki pages."""
    if not WIKI_DIR.exists():
        return {"pages": [], "error": "Wiki not cloned yet"}

    pages = []
    for f in sorted(WIKI_DIR.glob("*.md")):
        name = f.stem
        # Read first line for title
        try:
            first_line = f.read_text(encoding="utf-8", errors="replace").split("\n")[0]
            title = first_line.lstrip("# ").strip()
        except Exception:
            title = name
        pages.append({"name": name, "title": title})

    return {"pages": pages}


@app.get("/api/wiki/{page_name}")
async def get_wiki_page(page_name: str):
    """Get a single wiki page rendered as HTML."""
    # Sanitise page name — prevent path traversal
    safe_name = page_name.replace("/", "").replace("\\", "").replace("..", "")
    wiki_file = WIKI_DIR / f"{safe_name}.md"

    if not wiki_file.exists():
        raise HTTPException(status_code=404, detail=f"Wiki page '{safe_name}' not found")

    raw_md = wiki_file.read_text(encoding="utf-8", errors="replace")

    # Convert markdown to HTML with tables and fenced code support
    html = markdown.markdown(
        raw_md,
        extensions=["tables", "fenced_code", "toc", "nl2br"],
    )

    return {
        "name": safe_name,
        "markdown": raw_md,
        "html": html,
    }


# ── API Routes: Config (read-only) ──────────────────────────────────────────


@app.get("/api/config")
async def get_config():
    """Read-only view of current trading configuration.
    Reads config.yaml directly — no secrets exposed."""
    import yaml

    config_path = Path(os.getenv("CONFIG_PATH", "/app/config/config.yaml"))
    if not config_path.exists():
        return {"error": "Config file not found"}

    try:
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        return {"config": cfg}
    except Exception as e:
        return {"error": str(e)}


# ── API Routes: Health ───────────────────────────────────────────────────────


@app.get("/api/health")
async def health_check():
    """Dashboard health check — verifies connectivity to MCP and SQLite."""
    status = {"dashboard": "ok", "mcp_server": "unknown", "database": "unknown"}

    # Check MCP
    try:
        mcp_health = await mcp_get("/health")
        status["mcp_server"] = "ok" if "error" not in mcp_health else "degraded"
    except Exception:
        status["mcp_server"] = "unreachable"

    # Check SQLite
    status["database"] = "ok" if db_available() else "unreachable"

    return status


# ── API Routes: Bot Commands (proxied to forex-bot:8060) ─────────────────────
# These endpoints let the dashboard control the trading bot in real-time.
# All POST requests are proxied to the bot's internal command API.

@app.get("/api/cmd/status")
async def cmd_status():
    """Bot status: paused, disabled directions/pairs, config values."""
    return await bot_cmd("/cmd/status", method="GET")

@app.get("/api/cmd/balance")
async def cmd_balance():
    """Account balance from IG broker."""
    return await bot_cmd("/cmd/balance", method="GET")

@app.get("/api/cmd/remediation")
async def cmd_remediation_list():
    """List pending remediation recommendations."""
    return await bot_cmd("/cmd/remediation", method="GET")

@app.post("/api/cmd/pause")
async def cmd_pause():
    return await bot_cmd("/cmd/pause")

@app.post("/api/cmd/resume")
async def cmd_resume():
    return await bot_cmd("/cmd/resume")

@app.post("/api/cmd/close-all")
async def cmd_close_all():
    return await bot_cmd("/cmd/close-all")

@app.post("/api/cmd/close-pair")
async def cmd_close_pair(body: dict):
    return await bot_cmd("/cmd/close-pair", body=body)

@app.post("/api/cmd/close-profitable")
async def cmd_close_profitable():
    return await bot_cmd("/cmd/close-profitable")

@app.post("/api/cmd/close-losing")
async def cmd_close_losing():
    return await bot_cmd("/cmd/close-losing")

@app.post("/api/cmd/close/{deal_id}")
async def cmd_close_single(deal_id: str):
    return await bot_cmd(f"/cmd/close/{deal_id}")

@app.post("/api/cmd/config")
async def cmd_config(body: dict):
    return await bot_cmd("/cmd/config", body=body)

@app.post("/api/cmd/remediation/{action_id}/approve")
async def cmd_remediation_approve(action_id: int):
    return await bot_cmd(f"/cmd/remediation/{action_id}/approve")

@app.post("/api/cmd/remediation/{action_id}/reject")
async def cmd_remediation_reject(action_id: int):
    return await bot_cmd(f"/cmd/remediation/{action_id}/reject")

@app.post("/api/cmd/enable-direction")
async def cmd_enable_direction(body: dict):
    return await bot_cmd("/cmd/enable-direction", body=body)

@app.post("/api/cmd/enable-pair")
async def cmd_enable_pair(body: dict):
    return await bot_cmd("/cmd/enable-pair", body=body)


# ── API Routes: AI Chat ─────────────────────────────────────────────────────
# Messenger-style chat with Claude. Each message gets live trading context
# injected so Claude can answer questions about positions, P&L, strategy.

import uuid

_chat_sessions: dict = {}  # session_id -> list of messages
_MAX_CHAT_HISTORY = 20


@app.post("/api/chat")
async def chat(body: dict):
    """Send a message to Claude with full trading history context.

    Injects comprehensive data from SQLite so Claude can answer questions
    about any day, pair, or time period — not just today.
    """
    message = body.get("message", "").strip()
    session_id = body.get("session_id") or str(uuid.uuid4())

    if not message:
        raise HTTPException(status_code=400, detail="Message is required")
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY not configured")

    # ── Gather comprehensive trading context from SQLite ──────────────
    context_parts = []
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")

    try:
        with get_db() as db:
            # 1. Daily P&L summary for the last 14 days — lets Claude answer
            #    "how was Friday?" or "compare this week to last week"
            daily_rows = db.execute("""
                SELECT DATE(opened_at) as trade_date,
                       COUNT(*) as trades,
                       SUM(CASE WHEN pl > 0.01 THEN 1 ELSE 0 END) as wins,
                       SUM(CASE WHEN pl < -0.01 THEN 1 ELSE 0 END) as losses,
                       ROUND(SUM(pl), 2) as net_pl,
                       ROUND(AVG(pl), 2) as avg_pl
                FROM trades
                WHERE closed_at IS NOT NULL
                  GROUP BY trade_date
                ORDER BY trade_date DESC
            """).fetchall()

            if daily_rows:
                context_parts.append("DAILY P&L HISTORY (all dates):")
                for r in daily_rows:
                    wr = round(r["wins"] / r["trades"] * 100) if r["trades"] > 0 else 0
                    context_parts.append(
                        f"  {r['trade_date']}: {r['trades']} trades, "
                        f"{r['wins']}W/{r['losses']}L ({wr}% win rate), "
                        f"P&L: £{r['net_pl']}, avg: £{r['avg_pl']}"
                    )

            # 2. Per-pair performance (last 14 days)
            pair_rows = db.execute("""
                SELECT pair,
                       COUNT(*) as trades,
                       SUM(CASE WHEN pl > 0.01 THEN 1 ELSE 0 END) as wins,
                       ROUND(SUM(pl), 2) as net_pl
                FROM trades
                WHERE closed_at IS NOT NULL
                  GROUP BY pair
                ORDER BY net_pl DESC
            """).fetchall()

            if pair_rows:
                context_parts.append("\nPER-PAIR PERFORMANCE (all-time):")
                for r in pair_rows:
                    wr = round(r["wins"] / r["trades"] * 100) if r["trades"] > 0 else 0
                    context_parts.append(
                        f"  {r['pair'].replace('_', '/')}: {r['trades']} trades, "
                        f"{wr}% win rate, P&L: £{r['net_pl']}"
                    )

            # 3. Direction performance (last 14 days)
            dir_rows = db.execute("""
                SELECT direction,
                       COUNT(*) as trades,
                       SUM(CASE WHEN pl > 0.01 THEN 1 ELSE 0 END) as wins,
                       ROUND(SUM(pl), 2) as net_pl
                FROM trades
                WHERE closed_at IS NOT NULL
                  GROUP BY direction
            """).fetchall()

            if dir_rows:
                context_parts.append("\nDIRECTION PERFORMANCE (all-time):")
                for r in dir_rows:
                    wr = round(r["wins"] / r["trades"] * 100) if r["trades"] > 0 else 0
                    context_parts.append(
                        f"  {r['direction']}: {r['trades']} trades, "
                        f"{wr}% win rate, P&L: £{r['net_pl']}"
                    )

            # 4. Today's open positions
            open_trades = db.execute(
                "SELECT pair, direction, fill_price, confidence_score, opened_at "
                "FROM trades WHERE closed_at IS NULL ORDER BY opened_at DESC"
            ).fetchall()

            if open_trades:
                context_parts.append(f"\nOPEN POSITIONS ({len(open_trades)}):")
                for t in open_trades:
                    context_parts.append(
                        f"  {t['pair'].replace('_', '/')} {t['direction']} "
                        f"@ {t['fill_price']} ({t['confidence_score']:.0f}% confidence, "
                        f"opened {t['opened_at'][:16]})"
                    )

            # 5. Recent closed trades (last 10) — for "what was the last trade?" questions
            recent = db.execute(
                "SELECT pair, direction, pl, confidence_score, close_reason, "
                "       opened_at, closed_at "
                "FROM trades WHERE closed_at IS NOT NULL "
                "ORDER BY closed_at DESC LIMIT 20"
            ).fetchall()

            if recent:
                context_parts.append("\nLAST 20 CLOSED TRADES:")
                for t in recent:
                    context_parts.append(
                        f"  {t['opened_at'][:16]} {t['pair'].replace('_', '/')} "
                        f"{t['direction']} → £{t['pl']:.2f} "
                        f"({t['close_reason'] or 'unknown'}, {t['confidence_score']:.0f}%)"
                    )

            # 6. All-time summary
            alltime = db.execute("""
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN pl > 0.01 THEN 1 ELSE 0 END) as wins,
                       ROUND(SUM(pl), 2) as total_pl,
                       ROUND(AVG(pl), 2) as avg_pl,
                       MIN(DATE(opened_at)) as first_trade,
                       MAX(DATE(opened_at)) as last_trade
                FROM trades WHERE closed_at IS NOT NULL
            """).fetchone()

            if alltime and alltime["total"] > 0:
                wr = round(alltime["wins"] / alltime["total"] * 100)
                context_parts.append(
                    f"\nALL-TIME: {alltime['total']} trades, {wr}% win rate, "
                    f"total P&L: £{alltime['total_pl']}, avg: £{alltime['avg_pl']}/trade "
                    f"({alltime['first_trade']} to {alltime['last_trade']})"
                )

    except Exception as e:
        context_parts.append(f"(Database query error: {e})")

    # Bot status from command API
    try:
        status = await bot_cmd("/cmd/status", method="GET")
        paused = "PAUSED" if status.get("paused") else "ACTIVE"
        context_parts.append(f"\nBOT STATUS: {paused}")
        if status.get("disabled_directions"):
            context_parts.append(f"DISABLED DIRECTIONS: {', '.join(status['disabled_directions'])}")
        if status.get("disabled_pairs"):
            context_parts.append(f"DISABLED PAIRS: {', '.join(status['disabled_pairs'])}")
        context_parts.append(
            f"CONFIG: min_confidence={status.get('min_confidence', '?')}%, "
            f"risk={status.get('per_trade_risk_pct', '?')}%, "
            f"SL={status.get('stop_loss_atr_multiplier', '?')}x ATR, "
            f"TP={status.get('take_profit_ratio', '?')}:1"
        )
    except Exception:
        context_parts.append("(Bot status unavailable)")

    # LSTM model info from MCP
    try:
        model = await mcp_get("/analytics/model")
        if model and "error" not in model:
            current = model.get("current_model", {})
            if current:
                acc = (current.get("val_accuracy") or 0) * 100
                context_parts.append(
                    f"LSTM MODEL: accuracy {acc:.0f}%, "
                    f"last trained {(current.get('timestamp') or '?')[:16]}"
                )
    except Exception:
        pass

    # Build system prompt with full historical context
    context_text = "\n".join(context_parts) if context_parts else "No trading data available."
    system_prompt = (
        "You are an AI trading assistant for Joseph's forex day trading bot. "
        "You have access to the COMPLETE trading history below — every day's P&L, "
        "per-pair and per-direction performance, recent individual trades, open positions, "
        "and current bot configuration. Use this data to answer questions about "
        "ANY day, pair, time period, or pattern. "
        "Be concise, data-driven, and always cite specific numbers from the data. "
        "If the user asks about a specific date, find it in the daily P&L history. "
        f"Today's date is {today} (UTC). The bot trades forex on IG Group (demo account, £500 capital). "
        "Format amounts as £. Keep responses under 500 words.\n\n"
        f"COMPLETE TRADING DATA:\n{context_text}"
    )

    # Get or create conversation history
    if session_id not in _chat_sessions:
        _chat_sessions[session_id] = []
    history = _chat_sessions[session_id]

    # Add user message
    history.append({"role": "user", "content": message})

    # Trim history to max length
    if len(history) > _MAX_CHAT_HISTORY:
        history = history[-_MAX_CHAT_HISTORY:]
        _chat_sessions[session_id] = history

    # Call Claude
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            system=system_prompt,
            messages=history,
        )
        reply = response.content[0].text

        # Add assistant reply to history
        history.append({"role": "assistant", "content": reply})

        return {"reply": reply, "session_id": session_id}

    except Exception as e:
        logger.error(f"Chat API failed: {e}")
        raise HTTPException(status_code=502, detail=f"AI chat failed: {str(e)[:200]}")


# ── API Routes: Enhanced Analytics ───────────────────────────────────────────

@app.get("/api/analytics/heatmap")
async def analytics_heatmap():
    """Pair x Hour performance heatmap — win rate and P&L by pair and hour of day."""
    try:
        with get_db() as db:
            rows = db.execute("""
                SELECT pair,
                       CAST(strftime('%H', opened_at) AS INTEGER) as hour,
                       COUNT(*) as trades,
                       SUM(CASE WHEN pl > 0.01 THEN 1 ELSE 0 END) as wins,
                       SUM(pl) as net_pl
                FROM trades
                WHERE closed_at IS NOT NULL AND opened_at IS NOT NULL
                GROUP BY pair, hour
                ORDER BY pair, hour
            """).fetchall()

        heatmap = {}
        for r in rows:
            pair = r["pair"]
            hour = r["hour"]
            if pair not in heatmap:
                heatmap[pair] = {}
            heatmap[pair][str(hour)] = {
                "trades": r["trades"],
                "wins": r["wins"],
                "win_rate": round(r["wins"] / r["trades"] * 100, 1) if r["trades"] > 0 else 0,
                "net_pl": round(r["net_pl"] or 0, 2),
            }
        return heatmap
    except Exception as e:
        logger.error(f"Heatmap query failed: {e}")
        return {}


@app.get("/api/analytics/sessions")
async def analytics_sessions():
    """Performance by forex trading session (Sydney, Tokyo, London, New York)."""
    # Session definitions (UTC hours)
    sessions = {
        "Sydney":   (22, 7),   # 22:00 - 07:00 UTC
        "Tokyo":    (0, 9),    # 00:00 - 09:00 UTC
        "London":   (8, 17),   # 08:00 - 17:00 UTC
        "New York": (13, 22),  # 13:00 - 22:00 UTC
    }

    try:
        with get_db() as db:
            rows = db.execute("""
                SELECT pair, direction,
                       CAST(strftime('%H', opened_at) AS INTEGER) as hour,
                       pl, confidence_score
                FROM trades
                WHERE closed_at IS NOT NULL AND opened_at IS NOT NULL
            """).fetchall()

        def in_session(hour, start, end):
            if start < end:
                return start <= hour < end
            else:  # Wraps midnight (e.g., Sydney 22-7)
                return hour >= start or hour < end

        result = {}
        for session_name, (start, end) in sessions.items():
            session_trades = [dict(r) for r in rows if in_session(r["hour"], start, end)]
            # Per-pair breakdown
            pair_stats = {}
            for t in session_trades:
                pair = t["pair"]
                if pair not in pair_stats:
                    pair_stats[pair] = {"trades": 0, "wins": 0, "pl": 0}
                pair_stats[pair]["trades"] += 1
                if (t["pl"] or 0) > 0.01:
                    pair_stats[pair]["wins"] += 1
                pair_stats[pair]["pl"] += t["pl"] or 0

            for stats in pair_stats.values():
                stats["win_rate"] = round(stats["wins"] / stats["trades"] * 100, 1) if stats["trades"] > 0 else 0
                stats["pl"] = round(stats["pl"], 2)

            total_trades = len(session_trades)
            total_wins = sum(1 for t in session_trades if (t["pl"] or 0) > 0.01)
            total_pl = sum(t["pl"] or 0 for t in session_trades)

            result[session_name] = {
                "trades": total_trades,
                "wins": total_wins,
                "win_rate": round(total_wins / total_trades * 100, 1) if total_trades > 0 else 0,
                "pl": round(total_pl, 2),
                "pairs": pair_stats,
            }

        return result
    except Exception as e:
        logger.error(f"Sessions query failed: {e}")
        return {}


@app.get("/api/trades/{trade_id}/detail")
async def trade_detail(trade_id: int):
    """Full trade detail for the trade journal — reasoning, breakdown, context."""
    try:
        with get_db() as db:
            row = db.execute(
                "SELECT * FROM trades WHERE id = ?", (trade_id,)
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Trade not found")

            trade = dict(row)
            # Parse the breakdown JSON if stored
            if trade.get("breakdown"):
                try:
                    import json
                    trade["breakdown"] = json.loads(trade["breakdown"])
                except Exception:
                    pass
            return trade
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Trade detail query failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/charts/pl-intraday")
async def pl_intraday():
    """Hourly P&L data points for today — used by the live intraday chart."""
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with get_db() as db:
            rows = db.execute("""
                SELECT CAST(strftime('%H', closed_at) AS INTEGER) as hour,
                       SUM(pl) as hourly_pl,
                       COUNT(*) as trades
                FROM trades
                WHERE closed_at LIKE ? AND pl IS NOT NULL
                GROUP BY hour
                ORDER BY hour
            """, (f"{today}%",)).fetchall()

        data = []
        cumulative = 0
        for r in rows:
            cumulative += r["hourly_pl"] or 0
            data.append({
                "hour": r["hour"],
                "hourly_pl": round(r["hourly_pl"] or 0, 2),
                "cumulative_pl": round(cumulative, 2),
                "trades": r["trades"],
            })
        return data
    except Exception as e:
        logger.error(f"Intraday P&L query failed: {e}")
        return []


# ── API Routes: What-If Simulator ────────────────────────────────────────────

@app.post("/api/analysis/what-if")
async def what_if_simulation(body: dict):
    """Simulate how historical trades would have performed under different settings.

    Takes a set of hypothetical config values and replays all trades from a
    given period, filtering out trades that wouldn't have passed the new rules.
    Returns actual vs simulated P&L comparison.

    Body:
      days: int (default 7) — how many days to look back
      min_confidence: float (optional) — hypothetical min confidence threshold
      disabled_directions: list[str] (optional) — e.g. ["SELL"]
      disabled_pairs: list[str] (optional) — e.g. ["GBP_JPY"]
      hold_overnight_threshold: float (optional) — hypothetical overnight hold %
    """
    days = body.get("days", 7)
    sim_min_conf = body.get("min_confidence")
    sim_disabled_dirs = set(d.upper() for d in body.get("disabled_directions", []))
    sim_disabled_pairs = set(p.upper().replace("/", "_") for p in body.get("disabled_pairs", []))
    sim_overnight = body.get("hold_overnight_threshold")

    try:
        with get_db() as db:
            rows = db.execute("""
                SELECT id, pair, direction, pl, confidence_score, close_reason,
                       opened_at, closed_at, fill_price, close_price, stop_loss, take_profit
                FROM trades
                WHERE closed_at IS NOT NULL
                  AND opened_at >= date('now', ?)
                ORDER BY opened_at
            """, (f"-{days} days",)).fetchall()

        actual_trades = [dict(r) for r in rows]
        if not actual_trades:
            return {
                "days": days,
                "actual": {"trades": 0, "pl": 0, "wins": 0, "win_rate": 0},
                "simulated": {"trades": 0, "pl": 0, "wins": 0, "win_rate": 0},
                "filtered_out": [],
                "kept": [],
            }

        kept = []
        filtered_out = []

        for t in actual_trades:
            reasons = []

            # Check confidence threshold
            if sim_min_conf is not None and t["confidence_score"] is not None:
                if t["confidence_score"] < sim_min_conf:
                    reasons.append(f"confidence {t['confidence_score']:.0f}% < {sim_min_conf}%")

            # Check disabled directions
            if t["direction"] in sim_disabled_dirs:
                reasons.append(f"{t['direction']} direction disabled")

            # Check disabled pairs
            if t["pair"] in sim_disabled_pairs:
                reasons.append(f"{t['pair'].replace('_', '/')} pair disabled")

            # Check overnight hold threshold — if the trade was closed due to EOD
            # and the new threshold is lower, it might have survived
            close_reason = (t["close_reason"] or "").lower()
            is_eod_close = any(x in close_reason for x in ["eod", "end of day", "force close"])

            if is_eod_close and sim_overnight is not None:
                # If the trade's confidence was above the new overnight threshold
                # AND it was profitable, it would have been held instead of closed
                if t["confidence_score"] and t["confidence_score"] >= sim_overnight and (t["pl"] or 0) > 0:
                    # Mark this trade as "would have been held overnight" —
                    # we can't know the final outcome, so flag it
                    t["_would_hold_overnight"] = True

            if reasons:
                t["_filter_reasons"] = reasons
                filtered_out.append(t)
            else:
                kept.append(t)

        # Calculate actual stats
        actual_pl = sum(t["pl"] or 0 for t in actual_trades)
        actual_wins = sum(1 for t in actual_trades if (t["pl"] or 0) > 0.01)

        # Calculate simulated stats (only kept trades)
        sim_pl = sum(t["pl"] or 0 for t in kept)
        sim_wins = sum(1 for t in kept if (t["pl"] or 0) > 0.01)

        # P&L of filtered-out trades (what we would have avoided)
        filtered_pl = sum(t["pl"] or 0 for t in filtered_out)
        filtered_wins = sum(1 for t in filtered_out if (t["pl"] or 0) > 0.01)
        filtered_losses = sum(1 for t in filtered_out if (t["pl"] or 0) < -0.01)

        # Per-pair breakdown of filtered trades
        filtered_by_pair = {}
        for t in filtered_out:
            pair = t["pair"]
            if pair not in filtered_by_pair:
                filtered_by_pair[pair] = {"trades": 0, "pl": 0}
            filtered_by_pair[pair]["trades"] += 1
            filtered_by_pair[pair]["pl"] += t["pl"] or 0

        # Per-reason breakdown
        reason_counts = {}
        for t in filtered_out:
            for r in t.get("_filter_reasons", []):
                reason_counts[r] = reason_counts.get(r, 0) + 1

        # Format for response
        def fmt_trade(t):
            return {
                "id": t["id"],
                "pair": t["pair"],
                "direction": t["direction"],
                "pl": round(t["pl"] or 0, 2),
                "confidence": t["confidence_score"],
                "close_reason": t["close_reason"],
                "opened_at": t["opened_at"],
                "filter_reasons": t.get("_filter_reasons", []),
                "would_hold_overnight": t.get("_would_hold_overnight", False),
            }

        return {
            "days": days,
            "settings_tested": {
                "min_confidence": sim_min_conf,
                "disabled_directions": sorted(sim_disabled_dirs) if sim_disabled_dirs else None,
                "disabled_pairs": sorted(sim_disabled_pairs) if sim_disabled_pairs else None,
                "hold_overnight_threshold": sim_overnight,
            },
            "actual": {
                "trades": len(actual_trades),
                "pl": round(actual_pl, 2),
                "wins": actual_wins,
                "losses": len(actual_trades) - actual_wins,
                "win_rate": round(actual_wins / len(actual_trades) * 100, 1) if actual_trades else 0,
            },
            "simulated": {
                "trades": len(kept),
                "pl": round(sim_pl, 2),
                "wins": sim_wins,
                "losses": len(kept) - sim_wins,
                "win_rate": round(sim_wins / len(kept) * 100, 1) if kept else 0,
            },
            "improvement": {
                "pl_difference": round(sim_pl - actual_pl, 2),
                "trades_avoided": len(filtered_out),
                "avoided_pl": round(filtered_pl, 2),
                "avoided_wins": filtered_wins,
                "avoided_losses": filtered_losses,
            },
            "filtered_by_pair": {k: {"trades": v["trades"], "pl": round(v["pl"], 2)}
                                 for k, v in sorted(filtered_by_pair.items(), key=lambda x: x[1]["pl"])},
            "filter_reasons": reason_counts,
            "filtered_out": [fmt_trade(t) for t in filtered_out],
            "kept": [fmt_trade(t) for t in kept[:20]],  # First 20 for display
        }

    except Exception as e:
        logger.error(f"What-if simulation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Serve React Frontend ────────────────────────────────────────────────────
# Mount static files LAST so API routes take priority

if STATIC_DIR.exists():
    # Serve static assets (JS, CSS, images) with correct MIME types
    app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Serve React SPA — all non-API routes return index.html
        so React Router can handle client-side routing."""
        file_path = STATIC_DIR / full_path
        if file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(str(STATIC_DIR / "index.html"))
