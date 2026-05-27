"""
FinBERT sentiment scorer — optional heavyweight ML layer.

Uses yiyanghkust/finbert-tone (Hugging Face) for financial-news sentiment.
Lazy-loaded: torch/transformers are imported only on first call.

If the model or dependencies are missing, falls back to VADER-only
(with a warning logged once).
"""
from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from transformers import pipeline

from .news_feeds import NewsItem

_log = logging.getLogger(__name__)

# Model cached per-thread (thread-safe, models are often not picklable)
_local = threading.local()

# Mapping from FinBERT label to [-1, 1] score
_LABEL_SCORE: dict[str, float] = {
    "Positive": 0.65,
    "Neutral": 0.0,
    "Negative": -0.65,
}


def _load_pipeline():
    """Lazy-load the FinBERT sentiment pipeline."""
    if hasattr(_local, "pipeline"):
        return _local.pipeline
    try:
        from transformers import pipeline
        _log.info("Loading FinBERT model (yiyanghkust/finbert-tone)...")
        pipe = pipeline(
            "sentiment-analysis",
            model="yiyanghkust/finbert-tone",
            tokenizer="yiyanghkust/finbert-tone",
            device=-1,  # CPU; set to 0 if CUDA available
            truncation=True,
            max_length=512,
        )
        _local.pipeline = pipe
        _log.info("FinBERT loaded successfully.")
        return pipe
    except Exception as exc:
        _local.pipeline = None
        _log.warning("FinBERT unavailable: %s. Falling back to VADER-only.", exc)
        return None


def finbert_available() -> bool:
    """Check whether FinBERT pipeline is ready."""
    return _load_pipeline() is not None


def score_headlines_finbert(items: list[NewsItem]) -> list[float]:
    """Score a list of headlines with FinBERT. Returns scores in [-1, 1].

    If FinBERT is not available, returns empty list (caller should fall back).
    """
    pipe = _load_pipeline()
    if pipe is None or not items:
        return []
    titles = [it.title for it in items]
    try:
        results = pipe(titles, batch_size=min(32, len(titles)))
    except Exception as exc:
        _log.warning("FinBERT batch inference failed: %s", exc)
        return []
    scores: list[float] = []
    for r in results:
        label = r.get("label", "Neutral") if isinstance(r, dict) else getattr(r, "label", "Neutral")
        # Confidence-weighted score: label_score * confidence
        conf = r.get("score", 0.5) if isinstance(r, dict) else getattr(r, "score", 0.5)
        base = _LABEL_SCORE.get(label, 0.0)
        scores.append(float(base * conf))
    return scores


def ensemble_score(vader_scores: list[float], finbert_scores: list[float], *, vader_weight: float = 0.35, finbert_weight: float = 0.65) -> list[float]:
    """Weighted ensemble of VADER and FinBERT scores.

    If finbert_scores is empty, returns vader_scores unchanged.
    """
    if not finbert_scores or len(vader_scores) != len(finbert_scores):
        return vader_scores
    out: list[float] = []
    for vs, fs in zip(vader_scores, finbert_scores):
        out.append(vs * vader_weight + fs * finbert_weight)
    return out
