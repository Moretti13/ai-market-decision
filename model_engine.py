from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List
import os
import tempfile
import threading
import time as time_module

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import accuracy_score, brier_score_loss, mean_absolute_error, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from config import DEFAULTS, HORIZON_DAYS, MODEL_FEATURES
from data_layer import DataError, add_market_features, daily_features, fetch, safe_float
from market_clock import market_status


@dataclass
class EnsembleBundle:
    clf_models: list
    reg_models: list
    medians: pd.Series


@dataclass
class ModelArtifact:
    bundle: EnsembleBundle
    calibrator: object | None
    metrics: Dict
    quality_score: float
    features: List[str]
    latest: pd.DataFrame
    data_asof: str
    trained_at: str
    purge_days: int
    schema: str


_MODEL_CACHE_SCHEMA = "7.1.0-perf-1"
_MODEL_ARTIFACT_CACHE: dict[tuple[str, str], ModelArtifact] = {}
_MODEL_LOCK = threading.RLock()
_ASOF_CACHE: dict[str, tuple[float, str | None]] = {}
_POST_DAILY_REFRESHED: set[tuple[str, str]] = set()
_MODEL_CACHE_DIR = Path(os.getenv("MODEL_CACHE_DIR", str(Path(tempfile.gettempdir()) / "ai_market_decision_v71_models")))


def _complete_features(d: pd.DataFrame, features: List[str]) -> List[str]:
    usable = [c for c in features if c in d.columns]
    if len(usable) < max(8, int(len(features) * 0.55)):
        raise RuntimeError(f"Feature insufficienti: {len(usable)}/{len(features)}")
    return usable


def _latest_complete_row(d: pd.DataFrame, ticker: str, features: List[str]) -> pd.DataFrame:
    clean = d.dropna(subset=features).copy()
    if clean.empty:
        raise RuntimeError("Nessuna feature valida per la previsione")
    status = market_status(ticker)
    today = status["now"].date()
    dates = clean.index.date
    if status["is_pre"] or status["is_open"]:
        mask = dates < today
        if mask.any():
            return clean.loc[mask].iloc[[-1]].copy()
    return clean.iloc[[-1]].copy()


def _fill(train: pd.DataFrame, features: List[str], medians: pd.Series | None = None) -> tuple[pd.DataFrame, pd.Series]:
    x = train[features].apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan)
    meds = medians if medians is not None else x.median(numeric_only=True).fillna(0.0)
    return x.fillna(meds).fillna(0.0), meds


def _fit_ensemble(train: pd.DataFrame, features: List[str]) -> EnsembleBundle:
    x, medians = _fill(train, features)
    y = train["target"].astype(int)
    r = train["future_return"].astype(float)
    if y.nunique() < 2:
        raise RuntimeError("Target storico con una sola classe")

    # The ensemble remains nonlinear + linear, but iterations are capped to keep
    # Streamlit Community Cloud CPU usage sustainable. The models are persisted and
    # retrained only when a new completed daily bar is available.
    hgb_c = HistGradientBoostingClassifier(
        max_iter=120, learning_rate=0.055, max_depth=3, min_samples_leaf=12,
        l2_regularization=0.30, random_state=42,
    )
    log_c = make_pipeline(
        StandardScaler(),
        LogisticRegression(max_iter=600, C=0.45, class_weight="balanced", random_state=42),
    )
    hgb_r = HistGradientBoostingRegressor(
        max_iter=120, learning_rate=0.055, max_depth=3, min_samples_leaf=12,
        l2_regularization=0.30, random_state=42,
    )
    ridge_r = make_pipeline(StandardScaler(), Ridge(alpha=8.0))
    hgb_c.fit(x, y)
    log_c.fit(x, y)
    hgb_r.fit(x, r)
    ridge_r.fit(x, r)
    return EnsembleBundle([hgb_c, log_c], [hgb_r, ridge_r], medians)


