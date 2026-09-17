from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Iterable, List

import pandas as pd

from model_engine import train_medium_model
from signal_engine import premarket_analysis


def _scan_one(ticker: str) -> dict:
    try:
        pre = premarket_analysis(ticker)
        try:
            week = train_medium_model(ticker, "WEEK")
        except Exception as exc:
            week = {"signal": "N/A", "p_up": 0.5, "expected_return": 0.0, "error": str(exc)}
        try:
            month = train_medium_model(ticker, "MONTH")
        except Exception as exc:
            month = {"signal": "N/A", "p_up": 0.5, "expected_return": 0.0, "error": str(exc)}
        return {
            "ticker": ticker,
            "DAY": pre["signal"], "DAY_prob": round(pre["p_up"] * 100, 1), "DAY_exp": round(pre["expected_return"] * 100, 2),
            "WEEK": week.get("signal", "N/A"), "WEEK_prob": round(week.get("p_up", 0.5) * 100, 1), "WEEK_exp": round(week.get("expected_return", 0.0) * 100, 2),
            "MONTH": month.get("signal", "N/A"), "MONTH_prob": round(month.get("p_up", 0.5) * 100, 1), "MONTH_exp": round(month.get("expected_return", 0.0) * 100, 2),
            "regime": pre["regime"]["regime"],
            "news": round(pre["news_sentiment"], 3),
            "score_day": round(pre["confidence"] * max(pre["p_up"], 1 - pre["p_up"]) * 100, 1),
        }
    except Exception as exc:
        return {"ticker": ticker, "ERROR": str(exc)}


def scanner(universe: Iterable[str], max_assets: int = 10) -> pd.DataFrame:
    tickers = list(dict.fromkeys(universe))[:max_assets]
    rows: List[dict] = []
    with ThreadPoolExecutor(max_workers=min(4, max(1, len(tickers)))) as pool:
        futures = {pool.submit(_scan_one, t): t for t in tickers}
        for fut in as_completed(futures):
            row = fut.result()
            if "ERROR" not in row:
                rows.append(row)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(["score_day", "DAY_prob"], ascending=[False, False]).reset_index(drop=True)
