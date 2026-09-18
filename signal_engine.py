from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Dict, List

import numpy as np
import pandas as pd

from config import DEFAULTS
from data_layer import (
    current_fundamentals,
    daily_features,
    data_health,
    fetch,
    intraday_features,
    latest_regular,
    premarket_df,
    safe_float,
)
from events_engine import event_context
from market_clock import market_status
from model_engine import train_day_model, train_medium_model
from regime_engine import market_regime


def _completed_daily(ticker: str) -> pd.DataFrame:
    d = daily_features(fetch(ticker, "2y", "1d"))
    if d.empty:
        return d
    status = market_status(ticker)
    if status["is_pre"] or status["is_open"]:
        d = d.loc[np.array(d.index.date) < status["now"].date()]
    return d


def _decision_overlay(day_model: Dict, gap: float, events: Dict, regime: Dict, intraday: Dict | None = None) -> Dict:
    base_p = safe_float(day_model.get("p_up"), 0.5)
    p = base_p
    expected = safe_float(day_model.get("expected_return"), 0.0)
    drivers: List[tuple[float, str]] = []

    gap_adj = float(np.clip(gap * 1.25, -0.05, 0.05))
    p += gap_adj
    if abs(gap_adj) >= 0.005:
        drivers.append((abs(gap_adj), "gap/overnight"))

    news = events.get("news", {})
    news_adj = float(np.clip(safe_float(news.get("sentiment")) * (0.3 + safe_float(news.get("importance"))) * 0.045, -0.05, 0.05))
    p += news_adj
    if abs(news_adj) >= 0.005:
        drivers.append((abs(news_adj), f"news/{events.get('catalyst', 'GENERAL')}") )

    regime_adj = float(np.clip(safe_float(regime.get("score")) / 100 * 0.045, -0.045, 0.045))
    p += regime_adj
    if abs(regime_adj) >= 0.005:
        drivers.append((abs(regime_adj), "regime mercato"))

    intraday_adj = 0.0
    if intraday and int(intraday.get("bars", 0)) >= 2:
        current = safe_float(intraday.get("current"))
        vwap = safe_float(intraday.get("vwap"), current)
        ret15 = safe_float(intraday.get("ret15m"))
        vol_ratio = safe_float(intraday.get("volume_ratio"), 1.0)
        if current >= vwap and ret15 > 0:
            intraday_adj += 0.02
        elif current <= vwap and ret15 < 0:
            intraday_adj -= 0.02
        if vol_ratio >= 1.25:
            intraday_adj *= 1.25
        intraday_adj = float(np.clip(intraday_adj, -0.03, 0.03))
        p += intraday_adj
        if abs(intraday_adj) >= 0.005:
            drivers.append((abs(intraday_adj), "momentum intraday/VWAP"))

    p = float(np.clip(p, 0.01, 0.99))
    expected += (p - base_p) * 0.025
    expected = float(np.clip(expected, -0.15, 0.15))

    base_quality = safe_float(day_model.get("quality_score"), 0.4)
    event_penalty = safe_float(events.get("risk_penalty"), 0.0)
    confidence = float(np.clip((0.45 + abs(p - 0.5)) * (0.65 + 0.35 * base_quality) * (1 - event_penalty), 0.30, 0.95))

    if p >= DEFAULTS["buy_prob"] and expected >= DEFAULTS["day_min_edge"]:
        signal = "PRE-BUY"
    elif p <= DEFAULTS["sell_prob"] and expected <= -DEFAULTS["day_min_edge"]:
        signal = "PRE-SELL"
    else:
        signal = "WAIT"

    return {
        "p_up": p,
        "p_down": 1 - p,
        "expected_return": expected,
        "confidence": confidence,
        "signal": signal,
        "decision_score": float(np.clip(50 + (p - 0.5) * 100, 0, 100)),
        "drivers": [x[1] for x in sorted(drivers, reverse=True)[:6]],
    }