def _predict_bundle(bundle: EnsembleBundle, frame: pd.DataFrame, features: List[str]) -> tuple[np.ndarray, np.ndarray]:
    x, _ = _fill(frame, features, bundle.medians)
    probs = np.vstack([m.predict_proba(x)[:, 1] for m in bundle.clf_models])
    rets = np.vstack([m.predict(x) for m in bundle.reg_models])
    return probs.mean(axis=0), rets.mean(axis=0)


def _safe_split(n: int) -> tuple[int, int]:
    train_end = max(160, int(n * 0.68))
    calib_end = max(train_end + 30, int(n * 0.84))
    calib_end = min(calib_end, n - 20)
    if train_end >= calib_end or calib_end >= n:
        raise RuntimeError("Storico insufficiente per split temporale train/calibration/test")
    return train_end, calib_end


def _fit_calibrator(calib_y: pd.Series, calib_p: np.ndarray) -> object | None:
    if len(calib_y) >= 25 and calib_y.nunique() >= 2:
        iso = IsotonicRegression(y_min=0.01, y_max=0.99, out_of_bounds="clip")
        iso.fit(calib_p, calib_y.astype(float))
        return iso
    return None


def _apply_calibrator(calibrator: object | None, values: np.ndarray) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    if calibrator is None:
        return arr
    return np.asarray(calibrator.transform(arr), dtype=float)


def _calibrate(calib_y: pd.Series, calib_p: np.ndarray, test_p: np.ndarray, latest_p: float):
    calibrator = _fit_calibrator(calib_y, calib_p)
    if calibrator is not None:
        return _apply_calibrator(calibrator, test_p), float(_apply_calibrator(calibrator, np.asarray([latest_p]))[0]), "isotonic"
    return test_p, float(latest_p), "raw"


def _metrics(test: pd.DataFrame, p_test: np.ndarray, ret_test: np.ndarray) -> Dict:
    y = test["target"].astype(int).to_numpy()
    pred = (p_test >= 0.5).astype(int)
    accuracy = safe_float(accuracy_score(y, pred), 0.5)
    brier = safe_float(brier_score_loss(y, p_test), 0.25)
    mae = safe_float(mean_absolute_error(test["future_return"], ret_test), 0.10)
    auc = safe_float(roc_auc_score(y, p_test), 0.5) if len(np.unique(y)) >= 2 else 0.5
    climatology = float(np.mean(y)) if len(y) else 0.5
    baseline_brier = float(np.mean((y - climatology) ** 2)) if len(y) else 0.25
    brier_skill = 1.0 - brier / max(baseline_brier, 1e-9)
    return {
        "accuracy": accuracy,
        "brier": brier,
        "mae": mae,
        "auc": auc,
        "brier_skill": float(np.clip(brier_skill, -2.0, 1.0)),
    }


def _quality(metrics: Dict) -> float:
    auc_component = np.clip((safe_float(metrics.get("auc"), 0.5) - 0.5) / 0.20, 0, 1)
    brier_component = np.clip((safe_float(metrics.get("brier_skill"), 0.0) + 0.2) / 0.7, 0, 1)
    acc_component = np.clip((safe_float(metrics.get("accuracy"), 0.5) - 0.45) / 0.20, 0, 1)
    return float(np.clip(0.4 * auc_component + 0.4 * brier_component + 0.2 * acc_component, 0, 1))


def _historical_daily(ticker: str) -> pd.DataFrame:
    """Return daily history with the current incomplete session removed.

    After the close, force one fresh 7y download per ticker/date so a daily bar
    cached during the open cannot be mistaken for a completed close.
    """
    status = market_status(ticker)
    refresh_key = (ticker.upper(), str(status["now"].date()))
    force = bool(status.get("is_post")) and refresh_key not in _POST_DAILY_REFRESHED
    raw = fetch(ticker, "7y", "1d", force=force)
    if force:
        _POST_DAILY_REFRESHED.add(refresh_key)
    if raw.empty:
        return raw
    if status["is_pre"] or status["is_open"]:
        mask = np.asarray(raw.index.date) < status["now"].date()
        raw = raw.loc[mask] if mask.any() else raw.iloc[0:0]
    return raw


