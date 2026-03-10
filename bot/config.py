"""
bot/config.py — Configuration Loader
─────────────────────────────────────
Loads settings from config.yaml and environment variables (.env).
All other modules import from here so there's one single source of truth
for every setting in the application.
"""

import os
import yaml
from pathlib import Path
from dotenv import load_dotenv
from loguru import logger

# Load .env file into environment variables
load_dotenv()

# Path to the config file
CONFIG_PATH = Path(__file__).parent.parent / "config" / "config.yaml"


def _load_yaml() -> dict:
    """Load and parse the YAML config file."""
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)


# Load config once at import time
_cfg = _load_yaml()


# ── OANDA Broker ──────────────────────────────────────────────────────────────

OANDA_API_TOKEN = os.getenv("OANDA_API_TOKEN")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")
OANDA_ENVIRONMENT = os.getenv("OANDA_ENVIRONMENT", "practice")  # Default to demo

# ── Telegram ──────────────────────────────────────────────────────────────────

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ── Claude AI ─────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# ── Capital & Risk ────────────────────────────────────────────────────────────

MAX_CAPITAL = float(os.getenv("MAX_CAPITAL", 500))
MIN_CONFIDENCE_SCORE = float(os.getenv("MIN_CONFIDENCE_SCORE", 60))
HOLD_OVERNIGHT_THRESHOLD = float(os.getenv("HOLD_OVERNIGHT_THRESHOLD", 98))

# ── Trading Parameters (from config.yaml) ────────────────────────────────────

PAIRS = _cfg["trading"]["pairs"]
SCAN_INTERVAL_MINUTES = _cfg["trading"]["scan_interval_minutes"]
MAX_OPEN_POSITIONS = _cfg["trading"]["max_open_positions"]
PER_TRADE_RISK_PCT = _cfg["trading"]["per_trade_risk_pct"]
TIMEFRAME = _cfg["trading"]["timeframe"]
LOOKBACK_CANDLES = _cfg["trading"]["lookback_candles"]

# ── Confidence Score Weights ──────────────────────────────────────────────────

CONFIDENCE_WEIGHTS = _cfg["confidence"]["weights"]

# ── Risk Management ───────────────────────────────────────────────────────────

STOP_LOSS_ATR_MULTIPLIER = _cfg["risk"]["stop_loss_atr_multiplier"]
TAKE_PROFIT_RATIO = _cfg["risk"]["take_profit_ratio"]
DAILY_LOSS_CIRCUIT_BREAKER_PCT = _cfg["risk"]["daily_loss_circuit_breaker_pct"]
OVERNIGHT_PROFIT_PROTECTION_PCT = _cfg["risk"]["overnight_profit_protection_pct"]

# ── Schedule ──────────────────────────────────────────────────────────────────

EOD_CLOSE_TIME = _cfg["schedule"]["eod_close_time"]
EOD_EVALUATION_TIME = _cfg["schedule"]["eod_evaluation_time"]
DAILY_REPORT_TIME = _cfg["schedule"]["daily_report_time"]
WEEKLY_REPORT_DAY = _cfg["schedule"]["weekly_report_day"]
WEEKLY_REPORT_TIME = _cfg["schedule"]["weekly_report_time"]
WEEKLY_ANALYSIS_DAY = _cfg["schedule"]["weekly_analysis_day"]
WEEKLY_ANALYSIS_TIME = _cfg["schedule"]["weekly_analysis_time"]

# ── MCP Analysis ─────────────────────────────────────────────────────────────

MCP_CONFIG = _cfg["mcp"]
CLAUDE_MODEL = _cfg["mcp"]["claude_model"]

# ── Data Storage ─────────────────────────────────────────────────────────────

DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

INITIAL_HISTORY_DAYS = _cfg["data"]["initial_history_days"]

# ── Notifications ─────────────────────────────────────────────────────────────

TIMEZONE = _cfg["notifications"]["timezone"]
SHOW_CONFIDENCE_BREAKDOWN = _cfg["notifications"]["show_confidence_breakdown"]
SHOW_AI_REASONING = _cfg["notifications"]["show_ai_reasoning"]



# ── Instance / Multi-Bot Settings ────────────────────────────────────────────
# These come from config.yaml instance section
# Defaults mean single-instance mode — no changes needed for now

_instance_cfg = _cfg.get("instance", {})

INSTANCE_ID = _instance_cfg.get("instance_id", "primary")
INSTANCE_ACTIVE = _instance_cfg.get("active", True)
COORDINATION_MODE = _instance_cfg.get("coordination_mode", "independent")
FAILOVER_TIMEOUT_SECONDS = _instance_cfg.get("failover_timeout_seconds", 120)
HEARTBEAT_INTERVAL_SECONDS = _instance_cfg.get("heartbeat_interval_seconds", 30)

def validate():
    """
    Check all required environment variables are set.
    Called at startup — the bot won't run if anything is missing.
    """
    required = {
        "OANDA_API_TOKEN": OANDA_API_TOKEN,
        "OANDA_ACCOUNT_ID": OANDA_ACCOUNT_ID,
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
        "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
    }

    missing = [k for k, v in required.items() if not v]

    if missing:
        logger.error(f"Missing required environment variables: {missing}")
        logger.error("Please check your .env file against .env.example")
        raise EnvironmentError(f"Missing config: {missing}")

    logger.info("✅ All configuration loaded successfully")
    logger.info(f"   Environment: {OANDA_ENVIRONMENT.upper()}")
    logger.info(f"   Max capital: £{MAX_CAPITAL}")
    logger.info(f"   Pairs to trade: {', '.join(PAIRS)}")
    logger.info(f"   Min confidence to trade: {MIN_CONFIDENCE_SCORE}%")
