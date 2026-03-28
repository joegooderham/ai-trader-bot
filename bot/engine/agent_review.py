"""
bot/engine/agent_review.py — Agentic Trade Review Pipeline
───────────���──────────────────────────────────────────────────────────────
Wires the persona instruction files into the trade decision pipeline.
Two Claude calls per trade candidate:

  1. Trade Orchestrator — synthesises all agent data into a final assessment
     with documented reasoning (replaces the opaque confidence score with
     an explained decision).

  2. Trade Critic — adversarial "what could go wrong?" review that can
     downgrade the confidence score before execution.

Only called for pairs that pass the initial confidence threshold, so
typically 1-3 Claude calls per scan cycle, not per pair.

The rule-based confidence score remains the primary gate. The AI review
adds reasoning quality, conflict resolution, and risk awareness on top.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from loguru import logger

import anthropic
from bot import config

# ── Load Persona Instructions ──────────────���─────────────────────────────────

PERSONA_DIR = Path(__file__).resolve().parent.parent.parent / "personas"


def _load_persona(name: str) -> str:
    """Load a persona .md file and return its content as a system prompt.
    Strips the YAML frontmatter (between --- delimiters) since it's metadata
    for humans, not for the model."""
    path = PERSONA_DIR / f"{name}.md"
    if not path.exists():
        logger.warning(f"Persona file not found: {path}")
        return ""
    content = path.read_text(encoding="utf-8")
    # Strip YAML frontmatter
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            content = parts[2].strip()
    return content


# Cache personas at module level — they don't change at runtime
_orchestrator_prompt = None
_critic_prompt = None


def _get_orchestrator_prompt() -> str:
    global _orchestrator_prompt
    if _orchestrator_prompt is None:
        _orchestrator_prompt = _load_persona("trade-orchestrator")
    return _orchestrator_prompt


def _get_critic_prompt() -> str:
    global _critic_prompt
    if _critic_prompt is None:
        _critic_prompt = _load_persona("trade-critic")
    return _critic_prompt


# ── Claude Client ──────────────��─────────────────────────────────────────────

def _get_client() -> anthropic.Anthropic:
    """Get Anthropic client. Returns None if API key not configured."""
    if not config.ANTHROPIC_API_KEY:
        return None
    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


def _call_claude(system_prompt: str, user_prompt: str, max_tokens: int = 800) -> str:
    """Call Claude with a persona system prompt. Returns response text or None."""
    client = _get_client()
    if not client:
        logger.debug("Agent review skipped — no ANTHROPIC_API_KEY")
        return None
    try:
        response = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}]
        )
        return response.content[0].text
    except Exception as e:
        logger.warning(f"Agent review Claude call failed: {e}")
        return None


# ── Trade Orchestrator ──────────���────────────────────────────────────────────

def orchestrator_review(
    pair: str,
    direction: str,
    confidence_score: float,
    breakdown: dict,
    indicators: object,
    ml_prediction: dict = None,
    mcp_context: dict = None,
    open_positions: list = None,
) -> dict:
    """
    Run the Trade Orchestrator persona to synthesise all agent data into
    a reasoned assessment.

    Returns dict with:
      - reasoning: str (plain-English explanation of the trade thesis)
      - adjusted_score: float (may adjust score slightly based on synthesis)
      - conflicts: list[str] (any agent disagreements identified)
      - proceed: bool (should we continue to critic review?)

    Falls back gracefully if Claude is unavailable — returns the original
    score with no reasoning enhancement.
    """
    prompt = _get_orchestrator_prompt()
    if not prompt:
        return _fallback_result(confidence_score, "Orchestrator persona not loaded")

    # Build the context payload for the orchestrator
    now = datetime.now(timezone.utc)
    session = _get_session_name(now.hour)

    user_msg = f"""Analyse this trade proposal and provide your assessment.

