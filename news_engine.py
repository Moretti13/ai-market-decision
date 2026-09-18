from __future__ import annotations

import math
import re
import time
from datetime import datetime, timezone
from typing import Dict, List

import numpy as np
import pandas as pd

from data_layer import raw_news, safe_float

POSITIVE = {
    "beat": 1.0, "beats": 1.0, "strong": 0.7, "growth": 0.6, "upgrade": 1.0,
    "surge": 0.8, "record": 0.7, "profit": 0.6, "outperform": 0.9, "approval": 1.0,
    "buyback": 0.8, "raises": 0.8, "raise": 0.7, "wins": 0.6, "contract": 0.5,
    "guidance raised": 1.0, "revenue beat": 1.0, "earnings beat": 1.0,
}
NEGATIVE = {
    "miss": -1.0, "misses": -1.0, "weak": -0.7, "decline": -0.6, "downgrade": -1.0,
    "drop": -0.7, "loss": -0.7, "warning": -0.9, "cuts": -0.8, "cut": -0.7,
    "lawsuit": -0.6, "recall": -0.8, "investigation": -0.8, "tariff": -0.5,
    "guidance cut": -1.0, "revenue miss": -1.0, "earnings miss": -1.0,
}

_NEWS_INTEL_CACHE: dict[tuple[str, int], tuple[float, Dict]] = {}

CATEGORY_TERMS = {
    "EARNINGS": ["earnings", "eps", "revenue", "quarter", "guidance", "profit"],
    "ANALYST": ["upgrade", "downgrade", "price target", "outperform", "underperform", "rating"],
    "M&A": ["acquire", "acquisition", "merger", "takeover", "buyout"],
    "LEGAL/REGULATORY": ["lawsuit", "investigation", "regulator", "antitrust", "sec ", "doj", "recall"],
    "PRODUCT": ["launch", "product", "chip", "drug", "approval", "fda", "contract"],
    "MACRO": ["fed", "inflation", "rates", "cpi", "jobs", "tariff", "gdp", "yield"],
}


def _normalise_title(title: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", title.lower())).strip()


def _published_ts(value) -> pd.Timestamp | None:
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)) and value > 10_000_000:
            ts = pd.to_datetime(value, unit="s", utc=True, errors="coerce")
        else:
            ts = pd.to_datetime(value, utc=True, errors="coerce")
        return None if pd.isna(ts) else ts
    except Exception:
        return None


def score_title(title: str) -> Dict:
    text = _normalise_title(title)
    score = 0.0
    matched = []
    for term, weight in POSITIVE.items():
        if term in text:
            score += weight
            matched.append(term)
    for term, weight in NEGATIVE.items():
        if term in text:
            score += weight
            matched.append(term)
    sentiment = float(np.tanh(score / 2.0))

    categories = []
    for cat, terms in CATEGORY_TERMS.items():
        if any(term in text for term in terms):
            categories.append(cat)
    importance = 0.25
    if "EARNINGS" in categories:
        importance += 0.35
    if "M&A" in categories or "LEGAL/REGULATORY" in categories:
        importance += 0.25
    if "ANALYST" in categories or "PRODUCT" in categories:
        importance += 0.15
    if abs(sentiment) > 0.5:
        importance += 0.1
    importance = float(np.clip(importance, 0.0, 1.0))
    uncertainty = float(np.clip(0.25 + 0.15 * sum(w in text for w in ["may", "could", "reportedly", "rumor", "considering"]), 0, 1))
    return {
        "sentiment": sentiment,
        "importance": importance,
        "uncertainty": uncertainty,
        "categories": categories or ["GENERAL"],
        "matched_terms": matched,
    }


def news_intelligence(ticker: str, limit: int = 20, force: bool = False) -> Dict:
    ticker = ticker.strip().upper()
    key = (ticker, int(limit))
    cache_now = time.time()
    cached = _NEWS_INTEL_CACHE.get(key)
    if not force and cached and cache_now - cached[0] < 300:
        return dict(cached[1])
    raw = raw_news(ticker, limit=limit)
    now = pd.Timestamp.now(tz="UTC")
    seen = set()
    enriched: List[Dict] = []
    weighted_sentiment = 0.0
    total_weight = 0.0

    for item in raw:
        title = str(item.get("title") or "News")
        norm = _normalise_title(title)
        fingerprint = " ".join(norm.split()[:12])
        if not fingerprint or fingerprint in seen:
            continue
        seen.add(fingerprint)
        scored = score_title(title)
        ts = _published_ts(item.get("published"))
        age_hours = max(0.0, (now - ts).total_seconds() / 3600) if ts is not None else 72.0
        recency = math.exp(-math.log(2) * age_hours / 36.0)
        weight = recency * (0.35 + 0.65 * scored["importance"]) * (1.0 - 0.35 * scored["uncertainty"])
        weighted_sentiment += scored["sentiment"] * weight
        total_weight += weight
        enriched.append({
            **item,
            **scored,
            "published_iso": ts.isoformat() if ts is not None else None,
            "age_hours": round(age_hours, 1),
            "recency_weight": round(recency, 3),
        })

    aggregate = weighted_sentiment / total_weight if total_weight > 0 else 0.0
    important = [x for x in enriched if x["importance"] >= 0.6]
    category_counts: Dict[str, int] = {}
    for item in enriched:
        for cat in item["categories"]:
            category_counts[cat] = category_counts.get(cat, 0) + 1
    top_category = max(category_counts, key=category_counts.get) if category_counts else "NONE"
    result = {
        "ticker": ticker,
        "items": enriched,
        "count": len(enriched),
        "sentiment": float(np.clip(aggregate, -1.0, 1.0)),
        "importance": safe_float(np.mean([x["importance"] for x in important]), 0.0) if important else 0.0,
        "important_count": len(important),
        "top_category": top_category,
        "method": "rule-based recency/importance scoring",
    }
    _NEWS_INTEL_CACHE[key] = (cache_now, dict(result))
    return result
