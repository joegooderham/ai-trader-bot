"""
bot/engine/confidence.py — Confidence Score Engine
────────────────────────────────────────────────────
This is the brain of the decision-making process.

Every trade decision goes through this module. It takes:
  1. Technical indicator values (RSI, MACD, etc.)
  2. MCP context (economic calendar, sentiment, correlations, etc.)
  3. ML model prediction (LSTM neural network)

And produces:
  - A confidence score (0–100%)
  - A trade direction ("BUY", "SELL", or "NO_TRADE")
  - A plain-English reasoning explanation

The reasoning is logged and sent to you via Telegram so you always know
EXACTLY why the bot made a decision. Nothing is a black box.

Confidence Score Breakdown:
  50% — LSTM Neural Network prediction
  20% — MACD + RSI consensus
  15% — EMA trend alignment
  10% — Bollinger Band position
   5% — Volume confirmation
"""

from dataclasses import dataclass
from typing import Optional
from loguru import logger

from bot.engine.indicators import IndicatorResult
from bot import config


@dataclass
class ConfidenceResult:
    """
    The output of the confidence scoring process.
    Everything is explained so you know why the bot acted.
    """
    score: float                # 0–100 — the overall confidence percentage
    direction: str              # "BUY", "SELL", or "NO_TRADE"
    reasoning: str              # Plain English explanation of the decision
    breakdown: dict             # Score contribution from each component
    should_trade: bool          # True if score >= minimum threshold


