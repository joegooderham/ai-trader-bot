"""
mcp_server/finbert_sentiment.py — FinBERT Financial Sentiment Analysis
────────────────────────────────────────────────────────────────────────
Replaces keyword-based news sentiment with proper NLP using FinBERT —
a BERT model fine-tuned specifically on financial text.

FinBERT understands context that keyword matching misses:
  - "Apple falls short of expectations" → bearish (keywords would miss this)
  - "Fed raises rates, surprising no one" → neutral (keywords would say bullish)
  - "EUR strengthens despite weak data" → bullish EUR (complex reasoning)

Model: ProsusAI/finbert (open source, Apache 2.0 license)
  - Downloads ~420MB on first use, cached locally
  - Runs on CPU — no GPU needed
  - ~50ms per headline (fast enough for our use case)
  - No API key, no account, no credit card, completely free

Integration:
  - Called by sentiment.py as an alternative to keyword scoring
  - Returns score -1.0 (bearish) to +1.0 (bullish) per headline
  - Falls back to keyword scoring if model fails to load
"""

import time
from typing import Optional
from loguru import logger

# Lazy-loaded model — downloads on first use, ~420MB
_model = None
_tokenizer = None
_model_loaded = False
_load_attempted = False


def _ensure_model_loaded():
    """Load FinBERT model and tokenizer. Downloads on first use."""
    global _model, _tokenizer, _model_loaded, _load_attempted

    if _model_loaded or _load_attempted:
        return _model_loaded

    _load_attempted = True

    try:
        from transformers import AutoTokenizer, AutoModelForSequenceClassification
        import torch

        logger.info("Loading FinBERT model (first load downloads ~420MB)...")
        model_name = "ProsusAI/finbert"

        _tokenizer = AutoTokenizer.from_pretrained(model_name)
        _model = AutoModelForSequenceClassification.from_pretrained(model_name)
        _model.eval()  # Set to inference mode

        _model_loaded = True
        logger.info("FinBERT model loaded successfully")
        return True

    except ImportError:
        logger.warning("transformers library not installed — FinBERT disabled. "
                       "Install with: pip install transformers")
        return False
    except Exception as e:
        logger.warning(f"FinBERT model load failed: {e}")
        return False


def score_headline(headline: str) -> Optional[float]:
    """
    Score a single headline using FinBERT.

    Returns:
      float: -1.0 (very bearish) to +1.0 (very bullish), or None if model unavailable
    """
    if not _ensure_model_loaded():
        return None

    try:
        import torch

        inputs = _tokenizer(headline, return_tensors="pt", truncation=True, max_length=128)

        with torch.no_grad():
            outputs = _model(**inputs)
            probabilities = torch.nn.functional.softmax(outputs.logits, dim=-1)

        # FinBERT outputs: [positive, negative, neutral]
        positive = probabilities[0][0].item()
        negative = probabilities[0][1].item()
        # neutral = probabilities[0][2].item()

        # Convert to -1 to +1 scale
        score = positive - negative

        return round(score, 3)

    except Exception as e:
        logger.debug(f"FinBERT scoring failed for headline: {e}")
        return None


def score_headlines(headlines: list[str]) -> list[dict]:
    """
    Score multiple headlines efficiently.

    Returns list of {"headline": str, "score": float, "label": str}
    """
    if not _ensure_model_loaded():
        return []

    results = []
    for headline in headlines:
        score = score_headline(headline)
        if score is not None:
            if score > 0.15:
                label = "Bullish"
            elif score < -0.15:
                label = "Bearish"
            else:
                label = "Neutral"

            results.append({
                "headline": headline,
                "score": score,
                "label": label,
            })

    return results


def is_available() -> bool:
    """Check if FinBERT is available (model loaded or can be loaded)."""
    return _ensure_model_loaded()
