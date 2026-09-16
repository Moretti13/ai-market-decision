from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import yfinance as yf

from config import BENCHMARKS

NY = ZoneInfo("America/New_York")

def safe_float(value, default=0.0) -> float:
    try:
        v = float(value)
        return default if not np.isfinite(v) else v
    except Exception:
        return default

def download(ticker: str, period="5d", interval="5m", prepost=False) -> pd.DataFrame:
    df = yf.download(
        ticker,
        period=period,
        interval=interval,
        prepost=prepost,
        auto_adjust=False,
        progress=False,
        threads=False,
    )
    if df is None or df.empty:
        raise ValueError(f"Nessun dato disponibile per {ticker}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
    df = df[cols].dropna()
    idx = pd.to_datetime(df.index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    df.index = idx.tz_convert(NY)
    return df

def rsi(series: pd.Series, n=14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)

def atr(df: pd.DataFrame, n=14) -> pd.Series:
    prev = df["Close"].shift(1)
    tr = pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - prev).abs(),
            (df["Low"] - prev).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(n).mean().bfill()

def market_session(now: Optional[datetime] = None) -> str:
    now = now.astimezone(NY) if now else datetime.now(NY)
    # Weekends
    if now.weekday() >= 5:
        return "CLOSED"
    mins = now.hour * 60 + now.minute
    if 4 * 60 <= mins < 9 * 60 + 30:
        return "PRE-MARKET"
    if 9 * 60 + 30 <= mins < 16 * 60:
        return "REGULAR SESSION"
    if 16 * 60 <= mins < 20 * 60:
        return "AFTER-HOURS"
    return "CLOSED"

def split_sessions(df: pd.DataFrame):
    local = df.index
    mins = local.hour * 60 + local.minute
    regular = df[(mins >= 9 * 60 + 30) & (mins < 16 * 60)]
    pre = df[(mins >= 4 * 60) & (mins < 9 * 60 + 30)]
    post = df[(mins >= 16 * 60) & (mins < 20 * 60)]
    return pre, regular, post

def latest_daily(ticker: str) -> Dict:
    d = download(ticker, "1y", "1d")
    c = d["Close"]
    tmp = pd.DataFrame(index=d.index)
    tmp["ret1"] = c.pct_change(1)
    tmp["ret5"] = c.pct_change(5)
    tmp["ret20"] = c.pct_change(20)
    tmp["sma20"] = c.rolling(20).mean()
    tmp["sma50"] = c.rolling(50).mean()
    tmp["rsi"] = rsi(c)
    tmp["atr"] = atr(d)
    row = tmp.dropna().iloc[-1]
    price = safe_float(c.iloc[-1], 0.0)
    atr_val = safe_float(row["atr"], price * 0.01)
    return {
        "price": price,
        "ret1": safe_float(row["ret1"]),
        "ret5": safe_float(row["ret5"]),
        "ret20": safe_float(row["ret20"]),
        "sma20": safe_float(row["sma20"], price),
        "sma50": safe_float(row["sma50"], price),
        "rsi": safe_float(row["rsi"], 50),
        "atr_pct": safe_float(atr_val / price if price else 0.01, 0.01),
    }

def market_snapshot() -> Dict[str, float]:
    out = {}
    for name, ticker in BENCHMARKS.items():
        try:
            d = download(ticker, "10d", "1d")
            out[name] = safe_float(d["Close"].pct_change().iloc[-1])
        except Exception:
            out[name] = 0.0
    return out

def premarket_signal(ticker: str) -> Dict:
    d = download(ticker, "2d", "5m", prepost=True)
    pre, regular, _ = split_sessions(d)
    today = datetime.now(NY).date()
    today_pre = pre[pre.index.date == today]
    today_reg = regular[regular.index.date == today]

    if not today_pre.empty:
        source_pre = today_pre
    elif not pre.empty:
        source_pre = pre[pre.index.date == pre.index.date[-1]]
    else:
        source_pre = d.iloc[-10:]

    daily = latest_daily(ticker)
    mkt = market_snapshot()

    prev_close = safe_float(daily["price"])
    indicative = safe_float(source_pre["Close"].iloc[-1] if len(source_pre) else prev_close, prev_close)
    gap = safe_float(indicative / prev_close - 1 if prev_close else 0.0)

    if len(source_pre) > 1:
        pre_ret = safe_float(source_pre["Close"].pct_change().iloc[-1])
    else:
        pre_ret = 0.0

    score = 50.0
    drivers = []

    def add(value, text):
        nonlocal score
        value = safe_float(value)
        score += value
        if abs(value) >= 3:
            drivers.append((abs(value), text))

    add(np.clip(gap * 250, -15, 15), "gap / pre-market")
    add(np.clip(pre_ret * 1200, -10, 10), "momentum pre-market")
    add(np.clip(daily["ret5"] * 100, -10, 10), "trend 5 giorni")
    add(np.clip(daily["ret20"] * 45, -8, 8), "trend 20 giorni")
    add(np.clip((daily["rsi"] - 50) * 0.10, -5, 5), "RSI")
    add(np.clip(mkt.get("QQQ", 0.0) * 80, -6, 6), "Nasdaq / QQQ")
    add(np.clip(mkt.get("SPY", 0.0) * 70, -5, 5), "S&P 500")
    add(np.clip(mkt.get("VIX", 0.0) * 20, -4, 4), "VIX")

    score = safe_float(np.clip(score, 1, 99), 50)
    if score >= 65:
        signal = "PRE-BUY"
    elif score <= 35:
        signal = "PRE-SELL"
    else:
        signal = "WAIT"

    p_up = safe_float(np.clip(0.5 + (score - 50) / 110, 0.03, 0.97), 0.5)
    expected = safe_float(np.clip((score - 50) / 50 * max(daily["atr_pct"], 0.005) * 1.3, -0.08, 0.08))
    confidence = safe_float(np.clip(0.50 + abs(score - 50) / 100, 0.50, 0.92), 0.50)

    return {
        "ticker": ticker,
        "signal": signal,
        "score": round(score, 1),
        "p_up": round(p_up, 3),
        "p_down": round(1 - p_up, 3),
        "expected_return": round(expected, 4),
        "confidence": round(confidence, 3),
        "prev_close": round(prev_close, 4),
        "indicative": round(indicative, 4),
        "gap": round(gap, 4),
        "drivers": [t for _, t in sorted(drivers, reverse=True)[:6]],
        "session": "OPENED" if not today_reg.empty else "PRE-MARKET",
        "updated": str(d.index[-1]),
    }

def intraday_confirmation(ticker: str, pre_signal: Dict) -> Dict:
    d = download(ticker, "2d", "5m", prepost=False)
    _, regular, _ = split_sessions(d)
    today = datetime.now(NY).date()
    today_reg = regular[regular.index.date == today]

    if today_reg.empty:
        return {
            "status": "WAIT_FOR_OPEN",
            "signal": pre_signal["signal"],
            "message": "Apertura non ancora disponibile.",
        }

    first = today_reg.iloc[0]
    current = today_reg.iloc[-1]
    open_price = safe_float(first["Open"])
    current_price = safe_float(current["Close"], open_price)
    elapsed = len(today_reg)

    if elapsed < 1:
        return {
            "status": "WAIT_FOR_OPEN",
            "signal": pre_signal["signal"],
            "message": "Apertura non ancora disponibile.",
        }

    # Confirmation is evaluated using first regular-session bar.
    bullish = safe_float(first["Close"]) >= open_price
    bearish = safe_float(first["Close"]) < open_price

    if elapsed == 1:
        status = "CONFIRMING"
        signal = pre_signal["signal"]
    else:
        if pre_signal["signal"] == "PRE-BUY":
            signal = "ENTER BUY" if bullish else "WAIT / INVALIDATED"
        elif pre_signal["signal"] == "PRE-SELL":
            signal = "ENTER SELL" if bearish else "WAIT / INVALIDATED"
        else:
            signal = (
                "ENTER BUY"
                if bullish and current_price >= open_price
                else "ENTER SELL"
                if bearish and current_price < open_price
                else "WAIT"
            )
        status = (
            "CONFIRMED"
            if signal.startswith("ENTER")
            else "INVALIDATED"
            if "INVALID" in signal
            else "WAIT"
        )

    ret_from_open = safe_float(current_price / open_price - 1 if open_price else 0.0)
    return {
        "status": status,
        "signal": signal,
        "message": f"Barre regular session disponibili: {elapsed}",
        "open": open_price,
        "current": current_price,
        "ret_from_open": ret_from_open,
        "bar_count": elapsed,
    }

def build_trade_plan(pre: Dict, confirmation: Optional[Dict], capital: float, risk_pct: float) -> Dict:
    pre_signal = pre.get("signal", "WAIT")
    current = safe_float(
        confirmation.get("current") if confirmation and confirmation.get("current") is not None else pre.get("indicative"),
        pre.get("prev_close", 0.0),
    )
    if current <= 0:
        return {
            "side": "NONE",
            "action": "NON ENTRARE",
            "entry": None,
            "stop": None,
            "target": None,
            "rr": None,
            "shares": 0,
            "risk_amount": 0.0,
            "validity": "Nessun piano: prezzo non disponibile.",
        }

    # Approximate day risk distance. In V5.1 this is deliberately conservative;
    # the ML/volatility model will replace it in a later production version.
    atr_pct = max(abs(safe_float(pre.get("expected_return"))) * 0.75, 0.006)
    stop_distance = current * atr_pct * 1.25
    target_distance = stop_distance * 2.0

    if pre_signal == "PRE-BUY":
        side = "BUY"
        entry = current * 1.001
        stop = entry - stop_distance
        target = entry + target_distance
        action = "ENTRA ALL'APERTURA SE CONFERMATO" if not confirmation or confirmation.get("status") not in ("CONFIRMED", "INVALIDATED") else "ENTRA LONG"
    elif pre_signal == "PRE-SELL":
        side = "SELL"
        entry = current * 0.999
        stop = entry + stop_distance
        target = entry - target_distance
        action = "APRI SHORT ALL'APERTURA SE CONFERMATO" if not confirmation or confirmation.get("status") not in ("CONFIRMED", "INVALIDATED") else "ENTRA SHORT"
    else:
        return {
            "side": "NONE",
            "action": "NON ENTRARE",
            "entry": None,
            "stop": None,
            "target": None,
            "rr": None,
            "shares": 0,
            "risk_amount": 0.0,
            "validity": "Segnale WAIT: nessuna operazione pronta.",
        }

    risk_per_share = abs(entry - stop)
    risk_amount = max(0.0, safe_float(capital) * max(0.0, safe_float(risk_pct)) / 100.0)
    shares = int(risk_amount // risk_per_share) if risk_per_share > 0 else 0
    rr = abs(target - entry) / risk_per_share if risk_per_share > 0 else 0.0

    return {
        "side": side,
        "action": action,
        "entry": round(entry, 4),
        "stop": round(stop, 4),
        "target": round(target, 4),
        "rr": round(safe_float(rr), 2),
        "shares": shares,
        "risk_amount": round(risk_amount, 2),
        "validity": "Oggi: apertura → chiusura, salvo stop, target o cambio segnale.",
    }

def medium_signal(ticker: str, horizon: str) -> Dict:
    d = download(ticker, "1y", "1d")
    c = d["Close"]
    ret5 = safe_float(c.pct_change(5).iloc[-1])
    ret20 = safe_float(c.pct_change(20).iloc[-1])
    sma20 = safe_float(c.rolling(20).mean().iloc[-1], c.iloc[-1])
    sma50 = safe_float(c.rolling(50).mean().iloc[-1], c.iloc[-1])
    rv = safe_float(rsi(c).iloc[-1], 50)
    score = 50 + np.clip(ret5 * 110, -12, 12) + np.clip(ret20 * 55, -10, 10)
    score += np.clip((c.iloc[-1] / sma20 - 1) * 70, -8, 8)
    score += np.clip((c.iloc[-1] / sma50 - 1) * 45, -7, 7)
    score += np.clip((rv - 50) * 0.10, -5, 5)
    score = safe_float(np.clip(score, 1, 99), 50)
    signal = "BUY" if score >= 65 else "SELL" if score <= 35 else "HOLD"
    mult = 3 if horizon == "WEEK" else 7
    vol = safe_float(c.pct_change().rolling(20).std().iloc[-1], 0.01)
    expected = safe_float(np.clip((score - 50) / 50 * max(vol, 0.008) * mult, -0.25, 0.25))
    p_up = safe_float(np.clip(0.5 + (score - 50) / 115, 0.02, 0.98), 0.5)
    return {"signal": signal, "score": round(score, 1), "p_up": round(p_up, 3), "expected_return": round(expected, 4)}

def analyze(ticker: str, capital: float = 10000.0, risk_pct: float = 1.0) -> Dict:
    ticker = ticker.strip().upper()
    pre = premarket_signal(ticker)
    session = market_session()

    # Confirm only after/inside regular session.
    confirmation = (
        intraday_confirmation(ticker, pre)
        if session == "REGULAR SESSION"
        else {
            "status": "WAIT_FOR_OPEN" if session == "PRE-MARKET" else "OUTSIDE_REGULAR",
            "signal": pre["signal"],
            "message": f"Sessione attuale: {session}.",
        }
    )

    plan = build_trade_plan(pre, confirmation if confirmation.get("status") in ("CONFIRMED", "INVALIDATED") else None, capital, risk_pct)

    return {
        "ticker": ticker,
        "session": session,
        "day": pre,
        "confirmation": confirmation,
        "plan": plan,
        "week": medium_signal(ticker, "WEEK"),
        "month": medium_signal(ticker, "MONTH"),
        "timestamp": datetime.now(NY).isoformat(),
    }

def scanner(universe: List[str], max_assets: int = 15) -> pd.DataFrame:
    rows = []
    for ticker in universe[:max_assets]:
        try:
            x = analyze(ticker)
            rows.append({
                "ticker": ticker,
                "day": x["day"]["signal"],
                "day_score": x["day"]["score"],
                "day_expected": x["day"]["expected_return"],
                "week": x["week"]["signal"],
                "week_score": x["week"]["score"],
                "week_expected": x["week"]["expected_return"],
                "month": x["month"]["signal"],
                "month_score": x["month"]["score"],
                "month_expected": x["month"]["expected_return"],
            })
        except Exception:
            continue
    return pd.DataFrame(rows)
