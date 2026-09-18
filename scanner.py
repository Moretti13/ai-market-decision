from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable, List

import numpy as np
import pandas as pd

from config import DEFAULTS
from events_engine import event_context
from model_engine import train_medium_model
from regime_engine import market_regime
from signal_engine import premarket_analysis


def _opportunity(signal: str, p_up: float, exp: float, quality: float, min_edge: float) -> float:
    s = str(signal).upper()
    if s in {"WAIT", "HOLD", "N/A"}:
        return 0.0
    directional_prob = p_up if "BUY" in s else 1 - p_up
    prob_edge = max(0.0, directional_prob - 0.5) * 2
    ret_edge = min(2.0, abs(exp) / max(min_edge, 1e-6)) / 2
    return float(np.clip(100 * prob_edge * ret_edge * (0.55 + 0.45 * quality), 0, 100))


def _scan_one(ticker: str, shared_regime: dict) -> dict:
    try:
        events = event_context(ticker)
        pre = premarket_analysis(ticker, events=events, regime=shared_regime)
        try:
            week = train_medium_model(ticker, "WEEK")
        except Exception as exc:
            week = {"signal": "N/A", "p_up": 0.5, "expected_return": 0.0, "quality_score": 0.0, "error": str(exc)}
        try:
            month = train_medium_model(ticker, "MONTH")
        except Exception as exc:
            month = {"signal": "N/A", "p_up": 0.5, "expected_return": 0.0, "quality_score": 0.0, "error": str(exc)}
        day_score = _opportunity(pre["signal"], pre["p_up"], pre["expected_return"], pre.get("quality_score", pre.get("confidence", 0.4)), DEFAULTS["day_min_edge"])
        week_score = _opportunity(week.get("signal", "N/A"), week.get("p_up", 0.5), week.get("expected_return", 0.0), week.get("quality_score", 0.0), DEFAULTS["week_min_edge"])
        month_score = _opportunity(month.get("signal", "N/A"), month.get("p_up", 0.5), month.get("expected_return", 0.0), month.get("quality_score", 0.0), DEFAULTS["month_min_edge"])
        return {
            "ticker": ticker,
            "DAY": pre["signal"], "DAY_prob": round(pre["p_up"] * 100, 1), "DAY_exp": round(pre["expected_return"] * 100, 2), "DAY_score": round(day_score, 1),
            "WEEK": week.get("signal", "N/A"), "WEEK_prob": round(week.get("p_up", 0.5) * 100, 1), "WEEK_exp": round(week.get("expected_return", 0.0) * 100, 2), "WEEK_score": round(week_score, 1),
            "MONTH": month.get("signal", "N/A"), "MONTH_prob": round(month.get("p_up", 0.5) * 100, 1), "MONTH_exp": round(month.get("expected_return", 0.0) * 100, 2), "MONTH_score": round(month_score, 1),
            "regime": shared_regime.get("regime", "UNKNOWN"),
            "event_risk": events.get("event_risk", "NORMAL"),
            "catalyst": events.get("catalyst", "NONE"),
            "news": round(events.get("news", {}).get("sentiment", 0.0), 3),
        }
    except Exception as exc:
        return {"ticker": ticker, "ERROR": str(exc)}


def scanner(universe: Iterable[str], max_assets: int = 10, workers: int | None = None) -> pd.DataFrame:
    tickers = list(dict.fromkeys([str(t).upper() for t in universe if str(t).strip()]))[: int(max_assets)]
    if not tickers:
        return pd.DataFrame()
    rows: List[dict] = []
    regime = market_regime()
    max_workers = min(int(workers or DEFAULTS["scanner_workers"]), max(1, len(tickers)))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_scan_one, t, regime): t for t in tickers}
        for fut in as_completed(futures):
            try:
                row = fut.result()
            except Exception as exc:
                row = {"ticker": futures[fut], "ERROR": str(exc)}
            if "ERROR" not in row:
                rows.append(row)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["BEST_score"] = df[["DAY_score", "WEEK_score", "MONTH_score"]].max(axis=1)
    return df.sort_values(["BEST_score", "DAY_score"], ascending=[False, False]).reset_index(drop=True)
