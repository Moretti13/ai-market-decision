from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import accuracy_score, brier_score_loss, mean_absolute_error

from config import DAY_FEATURES, DEFAULTS, MODEL_FEATURES
from data_layer import DataError, add_market_features, daily_features, fetch, safe_float
from market_clock import market_status


@dataclass
class ModelResult:
    horizon: str
    signal: str
    p_up: float
    p_down: float
    expected_return: float
    accuracy: float
    brier: float
    mae: float
    train_rows: int
    calibration_rows: int
    test_rows: int
    data_asof: str
    model_note: str


def _build_medium_frame(ticker: str, horizon_days: int) -> tuple[pd.DataFrame, pd.DataFrame, List[str]]:
    raw = fetch(ticker, "5y", "1d")
    d = daily_features(raw)
    if d.empty:
        raise DataError("Storico giornaliero insufficiente")
    d = add_market_features(d)
    features = [c for c in MODEL_FEATURES if c in d.columns]
    for c in features:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d["future_return"] = d["Close"].shift(-horizon_days) / d["Close"] - 1
    labeled = d.dropna(subset=features + ["future_return"]).copy()
    labeled["target"] = (labeled["future_return"] > 0).astype(int)
    latest = _latest_complete_row(d, ticker, features)
    return labeled, latest, features


def _latest_complete_row(d: pd.DataFrame, ticker: str, features: List[str]) -> pd.DataFrame:
    clean = d.dropna(subset=features).copy()
    if clean.empty:
        raise RuntimeError("Nessuna feature valida per la previsione")
    status = market_status(ticker)
    local_today = status["now"].date()
    local_dates = clean.index.tz_convert(status["timezone"]).date
    # During pre-market/regular session, the current daily candle is not complete.
    if status["is_pre"] or status["is_open"]:
        mask = local_dates < local_today
        if mask.any():
            return clean.loc[mask].iloc[[-1]].copy()
    return clean.iloc[[-1]].copy()


def _fit_models(train: pd.DataFrame, features: List[str]):
    clf = HistGradientBoostingClassifier(
        max_iter=260, learning_rate=0.04, max_depth=3, min_samples_leaf=12, l2_regularization=0.25, random_state=42
    )
    reg = HistGradientBoostingRegressor(
        max_iter=260, learning_rate=0.04, max_depth=3, min_samples_leaf=12, l2_regularization=0.25, random_state=42
    )
    clf.fit(train[features], train["target"])
    reg.fit(train[features], train["future_return"])
    return clf, reg


def _safe_split(n: int) -> tuple[int, int]:
    train_end = int(n * 0.70)
    calib_end = int(n * 0.85)
    train_end = max(train_end, 120)
    calib_end = max(calib_end, train_end + 25)
    calib_end = min(calib_end, n - 15)
    return train_end, calib_end


