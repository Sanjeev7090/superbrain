"""
Sentiment Provider — News + Alternative Data for NSE stocks.

Sources:
  1. yfinance news headlines (free, no key required)
  2. Simple financial lexicon-based sentiment scoring
  3. NSE PCR (Put-Call Ratio) as alternative sentiment signal
  4. India VIX as fear gauge
"""

import logging
import time
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

# ─── Financial Sentiment Lexicon ──────────────────────────────────────────────

BULLISH_WORDS = {
    "surge", "rally", "gain", "rise", "jump", "soar", "climb", "recover",
    "breakout", "outperform", "upgrade", "beat", "record", "high", "boom",
    "growth", "positive", "strong", "bullish", "buy", "target", "upside",
    "profits", "revenue", "profit", "earnings", "dividend", "expansion",
    "confident", "optimistic", "momentum", "support", "demand", "increase",
}

BEARISH_WORDS = {
    "fall", "drop", "decline", "crash", "plunge", "tumble", "sink", "slide",
    "slump", "underperform", "downgrade", "miss", "low", "loss", "weak",
    "bearish", "sell", "downside", "cut", "risk", "concern", "worry",
    "deficit", "debt", "default", "caution", "fear", "panic", "inflation",
    "recession", "contraction", "pressure", "headwinds", "challenge",
}

INTENSIFIERS = {"very", "extremely", "significant", "major", "sharp", "massive", "huge"}

_NEWS_CACHE: Dict[str, tuple] = {}
_CACHE_TTL = 120   # 2-min cache for news


def _score_headline(text: str) -> float:
    """
    Score a headline: +1 bullish, -1 bearish, 0 neutral.
    Returns float in [-1, 1].
    """
    if not text:
        return 0.0
    words = text.lower().split()
    score = 0.0
    multiplier = 1.0
    for word in words:
        w = word.strip(".,;!?\"'()")
        if w in INTENSIFIERS:
            multiplier = 1.5
        elif w in BULLISH_WORDS:
            score += 1.0 * multiplier
            multiplier = 1.0
        elif w in BEARISH_WORDS:
            score -= 1.0 * multiplier
            multiplier = 1.0
        # Negation: "not", "no" → flip last signal
        elif w in {"not", "no", "never"}:
            multiplier = -1.0

    # Normalize by word count
    n = max(len(words), 1)
    raw = score / np.sqrt(n)      # sqrt normalise — longer text shouldn't dominate
    return float(np.clip(raw, -1.0, 1.0))


def _classify(score: float) -> str:
    if score >= 0.3:   return "BULLISH"
    if score >= 0.1:   return "SLIGHTLY_BULLISH"
    if score <= -0.3:  return "BEARISH"
    if score <= -0.1:  return "SLIGHTLY_BEARISH"
    return "NEUTRAL"


# ─── yfinance news fetcher ────────────────────────────────────────────────────

def get_news_sentiment(ticker: str, max_items: int = 15) -> dict:
    """
    Fetch and score yfinance news for a ticker.
    Returns headlines with scores + aggregate.
    """
    now = time.time()
    if ticker in _NEWS_CACHE and (now - _NEWS_CACHE[ticker][1]) < _CACHE_TTL:
        return _NEWS_CACHE[ticker][0]

    try:
        import yfinance as yf
        yt  = yf.Ticker(ticker)
        raw = getattr(yt, "news", None) or []
    except Exception as exc:
        logger.warning("yfinance news error for %s: %s", ticker, exc)
        raw = []

    articles = []
    scores   = []
    for item in raw[:max_items]:
        title    = item.get("title", "")
        link     = item.get("link", "")
        pub_time = item.get("providerPublishTime", 0)
        if not title:
            continue
        score  = _score_headline(title)
        scores.append(score)
        articles.append({
            "title":     title,
            "url":       link,
            "published": pub_time,
            "score":     round(score, 4),
            "label":     _classify(score),
        })

    if scores:
        avg_score = float(np.mean(scores))
        # Weighted by recency (more recent = higher weight)
        if len(scores) > 1:
            weights    = np.linspace(0.5, 1.0, len(scores))[::-1]  # oldest→lowest
            avg_score  = float(np.average(scores, weights=weights))
    else:
        avg_score = 0.0

    result = {
        "ticker":         ticker,
        "articles":       articles,
        "article_count":  len(articles),
        "avg_score":      round(avg_score, 4),
        "aggregate_label": _classify(avg_score),
        "bullish_count":  sum(1 for s in scores if s > 0.1),
        "bearish_count":  sum(1 for s in scores if s < -0.1),
        "neutral_count":  sum(1 for s in scores if abs(s) <= 0.1),
        "cached":         False,
    }

    _NEWS_CACHE[ticker] = (result, now)
    return result


