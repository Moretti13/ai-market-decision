from __future__ import annotations

import math
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from config import DEFAULTS
from data_layer import (
    daily_features,
    fetch,
    intraday_features,
    latest_regular,
    news_context,
    premarket_df,
    safe_float,
    current_fundamentals,
    data_health,
)
from market_clock import market_status
from model_engine import train_day_model, train_medium_model


def _regime() -> Dict:
    try:
        spy = daily_features(fetch("SPY", "6mo", "1d"))
        qqq = daily_features(fetch("QQQ", "6mo", "1d"))
        vix = daily_features(fetch("^VIX", "6mo", "1d"))
        spy5 = safe_float(spy["ret5"].iloc[-1])
        qqq5 = safe_float(qqq["ret5"].iloc[-1])
        vix_last = safe_float(vix["Close"].iloc[-1], 20.0)
        vix20 = safe_float(vix["Close"].rolling(20, min_periods=5).mean().iloc[-1], vix_last)
        risk_off = (spy5 < -0.02 and qqq5 < -0.02) or vix_last > max(25.0, vix20 * 1.15)
        risk_on = spy5 > 0.02 and qqq5 > 0.02 and vix_last < vix20 * 1.10
        regime = "RISK-OFF / HIGH VOL" if risk_off else "RISK-ON" if risk_on else "NEUTRAL"
        return {"regime": regime, "spy5": spy5, "qqq5": qqq5, "vix": vix_last, "vix20": vix20}
    except Exception:
        return {"regime": "UNKNOWN", "spy5": 0.0, "qqq5": 0.0, "vix": 20.0, "vix20": 20.0}


def _overlay_day(day: Dict, gap: float, news_sentiment: float, regime: Dict, intraday: Dict | None = None) -> Dict:
    p = safe_float(day.get("p_up"), 0.5)
    expected = safe_float(day.get("expected_return"))
    drivers: List[tuple[float, str]] = []

    gap_adj = float(np.clip(gap * 1.5, -0.10, 0.10))
    p = float(np.clip(p + gap_adj, 0.01, 0.99))
    drivers.append((abs(gap_adj), "gap/overnight"))

    news_adj = float(np.clip(news_sentiment * 0.05, -0.05, 0.05))
    p = float(np.clip(p + news_adj, 0.01, 0.99))
    if abs(news_adj) >= 0.01:
        drivers.append((abs(news_adj), "news"))

    regime_adj = 0.0
    if regime["regime"] == "RISK-OFF / HIGH VOL":
        regime_adj = -0.035
    elif regime["regime"] == "RISK-ON":
        regime_adj = 0.025
    p = float(np.clip(p + regime_adj, 0.01, 0.99))
    if regime_adj:
        drivers.append((abs(regime_adj), "market regime"))

    if intraday and intraday.get("bars", 0) >= 2:
        intraday_adj = 0.0
        if safe_float(intraday.get("current")) >= safe_float(intraday.get("vwap")) and safe_float(intraday.get("ret15m")) > 0:
            intraday_adj = 0.025
        elif safe_float(intraday.get("current")) <= safe_float(intraday.get("vwap")) and safe_float(intraday.get("ret15m")) < 0:
            intraday_adj = -0.025
        p = float(np.clip(p + intraday_adj, 0.01, 0.99))
        if intraday_adj:
            drivers.append((abs(intraday_adj), "momentum intraday/VWAP"))

    expected += (p - safe_float(day.get("p_up"), 0.5)) * 0.02
    expected = float(np.clip(expected, -0.15, 0.15))
    conf = float(np.clip(0.50 + abs(p - 0.5) * 0.95, 0.50, 0.95))
    signal = "PRE-BUY" if p >= DEFAULTS["buy_prob"] and expected >= DEFAULTS["day_min_edge"] else "PRE-SELL" if p <= DEFAULTS["sell_prob"] and expected <= -DEFAULTS["day_min_edge"] else "WAIT"
    return {"p_up": p, "p_down": 1 - p, "expected_return": expected, "confidence": conf, "signal": signal, "drivers": [x[1] for x in sorted(drivers, reverse=True)[:6]]}


