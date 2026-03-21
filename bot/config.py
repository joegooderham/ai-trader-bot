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

TELEGRAM_BOT_TOKEN     = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID       = os.getenv("TELEGRAM_CHAT_ID")
# Separate bot for system/ops notifications (health, fallback, startup, drift alerts)
# Falls back to the trading bot token if not set
TELEGRAM_BOT_SYS_TOKEN = os.getenv("TELEGRAM_BOT_SYS_TOKEN", "") or TELEGRAM_BOT_TOKEN

# ── Claude AI ─────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# ── GitHub ───────────────────────────────────────────────────────────────────
# Personal Access Token with repo + workflow scopes — used for triggering deploys
GITHUB_PAT = os.getenv("GITHUB_PAT", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "joegooderham/ai-trader-bot")

# ── Capital & Risk ────────────────────────────────────────────────────────────

# Capital from env var (set in GitHub Secrets)
MAX_CAPITAL               = float(os.getenv("MAX_CAPITAL", 500))

# Confidence and overnight thresholds — read from config.yaml as the single source of truth.
# Previously these read from env vars with hardcoded defaults, which meant YAML changes
# (like setting min_to_trade to 85%) were silently ignored at startup.
MIN_CONFIDENCE_SCORE      = float(_cfg["confidence"]["min_to_trade"])
HOLD_OVERNIGHT_THRESHOLD  = float(_cfg["confidence"]["hold_overnight_threshold"])

# ── Trading Parameters (from config.yaml) ────────────────────────────────────

PAIRS                = _cfg["trading"]["pairs"]
SCAN_INTERVAL_MINUTES = _cfg["trading"]["scan_interval_minutes"]
MAX_OPEN_POSITIONS   = _cfg["trading"]["max_open_positions"]
PER_TRADE_RISK_PCT   = _cfg["trading"]["per_trade_risk_pct"]
MAX_PER_TRADE_SPEND  = _cfg["trading"].get("max_per_trade_spend", 999.0)
TIMEFRAME            = _cfg["trading"]["timeframe"]
LOOKBACK_CANDLES     = _cfg["trading"]["lookback_candles"]
ENABLE_STREAMING             = _cfg["trading"].get("enable_streaming", True)
STREAMING_PL_ALERT_THRESHOLD = _cfg["trading"].get("streaming_pl_alert_threshold", 5.0)
SESSION_CONFIDENCE_BOOST = _cfg["trading"].get("session_confidence_boost", {})
SESSION_JPY_EXEMPT       = _cfg["trading"].get("session_jpy_exempt_from_tokyo", True)
HTF_TIMEFRAME        = _cfg["trading"].get("htf_timeframe", "H4")
HTF_LOOKBACK_CANDLES = _cfg["trading"].get("htf_lookback_candles", 60)
HTF_ALIGNMENT_BONUS  = _cfg["trading"].get("htf_alignment_bonus", 10)
HTF_CONFLICT_PENALTY = _cfg["trading"].get("htf_conflict_penalty", 15)

# ── Confidence Score Weights ──────────────────────────────────────────────────

CONFIDENCE_WEIGHTS = _cfg["confidence"]["weights"]

# ── Risk Management ───────────────────────────────────────────────────────────

STOP_LOSS_ATR_MULTIPLIER         = _cfg["risk"]["stop_loss_atr_multiplier"]
TAKE_PROFIT_RATIO                = _cfg["risk"]["take_profit_ratio"]
DAILY_LOSS_CIRCUIT_BREAKER_PCT   = _cfg["risk"]["daily_loss_circuit_breaker_pct"]
TRAILING_STOP_ACTIVATION_ATR     = _cfg["risk"].get("trailing_stop_activation_atr", 1.5)
TRAILING_STOP_TRAIL_ATR          = _cfg["risk"].get("trailing_stop_trail_atr", 1.0)
CORRELATION_BLOCK_THRESHOLD      = _cfg["risk"].get("correlation_block_threshold", 0.75)
OVERNIGHT_PROFIT_PROTECTION_PCT  = _cfg["risk"]["overnight_profit_protection_pct"]