def _build_medium_frame(ticker: str, horizon_days: int) -> tuple[pd.DataFrame, pd.DataFrame, List[str]]:
    raw = _historical_daily(ticker)
    d = add_market_features(daily_features(raw))
    if d.empty:
        raise DataError("Storico giornaliero insufficiente")
    features = _complete_features(d, MODEL_FEATURES)
    d["future_return"] = d["Close"].shift(-horizon_days) / d["Close"] - 1
    labeled = d.dropna(subset=features + ["future_return"]).copy()
    labeled["target"] = (labeled["future_return"] > 0).astype(int)
    latest = _latest_complete_row(d, ticker, features)
    return labeled, latest, features


def _day_frame(ticker: str) -> tuple[pd.DataFrame, pd.DataFrame, List[str]]:
    raw = _historical_daily(ticker)
    d = add_market_features(daily_features(raw))
    if d.empty:
        raise DataError("Storico giornaliero insufficiente")
    model_features = _complete_features(d, MODEL_FEATURES)
    base = d.copy()
    base["future_return"] = d["Close"] / d["Open"] - 1
    lag = d.shift(1)
    for col in model_features:
        base[col] = lag[col]
    base["gap"] = d["Open"] / d["Close"].shift(1) - 1
    features = model_features + ["gap"]
    labeled = base.dropna(subset=features + ["future_return"]).copy()
    labeled["target"] = (labeled["future_return"] > 0).astype(int)
    latest = _latest_complete_row(d, ticker, model_features).copy()
    latest["gap"] = 0.0
    return labeled, latest, features


def _fit_artifact(labeled: pd.DataFrame, latest: pd.DataFrame, features: List[str], purge_days: int = 0) -> ModelArtifact:
    if len(labeled) < 220:
        raise RuntimeError(f"Storico insufficiente: {len(labeled)} righe etichettate")
    train_end, calib_end = _safe_split(len(labeled))
    purge_days = max(0, int(purge_days))

    eval_train_end = max(160, train_end - purge_days)
    eval_calib_end = max(train_end + 20, calib_end - purge_days)
    train = labeled.iloc[:eval_train_end]
    calib = labeled.iloc[train_end:eval_calib_end]
    test = labeled.iloc[calib_end:]
    if len(calib) < 20 or len(test) < 20:
        raise RuntimeError("Finestre calibration/test insufficienti dopo il purge temporale")

    eval_bundle = _fit_ensemble(train, features)
    p_calib, _ = _predict_bundle(eval_bundle, calib, features)
    p_test_raw, ret_test = _predict_bundle(eval_bundle, test, features)
    eval_calibrator = _fit_calibrator(calib["target"], p_calib)
    p_test = _apply_calibrator(eval_calibrator, p_test_raw)
    metrics = _metrics(test, p_test, ret_test)

    prod_calib_size = max(40, int(len(labeled) * 0.15))
    prod_calib_start = len(labeled) - prod_calib_size
    prod_train_end = max(160, prod_calib_start - purge_days)
    prod_train = labeled.iloc[:prod_train_end]
    prod_calib = labeled.iloc[prod_calib_start:]
    prod_bundle = _fit_ensemble(prod_train, features)
    prod_p_calib, _ = _predict_bundle(prod_bundle, prod_calib, features)
    prod_calibrator = _fit_calibrator(prod_calib["target"], prod_p_calib)

    return ModelArtifact(
        bundle=prod_bundle,
        calibrator=prod_calibrator,
        metrics={
            **metrics,
            "train_rows": len(prod_train),
            "calibration_rows": len(prod_calib),
            "test_rows": len(test),
            "calibration": "isotonic" if prod_calibrator is not None else "raw",
            "evaluation_calibration": "isotonic" if eval_calibrator is not None else "raw",
        },
        quality_score=_quality(metrics),
        features=list(features),
        latest=latest.copy(),
        data_asof=str(latest.index[-1]),
        trained_at=datetime.now(timezone.utc).isoformat(),
        purge_days=purge_days,
        schema=_MODEL_CACHE_SCHEMA,
    )


