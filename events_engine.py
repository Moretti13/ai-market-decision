from __future__ import annotations

import time
from typing import Dict

from data_layer import earnings_context
from news_engine import news_intelligence

_EVENT_CACHE: dict[str, tuple[float, Dict]] = {}


def event_context(ticker: str, force: bool = False) -> Dict:
    ticker = ticker.strip().upper()
    now = time.time()
    cached = _EVENT_CACHE.get(ticker)
    if not force and cached and now - cached[0] < 300:
        return dict(cached[1])
    earnings = earnings_context(ticker)
    news = news_intelligence(ticker)
    d = earnings.get("days_to_earnings")
    if d is not None and d <= 1:
        risk = "HIGH"
        risk_penalty = 0.20
    elif d is not None and d <= 3:
        risk = "MEDIUM"
        risk_penalty = 0.10
    else:
        risk = "NORMAL"
        risk_penalty = 0.0
    if news.get("important_count", 0) >= 3 and abs(news.get("sentiment", 0.0)) >= 0.35:
        risk = "HIGH" if risk != "HIGH" else risk
        risk_penalty = max(risk_penalty, 0.15)
    result = {
        "earnings": earnings,
        "news": news,
        "event_risk": risk,
        "risk_penalty": risk_penalty,
        "catalyst": news.get("top_category", "NONE"),
    }
    _EVENT_CACHE[ticker] = (now, dict(result))
    return result