def _live_gap(ticker: str, prev_close: float) -> tuple[float, float, str, int]:
    status = market_status(ticker)
    if prev_close <= 0:
        return 0.0, 0.0, "unavailable", 0
    if status["is_open"]:
        try:
            raw = latest_regular(ticker)
            if not raw.empty:
                dates = raw.index.tz_convert(status["timezone"]).date
                today = raw.loc[np.array(dates) == status["now"].date()]
                if not today.empty:
                    op = safe_float(today["Open"].iloc[0], prev_close)
                    return op / prev_close - 1, safe_float(today["Close"].iloc[-1], op), "regular open", len(today)
        except Exception:
            pass
    if status["is_pre"]:
        try:
            pre = premarket_df(ticker)
            if not pre.empty:
                dates = pre.index.tz_convert(status["timezone"]).date
                today = pre.loc[np.array(dates) == status["now"].date()]
                if not today.empty:
                    indicative = safe_float(today["Close"].iloc[-1], prev_close)
                    return indicative / prev_close - 1, indicative, "premarket 5m", len(today)
        except Exception:
            pass
    return 0.0, prev_close, "gap non noto fino alla prossima sessione", 0


def premarket_analysis(ticker: str, events: Dict | None = None, regime: Dict | None = None) -> Dict:
    t = ticker.strip().upper()
    d = _completed_daily(t)
    if d.empty:
        raise RuntimeError("Storico giornaliero completato non disponibile")
    prev_close = safe_float(d["Close"].iloc[-1])
    gap, indicative, gap_source, pre_bars = _live_gap(t, prev_close)
    events = events or event_context(t)
    regime = regime or market_regime()

    try:
        model = train_day_model(t, live_gap=gap)
    except Exception as exc:
        model = {
            "signal": "WAIT", "p_up": 0.5, "p_down": 0.5, "expected_return": 0.0,
            "accuracy": 0.0, "brier": 0.25, "mae": 0.0, "auc": 0.5,
            "quality_score": 0.0, "error": str(exc), "model_note": "DAY fallback",
            "data_asof": str(d.index[-1]),
        }
    over = _decision_overlay(model, gap, events, regime)
    return {
        "ticker": t,
        **over,
        "score": round(over["decision_score"], 1),
        "prev_close": prev_close,
        "indicative": indicative,
        "gap": gap,
        "gap_source": gap_source,
        "premarket_bars": pre_bars,
        "model_accuracy": safe_float(model.get("accuracy")),
        "model_brier": safe_float(model.get("brier"), 0.25),
        "model_auc": safe_float(model.get("auc"), 0.5),
        "model_mae": safe_float(model.get("mae")),
        "quality_score": safe_float(model.get("quality_score")),
        "model_note": model.get("model_note", ""),
        "model_error": model.get("error"),
        "model_data_asof": model.get("data_asof", str(d.index[-1])),
        "model_trained_at": model.get("trained_at"),
        "model_cache_source": model.get("cache_source", "UNKNOWN"),
        "atr14": safe_float(d["atr14"].iloc[-1], prev_close * 0.01),
        "atr_pct": safe_float(d["atr_pct"].iloc[-1], 0.01),
        "event_risk": events.get("event_risk", "NORMAL"),
        "catalyst": events.get("catalyst", "NONE"),
        "news_sentiment": safe_float(events.get("news", {}).get("sentiment")),
        "news_count": int(events.get("news", {}).get("count", 0)),
        "regime": regime,
    }


def intraday_state(ticker: str) -> Dict:
    status = market_status(ticker)
    try:
        x = intraday_features(latest_regular(ticker), ticker)
        if x.empty:
            return {"status": "WAIT_FOR_OPEN", "bars": 0, **status}
        dates = x.index.tz_convert(status["timezone"]).date
        today = x.loc[np.array(dates) == status["now"].date()]
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
            "session_ret": safe_float(row["session_ret"]),
            "vwap": safe_float(row["vwap"]),
            "vwap_dist": safe_float(row["vwap_dist"]),
            "volume_ratio": safe_float(row["volume_ratio"], 1.0),
            "or_high": safe_float(row["or_high"]),
            "or_low": safe_float(row["or_low"]),
            "or_pos": safe_float(row["or_pos"], 0.5),
            "updated": str(today.index[-1]),
            **status,
        }
    except Exception as exc:
        return {"status": "DATA_ERROR", "bars": 0, "error": str(exc), **status}