**Pair:** {pair.replace('_', '/')}
**Direction:** {direction}
**Rule-Based Confidence Score:** {confidence_score:.1f}%
**Session:** {session}
**Time:** {now.strftime('%H:%M UTC')}

**Confidence Breakdown:**
{json.dumps(breakdown, indent=2) if breakdown else 'Not available'}

**Technical Indicators:**
- RSI: {getattr(indicators, 'rsi', 'N/A')}
- MACD Signal: {getattr(indicators, 'macd_signal', 'N/A')}
- EMA Trend: {getattr(indicators, 'ema_trend', 'N/A')}
- Bollinger Position: {getattr(indicators, 'bb_position', 'N/A')}
- ATR: {getattr(indicators, 'atr', 'N/A')}
- Current Price: {getattr(indicators, 'current_price', 'N/A')}

**LSTM Prediction:**
{json.dumps(ml_prediction, indent=2) if ml_prediction else 'Not available (shadow mode or disabled)'}

**Market Context (MCP):**
{_summarise_mcp(mcp_context) if mcp_context else 'MCP unavailable'}

**Open Positions:** {len(open_positions) if open_positions else 0}
{_summarise_positions(open_positions) if open_positions else 'None'}

Respond in this exact JSON format (no markdown, no code fences):
{{"reasoning": "2-3 sentence plain-English explanation", "adjusted_score": <number>, "conflicts": ["list any agent disagreements"], "proceed": true/false}}
"""

    response = _call_claude(prompt, user_msg, max_tokens=500)
    if not response:
        return _fallback_result(confidence_score, "Claude unavailable")

    try:
        # Parse JSON from response — handle markdown code fences if present
        cleaned = response.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        result = json.loads(cleaned)

        # Clamp the adjusted score — the orchestrator can adjust by ±10 max
        original = confidence_score
        adjusted = float(result.get("adjusted_score", confidence_score))
        adjusted = max(adjusted, original - 10)
        adjusted = min(adjusted, original + 10)
        result["adjusted_score"] = round(adjusted, 1)
        result["proceed"] = result.get("proceed", True)

        logger.info(
            f"Orchestrator {pair}: {original:.1f}% → {adjusted:.1f}% | "
            f"{result.get('reasoning', '')[:100]}"
        )
        return result

    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.warning(f"Orchestrator response parse failed: {e}")
        # Still use the reasoning text even if JSON parse fails
        return {
            "reasoning": response[:300] if response else "Parse error",
            "adjusted_score": confidence_score,
            "conflicts": [],
            "proceed": True,
        }


# ── Trade Critic ────────────────���────────────────────────────────────────────

def critic_review(
    pair: str,
    direction: str,
    confidence_score: float,
    indicators: object,
    recent_trades: list = None,
    open_positions: list = None,
) -> dict:
    """
    Run the Trade Critic persona for adversarial review.

    Returns dict with:
      - verdict: str (CLEAR / CAUTION / DOWNGRADE / SKIP)
      - adjustment: int (0 to -15)
      - risks: list[str]
      - reasoning: str

    Falls back gracefully if unavailable.
    """
    prompt = _get_critic_prompt()
    if not prompt:
        return {"verdict": "CLEAR", "adjustment": 0, "risks": [], "reasoning": "Critic persona not loaded"}

    # Summarise recent trade history for this pair
    pair_history = ""
    if recent_trades:
        pair_trades = [t for t in recent_trades if t.get("pair") == pair][-5:]
        if pair_trades:
            pair_history = "**Recent trades on this pair:**\n"
            for t in pair_trades:
                pl = t.get("pl", 0)
                reason = t.get("close_reason", "unknown")
                dur = ""
                if t.get("opened_at") and t.get("closed_at"):
                    try:
                        opened = datetime.fromisoformat(t["opened_at"])
                        closed = datetime.fromisoformat(t["closed_at"])
                        mins = (closed - opened).total_seconds() / 60
                        dur = f", duration: {mins:.0f} min"
                    except Exception:
                        pass
                pair_history += f"  - {t.get('direction', '?')} | P&L: £{pl:.2f} | {reason}{dur}\n"
        else:
            pair_history = "No recent trades on this pair.\n"

    user_msg = f"""Review this trade proposal for risks.