def _infer_artifact(artifact: ModelArtifact, latest: pd.DataFrame | None = None) -> Dict:
    frame = artifact.latest.copy() if latest is None else latest.copy()
    p_raw, ret = _predict_bundle(artifact.bundle, frame, artifact.features)
    p = float(_apply_calibrator(artifact.calibrator, np.asarray([float(p_raw[0])]))[0])
    return {
        "p_up": float(np.clip(p, 0.01, 0.99)),
        "p_down": float(np.clip(1 - p, 0.01, 0.99)),
        "expected_return": float(ret[0]),
        **artifact.metrics,
        "quality_score": artifact.quality_score,
        "purge_days": artifact.purge_days,
    }


def _train_predict(labeled: pd.DataFrame, latest: pd.DataFrame, features: List[str], purge_days: int = 0) -> Dict:
    artifact = _fit_artifact(labeled, latest, features, purge_days=purge_days)
    return _infer_artifact(artifact, latest)


def _safe_ticker_name(ticker: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in ticker.upper())


def _artifact_path(ticker: str, horizon: str) -> Path:
    return _MODEL_CACHE_DIR / f"{_safe_ticker_name(ticker)}__{horizon.upper()}.joblib"


def _artifact_matches_asof(artifact: ModelArtifact, asof: str | None) -> bool:
    if artifact.schema != _MODEL_CACHE_SCHEMA:
        return False
    if not asof:
        return True
    try:
        return str(pd.Timestamp(artifact.data_asof).date()) == str(pd.Timestamp(asof).date())
    except Exception:
        return artifact.data_asof == asof


def _current_completed_asof(ticker: str) -> str | None:
    """Cheap freshness check that never promotes an incomplete daily bar."""
    ticker = ticker.upper()
    now_s = time_module.time()
    cached = _ASOF_CACHE.get(ticker)
    if cached and now_s - cached[0] < 900:
        return cached[1]
    try:
        status = market_status(ticker)
        raw = fetch(ticker, "6mo", "1d", force=bool(status.get("is_post")))
        if raw.empty:
            return None
        d = raw.copy()
        if status["is_pre"] or status["is_open"]:
            mask = np.asarray(d.index.date) < status["now"].date()
            d = d.loc[mask] if mask.any() else d.iloc[0:0]
        asof = str(d.index[-1]) if not d.empty else None
        _ASOF_CACHE[ticker] = (now_s, asof)
        return asof
    except Exception:
        return None


def _load_disk_artifact(ticker: str, horizon: str, asof: str | None) -> ModelArtifact | None:
    path = _artifact_path(ticker, horizon)
    if not path.exists():
        return None
    try:
        artifact = joblib.load(path)
        if isinstance(artifact, ModelArtifact) and _artifact_matches_asof(artifact, asof):
            return artifact
    except Exception:
        return None
    return None


def _save_disk_artifact(ticker: str, horizon: str, artifact: ModelArtifact) -> None:
    try:
        _MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _artifact_path(ticker, horizon)
        tmp = path.with_suffix(".tmp")
        joblib.dump(artifact, tmp, compress=1)
        os.replace(tmp, path)
    except Exception:
        # Disk persistence is an optimisation. Never break analysis if the host
        # filesystem is read-only or ephemeral.
        pass


def _get_artifact(ticker: str, horizon: str) -> tuple[ModelArtifact, str]:
    ticker = ticker.upper()
    horizon = horizon.upper()
    key = (ticker, horizon)
    asof = _current_completed_asof(ticker)

    cached = _MODEL_ARTIFACT_CACHE.get(key)
    if cached is not None and _artifact_matches_asof(cached, asof):
        return cached, "MEMORY"

    disk = _load_disk_artifact(ticker, horizon, asof)
    if disk is not None:
        _MODEL_ARTIFACT_CACHE[key] = disk
        return disk, "DISK"

    with _MODEL_LOCK:
        # Re-check after waiting: scanner or another rerun may have trained it.
        cached = _MODEL_ARTIFACT_CACHE.get(key)
        if cached is not None and _artifact_matches_asof(cached, asof):
            return cached, "MEMORY"
        disk = _load_disk_artifact(ticker, horizon, asof)
        if disk is not None:
            _MODEL_ARTIFACT_CACHE[key] = disk
            return disk, "DISK"

        if horizon == "DAY":
            labeled, latest, features = _day_frame(ticker)
            purge_days = 0
        elif horizon in {"WEEK", "MONTH"}:
            days = HORIZON_DAYS[horizon]
            labeled, latest, features = _build_medium_frame(ticker, days)
            purge_days = days
        else:
            raise ValueError("Orizzonte non valido")

        artifact = _fit_artifact(labeled, latest, features, purge_days=purge_days)
        _MODEL_ARTIFACT_CACHE[key] = artifact
        _save_disk_artifact(ticker, horizon, artifact)
        return artifact, "TRAINED"