def premarket_analysis(ticker: str) -> Dict:
    t = ticker.strip().upper()
    d = daily_features(fetch(t, "2y", "1d"))
    if d.empty:
        raise RuntimeError("Storico giornaliero non disponibile")
    prev_close = safe_float(d["Close"].iloc[-1])
    pre = premarket_df(t)
    indicative = safe_float(pre["Close"].iloc[-1], prev_close) if not pre.empty else prev_close
    gap = indicative / prev_close - 1 if prev_close else 0.0
    nctx = news_context(t)
    regime = _regime()

    try:
        day_model = train_day_model(t, live_gap=gap)
    except Exception as exc:
        day_model = {"signal": "WAIT", "p_up": 0.5, "p_down": 0.5, "expected_return": 0.0, "accuracy": 0.0, "brier": 0.0, "mae": 0.0, "error": str(exc), "model_note": "DAY fallback"}

    over = _overlay_day(day_model, gap, nctx["sentiment"], regime)
    pre_volume_ratio = 1.0
    if not pre.empty:
        v = pd.to_numeric(pre["Volume"], errors="coerce").fillna(0)
        baseline = v.rolling(20, min_periods=5).mean().iloc[-1]
        pre_volume_ratio = safe_float(v.iloc[-1] / baseline, 1.0) if baseline else 1.0
    if abs(pre_volume_ratio - 1) > 0.5:
        over["drivers"].insert(0, "volume pre-market")

    return {
        "ticker": t,
        "signal": over["signal"],
        "score": round(over["p_up"] * 100, 1),
        "p_up": over["p_up"],
        "p_down": over["p_down"],
        "expected_return": over["expected_return"],
        "confidence": over["confidence"],
        "prev_close": prev_close,
        "indicative": indicative,
        "gap": gap,
        "drivers": over["drivers"],
        "model_accuracy": day_model.get("accuracy", 0.0),
        "model_brier": day_model.get("brier", 0.0),
        "model_mae": day_model.get("mae", 0.0),
        "model_note": day_model.get("model_note", ""),
        "model_error": day_model.get("error"),
        "premarket_bars": len(pre),
        "news_sentiment": nctx["sentiment"],
        "news_count": nctx["count"],
        "regime": regime,
    }


def intraday_state(ticker: str) -> Dict:
    status = market_status(ticker)
    try:
        raw = latest_regular(ticker)
        x = intraday_features(raw, ticker)
        if x.empty:
            return {"status": "WAIT_FOR_OPEN", "bars": 0, **status}
        dates = x.index.tz_convert(status["timezone"]).date
        today_mask = dates == status["now"].date()
        today = x.loc[today_mask]
        if today.empty:
            return {"status": "WAIT_FOR_OPEN", "bars": 0, **status}
        row = today.iloc[-1]
        return {
            "status": "LIVE" if status["is_open"] else status["status"],
            "bars": int(len(today)),
            "open": safe_float(today["Open"].iloc[0]),
            "current": safe_float(row["Close"]),
            "high": safe_float(today["High"].max()),
            "low": safe_float(today["Low"].min()),
            "ret5m": safe_float(row["ret5m"]),
            "ret15m": safe_float(row["ret15m"]),
            "ret30m": safe_float(row["ret30m"]),
            "ret60m": safe_float(row["ret60m"]),
            "vwap": safe_float(row["vwap"]),
            "vwap_dist": safe_float(row["vwap_dist"]),
            "volume_ratio": safe_float(row["volume_ratio"], 1.0),
            "or_high": safe_float(row["or_high"]),
            "or_low": safe_float(row["or_low"]),
            "or_pos": safe_float(row["or_pos"], 0.5),
            "updated": str(x.index[-1]),
            **status,
        }
    except Exception as exc:
        return {"status": "DATA_ERROR", "bars": 0, "error": str(exc), **status}


def confirm_open(ticker: str, pre: Dict) -> Dict:
    status = market_status(ticker)
    state = intraday_state(ticker)
    if not status["is_open"]:
        return {"status": "WAIT_FOR_OPEN", "signal": "WAIT", "message": "Attendi la sessione regolare.", **state}
    bars = int(state.get("bars", 0))
    if bars < 1:
        return {"status": "WAIT_FOR_OPEN", "signal": "WAIT", "message": "La prima barra 5m non è disponibile.", **state}

    current = safe_float(state.get("current"))
    opening = safe_float(state.get("open"))
    vwap = safe_float(state.get("vwap"), opening)
    ret15 = safe_float(state.get("ret15m"))
    aligned_long = current > opening and current >= vwap and ret15 >= 0
    aligned_short = current < opening and current <= vwap and ret15 <= 0

    # Wait for two completed 5m bars (~10 minutes) before a hard confirmation.
    if bars < 2:
        return {"status": "CONFIRMING", "signal": pre["signal"], "message": "Aspetta 2 barre 5m complete.", **state}

    if pre["signal"] == "PRE-BUY" and aligned_long:
        return {"status": "CONFIRMED", "signal": "ENTER BUY", "message": "Conferma rialzista: prezzo sopra open e VWAP.", **state}
    if pre["signal"] == "PRE-SELL" and aligned_short:
        return {"status": "CONFIRMED", "signal": "ENTER SELL", "message": "Conferma ribassista: prezzo sotto open e VWAP.", **state}
    if pre["signal"] in {"PRE-BUY", "PRE-SELL"}:
        return {"status": "INVALIDATED", "signal": "WAIT", "message": "La conferma intraday contraddice il segnale pre-market.", **state}
    return {"status": "WAIT", "signal": "WAIT", "message": "Nessuna conferma sufficiente.", **state}


