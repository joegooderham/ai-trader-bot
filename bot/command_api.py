"""
bot/command_api.py — Dashboard Command API
────────────────────────────────────────────
Lightweight FastAPI app running on port 8060 inside the forex-bot container.
Started as a daemon thread alongside the scheduler, giving it direct access
to all bot internals (broker, storage, notifier, integrity_monitor, config).

This is the HTTP bridge that lets the dashboard send commands to the bot:
  - Pause/resume trading
  - Close positions (all, by pair, profitable, losing, individual)
  - Change config at runtime (apply_runtime_config)
  - Approve/reject remediation recommendations
  - Enable/disable directions and pairs

Auth: Bearer token via DASHBOARD_CMD_TOKEN env var. Internal Docker network only.
"""

import os
import uvicorn
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Depends, Header
from pydantic import BaseModel
from typing import Optional
from loguru import logger

from bot import config

# ── Auth ─────────────────────────────────────────────────────────────────────

CMD_TOKEN = os.getenv("DASHBOARD_CMD_TOKEN", "")


def verify_token(authorization: Optional[str] = Header(None)):
    """Verify the dashboard command token. Skip if no token configured."""
    if not CMD_TOKEN:
        return  # No token set — allow all (dev mode)
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    if authorization.split(" ", 1)[1] != CMD_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid token")


# ── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(title="Bot Command API", docs_url="/cmd/docs")

# These are set by start_command_api() from the scheduler's singleton instances
_broker = None
_notifier = None
_storage = None
_integrity_monitor = None


# ── Request Models ───────────────────────────────────────────────────────────

class PairRequest(BaseModel):
    pair: str

class DirectionRequest(BaseModel):
    direction: str

class ConfigRequest(BaseModel):
    key: str
    value: object  # Can be float, int, bool, str


# ── GET Endpoints ────────────────────────────────────────────────────────────

@app.get("/cmd/status")
def get_status(auth=Depends(verify_token)):
    """Current bot status: paused state, disabled directions/pairs, circuit breaker."""
    import bot.scheduler as scheduler
    cb = scheduler._circuit_breaker_until
    cb_active = False
    if cb and datetime.now(timezone.utc) < cb:
        cb_active = True

    return {
        "paused": scheduler._trading_paused,
        "disabled_directions": sorted(config.DISABLED_DIRECTIONS),
        "disabled_pairs": sorted(config.DISABLED_PAIRS),
        "circuit_breaker_active": cb_active,
        "pairs": config.PAIRS,
        "min_confidence": config.MIN_CONFIDENCE_SCORE,
        "per_trade_risk_pct": config.PER_TRADE_RISK_PCT,
        "hold_overnight_threshold": config.HOLD_OVERNIGHT_THRESHOLD,
        "stop_loss_atr_multiplier": config.STOP_LOSS_ATR_MULTIPLIER,
        "trailing_stop_activation_atr": config.TRAILING_STOP_ACTIVATION_ATR,
        "trailing_stop_trail_atr": config.TRAILING_STOP_TRAIL_ATR,
        "take_profit_ratio": config.TAKE_PROFIT_RATIO,
        "lstm_shadow_mode": config.LSTM_SHADOW_MODE,
    }


@app.get("/cmd/balance")
def get_balance(auth=Depends(verify_token)):
    """Account balance and equity from IG broker."""
    try:
        summary = _broker.get_account_summary()
        return summary
    except Exception as e:
        logger.error(f"Command API balance failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/cmd/remediation")
def get_remediation(auth=Depends(verify_token)):
    """List of pending remediation recommendations."""
    actions = []
    for a in _integrity_monitor.pending_actions:
        actions.append({
            "action_id": a.action_id,
            "title": a.title,
            "detail": a.detail,
            "config_key": a.config_key,
            "config_value": a.config_value,
            "action_type": a.action_type,
        })
    return {"pending_actions": actions}


# ── POST Endpoints — Trading Control ─────────────────────────────────────────

@app.post("/cmd/pause")
def pause_trading(auth=Depends(verify_token)):
    """Pause trading — no new trades will be opened."""
    import bot.scheduler as scheduler
    if scheduler._trading_paused:
        return {"ok": True, "message": "Already paused"}
    scheduler._trading_paused = True
    logger.info("Trading PAUSED via dashboard command API")
    return {"ok": True, "message": "Trading paused"}


@app.post("/cmd/resume")
def resume_trading(auth=Depends(verify_token)):
    """Resume trading after a pause."""
    import bot.scheduler as scheduler
    if not scheduler._trading_paused:
        return {"ok": True, "message": "Already running"}
    scheduler._trading_paused = False
    logger.info("Trading RESUMED via dashboard command API")
    return {"ok": True, "message": "Trading resumed"}