def clear_model_cache(ticker: str | None = None) -> int:
    """Clear persisted/in-memory model artifacts. Returns number of disk files removed."""
    removed = 0
    with _MODEL_LOCK:
        if ticker:
            t = ticker.upper()
            _ASOF_CACHE.pop(t, None)
            for k in [x for x in _POST_DAILY_REFRESHED if x[0] == t]:
                _POST_DAILY_REFRESHED.discard(k)
            for key in [k for k in _MODEL_ARTIFACT_CACHE if k[0] == t]:
                _MODEL_ARTIFACT_CACHE.pop(key, None)
        else:
            _MODEL_ARTIFACT_CACHE.clear()
            _ASOF_CACHE.clear()
            _POST_DAILY_REFRESHED.clear()
        try:
            if _MODEL_CACHE_DIR.exists():
                pattern = f"{_safe_ticker_name(ticker)}__*.joblib" if ticker else "*.joblib"
                for p in _MODEL_CACHE_DIR.glob(pattern):
                    try:
                        p.unlink()
                        removed += 1
                    except Exception:
                        pass
        except Exception:
            pass
    return removed


def train_medium_model(ticker: str, horizon: str) -> Dict:
    horizon = horizon.upper()
    ticker = ticker.upper()
    if horizon not in {"WEEK", "MONTH"}:
        raise ValueError("horizon deve essere WEEK o MONTH")
    artifact, cache_source = _get_artifact(ticker, horizon)
    out = _infer_artifact(artifact)
    min_edge = DEFAULTS["week_min_edge"] if horizon == "WEEK" else DEFAULTS["month_min_edge"]
    p, exp = out["p_up"], out["expected_return"]
    if p >= DEFAULTS["buy_prob"] and exp >= min_edge:
        signal = "BUY"
    elif p <= DEFAULTS["sell_prob"] and exp <= -min_edge:
        signal = "SELL"
    else:
        signal = "HOLD"
    return {
        "horizon": horizon,
        "signal": signal,
        **out,
        "data_asof": artifact.data_asof,
        "features": len(artifact.features),
        "trained_at": artifact.trained_at,
        "cache_source": cache_source,
        "model_note": "Ensemble temporale persistente: retraining solo con nuova barra daily completata; inference riutilizzata durante la sessione.",
    }


def train_day_model(ticker: str, live_gap: float | None = None) -> Dict:
    ticker = ticker.upper()
    artifact, cache_source = _get_artifact(ticker, "DAY")
    latest = artifact.latest.copy()
    latest["gap"] = float(live_gap or 0.0)
    out = _infer_artifact(artifact, latest)
    p, exp = out["p_up"], out["expected_return"]
    if p >= DEFAULTS["buy_prob"] and exp >= DEFAULTS["day_min_edge"]:
        signal = "PRE-BUY"
    elif p <= DEFAULTS["sell_prob"] and exp <= -DEFAULTS["day_min_edge"]:
        signal = "PRE-SELL"
    else:
        signal = "WAIT"
    return {
        "horizon": "DAY",
        "signal": signal,
        **out,
        "data_asof": artifact.data_asof,
        "features": len(artifact.features),
        "trained_at": artifact.trained_at,
        "cache_source": cache_source,
        "model_note": "DAY open→close: modello persistente sullo storico completato; gap e dati intraday aggiornano la decisione senza retraining continuo.",
    }