def trade_plan(ticker: str, pre: Dict, confirm: Dict, capital: float, risk_pct: float) -> Dict:
    current = safe_float(confirm.get("current"), safe_float(pre.get("indicative")))
    d = daily_features(fetch(ticker, "6mo", "1d"))
    atr_pct = safe_float(d["atr_pct"].iloc[-1], 0.01) if not d.empty else 0.01
    atr_pct = float(np.clip(atr_pct, 0.003, 0.12))
    side = None
    if confirm.get("status") == "CONFIRMED" and confirm.get("signal") == "ENTER BUY":
        side = "LONG"
    elif confirm.get("status") == "CONFIRMED" and confirm.get("signal") == "ENTER SELL":
        side = "SHORT"

    trigger_text = "Dopo 2 barre 5m complete, con prezzo sopra VWAP/open" if pre["signal"] == "PRE-BUY" else "Dopo 2 barre 5m complete, con prezzo sotto VWAP/open" if pre["signal"] == "PRE-SELL" else "Nessun trigger attivo"
    if side is None:
        return {
            "status": "WAIT",
            "action": "NON ENTRARE",
            "side": None,
            "entry": current,
            "stop": current,
            "target": current,
            "rr": 0.0,
            "shares": 0,
            "risk_amount": 0.0,
            "trigger": trigger_text,
            "validity": "DAY: chiusura entro fine sessione o uscita anticipata se stop/target/cambio segnale.",
        }

    risk_per_share = current * atr_pct * 1.2
    rr_target = 2.0
    if side == "LONG":
        entry, stop, target = current, current - risk_per_share, current + risk_per_share * rr_target
        action = "ENTER LONG NOW"
    else:
        entry, stop, target = current, current + risk_per_share, current - risk_per_share * rr_target
        action = "ENTER SHORT NOW"

    risk_budget = max(0.0, float(capital) * float(risk_pct))
    shares_by_risk = math.floor(risk_budget / max(abs(entry - stop), 1e-9)) if risk_budget else 0
    max_notional = float(capital) * DEFAULTS["max_capital_fraction"]
    shares_by_capital = math.floor(max_notional / max(entry, 1e-9))
    shares = max(0, min(shares_by_risk, shares_by_capital))
    rr = abs(target - entry) / max(abs(entry - stop), 1e-9)
    status = "READY" if shares > 0 and rr >= DEFAULTS["min_rr"] else "WAIT"
    return {
        "status": status,
        "action": action if status == "READY" else "NON ENTRARE",
        "side": side,
        "entry": entry,
        "stop": stop,
        "target": target,
        "rr": rr,
        "shares": shares,
        "risk_amount": min(risk_budget, shares * abs(entry - stop)),
        "trigger": "Confermato: entrare sul prezzo corrente/next bar" if confirm.get("status") == "CONFIRMED" else trigger_text,
        "validity": "DAY: uscita entro chiusura, salvo stop/target/cambio segnale.",
    }


def analyze_asset(ticker: str, capital: float = 10000.0, risk_pct: float = 0.01) -> Dict:
    t = ticker.strip().upper()
    clock = market_status(t)
    pre = premarket_analysis(t)
    intraday = intraday_state(t) if clock["is_open"] else None
    if intraday:
        day_base = {"p_up": pre["p_up"], "expected_return": pre["expected_return"]}
        adjusted = _overlay_day(day_base, pre["gap"], pre["news_sentiment"], pre["regime"], intraday)
        pre.update({k: adjusted[k] for k in ["signal", "p_up", "p_down", "expected_return", "confidence", "drivers"]})
    confirm = confirm_open(t, pre)
    plan = trade_plan(t, pre, confirm, capital, risk_pct)

    try:
        week = train_medium_model(t, "WEEK")
    except Exception as exc:
        week = {"signal": "N/A", "p_up": 0.5, "p_down": 0.5, "expected_return": 0.0, "accuracy": 0.0, "brier": 0.0, "mae": 0.0, "error": str(exc)}
    try:
        month = train_medium_model(t, "MONTH")
    except Exception as exc:
        month = {"signal": "N/A", "p_up": 0.5, "p_down": 0.5, "expected_return": 0.0, "accuracy": 0.0, "brier": 0.0, "mae": 0.0, "error": str(exc)}

    return {
        "ticker": t,
        "clock": clock,
        "pre": pre,
        "confirm": confirm,
        "plan": plan,
        "week": week,
        "month": month,
        "news": news_context(t)["items"],
        "fundamentals": current_fundamentals(t),
        "regime": pre["regime"],
        "health": data_health(t),
        "generated_at": datetime.now().isoformat(),
    }