@app.post("/cmd/close-all")
def close_all(auth=Depends(verify_token)):
    """Close all open positions."""
    try:
        results = _broker.close_all_positions()
        if not results:
            return {"ok": True, "closed": 0, "message": "No open positions"}

        try:
            balance = _broker.get_account_balance()
        except Exception:
            balance = 0

        total_pl = 0
        for r in results:
            deal_id = r.get("deal_id")
            pl = r.get("pl", 0)
            total_pl += pl
            if deal_id:
                _storage.update_trade(deal_id, {
                    "close_price": r.get("close_price"),
                    "pl": pl,
                    "closed_at": r.get("closed_at", datetime.now(timezone.utc).isoformat()),
                    "close_reason": "Dashboard close all",
                    "status": "CLOSED",
                })
                trade_num = _storage.get_trade_number(deal_id)
                _notifier.trade_closed(
                    pair=r.get("pair", "Unknown"),
                    direction="N/A",
                    close_price=r.get("close_price", 0),
                    pl=pl,
                    reason="Dashboard close all",
                    account_balance=balance,
                    trade_number=trade_num,
                )

        return {"ok": True, "closed": len(results), "total_pl": round(total_pl, 2)}
    except Exception as e:
        logger.error(f"Command API close-all failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/cmd/close-pair")
def close_pair(req: PairRequest, auth=Depends(verify_token)):
    """Close all positions for a specific pair."""
    pair = req.pair.upper().replace("/", "_")
    if "_" not in pair and len(pair) == 6:
        pair = pair[:3] + "_" + pair[3:]

    try:
        open_trades = _broker.get_open_trades()
        matching = [t for t in open_trades if (t.get("pair") or t.get("instrument")) == pair]

        if not matching:
            return {"ok": True, "closed": 0, "message": f"No open positions for {pair}"}

        total_pl = 0
        closed = 0
        try:
            balance = _broker.get_account_balance()
        except Exception:
            balance = 0

        for trade in matching:
            deal_id = trade.get("dealId")
            size = float(trade.get("dealSize", 1))
            direction = trade.get("direction", "BUY")
            result = _broker.close_trade(deal_id, size, direction)
            if result:
                pl = result.get("pl", 0)
                total_pl += pl
                closed += 1
                _storage.update_trade(deal_id, {
                    "close_price": result.get("close_price"),
                    "pl": pl,
                    "closed_at": result.get("closed_at", datetime.now(timezone.utc).isoformat()),
                    "close_reason": f"Dashboard close {pair}",
                    "status": "CLOSED",
                })
                trade_num = _storage.get_trade_number(deal_id)
                _notifier.trade_closed(
                    pair=pair, direction=direction,
                    close_price=result.get("close_price", 0),
                    pl=pl, reason="Dashboard close pair",
                    account_balance=balance, trade_number=trade_num,
                )

        return {"ok": True, "closed": closed, "total_pl": round(total_pl, 2)}
    except Exception as e:
        logger.error(f"Command API close-pair failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/cmd/close-profitable")
def close_profitable(auth=Depends(verify_token)):
    """Close only positions currently in profit."""
    return _close_filtered(lambda t: float(t.get("unrealizedPL", 0)) > 0, "Dashboard close profitable")


@app.post("/cmd/close-losing")
def close_losing(auth=Depends(verify_token)):
    """Close only positions currently at a loss."""
    return _close_filtered(lambda t: float(t.get("unrealizedPL", 0)) < 0, "Dashboard close losing")


def _close_filtered(filter_fn, reason: str):
    """Close positions matching a filter function."""
    try:
        open_trades = _broker.get_open_trades()
        matching = [t for t in open_trades if filter_fn(t)]

        if not matching:
            return {"ok": True, "closed": 0, "message": "No matching positions"}

        total_pl = 0
        closed = 0
        try:
            balance = _broker.get_account_balance()
        except Exception:
            balance = 0

        for trade in matching:
            deal_id = trade.get("dealId")
            size = float(trade.get("dealSize", 1))
            direction = trade.get("direction", "BUY")
            pair = trade.get("pair") or trade.get("instrument", "Unknown")
            result = _broker.close_trade(deal_id, size, direction)
            if result:
                pl = result.get("pl", 0)
                total_pl += pl
                closed += 1
                _storage.update_trade(deal_id, {
                    "close_price": result.get("close_price"),
                    "pl": pl,
                    "closed_at": result.get("closed_at", datetime.now(timezone.utc).isoformat()),
                    "close_reason": reason,
                    "status": "CLOSED",
                })
                trade_num = _storage.get_trade_number(deal_id)
                _notifier.trade_closed(
                    pair=pair, direction=direction,
                    close_price=result.get("close_price", 0),
                    pl=pl, reason=reason,
                    account_balance=balance, trade_number=trade_num,
                )

        return {"ok": True, "closed": closed, "total_pl": round(total_pl, 2)}
    except Exception as e:
        logger.error(f"Command API {reason} failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/cmd/close/{deal_id}")