def calculate_confidence(
    pair: str,
    indicators: IndicatorResult,
    mcp_context: dict,
    ml_prediction: Optional[dict] = None,
    mtf_context: Optional[dict] = None
) -> ConfidenceResult:
    """
    Calculate the overall confidence score for a potential trade.

    This function is the core of the entire bot. Every component's
    contribution to the final score is tracked separately so you can
    see exactly what drove the decision.

    Args:
        pair: Currency pair e.g. "EUR_USD"
        indicators: Technical indicator values
        mcp_context: Market context from the MCP analysis server
        ml_prediction: LSTM model output (optional — falls back to 50 if not available)

    Returns:
        ConfidenceResult with score, direction, and full reasoning
    """
    reasoning_parts = []
    breakdown = {}

    # ── Step 1: Determine likely direction from indicators ────────────────────
    # We tally bullish and bearish signals to determine overall direction
    bullish_signals = 0
    bearish_signals = 0

    # RSI analysis — symmetric thresholds for BUY and SELL signals.
    # Previously SELL was disadvantaged: needed RSI > 70 (rare) while BUY
    # triggered at RSI < 30. Now the mild zones are symmetric too.
    if indicators.rsi < 30:
        bullish_signals += 2    # Strongly oversold — good time to buy
        reasoning_parts.append(f"RSI {indicators.rsi:.1f} is oversold (below 30) — price may bounce up")
    elif indicators.rsi > 70:
        bearish_signals += 2    # Strongly overbought — good time to sell
        reasoning_parts.append(f"RSI {indicators.rsi:.1f} is overbought (above 70) — price may fall")
    elif indicators.rsi < 45:
        bullish_signals += 1    # Mildly oversold — slight bullish lean
    elif indicators.rsi > 55:
        bearish_signals += 1    # Mildly overbought — slight bearish lean

    # MACD analysis
    if "bullish" in indicators.macd_signal:
        weight = 2 if "crossover" in indicators.macd_signal else 1
        bullish_signals += weight
        reasoning_parts.append(f"MACD showing {'strong ' if weight == 2 else ''}bullish momentum")
    elif "bearish" in indicators.macd_signal:
        weight = 2 if "crossover" in indicators.macd_signal else 1
        bearish_signals += weight
        reasoning_parts.append(f"MACD showing {'strong ' if weight == 2 else ''}bearish momentum")

    # EMA trend
    if indicators.ema_trend == "bullish":
        bullish_signals += 1
        reasoning_parts.append("EMA trend is bullish (20 EMA above 50 EMA)")
    elif indicators.ema_trend == "bearish":
        bearish_signals += 1
        reasoning_parts.append("EMA trend is bearish (20 EMA below 50 EMA)")

    # Bollinger Bands
    if indicators.bb_position == "below_lower":
        bullish_signals += 2
        reasoning_parts.append("Price is below lower Bollinger Band — statistically likely to mean-revert upward")
    elif indicators.bb_position == "above_upper":
        bearish_signals += 2
        reasoning_parts.append("Price is above upper Bollinger Band — statistically likely to mean-revert downward")
    elif indicators.bb_position == "middle_upper":
        bullish_signals += 1
    elif indicators.bb_position == "middle_lower":
        bearish_signals += 1

    # Determine direction
    total_signals = bullish_signals + bearish_signals
    if total_signals == 0 or abs(bullish_signals - bearish_signals) <= 1:
        direction = "NO_TRADE"
        base_direction_score = 0
        reasoning_parts.append("Indicators are mixed — no clear directional signal")
    elif bullish_signals > bearish_signals:
        direction = "BUY"
        # Score based on how strongly the signals agree
        base_direction_score = min((bullish_signals / max(total_signals, 1)) * 100, 100)
    else:
        direction = "SELL"
        base_direction_score = min((bearish_signals / max(total_signals, 1)) * 100, 100)

    if direction == "NO_TRADE":
        return ConfidenceResult(
            score=0,
            direction="NO_TRADE",
            reasoning="No clear trade signal. " + " | ".join(reasoning_parts),
            breakdown={},
            should_trade=False
        )

    # ── Step 2: Score each component ──────────────────────────────────────────

    weights = config.CONFIDENCE_WEIGHTS

    # ML Model score (50% weight)
    if ml_prediction and ml_prediction.get("direction") == direction:
        ml_score = ml_prediction.get("probability", 0.5) * 100
        reasoning_parts.append(f"LSTM model predicts {direction} with {ml_score:.1f}% probability")
    else:
        # No ML model available — use indicator consensus as fallback
        ml_score = base_direction_score
        reasoning_parts.append(f"Using indicator consensus ({ml_score:.1f}%) in place of ML model")

    breakdown["lstm_model"] = round(ml_score * weights["lstm_model"] / 100, 2)

    # MACD + RSI consensus (20% weight) — symmetric thresholds for BUY/SELL.
    # Previously SELL needed RSI > 45 for 100pts (almost always true, too easy)
    # while BUY needed RSI < 55 (also easy). Now both use the same logic:
    # Full score (100) when MACD agrees AND RSI is in the confirming zone,
    # partial score (60) when either condition is met alone.
    macd_rsi_score = 0
    if direction == "BUY":
        if "bullish" in indicators.macd_signal and indicators.rsi < 50:
            macd_rsi_score = 100  # MACD bullish + RSI below midline = strong BUY
        elif "bullish" in indicators.macd_signal or indicators.rsi < 40:
            macd_rsi_score = 60
    else:  # SELL
        if "bearish" in indicators.macd_signal and indicators.rsi > 50:
            macd_rsi_score = 100  # MACD bearish + RSI above midline = strong SELL
        elif "bearish" in indicators.macd_signal or indicators.rsi > 60:
            macd_rsi_score = 60

    breakdown["macd_rsi"] = round(macd_rsi_score * weights["macd_rsi_consensus"] / 100, 2)

    # EMA trend alignment (15% weight)
    ema_score = 100 if indicators.ema_trend == direction.lower() or \
        (direction == "BUY" and indicators.ema_trend == "bullish") or \
        (direction == "SELL" and indicators.ema_trend == "bearish") else \
        (50 if indicators.ema_trend == "neutral" else 0)

    breakdown["ema_trend"] = round(ema_score * weights["ema_trend_alignment"] / 100, 2)

    # Bollinger Band position (10% weight)
    bb_score = 0
    if direction == "BUY":
        if indicators.bb_position == "below_lower":
            bb_score = 100
        elif indicators.bb_position == "middle_lower":
            bb_score = 60
        elif indicators.bb_position == "middle":
            bb_score = 40
    else:  # SELL
        if indicators.bb_position == "above_upper":
            bb_score = 100
        elif indicators.bb_position == "middle_upper":
            bb_score = 60
        elif indicators.bb_position == "middle":
            bb_score = 40

    breakdown["bollinger"] = round(bb_score * weights["bollinger_position"] / 100, 2)

    # Volume confirmation (5% weight)
    # High volume confirms the signal is backed by real market activity
    if indicators.relative_volume > 1.5:
        vol_score = 100
        reasoning_parts.append(f"Volume is {indicators.relative_volume:.1f}x average — strong confirmation")
    elif indicators.relative_volume > 1.0:
        vol_score = 70
    elif indicators.relative_volume > 0.7:
        vol_score = 40
    else:
        vol_score = 10
        reasoning_parts.append(f"Volume is low ({indicators.relative_volume:.1f}x avg) — signal less reliable")

    breakdown["volume"] = round(vol_score * weights["volume_confirmation"] / 100, 2)

    # ── Step 3: Apply MCP context modifiers ───────────────────────────────────
    mcp_modifier = _apply_mcp_context(pair, direction, mcp_context, reasoning_parts)

    # ── Step 3b: Apply multi-timeframe modifier (BACKLOG-004) ──────────────
    mtf_modifier = 0.0
    if mtf_context and mtf_context.get("trend") != "neutral":
        htf_trend = mtf_context["trend"]
        htf_strength = mtf_context.get("strength", 0)
        # Check if higher TF trend aligns with our entry signal
        direction_matches = (
            (direction == "BUY" and htf_trend == "bullish") or
            (direction == "SELL" and htf_trend == "bearish")
        )
        if direction_matches:
            mtf_modifier = config.HTF_ALIGNMENT_BONUS
            reasoning_parts.append(
                f"H4 trend is {htf_trend} (strength {htf_strength:.0f}%) — "
                f"aligns with {direction}, +{config.HTF_ALIGNMENT_BONUS} boost"
            )
        else:
            mtf_modifier = -config.HTF_CONFLICT_PENALTY
            reasoning_parts.append(
                f"⚠️ H4 trend is {htf_trend} (strength {htf_strength:.0f}%) — "
                f"conflicts with {direction}, -{config.HTF_CONFLICT_PENALTY} penalty"
            )

    # ── Step 4: Calculate final score ─────────────────────────────────────────
    raw_score = sum(breakdown.values())
    final_score = min(max(raw_score + mcp_modifier + mtf_modifier, 0), 100)

    breakdown["mcp_modifier"] = round(mcp_modifier, 2)
    breakdown["mtf_modifier"] = round(mtf_modifier, 2)
    breakdown["final"] = round(final_score, 2)

    # ── Step 5: Build the reasoning explanation ───────────────────────────────
    direction_word = "BUY (long)" if direction == "BUY" else "SELL (short)"
    reasoning = (
        f"Signal: {direction_word} on {pair}\n"
        f"Overall confidence: {final_score:.1f}%\n\n"
        f"Why: " + " | ".join(reasoning_parts)
    )

    should_trade = final_score >= config.MIN_CONFIDENCE_SCORE

    if not should_trade:
        reasoning += f"\n\nNot trading — score {final_score:.1f}% is below minimum {config.MIN_CONFIDENCE_SCORE}%"

    logger.info(f"Confidence for {pair} {direction}: {final_score:.1f}% | Trade: {should_trade}")

    return ConfidenceResult(
        score=round(final_score, 2),
        direction=direction,
        reasoning=reasoning,
        breakdown=breakdown,
        should_trade=should_trade
    )


