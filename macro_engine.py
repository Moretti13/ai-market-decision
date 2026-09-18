from __future__ import annotations

import io
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict

import numpy as np
import pandas as pd

try:
    import requests
except Exception:
    requests = None

from data_layer import daily_features, fetch, safe_float

_FRED_CACHE: dict[str, tuple[float, dict]] = {}
_MACRO_CACHE: tuple[float, dict] | None = None


def _fred_series(series_id: str) -> Dict:
    now = time.time()
    if series_id in _FRED_CACHE and now - _FRED_CACHE[series_id][0] < 21600:
        return _FRED_CACHE[series_id][1]
    result = {"value": None, "previous": None, "date": None}
    if requests is None:
        return result
    try:
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
        r = requests.get(url, timeout=3.5, headers={"User-Agent": "AI-Market-Decision/7"})
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        value_col = [c for c in df.columns if c != "DATE"][0]
        vals = pd.to_numeric(df[value_col], errors="coerce")
        clean = df.loc[vals.notna()].copy()
        clean[value_col] = pd.to_numeric(clean[value_col], errors="coerce")
        if len(clean):
            result["value"] = safe_float(clean[value_col].iloc[-1], None)
            result["previous"] = safe_float(clean[value_col].iloc[-2], result["value"]) if len(clean) > 1 else result["value"]
            result["date"] = str(clean["DATE"].iloc[-1])
    except Exception:
        pass
    _FRED_CACHE[series_id] = (now, result)
    return result


def macro_snapshot(force: bool = False) -> Dict:
    global _MACRO_CACHE
    cache_now = time.time()
    if not force and _MACRO_CACHE is not None and cache_now - _MACRO_CACHE[0] < 900:
        return dict(_MACRO_CACHE[1])
    out = {
        "vix": None, "vix_change": 0.0, "tnx": None, "tnx_change": 0.0,
        "dxy_change_5d": 0.0, "oil_change_5d": 0.0, "gold_change_5d": 0.0,
        "fed_funds": None, "cpi": None, "unemployment": None, "score": 0.0,
        "risk_label": "NEUTRAL", "warnings": [],
    }
    try:
        vix = daily_features(fetch("^VIX", "5y", "1d"))
        tnx = daily_features(fetch("^TNX", "5y", "1d"))
        dxy = daily_features(fetch("DX-Y.NYB", "5y", "1d"))
        oil = daily_features(fetch("CL=F", "5y", "1d"))
        gold = daily_features(fetch("GC=F", "5y", "1d"))
        out["vix"] = safe_float(vix["Close"].iloc[-1], 20.0)
        out["vix_change"] = safe_float(vix["ret5"].iloc[-1])
        out["tnx"] = safe_float(tnx["Close"].iloc[-1])
        out["tnx_change"] = safe_float(tnx["ret5"].iloc[-1])
        out["dxy_change_5d"] = safe_float(dxy["ret5"].iloc[-1])
        out["oil_change_5d"] = safe_float(oil["ret5"].iloc[-1])
        out["gold_change_5d"] = safe_float(gold["ret5"].iloc[-1])
    except Exception as exc:
        out["warnings"].append(f"market proxies: {exc}")

    # FRED calls are independent and optional; run them concurrently so a slow public
    # endpoint cannot block the dashboard three times in sequence.
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            fut = {name: pool.submit(_fred_series, sid) for name, sid in {
                "fed": "FEDFUNDS", "cpi": "CPIAUCSL", "unrate": "UNRATE"
            }.items()}
            fred = {name: f.result() for name, f in fut.items()}
    except Exception:
        fred = {"fed": {}, "cpi": {}, "unrate": {}}
    out["fed_funds"] = fred["fed"].get("value")
    out["cpi"] = fred["cpi"].get("value")
    out["unemployment"] = fred["unrate"].get("value")

    score = 0.0
    vix = safe_float(out.get("vix"), 20.0)
    if vix >= 30:
        score -= 35
    elif vix >= 24:
        score -= 20
    elif vix <= 16:
        score += 12
    score -= float(np.clip(safe_float(out.get("vix_change")) * 250, -20, 20))
    score -= float(np.clip(safe_float(out.get("dxy_change_5d")) * 300, -10, 10))
    score -= float(np.clip(safe_float(out.get("tnx_change")) * 120, -10, 10))
    score = float(np.clip(score, -100, 100))
    out["score"] = score
    out["risk_label"] = "RISK-OFF" if score <= -25 else "RISK-ON" if score >= 20 else "NEUTRAL"
    _MACRO_CACHE = (cache_now, dict(out))
    return out