# ─── Multi-ticker aggregate ───────────────────────────────────────────────────

def market_sentiment_aggregate(tickers: List[str]) -> dict:
    """
    Compute market-wide sentiment from multiple tickers.
    """
    all_scores  = []
    ticker_data = {}
    for t in tickers:
        try:
            ns = get_news_sentiment(t)
            if ns.get("article_count", 0) > 0:
                all_scores.append(ns["avg_score"])
                ticker_data[t] = {
                    "score":   ns["avg_score"],
                    "label":   ns["aggregate_label"],
                    "articles": ns["article_count"],
                }
        except Exception:
            continue

    if not all_scores:
        return {
            "aggregate_score": 0.0,
            "aggregate_label": "NEUTRAL",
            "tickers": {},
            "market_mood": "NEUTRAL",
        }

    agg = float(np.mean(all_scores))
    mood = "RISK_ON" if agg > 0.2 else ("RISK_OFF" if agg < -0.2 else "NEUTRAL")

    return {
        "aggregate_score": round(agg, 4),
        "aggregate_label": _classify(agg),
        "market_mood":     mood,
        "tickers":         ticker_data,
        "bullish_pct":     round(sum(1 for s in all_scores if s > 0.1) / len(all_scores) * 100, 1),
        "bearish_pct":     round(sum(1 for s in all_scores if s < -0.1) / len(all_scores) * 100, 1),
    }


# ─── Fear & Greed proxy ───────────────────────────────────────────────────────

def fear_greed_index(
    pcr: float         = 1.0,     # put-call ratio (PCR > 1 = fear)
    india_vix: float   = 15.0,    # India VIX
    breadth: float     = 0.5,     # market breadth (advancing/total) 0-1
    sentiment_score: float = 0.0, # news sentiment
) -> dict:
    """
    Composite Fear & Greed index [0-100].
      0-25:  Extreme Fear
      25-45: Fear
      45-55: Neutral
      55-75: Greed
      75-100: Extreme Greed
    """
    # PCR: low = greed, high = fear
    pcr_score    = float(np.clip((2.0 - pcr) / 2.0 * 100, 0, 100))   # PCR=0→100, PCR=2→0
    # VIX: low = greed, high = fear
    vix_score    = float(np.clip((30 - india_vix) / 30 * 100, 0, 100))
    # Breadth: high = greed
    breadth_score = float(breadth * 100)
    # Sentiment: positive = greed
    sent_score   = float(np.clip((sentiment_score + 1) / 2 * 100, 0, 100))

    composite = 0.30 * pcr_score + 0.25 * vix_score + 0.25 * breadth_score + 0.20 * sent_score

    if composite < 25:   label = "EXTREME_FEAR"
    elif composite < 45: label = "FEAR"
    elif composite < 55: label = "NEUTRAL"
    elif composite < 75: label = "GREED"
    else:                label = "EXTREME_GREED"

    return {
        "score":       round(composite, 1),
        "label":       label,
        "components": {
            "pcr_score":     round(pcr_score, 1),
            "vix_score":     round(vix_score, 1),
            "breadth_score": round(breadth_score, 1),
            "sentiment_score": round(sent_score, 1),
        },
    }