def _confirmation_logic(pre_signal: str, state: Dict, min_bars: int | None = None) -> Dict:
    min_bars = int(min_bars or DEFAULTS["day_confirm_bars"])
    bars = int(state.get("bars", 0))
    if bars < min_bars:
        return {"status": "CONFIRMING", "signal": pre_signal, "message": f"Attendi almeno {min_bars} barre 5m complete.", "alignment_score": 0}
    if pre_signal not in {"PRE-BUY", "PRE-SELL"}:
        return {"status": "WAIT", "signal": "WAIT", "message": "Nessun vantaggio operativo sufficiente.", "alignment_score": 0}

    current = safe_float(state.get("current"))
    opening = safe_float(state.get("open"), current)
    vwap = safe_float(state.get("vwap"), current)
    ret15 = safe_float(state.get("ret15m"))
    session_ret = safe_float(state.get("session_ret"))
    vol_ratio = safe_float(state.get("volume_ratio"), 1.0)
    or_pos = safe_float(state.get("or_pos"), 0.5)

    long_points = int(current > opening) + int(current >= vwap) + int(ret15 > 0) + int(session_ret > 0)
    short_points = int(current < opening) + int(current <= vwap) + int(ret15 < 0) + int(session_ret < 0)
    if bars >= DEFAULTS["day_opening_range_bars"]:
        long_points += int(or_pos >= 0.60)
        short_points += int(or_pos <= 0.40)
    if vol_ratio >= 1.10:
        if session_ret > 0:
            long_points += 1
        elif session_ret < 0:
            short_points += 1

    if pre_signal == "PRE-BUY":
        if long_points >= 3 and long_points >= short_points + 2:
            return {"status": "CONFIRMED", "signal": "ENTER BUY", "message": "Conferma rialzista su open, VWAP e momentum.", "alignment_score": long_points}
        if short_points >= 4 and bars >= 3:
            return {"status": "INVALIDATED", "signal": "WAIT", "message": "Il flusso intraday contraddice il PRE-BUY.", "alignment_score": -short_points}
        return {"status": "WATCH", "signal": "BUY WATCH", "message": "Setup rialzista non ancora completo: attendi conferma.", "alignment_score": long_points - short_points}

    if short_points >= 3 and short_points >= long_points + 2:
        return {"status": "CONFIRMED", "signal": "ENTER SELL", "message": "Conferma ribassista su open, VWAP e momentum.", "alignment_score": -short_points}
    if long_points >= 4 and bars >= 3:
        return {"status": "INVALIDATED", "signal": "WAIT", "message": "Il flusso intraday contraddice il PRE-SELL.", "alignment_score": long_points}
    return {"status": "WATCH", "signal": "SELL WATCH", "message": "Setup ribassista non ancora completo: attendi conferma.", "alignment_score": short_points - long_points}


def confirm_open(ticker: str, pre: Dict, state: Dict | None = None) -> Dict:
    status = market_status(ticker)
    state = state if state is not None else intraday_state(ticker)
    if not status["is_open"]:
        return {"status": "WAIT_FOR_OPEN", "signal": "WAIT", "message": "Attendi la sessione regolare.", **state}
    logic = _confirmation_logic(pre.get("signal", "WAIT"), state)
    return {**state, **logic}


