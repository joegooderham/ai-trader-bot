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
    allow_methods=["GET"],
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
    Uses WAL mode so reads don't block the trading bot's writes."""
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
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

            # Calculate today's P&L
            closed_today = [t for t in trades_today if t["closed_at"] is not None]
            today_pl = sum(t["profit_loss"] for t in closed_today if t["profit_loss"])
            wins = sum(1 for t in closed_today if t["profit_loss"] and t["profit_loss"] > 0)
            losses = sum(1 for t in closed_today if t["profit_loss"] and t["profit_loss"] <= 0)

            # Open positions
            open_positions = db.execute(
                "SELECT * FROM trades WHERE closed_at IS NULL ORDER BY opened_at DESC"
            ).fetchall()

            # All-time stats
            all_closed = db.execute(
                "SELECT COUNT(*) as total, SUM(CASE WHEN profit_loss > 0 THEN 1 ELSE 0 END) as wins, "
                "SUM(profit_loss) as total_pl FROM trades WHERE closed_at IS NOT NULL"
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


# ── API Routes: P&L Chart Data ──────────────────────────────────────────────


@app.get("/api/charts/pl-history")
async def get_pl_history(days: int = 30):
    """Daily P&L for charting."""
    try:
        with get_db() as db:
            rows = db.execute(
                """SELECT date(closed_at) as date,
                          SUM(profit_loss) as daily_pl,
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