def _fit_fast(train: pd.DataFrame, features: List[str]):
    x, meds = _fill(train, features)
    y = train["target"].astype(int)
    if y.nunique() < 2:
        return None, None, meds
    clf = HistGradientBoostingClassifier(max_iter=80, learning_rate=0.065, max_depth=3, min_samples_leaf=12, random_state=7)
    reg = HistGradientBoostingRegressor(max_iter=80, learning_rate=0.065, max_depth=3, min_samples_leaf=12, random_state=7)
    clf.fit(x, y)
    reg.fit(x, train["future_return"].astype(float))
    return clf, reg, meds


def _backtest_frame(frame: pd.DataFrame, features: List[str], horizon: str, max_folds: int = 80) -> Dict:
    if len(frame) < 240:
        raise RuntimeError("Storico insufficiente per walk-forward")
    days = 1 if horizon == "DAY" else HORIZON_DAYS[horizon]
    step = max(1, days)
    start = max(200, len(frame) - max_folds * step)
    indices = list(range(start, len(frame), step))[-max_folds:]
    round_trip_cost = (DEFAULTS["slippage_bps"] + DEFAULTS["commission_bps"]) * 2 / 10000.0
    trades: List[float] = []
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for i in indices:
        train_stop = i if horizon == "DAY" else i - days + 1
        if train_stop < 180:
            continue
        train = frame.iloc[:train_stop]
        clf, reg, meds = _fit_fast(train, features)
        if clf is None:
            continue
        x, _ = _fill(frame.iloc[[i]], features, meds)
        p = float(clf.predict_proba(x)[:, 1][0])
        exp = float(reg.predict(x)[0])
        min_edge = DEFAULTS["day_min_edge"] if horizon == "DAY" else DEFAULTS["week_min_edge"] if horizon == "WEEK" else DEFAULTS["month_min_edge"]
        direction = 1 if p >= DEFAULTS["buy_prob"] and exp >= min_edge else -1 if p <= DEFAULTS["sell_prob"] and exp <= -min_edge else 0
        if direction == 0:
            continue
        actual = float(frame["future_return"].iloc[i])
        net = direction * actual - round_trip_cost
        trades.append(net)
        equity *= max(0.01, 1 + net)
        peak = max(peak, equity)
        max_dd = max(max_dd, 1 - equity / peak)
    vals = np.asarray(trades, dtype=float)
    if len(vals):
        win_rate = float((vals > 0).mean())
        gross_profit = float(vals[vals > 0].sum())
        gross_loss = float(-vals[vals < 0].sum())
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        std = float(vals.std(ddof=1)) if len(vals) > 1 else 0.0
        sharpe_like = float(vals.mean() / std * np.sqrt(252 / max(days, 1))) if std > 0 else 0.0
        avg_trade = float(vals.mean())
    else:
        win_rate = profit_factor = sharpe_like = avg_trade = 0.0
    return {
        "horizon": horizon,
        "folds_tested": len(indices),
        "trades": len(vals),
        "signal_rate": len(vals) / max(len(indices), 1),
        "final_equity": equity,
        "cumulative_return": equity - 1,
        "max_drawdown": max_dd,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "sharpe_like": sharpe_like,
        "avg_trade": avg_trade,
        "round_trip_cost_bps": round_trip_cost * 10000,
    }


def walk_forward_backtest(ticker: str, horizon: str = "WEEK", max_folds: int = 80) -> Dict:
    horizon = horizon.upper()
    if horizon == "DAY":
        frame, _, features = _day_frame(ticker)
    elif horizon in {"WEEK", "MONTH"}:
        frame, _, features = _build_medium_frame(ticker, HORIZON_DAYS[horizon])
    else:
        raise ValueError("Orizzonte non valido")
    result = _backtest_frame(frame, features, horizon, max_folds=max_folds)
    result.update({
        "ticker": ticker.upper(),
        "as_of": str(frame.index[-1]),
        "note": "Walk-forward non sovrapposto per WEEK/MONTH, costi inclusi. È una diagnostica storica, non una promessa di rendimento.",
    })
    return result