def _apply_mcp_context(
    pair: str,
    direction: str,
    mcp_context: dict,
    reasoning_parts: list
) -> float:
    """
    Adjusts confidence score based on broader market context from the MCP server.

    Returns a modifier (positive or negative) to add to the raw score.
    Maximum adjustment: +15 or -20 points.
    """
    modifier = 0.0

    if not mcp_context:
        return modifier

    # Economic calendar — upcoming high-impact news events
    upcoming_events = mcp_context.get("upcoming_high_impact_events", [])
    if upcoming_events:
        # Reduce confidence if a major news event is within 2 hours
        modifier -= 15
        event_names = ", ".join([e.get("event", "Unknown") for e in upcoming_events[:2]])
        reasoning_parts.append(f"⚠️ High-impact event soon: {event_names} — reducing confidence")

    # Sentiment analysis — is market news positive or negative for this pair?
    sentiment = mcp_context.get("sentiment", {}).get(pair, "neutral")
    if sentiment == "bullish" and direction == "BUY":
        modifier += 8
        reasoning_parts.append("News sentiment is bullish — supports BUY signal")
    elif sentiment == "bearish" and direction == "SELL":
        modifier += 8
        reasoning_parts.append("News sentiment is bearish — supports SELL signal")
    elif sentiment == "bullish" and direction == "SELL":
        modifier -= 10
        reasoning_parts.append("⚠️ News sentiment is bullish but we're considering SELL — caution")
    elif sentiment == "bearish" and direction == "BUY":
        modifier -= 10
        reasoning_parts.append("⚠️ News sentiment is bearish but we're considering BUY — caution")

    # Volatility regime — is market in high or low volatility period?
    volatility_regime = mcp_context.get("volatility_regime", "normal")
    if volatility_regime == "extreme":
        modifier -= 15
        reasoning_parts.append("⚠️ Extreme volatility regime — reducing confidence significantly")
    elif volatility_regime == "high":
        modifier -= 5
        reasoning_parts.append("High volatility regime — slightly reducing confidence")
    elif volatility_regime == "low":
        modifier += 5
        reasoning_parts.append("Low volatility regime — market conditions calm and predictable")

    # Session performance — does this pair trade well in the current session?
    session_score = mcp_context.get("session_performance", {}).get(pair, 0)
    if session_score > 60:
        modifier += 5
        reasoning_parts.append(f"This pair historically performs well in the current session")
    elif session_score < 30:
        modifier -= 5
        reasoning_parts.append(f"This pair historically underperforms in the current session")

    # Correlation risk — are we already holding a correlated position?
    correlation_warning = mcp_context.get("correlation_warning", {}).get(pair)
    if correlation_warning:
        modifier -= 10
        reasoning_parts.append(f"⚠️ Correlation risk: already holding {correlation_warning} which moves similarly")

    # ── IG Client Sentiment — Contrarian Indicator (BACKLOG-013) ───────────
    # When a large majority of IG retail clients are positioned one way,
    # the market statistically moves against them. This is one of the most
    # reliable free signals available — IG's own research backs this up.
    #
    # Modifier logic:
    #   - If our trade direction ALIGNS with the contrarian signal → boost
    #   - If our trade OPPOSES the contrarian signal → penalty
    #   - Only applies when positioning is extreme (>75% one-sided)
    #   - The 75% threshold is intentionally conservative to avoid noise
    ig_sentiment = mcp_context.get("client_sentiment", {})
    contrarian_bias = ig_sentiment.get("contrarian_bias", "NEUTRAL")
    bias_strength = ig_sentiment.get("bias_strength", 50)

    if contrarian_bias != "NEUTRAL" and bias_strength >= 75:
        # Strong contrarian signal — retail is heavily positioned one way
        long_pct = ig_sentiment.get("long_percentage", 50)
        short_pct = ig_sentiment.get("short_percentage", 50)
        crowd_direction = "long" if long_pct > short_pct else "short"

        if contrarian_bias == direction:
            # Our trade aligns with the contrarian signal — confidence boost
            modifier += 8
            reasoning_parts.append(
                f"IG sentiment: {bias_strength:.0f}% of retail clients are {crowd_direction} "
                f"— contrarian signal supports our {direction}"
            )
        else:
            # Our trade goes with the crowd — contrarian penalty
            modifier -= 10
            reasoning_parts.append(
                f"⚠️ IG sentiment: {bias_strength:.0f}% of retail clients are {crowd_direction} "
                f"— we're trading WITH the crowd, contrarian signal opposes"
            )

    # ── FRED Macro Bias — Interest Rate Differentials (BACKLOG-014) ──────────
    # Carry trade logic: money flows towards higher-yielding currencies.
    # A significant interest rate differential (>0.5%) gives a directional hint.
    # Moderate influence — macro is a backdrop, not a timing signal.
    fred = mcp_context.get("fred_macro", {})
    fred_bias = fred.get("bias", "NEUTRAL")
    fred_strength = fred.get("bias_strength", 0)

    if fred_bias != "NEUTRAL" and fred_strength > 20:
        rate_diff = fred.get("rate_differential", 0)
        if fred_bias == direction:
            modifier += 5
            reasoning_parts.append(
                f"FRED macro: {abs(rate_diff):.1f}% rate differential "
                f"favours {direction} (carry trade)"
            )
        elif fred_bias != "NEUTRAL":
            modifier -= 5
            reasoning_parts.append(
                f"FRED macro: {abs(rate_diff):.1f}% rate differential "
                f"opposes our {direction} (against carry trade)"
            )

    # ── Myfxbook Community Sentiment (BACKLOG-015) ────────────────────────────
    # Cross-validates IG Client Sentiment with ~100k connected retail accounts.
    # When both IG and Myfxbook agree on extreme positioning, the contrarian
    # signal is stronger. Applied independently but stacks with IG sentiment.
    myfxbook = mcp_context.get("myfxbook_sentiment", {})
    mfx_bias = myfxbook.get("contrarian_bias", "NEUTRAL")
    mfx_strength = myfxbook.get("bias_strength", 50)

    if mfx_bias != "NEUTRAL" and mfx_strength >= 70:
        mfx_long = myfxbook.get("long_percentage", 50)
        mfx_crowd = "long" if mfx_long > 50 else "short"

        if mfx_bias == direction:
            modifier += 5
            reasoning_parts.append(
                f"Myfxbook: {mfx_strength:.0f}% of community are {mfx_crowd} "
                f"— contrarian supports our {direction}"
            )
        else:
            modifier -= 5
            reasoning_parts.append(
                f"Myfxbook: {mfx_strength:.0f}% of community are {mfx_crowd} "
                f"— contrarian opposes our {direction}"
            )

    # ── CFTC COT Positioning — Institutional Bias (BACKLOG-016) ───────────
    # Weekly data showing how hedge funds and banks are positioned.
    # Large speculator net positioning indicates institutional momentum.
    # Reduces confidence when trading against institutional positioning.
    cot = mcp_context.get("cot_positioning", {})
    cot_bias = cot.get("bias", "NEUTRAL")
    cot_strength = cot.get("bias_strength", 0)

    if cot_bias != "NEUTRAL" and cot_strength > 20:
        if cot_bias == direction:
            modifier += 5
            reasoning_parts.append(
                f"COT: large speculators are net {cot_bias.lower()} "
                f"— institutional momentum supports {direction}"
            )
        else:
            modifier -= 8
            reasoning_parts.append(
                f"⚠️ COT: large speculators are net {cot_bias.lower()} "
                f"— trading against institutional positioning"
            )

    return modifier