def trade_plan(ticker: str, pre: Dict, confirm: Dict, capital: float, risk_pct: float, events: Dict) -> Dict:
    current = safe_float(confirm.get("current"), safe_float(pre.get("indicative")))
    atr_value = safe_float(pre.get("atr14"), current * 0.01)
    atr_pct = safe_float(pre.get("atr_pct"), 0.01)
    atr_pct = float(np.clip(atr_pct, 0.004, 0.08))
    atr_value = max(atr_value, current * atr_pct)

    side = None
    if confirm.get("status") == "CONFIRMED" and confirm.get("signal") == "ENTER BUY":
        side = "LONG"
    elif confirm.get("status") == "CONFIRMED" and confirm.get("signal") == "ENTER SELL":
        side = "SHORT"

    trigger = (
        "Attendi ENTER BUY: almeno 2 barre 5m, prezzo/open/VWAP/momentum allineati"
        if pre.get("signal") == "PRE-BUY" else
        "Attendi ENTER SELL: almeno 2 barre 5m, prezzo/open/VWAP/momentum allineati"
        if pre.get("signal") == "PRE-SELL" else
        "Nessun trigger attivo"
    )
    if side is None or current <= 0:
        return {
            "status": "WAIT", "action": "NON ENTRARE", "side": None,
            "entry": None, "stop": None, "target": None, "rr": 0.0,
            "shares": 0, "risk_amount": 0.0, "trigger": trigger,
            "entry_window": "Nessuna finestra di ingresso attiva",
            "validity": "DAY: se confermato, uscita entro la chiusura salvo stop/target/invalidazione.",
            "event_risk": events.get("event_risk", "NORMAL"),
        }

    stop_distance = max(1.15 * atr_value, current * 0.005)
    stop_distance = min(stop_distance, current * 0.08)
    target_distance = stop_distance * DEFAULTS["target_rr"]
    if side == "LONG":
        entry, stop, target, action = current, current - stop_distance, current + target_distance, "ENTER LONG"
    else:
        entry, stop, target, action = current, current + stop_distance, current - target_distance, "ENTER SHORT"

    event_factor = 0.5 if events.get("event_risk") == "HIGH" else 0.75 if events.get("event_risk") == "MEDIUM" else 1.0
    risk_budget = max(0.0, float(capital) * float(risk_pct) * event_factor)
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
        "trigger": "Conferma attiva: entra sul prezzo corrente/next 5m senza inseguire il prezzo",
        "entry_window": "Valida finché il setup resta sopra/sotto VWAP e non si allontana > ~0.5 ATR dal trigger",
        "validity": "DAY: uscita entro la chiusura, salvo stop/target o invalidazione precedente.",
        "event_risk": events.get("event_risk", "NORMAL"),
    }


def analyze_asset(ticker: str, capital: float = 10_000.0, risk_pct: float = 0.01, include_fundamentals: bool = True) -> Dict:
    t = ticker.strip().upper()
    if not t:
        raise ValueError("Inserisci un ticker")
    clock = market_status(t)
    events = event_context(t)
    regime = market_regime()
    pre = premarket_analysis(t, events=events, regime=regime)
    intraday = intraday_state(t) if clock["is_open"] else None
    if intraday and intraday.get("bars", 0) >= 2:
        adjusted = _decision_overlay(pre, pre["gap"], events, regime, intraday)
        pre.update({k: adjusted[k] for k in ["signal", "p_up", "p_down", "expected_return", "confidence", "decision_score", "drivers"]})
        pre["score"] = round(pre["decision_score"], 1)
    confirm = confirm_open(t, pre, intraday if intraday is not None else None)
    plan = trade_plan(t, pre, confirm, capital, risk_pct, events)

    try:
        week = train_medium_model(t, "WEEK")
    except Exception as exc:
        week = {"signal": "N/A", "p_up": 0.5, "p_down": 0.5, "expected_return": 0.0, "quality_score": 0.0, "error": str(exc)}
    try:
        month = train_medium_model(t, "MONTH")
    except Exception as exc:
        month = {"signal": "N/A", "p_up": 0.5, "p_down": 0.5, "expected_return": 0.0, "quality_score": 0.0, "error": str(exc)}

    return {
        "ticker": t,
        "clock": clock,
        "pre": pre,
        "confirm": confirm,
        "plan": plan,
        "week": week,
        "month": month,
        "events": events,
        "news": events.get("news", {}).get("items", []),
        "fundamentals": current_fundamentals(t) if include_fundamentals else {},
        "regime": regime,
        "macro": regime.get("macro", {}),
        "health": data_health(t),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
