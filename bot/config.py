"""
bot/config.py — Configuration Loader
─────────────────────────────────────
Loads settings from config.yaml and environment variables.
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


# ── IG Group Broker ───────────────────────────────────────────────────────────

IG_API_KEY     = os.getenv("IG_API_KEY", "")
IG_USERNAME    = os.getenv("IG_USERNAME", "")
IG_PASSWORD    = os.getenv("IG_PASSWORD", "")
IG_ACCOUNT_ID  = os.getenv("IG_ACCOUNT_ID", "")
IG_ENVIRONMENT = os.getenv("IG_ENVIRONMENT", "demo")

# Base URL switches automatically based on environment
IG_BASE_URL = (
    "https://demo-api.ig.com/gateway/deal"
    if IG_ENVIRONMENT == "demo"
    else "https://api.ig.com/gateway/deal"
)

# ── Telegram ──────────────────────────────────────────────────────────────────

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

# ── Claude AI ─────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# ── Capital & Risk ────────────────────────────────────────────────────────────

MAX_CAPITAL               = float(os.getenv("MAX_CAPITAL", 500))
MIN_CONFIDENCE_SCORE      = float(os.getenv("MIN_CONFIDENCE_SCORE", 60))
HOLD_OVERNIGHT_THRESHOLD  = float(os.getenv("HOLD_OVERNIGHT_THRESHOLD", 98))

# ── Trading Parameters (from config.yaml) ────────────────────────────────────

PAIRS                = _cfg["trading"]["pairs"]
SCAN_INTERVAL_MINUTES = _cfg["trading"]["scan_interval_minutes"]
MAX_OPEN_POSITIONS   = _cfg["trading"]["max_open_positions"]
PER_TRADE_RISK_PCT   = _cfg["trading"]["per_trade_risk_pct"]
TIMEFRAME            = _cfg["trading"]["timeframe"]
LOOKBACK_CANDLES     = _cfg["trading"]["lookback_candles"]

# ── Confidence Score Weights ──────────────────────────────────────────────────

CONFIDENCE_WEIGHTS = _cfg["confidence"]["weights"]

# ── Risk Management ───────────────────────────────────────────────────────────

STOP_LOSS_ATR_MULTIPLIER         = _cfg["risk"]["stop_loss_atr_multiplier"]
TAKE_PROFIT_RATIO                = _cfg["risk"]["take_profit_ratio"]
DAILY_LOSS_CIRCUIT_BREAKER_PCT   = _cfg["risk"]["daily_loss_circuit_breaker_pct"]
OVERNIGHT_PROFIT_PROTECTION_PCT  = _cfg["risk"]["overnight_profit_protection_pct"]

# ── Schedule ──────────────────────────────────────────────────────────────────

EOD_CLOSE_TIME       = _cfg["schedule"]["eod_close_time"]
EOD_EVALUATION_TIME  = _cfg["schedule"]["eod_evaluation_time"]
DAILY_REPORT_TIME    = _cfg["schedule"]["daily_report_time"]
WEEKLY_REPORT_DAY    = _cfg["schedule"]["weekly_report_day"]
WEEKLY_REPORT_TIME   = _cfg["schedule"]["weekly_report_time"]
WEEKLY_ANALYSIS_DAY  = _cfg["schedule"]["weekly_analysis_day"]
WEEKLY_ANALYSIS_TIME = _cfg["schedule"]["weekly_analysis_time"]

# ── MCP Analysis ─────────────────────────────────────────────────────────────

MCP_CONFIG   = _cfg["mcp"]
CLAUDE_MODEL = _cfg["mcp"]["claude_model"]

# ── LSTM Model ──────────────────────────────────────────────────────────────

_lstm_cfg = _cfg.get("lstm", {})
LSTM_ENABLED                = _lstm_cfg.get("enabled", True)
LSTM_RETRAIN_INTERVAL_MIN   = _lstm_cfg.get("retrain_interval_minutes", 240)
LSTM_SHADOW_MODE            = _lstm_cfg.get("shadow_mode", True)
LSTM_EPOCHS                 = _lstm_cfg.get("epochs", 50)
LSTM_BATCH_SIZE             = _lstm_cfg.get("batch_size", 64)
LSTM_LEARNING_RATE          = _lstm_cfg.get("learning_rate", 0.001)
LSTM_PATIENCE               = _lstm_cfg.get("patience", 7)

# ── Data Storage ─────────────────────────────────────────────────────────────

DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data_store"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

INITIAL_HISTORY_DAYS = _cfg["data"]["initial_history_days"]

# ── Notifications ─────────────────────────────────────────────────────────────

TIMEZONE                  = _cfg["notifications"]["timezone"]
SHOW_CONFIDENCE_BREAKDOWN = _cfg["notifications"]["show_confidence_breakdown"]
SHOW_AI_REASONING         = _cfg["notifications"]["show_ai_reasoning"]

# ── Instance / Multi-Bot Settings ────────────────────────────────────────────

_instance_cfg = _cfg.get("instance", {})

INSTANCE_ID                 = _instance_cfg.get("instance_id", "primary")
INSTANCE_ACTIVE             = _instance_cfg.get("active", True)
COORDINATION_MODE           = _instance_cfg.get("coordination_mode", "independent")
FAILOVER_TIMEOUT_SECONDS    = _instance_cfg.get("failover_timeout_seconds", 120)
HEARTBEAT_INTERVAL_SECONDS  = _instance_cfg.get("heartbeat_interval_seconds", 30)


def validate():
    """
    Check all required environment variables are set.
    Called at startup — the bot won't run if anything is missing.
    """
    required = {
        "IG_API_KEY":          IG_API_KEY,
        "IG_USERNAME":         IG_USERNAME,
        "IG_PASSWORD":         IG_PASSWORD,
        "IG_ACCOUNT_ID":       IG_ACCOUNT_ID,
        "TELEGRAM_BOT_TOKEN":  TELEGRAM_BOT_TOKEN,
        "TELEGRAM_CHAT_ID":    TELEGRAM_CHAT_ID,
        "ANTHROPIC_API_KEY":   ANTHROPIC_API_KEY,
    }

    missing = [k for k, v in required.items() if not v]

    if missing:
        logger.error(f"Missing required environment variables: {missing}")
        logger.error("Please set these in your GitHub Secrets")
        raise EnvironmentError(f"Missing config: {missing}")

    logger.info("✅ All configuration loaded successfully")
    logger.info(f"   Environment: {IG_ENVIRONMENT.upper()}")
    logger.info(f"   Account: {IG_ACCOUNT_ID}")
    logger.info(f"   Max capital: £{MAX_CAPITAL}")
    logger.info(f"   Pairs to trade: {', '.join(PAIRS)}")
    logger.info(f"   Min confidence to trade: {MIN_CONFIDENCE_SCORE}%")