def close_single(deal_id: str, auth=Depends(verify_token)):
    """Close a specific position by deal ID."""
    try:
        open_trades = _broker.get_open_trades()
        matching = [t for t in open_trades if t.get("dealId") == deal_id]

        if not matching:
            raise HTTPException(status_code=404, detail=f"Position {deal_id} not found or already closed")

        trade = matching[0]
        size = float(trade.get("dealSize", 1))
        direction = trade.get("direction", "BUY")
        pair = trade.get("pair") or trade.get("instrument", "Unknown")

        result = _broker.close_trade(deal_id, size, direction)
        if not result:
            raise HTTPException(status_code=500, detail="Failed to close position")

        pl = result.get("pl", 0)
        _storage.update_trade(deal_id, {
            "close_price": result.get("close_price"),
            "pl": pl,
            "closed_at": result.get("closed_at", datetime.now(timezone.utc).isoformat()),
            "close_reason": "Dashboard close",
            "status": "CLOSED",
        })

        try:
            balance = _broker.get_account_balance()
        except Exception:
            balance = 0

        trade_num = _storage.get_trade_number(deal_id)
        _notifier.trade_closed(
            pair=pair, direction=direction,
            close_price=result.get("close_price", 0),
            pl=pl, reason="Dashboard close",
            account_balance=balance, trade_number=trade_num,
        )

        return {"ok": True, "deal_id": deal_id, "pl": round(pl, 2)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Command API close/{deal_id} failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── POST Endpoints — Config ──────────────────────────────────────────────────

@app.post("/cmd/config")
def update_config(req: ConfigRequest, auth=Depends(verify_token)):
    """Change a config parameter at runtime and persist to YAML."""
    try:
        description = config.apply_runtime_config(req.key, req.value)
        logger.info(f"Config changed via dashboard: {description}")
        return {"ok": True, "change": description}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Command API config failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── POST Endpoints — Remediation ─────────────────────────────────────────────

@app.post("/cmd/remediation/{action_id}/approve")
def approve_action(action_id: int, auth=Depends(verify_token)):
    """Approve a pending remediation recommendation."""
    result = _integrity_monitor.apply_action(action_id)
    if "not found" in result:
        raise HTTPException(status_code=404, detail=result)
    logger.info(f"Remediation #{action_id} approved via dashboard")
    return {"ok": True, "result": result}


@app.post("/cmd/remediation/{action_id}/reject")
def reject_action(action_id: int, auth=Depends(verify_token)):
    """Reject a pending remediation recommendation."""
    action = _integrity_monitor.get_action(action_id)
    if not action:
        raise HTTPException(status_code=404, detail=f"Action #{action_id} not found")
    _integrity_monitor.pending_actions = [
        a for a in _integrity_monitor.pending_actions if a.action_id != action_id
    ]
    logger.info(f"Remediation #{action_id} rejected via dashboard")
    return {"ok": True, "message": f"Action #{action_id} rejected"}


# ── POST Endpoints — Direction/Pair Control ──────────────────────────────────

@app.post("/cmd/enable-direction")
def enable_direction(req: DirectionRequest, auth=Depends(verify_token)):
    """Re-enable a disabled trading direction."""
    direction = req.direction.upper()
    if direction not in ("BUY", "SELL"):
        raise HTTPException(status_code=400, detail="Direction must be BUY or SELL")
    config.DISABLED_DIRECTIONS.discard(direction)
    logger.info(f"Direction {direction} re-enabled via dashboard")
    return {"ok": True, "message": f"{direction} re-enabled"}


@app.post("/cmd/enable-pair")
def enable_pair(req: PairRequest, auth=Depends(verify_token)):
    """Re-enable a disabled trading pair."""
    pair = req.pair.upper().replace("/", "_")
    config.DISABLED_PAIRS.discard(pair)
    if pair not in config.PAIRS:
        config.PAIRS.append(pair)
    logger.info(f"Pair {pair} re-enabled via dashboard")
    return {"ok": True, "message": f"{pair} re-enabled"}


# ── Startup ──────────────────────────────────────────────────────────────────

def start_command_api(broker, notifier, storage, integrity_monitor):
    """Start the command API server. Called from scheduler.main() in a daemon thread.

    Receives references to the bot's singleton instances so all operations
    use the same broker session, storage connection, and notifier.
    """
    global _broker, _notifier, _storage, _integrity_monitor
    _broker = broker
    _notifier = notifier
    _storage = storage
    _integrity_monitor = integrity_monitor

    logger.info("🌐 Starting dashboard command API on port 8060...")
    uvicorn.run(app, host="0.0.0.0", port=8060, log_level="warning")
