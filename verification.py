from __future__ import annotations

from datetime import date
from typing import Dict

import numpy as np
import pandas as pd

from config import APP_VERSION, DEFAULTS, HORIZON_DAYS
from data_layer import fetch, safe_float
from db import log_prediction_once, mark_prediction_evaluated, unresolved_predictions
from market_clock import market_status, next_session_date


def _parse_date(value) -> date:
    return pd.to_datetime(value, utc=True, errors="coerce").date()


def _target_date_for(ticker: str, horizon: str, data_asof: str | None, clock: dict) -> date:
    horizon = horizon.upper()
    if horizon == "DAY":
        if clock["is_pre"] or clock["is_open"]:
            return clock["now"].date()
        return next_session_date(ticker, clock["now"].date(), 1)
    base = _parse_date(data_asof) if data_asof else clock["now"].date()
    return next_session_date(ticker, base, HORIZON_DAYS[horizon])


def store_analysis_predictions(analysis: Dict) -> int:
    ticker = analysis["ticker"]
    clock = analysis["clock"]
    reference_price = safe_float(
        analysis.get("confirm", {}).get("current"),
        safe_float(analysis.get("pre", {}).get("indicative"), 0.0),
    )
    stored = 0
    rows = [
        ("DAY", analysis["pre"], "OPEN_CLOSE"),
        ("WEEK", analysis["week"], "CLOSE_CLOSE"),
        ("MONTH", analysis["month"], "CLOSE_CLOSE"),
    ]
    for horizon, model, target_type in rows:
        if model.get("signal") in {None, "N/A"}:
            continue
        data_asof = model.get("data_asof") or analysis.get("pre", {}).get("model_data_asof")
        target_date = _target_date_for(ticker, horizon, data_asof, clock)
        event_key = f"V7|{ticker}|{horizon}|{target_date.isoformat()}"
        if log_prediction_once(
            event_key=event_key,
            ticker=ticker,
            horizon=horizon,
            signal=model.get("signal", "WAIT"),
            p_up=safe_float(model.get("p_up"), 0.5),
            p_down=safe_float(model.get("p_down"), 0.5),
            expected_return=safe_float(model.get("expected_return"), 0.0),
            reference_price=reference_price,
            data_asof=str(data_asof or ""),
            prediction_date=clock["now"].date().isoformat(),
            target_date=target_date.isoformat(),
            target_days=HORIZON_DAYS[horizon],
            target_type=target_type,
            quality_score=safe_float(model.get("quality_score"), safe_float(model.get("confidence"), 0.0)),
            model_version=APP_VERSION,
        ):
            stored += 1
    return stored


def _signal_correct(signal: str, actual: float, horizon: str) -> int:
    s = str(signal).upper()
    threshold = DEFAULTS["day_min_edge"] if horizon == "DAY" else DEFAULTS["week_min_edge"] if horizon == "WEEK" else DEFAULTS["month_min_edge"]
    if "BUY" in s:
        return int(actual > 0)
    if "SELL" in s:
        return int(actual < 0)
    return int(abs(actual) < threshold)


def _evaluate_one(row: pd.Series, raw: pd.DataFrame | None = None) -> tuple[bool, float, int, int]:
    ticker = str(row["ticker"])
    horizon = str(row["horizon"]).upper()
    raw = raw if raw is not None else fetch(ticker, "7y", "1d", force=True)
    if raw.empty:
        return False, 0.0, 0, 0
    dates = np.array(raw.index.date)
    if horizon == "DAY":
        target = pd.to_datetime(row["target_date"]).date()
        status = market_status(ticker)
        # Never score a target session before its regular close; daily bars can be partial intraday.
        if target == status["now"].date() and status["is_session_day"] and status["now"].timetz().replace(tzinfo=None) < status["close"]:
            return False, 0.0, 0, 0
        matches = np.where(dates == target)[0]
        if not len(matches):
            return False, 0.0, 0, 0
        i = int(matches[0])
        op = safe_float(raw["Open"].iloc[i])
        cl = safe_float(raw["Close"].iloc[i])
        if op <= 0:
            return False, 0.0, 0, 0
        actual = cl / op - 1
    else:
        asof = pd.to_datetime(row["data_asof"], utc=True, errors="coerce")
        if pd.isna(asof):
            return False, 0.0, 0, 0
        base_date = asof.date()
        base_matches = np.where(dates == base_date)[0]
        if not len(base_matches):
            return False, 0.0, 0, 0
        i0 = int(base_matches[-1])
        i1 = i0 + int(row["target_days"])
        if i1 >= len(raw):
            return False, 0.0, 0, 0
        target_date = dates[i1]
        status = market_status(ticker)
        if target_date == status["now"].date() and status["is_session_day"] and status["now"].timetz().replace(tzinfo=None) < status["close"]:
            return False, 0.0, 0, 0
        base = safe_float(raw["Close"].iloc[i0])
        end = safe_float(raw["Close"].iloc[i1])
        if base <= 0:
            return False, 0.0, 0, 0
        actual = end / base - 1
    direction = 1 if actual > 0 else -1 if actual < 0 else 0
    correct = _signal_correct(row["signal"], actual, horizon)
    return True, float(actual), direction, correct


def verify_matured_predictions(limit: int | None = None) -> Dict:
    limit = int(limit or DEFAULTS["prediction_verify_limit"])
    pending = unresolved_predictions(limit)
    checked = evaluated = 0
    errors = []
    raw_cache: dict[str, pd.DataFrame] = {}
    for _, row in pending.iterrows():
        checked += 1
        ticker = str(row.get("ticker"))
        try:
            if ticker not in raw_cache:
                raw_cache[ticker] = fetch(ticker, "7y", "1d", force=True)
            ready, actual, direction, correct = _evaluate_one(row, raw=raw_cache[ticker])
            if ready:
                mark_prediction_evaluated(int(row["id"]), actual, direction, correct)
                evaluated += 1
        except Exception as exc:
            errors.append(f"{ticker} {row.get('horizon')}: {exc}")
    return {"checked": checked, "evaluated": evaluated, "errors": errors[:10]}