# ── Confidence-Tiered Risk ──────────────────────────────────────────────────
# Parameters scale with confidence score: low/medium/high tiers
CONFIDENCE_TIERS = _cfg["risk"].get("confidence_tiers", {})

# ── Partial Profit-Taking ───────────────────────────────────────────────────
PARTIAL_TP_ENABLED   = _cfg["risk"].get("partial_tp_enabled", False)
PARTIAL_TP_PCT       = _cfg["risk"].get("partial_tp_pct", 50) / 100  # Convert to decimal
PARTIAL_CLOSE_PCT    = _cfg["risk"].get("partial_close_pct", 50) / 100

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


# ── Runtime Config (mutable at runtime, no restart needed) ───────────────────
# These sets are checked by the scheduler to skip disabled directions/pairs.
# Managed by the integrity monitor's remediation system.

DISABLED_DIRECTIONS: set = set()   # e.g. {"SELL"} blocks all SELL trades
DISABLED_PAIRS: set = set()        # e.g. {"GBP_JPY"} removes pair at runtime

# ── Remediation Thresholds ───────────────────────────────────────────────────

_remediation_cfg = _cfg.get("remediation", {})
AUTOPAUSE_WEEKLY_LOSS_THRESHOLD    = _remediation_cfg.get("autopause_weekly_loss_threshold", -50)
DIRECTION_WINRATE_ALERT_THRESHOLD  = _remediation_cfg.get("direction_winrate_alert_threshold", 30)
LOSING_STREAK_SMART_ANALYSIS_MIN   = _remediation_cfg.get("losing_streak_smart_analysis_min", 5)


def apply_runtime_config(key: str, value) -> str:
    """
    Apply a config change at runtime (immediate effect) and persist to config.yaml.

    Maps flat keys to module-level variables AND their YAML path.
    Returns a description of what was changed.
    """
    import yaml

    # Map of flat key → (module attribute name, yaml section, yaml param)
    key_map = {
        "min_to_trade":                 ("MIN_CONFIDENCE_SCORE",          "confidence", "min_to_trade"),
        "per_trade_risk_pct":           ("PER_TRADE_RISK_PCT",            "trading",    "per_trade_risk_pct"),
        "hold_overnight_threshold":     ("HOLD_OVERNIGHT_THRESHOLD",      "confidence", "hold_overnight_threshold"),
        "trailing_stop_activation_atr": ("TRAILING_STOP_ACTIVATION_ATR",  "risk",       "trailing_stop_activation_atr"),
        "trailing_stop_trail_atr":      ("TRAILING_STOP_TRAIL_ATR",       "risk",       "trailing_stop_trail_atr"),
        "stop_loss_atr_multiplier":     ("STOP_LOSS_ATR_MULTIPLIER",      "risk",       "stop_loss_atr_multiplier"),
        "take_profit_ratio":            ("TAKE_PROFIT_RATIO",             "risk",       "take_profit_ratio"),
        "lstm_shadow_mode":             ("LSTM_SHADOW_MODE",              "lstm",       "shadow_mode"),
    }

    mapping = key_map.get(key)
    if not mapping:
        raise ValueError(f"Unknown runtime config key: {key}")

    attr_name, yaml_section, yaml_param = mapping

    # 1. Apply immediately to the running process
    import bot.config as self_module
    old_value = getattr(self_module, attr_name)
    setattr(self_module, attr_name, value)
    logger.info(f"Runtime config: {attr_name} changed {old_value} → {value}")

    # 2. Persist to config.yaml so restarts keep the change
    try:
        with open(CONFIG_PATH, "r") as f:
            cfg = yaml.safe_load(f)

        if yaml_section in cfg:
            cfg[yaml_section][yaml_param] = value
            with open(CONFIG_PATH, "w") as f:
                yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)
            logger.info(f"Config persisted: {yaml_section}.{yaml_param} = {value}")
        else:
            logger.warning(f"YAML section '{yaml_section}' not found — runtime change only")
    except Exception as e:
        logger.error(f"Failed to persist config change to YAML: {e}")

    return f"{attr_name}: {old_value} → {value}"


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