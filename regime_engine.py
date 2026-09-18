from __future__ import annotations

from typing import Dict

import numpy as np

from data_layer import daily_features, fetch, safe_float
from macro_engine import macro_snapshot


def market_regime() -> Dict:
    try:
        spy = daily_features(fetch("SPY", "5y", "1d"))
        qqq = daily_features(fetch("QQQ", "5y", "1d"))
        iwm = daily_features(fetch("IWM", "5y", "1d"))
        vix = daily_features(fetch("^VIX", "5y", "1d"))
        spy_last = safe_float(spy["Close"].iloc[-1])
        spy_sma20 = safe_float(spy["sma20"].iloc[-1], spy_last)
        spy_sma50 = safe_float(spy["sma50"].iloc[-1], spy_last)
        spy_sma200 = safe_float(spy["sma200"].iloc[-1], spy_last)
        breadth = np.mean([
            safe_float(spy["ret20"].iloc[-1]),
            safe_float(qqq["ret20"].iloc[-1]),
            safe_float(iwm["ret20"].iloc[-1]),
        ])
        vix_last = safe_float(vix["Close"].iloc[-1], 20.0)
        realized = safe_float(spy["vol20"].iloc[-1]) * np.sqrt(252)
        trend_score = 0.0
        trend_score += 20 if spy_last > spy_sma20 else -20
        trend_score += 20 if spy_last > spy_sma50 else -20
        trend_score += 20 if spy_last > spy_sma200 else -20
        trend_score += float(np.clip(breadth * 500, -25, 25))
        if vix_last > 30:
            trend_score -= 25
        elif vix_last < 17:
            trend_score += 10
        macro = macro_snapshot()
        score = float(np.clip(0.75 * trend_score + 0.25 * safe_float(macro.get("score")), -100, 100))
        high_vol = vix_last >= 25 or realized >= 0.28
        if score >= 35 and not high_vol:
            regime = "RISK-ON / TREND UP"
        elif score <= -35:
            regime = "RISK-OFF / TREND DOWN"
        elif high_vol:
            regime = "HIGH VOL / CHOP"
        elif spy_last > spy_sma50:
            regime = "MILD UP / NEUTRAL"
        elif spy_last < spy_sma50:
            regime = "MILD DOWN / NEUTRAL"
        else:
            regime = "NEUTRAL"
        return {
            "regime": regime,
            "score": score,
            "spy20": safe_float(spy["ret20"].iloc[-1]),
            "qqq20": safe_float(qqq["ret20"].iloc[-1]),
            "iwm20": safe_float(iwm["ret20"].iloc[-1]),
            "vix": vix_last,
            "realized_vol": realized,
            "macro": macro,
        }
    except Exception as exc:
        return {
            "regime": "UNKNOWN", "score": 0.0, "spy20": 0.0, "qqq20": 0.0,
            "iwm20": 0.0, "vix": 20.0, "realized_vol": 0.0,
            "macro": macro_snapshot(), "error": str(exc),
        }