def train_medium_model(ticker: str, horizon: str) -> Dict:
    days = 5 if horizon == "WEEK" else 20
    labeled, latest, features = _build_medium_frame(ticker, days)
    if len(labeled) < 180:
        raise RuntimeError(f"Storico insufficiente per {horizon}: {len(labeled)} righe etichettate")
    train_end, calib_end = _safe_split(len(labeled))
    train = labeled.iloc[:train_end]
    calib = labeled.iloc[train_end:calib_end]
    test = labeled.iloc[calib_end:]
    if len(test) < 10:
        raise RuntimeError(f"Holdout troppo piccolo per {horizon}")

    clf, reg = _fit_models(train, features)
    p_calib = clf.predict_proba(calib[features])[:, 1]
    if np.unique(calib["target"]).size >= 2 and len(calib) >= 20:
        calibrator = IsotonicRegression(y_min=0.01, y_max=0.99, out_of_bounds="clip")
        calibrator.fit(p_calib, calib["target"].astype(float))
        calibrated_test = calibrator.transform(clf.predict_proba(test[features])[:, 1])
        calibrated_latest = float(calibrator.predict([clf.predict_proba(latest[features])[:, 1][0]])[0])
        note = "Probabilità calibrata su finestra temporale separata"
    else:
        calibrated_test = clf.predict_proba(test[features])[:, 1]
        calibrated_latest = float(clf.predict_proba(latest[features])[:, 1][0])
        note = "Probabilità non calibrata: fallback"

    pred_class = (calibrated_test >= 0.5).astype(int)
    accuracy = safe_float(accuracy_score(test["target"], pred_class), 0.5)
    brier = safe_float(brier_score_loss(test["target"], calibrated_test), 0.25)
    test_pred_ret = reg.predict(test[features])
    mae = safe_float(mean_absolute_error(test["future_return"], test_pred_ret), 0.10)
    expected = float(reg.predict(latest[features])[0])

    min_edge = DEFAULTS["week_min_edge"] if horizon == "WEEK" else DEFAULTS["month_min_edge"]
    if calibrated_latest >= DEFAULTS["buy_prob"] and expected >= min_edge:
        signal = "BUY"
    elif calibrated_latest <= DEFAULTS["sell_prob"] and expected <= -min_edge:
        signal = "SELL"
    else:
        signal = "HOLD"

    return ModelResult(
        horizon=horizon,
        signal=signal,
        p_up=float(calibrated_latest),
        p_down=float(1 - calibrated_latest),
        expected_return=expected,
        accuracy=accuracy,
        brier=brier,
        mae=mae,
        train_rows=len(train),
        calibration_rows=len(calib),
        test_rows=len(test),
        data_asof=str(latest.index[-1]),
        model_note=note,
    ).__dict__


def _day_frame(ticker: str) -> tuple[pd.DataFrame, pd.DataFrame, List[str]]:
    raw = fetch(ticker, "5y", "1d")
    d = daily_features(raw)
    if d.empty:
        raise DataError("Storico giornaliero insufficiente")
    d = add_market_features(d)
    d["gap"] = pd.to_numeric(d["gap"], errors="coerce")
    # Predict today's open->close from information known before today's open.
    # Shift predictors by one session and use today's observed gap as a separate input.
    base = d.copy()
    base["future_return"] = d["Close"] / d["Open"] - 1
    lag = d.shift(1)
    for col in MODEL_FEATURES:
        base[col] = lag[col]
    # For each historical day the gap is known at the open; in live pre-market we use indicative gap.
    features = [c for c in DAY_FEATURES if c in base.columns]
    labeled = base.dropna(subset=features + ["future_return"]).copy()
    labeled["target"] = (labeled["future_return"] > 0).astype(int)

    latest = _latest_complete_row(d, ticker, MODEL_FEATURES + ["gap"])
    # Live gap is overridden by caller; default is the most recent completed session gap.
    latest["gap"] = safe_float(latest["gap"].iloc[0], 0.0)
    return labeled, latest, features


