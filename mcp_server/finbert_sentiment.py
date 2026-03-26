"""
mcp_server/finbert_sentiment.py — FinBERT Financial Sentiment Analysis
────────────────────────────────────────────────────────────────────────
Financial NLP using FinBERT — a BERT model fine-tuned for financial text.

Optimised for near-real-time:
  - Batch inference: scores all headlines in one pass (~5x faster than sequential)
  - Background pre-scoring: headlines scored as they arrive, results cached
  - Zero-latency at scan time: confidence engine reads from cache, never waits

Model: ProsusAI/finbert (open source, Apache 2.0 license)
  - Downloads ~420MB on first use, cached locally
  - Runs on CPU — no GPU needed
  - Batch of 50 headlines: ~500ms on CPU
  - No API key, no account, no credit card, completely free
"""

import time
import threading
from typing import Optional
from loguru import logger

# Lazy-loaded model — downloads on first use, ~420MB
_model = None
_tokenizer = None
_model_loaded = False
_load_attempted = False

# Pre-scored headline cache: {headline_text: {"score": float, "label": str, "scored_at": float}}
_score_cache = {}
_SCORE_CACHE_TTL = 900  # 15 minutes — headlines don't change sentiment quickly


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
    Score a single headline. Returns cached result if available,
    otherwise scores in real-time.

    Returns:
      float: -1.0 (very bearish) to +1.0 (very bullish), or None if unavailable
    """
    # Check cache first
    cached = _score_cache.get(headline)
    if cached and time.time() - cached["scored_at"] < _SCORE_CACHE_TTL:
        return cached["score"]

    # Score in real-time
    results = _batch_score([headline])
    if results:
        return results[0]["score"]
    return None


def score_headlines_batch(headlines: list[str]) -> list[dict]:
    """
    Score multiple headlines in one efficient batch.
    Uses cache for already-scored headlines, batch-scores the rest.

    Returns list of {"headline": str, "score": float, "label": str}
    """
    now = time.time()
    results = []
    to_score = []
    to_score_indices = []

    # Split into cached and uncached
    for i, headline in enumerate(headlines):
        cached = _score_cache.get(headline)
        if cached and now - cached["scored_at"] < _SCORE_CACHE_TTL:
            results.append({"headline": headline, "score": cached["score"], "label": cached["label"]})
        else:
            results.append(None)  # Placeholder
            to_score.append(headline)
            to_score_indices.append(i)

    # Batch score uncached headlines
    if to_score:
        batch_results = _batch_score(to_score)
        for idx, result in zip(to_score_indices, batch_results):
            results[idx] = result

    # Filter out None (failed scores)
    return [r for r in results if r is not None]


def _batch_score(headlines: list[str]) -> list[dict]:
    """Score a batch of headlines in one model pass. ~5x faster than sequential."""
    if not _ensure_model_loaded() or not headlines:
        return []

    try:
        import torch

        # Tokenise entire batch at once
        inputs = _tokenizer(
            headlines,
            return_tensors="pt",
            truncation=True,
            max_length=128,
            padding=True
        )

        with torch.no_grad():
            outputs = _model(**inputs)
            probabilities = torch.nn.functional.softmax(outputs.logits, dim=-1)

        results = []
        now = time.time()

        for i, headline in enumerate(headlines):
            positive = probabilities[i][0].item()
            negative = probabilities[i][1].item()
            score = round(positive - negative, 3)

            if score > 0.15:
                label = "Bullish"
            elif score < -0.15:
                label = "Bearish"
            else:
                label = "Neutral"

            result = {"headline": headline, "score": score, "label": label}
            results.append(result)

            # Cache the result
            _score_cache[headline] = {"score": score, "label": label, "scored_at": now}

        return results

    except Exception as e:
        logger.debug(f"FinBERT batch scoring failed: {e}")
        return []


def pre_score_headlines(headlines: list[str]):
    """Pre-score headlines in a background thread.
    Called by the sentiment module after fetching fresh headlines,
    so the cache is warm before the next confidence calculation."""
    if not headlines:
        return

    def _background():
        _batch_score(headlines)

    threading.Thread(target=_background, daemon=True).start()


def is_available() -> bool:
    """Check if FinBERT is available."""
    return _ensure_model_loaded()


def get_cache_stats() -> dict:
    """Return cache statistics for monitoring."""
    now = time.time()
    valid = sum(1 for v in _score_cache.values() if now - v["scored_at"] < _SCORE_CACHE_TTL)
    return {
        "total_cached": len(_score_cache),
        "valid_cached": valid,
        "model_loaded": _model_loaded,
    }