**Pair:** {pair.replace('_', '/')}
**Direction:** {direction}
**Confidence Score:** {confidence_score:.1f}%
**RSI:** {getattr(indicators, 'rsi', 'N/A')}
**ATR:** {getattr(indicators, 'atr', 'N/A')}

{pair_history}

**Open Positions:** {len(open_positions) if open_positions else 0}
{_summarise_positions(open_positions) if open_positions else 'None'}

Respond in this exact JSON format (no markdown, no code fences):
{{"verdict": "CLEAR|CAUTION|DOWNGRADE|SKIP", "adjustment": 0, "risks": ["list of risks"], "reasoning": "1-2 sentence explanation"}}
"""

    response = _call_claude(prompt, user_msg, max_tokens=400)
    if not response:
        return {"verdict": "CLEAR", "adjustment": 0, "risks": [], "reasoning": "Claude unavailable"}

    try:
        cleaned = response.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        result = json.loads(cleaned)

        # Clamp adjustment to -15..0
        adj = int(result.get("adjustment", 0))
        adj = max(adj, -15)
        adj = min(adj, 0)
        result["adjustment"] = adj

        verdict = result.get("verdict", "CLEAR")
        logger.info(
            f"Critic {pair}: {verdict} ({adj:+d}) | "
            f"{result.get('reasoning', '')[:100]}"
        )
        return result

    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.warning(f"Critic response parse failed: {e}")
        return {"verdict": "CLEAR", "adjustment": 0, "risks": [], "reasoning": response[:200] if response else "Parse error"}


# ── Helpers ───────────────────────────────────────────��──────────────────────

def _fallback_result(score: float, reason: str) -> dict:
    """Return a pass-through result when Claude is unavailable."""
    return {
        "reasoning": reason,
        "adjusted_score": score,
        "conflicts": [],
        "proceed": True,
    }


def _get_session_name(hour: int) -> str:
    """Map UTC hour to forex session name."""
    if 7 <= hour < 12:
        return "London"
    elif 12 <= hour < 16:
        return "London/New York overlap"
    elif 16 <= hour < 21:
        return "New York"
    elif 0 <= hour < 7:
        return "Tokyo"
    else:
        return "Sydney"


def _summarise_mcp(ctx: dict) -> str:
    """Produce a compact summary of MCP context for the prompt."""
    if not ctx:
        return "No MCP data"
    parts = []
    if ctx.get("economic_calendar"):
        events = ctx["economic_calendar"]
        if isinstance(events, list):
            high = [e for e in events if e.get("impact") == "high"]
            parts.append(f"Economic calendar: {len(high)} high-impact events upcoming")
        elif isinstance(events, dict):
            parts.append(f"Economic calendar: {events.get('summary', 'available')}")
    if ctx.get("sentiment"):
        parts.append(f"Sentiment: {ctx['sentiment']}")
    if ctx.get("volatility_regime"):
        parts.append(f"Volatility regime: {ctx['volatility_regime']}")
    if ctx.get("ig_sentiment"):
        parts.append(f"IG client sentiment: {ctx['ig_sentiment']}")
    if ctx.get("cot_bias"):
        parts.append(f"COT institutional bias: {ctx['cot_bias']}")
    return "\n".join(parts) if parts else "MCP data available but no notable signals"


def _summarise_positions(positions: list) -> str:
    """Summarise open positions for the prompt."""
    if not positions:
        return ""
    lines = []
    for p in positions:
        pair = p.get("pair", p.get("instrument", "?"))
        direction = p.get("direction", "?")
        upl = p.get("unrealizedPL", p.get("pl", 0))
        lines.append(f"  - {pair} {direction} | UPL: £{upl:.2f}")
    return "\n".join(lines)