def train_day_model(ticker: str, live_gap: float | None = None) -> Dict:
    labeled, latest, features = _day_frame(ticker)
    if len(labeled) < 180:
        raise RuntimeError(f"Storico insufficiente per DAY: {len(labeled)} righe etichettate")
    if live_gap is not None:
        latest["gap"] = float(live_gap)

    train_end, calib_end = _safe_split(len(labeled))
    train = labeled.iloc[:train_end]
    calib = labeled.iloc[train_end:calib_end]
    test = labeled.iloc[calib_end:]
    clf, reg = _fit_models(train, features)

    p_calib = clf.predict_proba(calib[features])[:, 1]
    if np.unique(calib["target"]).size >= 2 and len(calib) >= 20:
        calibrator = IsotonicRegression(y_min=0.01, y_max=0.99, out_of_bounds="clip")
        calibrator.fit(p_calib, calib["target"].astype(float))
        p_test = calibrator.transform(clf.predict_proba(test[features])[:, 1])
        latest_raw = clf.predict_proba(latest[features])[:, 1][0]
        p_latest = float(calibrator.predict([latest_raw])[0])
        note = "DAY: previsione ML open→close + gap, calibrata temporalmente"
    else:
        p_test = clf.predict_proba(test[features])[:, 1]
        p_latest = float(clf.predict_proba(latest[features])[:, 1][0])
        note = "DAY: fallback senza calibrazione"

    class_pred = (p_test >= 0.5).astype(int)
    accuracy = safe_float(accuracy_score(test["target"], class_pred), 0.5)
    brier = safe_float(brier_score_loss(test["target"], p_test), 0.25)
    expected = float(reg.predict(latest[features])[0])
    mae = safe_float(mean_absolute_error(test["future_return"], reg.predict(test[features])), 0.10)
    signal = "PRE-BUY" if p_latest >= DEFAULTS["buy_prob"] and expected >= DEFAULTS["day_min_edge"] else "PRE-SELL" if p_latest <= DEFAULTS["sell_prob"] and expected <= -DEFAULTS["day_min_edge"] else "WAIT"
    return {
        "horizon": "DAY",
        "signal": signal,
        "p_up": p_latest,
        "p_down": 1 - p_latest,
        "expected_return": expected,
        "accuracy": accuracy,
        "brier": brier,
        "mae": mae,
        "train_rows": len(train),
        "calibration_rows": len(calib),
        "test_rows": len(test),
        "data_asof": str(latest.index[-1]),
        "model_note": note,
    }


def walk_forward_backtest(ticker: str, horizon: str = "WEEK", max_folds: int = 60) -> Dict:
    days = 5 if horizon == "WEEK" else 20
    raw = fetch(ticker, "5y", "1d")
    d = add_market_features(daily_features(raw))
    features = [c for c in MODEL_FEATURES if c in d.columns]
    d["future_return"] = d["Close"].shift(-days) / d["Close"] - 1
    frame = d.dropna(subset=features + ["future_return"]).copy()
    if len(frame) < 220:
        raise RuntimeError("Storico insufficiente per walk-forward")

    start = max(180, len(frame) - max_folds)
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    trades = []
    round_trip_cost = (DEFAULTS["slippage_bps"] + DEFAULTS["commission_bps"]) * 2 / 10000.0
    for i in range(start, len(frame)):
        train = frame.iloc[:i]
        if train["future_return"].nunique() < 2:
            continue
        clf, reg = _fit_models(train, features)
        row = frame.iloc[[i]]
        p = float(clf.predict_proba(row[features])[:, 1][0])
        exp = float(reg.predict(row[features])[0])
        direction = 1 if p >= DEFAULTS["buy_prob"] and exp > 0 else -1 if p <= DEFAULTS["sell_prob"] and exp < 0 else 0
        actual = float(row["future_return"].iloc[0])
        if direction:
            ret = direction * actual - round_trip_cost
            equity *= max(0.0, 1 + ret)
            trades.append(ret)
            correct += int(ret > 0)
            total += 1
        peak = max(peak, equity)
        max_dd = max(max_dd, 1 - equity / peak)

    vals = np.asarray(trades, dtype=float)
    if len(vals):
        avg = float(vals.mean()); std = float(vals.std(ddof=1)) if len(vals) > 1 else 0.0
        sharpe = (avg / std * np.sqrt(252 / max(days, 1))) if std > 0 else 0.0
        win_rate = float((vals > 0).mean())
        gross_profit = float(vals[vals > 0].sum())
        gross_loss = float(-vals[vals < 0].sum())
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    else:
        sharpe = win_rate = 0.0
        profit_factor = 0.0

    return {
        "ticker": ticker,
        "horizon": horizon,
        "folds": len(trades),
        "final_equity": equity,
        "cumulative_return": equity - 1,
        "max_drawdown": max_dd,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "sharpe_like": sharpe,
        "as_of": str(frame.index[-1]),
        "note": f"Walk-forward storico con costi round-trip stimati {round_trip_cost*10000:.1f} bps; non sostituisce un backtest con microstruttura reale.",
    }
