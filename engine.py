from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from functools import lru_cache
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import feedparser
import numpy as np
import pandas as pd
import yfinance as yf
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.metrics import accuracy_score, brier_score_loss, mean_absolute_error
from sklearn.preprocessing import StandardScaler

try:
    import pandas_market_calendars as mcal
except Exception:
    mcal = None

from config import BENCHMARKS, DEFAULTS

NY = ZoneInfo("America/New_York")
ROME = ZoneInfo("Europe/Rome")
BERLIN = ZoneInfo("Europe/Berlin")
PARIS = ZoneInfo("Europe/Paris")
AMSTERDAM = ZoneInfo("Europe/Amsterdam")
MADRID = ZoneInfo("Europe/Madrid")

DB_PATH = Path(__file__).with_name("paper_trades.db")


def safe_float(x, default=0.0) -> float:
    try:
        y = float(x)
        return default if not np.isfinite(y) else y
    except Exception:
        return default


def safe_pct(a, b, default=0.0) -> float:
    a, b = safe_float(a), safe_float(b)
    if b == 0:
        return default
    return a / b - 1.0


def clean_series(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _flatten(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    idx = pd.to_datetime(df.index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    df = df.copy()
    df.index = idx
    return df


@lru_cache(maxsize=256)
def fetch(ticker: str, period="1y", interval="1d", prepost=False) -> pd.DataFrame:
    ticker = ticker.strip().upper()
    last_err = None
    # Prefer Ticker.history; it is less fragile across yfinance versions.
    try:
        obj = yf.Ticker(ticker)
        df = obj.history(period=period, interval=interval, prepost=prepost, auto_adjust=False)
        df = _flatten(df)
        if not df.empty:
            return df[[c for c in ["Open","High","Low","Close","Volume"] if c in df.columns]].dropna(how="all")
    except Exception as exc:
        last_err = exc
    try:
        df = yf.download(ticker, period=period, interval=interval, prepost=prepost,
                         auto_adjust=False, progress=False, threads=False)
        df = _flatten(df)
        if not df.empty:
            return df[[c for c in ["Open","High","Low","Close","Volume"] if c in df.columns]].dropna(how="all")
    except Exception as exc:
        last_err = exc
    raise RuntimeError(f"Dati non disponibili per {ticker}: {last_err}")


def rsi(series: pd.Series, n=14) -> pd.Series:
    d = clean_series(series).diff()
    gain = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.fillna(50)


def atr(df: pd.DataFrame, n=14) -> pd.Series:
    c = clean_series(df["Close"])
    prev = c.shift(1)
    tr = pd.concat([
        clean_series(df["High"]) - clean_series(df["Low"]),
        (clean_series(df["High"]) - prev).abs(),
        (clean_series(df["Low"]) - prev).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=3).mean().bfill()


def daily_features(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    c = clean_series(x["Close"])
    ret = c.pct_change()
    x["ret1"] = c.pct_change(1)
    x["ret3"] = c.pct_change(3)
    x["ret5"] = c.pct_change(5)
    x["ret10"] = c.pct_change(10)
    x["ret20"] = c.pct_change(20)
    x["vol5"] = ret.rolling(5, min_periods=3).std()
    x["vol20"] = ret.rolling(20, min_periods=10).std()
    x["sma10"] = c.rolling(10, min_periods=5).mean()
    x["sma20"] = c.rolling(20, min_periods=10).mean()
    x["sma50"] = c.rolling(50, min_periods=20).mean()
    x["rsi14"] = rsi(c)
    x["atr14"] = atr(x)
    v = clean_series(x["Volume"]).fillna(0)
    vm = v.rolling(20, min_periods=5).mean()
    vs = v.rolling(20, min_periods=5).std().replace(0, np.nan)
    x["volume_z"] = ((v - vm) / vs).fillna(0)
    x["dist_sma20"] = (c / x["sma20"] - 1).fillna(0)
    x["dist_sma50"] = (c / x["sma50"] - 1).fillna(0)
    x["range_pct"] = ((clean_series(x["High"]) - clean_series(x["Low"])) / c).replace([np.inf,-np.inf],0).fillna(0)
    return x.replace([np.inf,-np.inf], np.nan).dropna()


def intraday_regular(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    x = df.copy()
    tz, (op, cl) = _asset_tz_session(ticker)
    local = x.index.tz_convert(tz)
    mins = local.hour * 60 + local.minute
    opm = op.hour * 60 + op.minute
    clm = cl.hour * 60 + cl.minute
    mask = (mins >= opm) & (mins < clm)
    return x.loc[mask].copy()


def intraday_features(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    c = clean_series(x["Close"])
    v = clean_series(x["Volume"]).fillna(0)
    x["ret5m"] = c.pct_change(1)
    x["ret15m"] = c.pct_change(3)
    x["ret30m"] = c.pct_change(6)
    x["ret60m"] = c.pct_change(12)
    x["vwap"] = (c * v).cumsum() / v.replace(0, np.nan).cumsum()
    x["vwap"] = x["vwap"].fillna(c.expanding().mean())
    x["vwap_dist"] = c / x["vwap"] - 1
    x["vol20"] = c.pct_change().rolling(20, min_periods=5).std().fillna(0)
    vm = v.rolling(20, min_periods=5).mean().replace(0, np.nan)
    x["volume_ratio"] = (v / vm).replace([np.inf,-np.inf], np.nan).fillna(1)
    x["date"] = x.index.tz_convert(NY).date
    x["bar_num"] = x.groupby("date").cumcount()
    highs, lows = [], []
    for _, g in x.groupby("date"):
        oh = safe_float(g["High"].head(6).max(), safe_float(g["High"].max()))
        ol = safe_float(g["Low"].head(6).min(), safe_float(g["Low"].min()))
        highs.extend([oh] * len(g)); lows.extend([ol] * len(g))
    x["or_high"] = highs
    x["or_low"] = lows
    x["or_pos"] = ((c - x["or_low"]) / (x["or_high"] - x["or_low"]).replace(0,np.nan)).fillna(0.5)
    return x.replace([np.inf,-np.inf],np.nan).fillna(0)


def market_snapshot() -> Dict[str, Dict[str, float]]:
    out = {}
    for name, ticker in BENCHMARKS.items():
        try:
            d = daily_features(fetch(ticker, "3mo", "1d"))
            row = d.iloc[-1]
            out[name] = {
                "ret1": safe_float(row["ret1"]),
                "ret5": safe_float(row["ret5"]),
                "rsi": safe_float(row["rsi14"], 50),
            }
        except Exception:
            out[name] = {"ret1":0.0,"ret5":0.0,"rsi":50.0}
    return out


def _asset_tz_session(ticker: str):
    t = ticker.upper()
    if t.endswith(".MI"):
        return ROME, (time(9,0), time(17,30))
    if t.endswith(".DE"):
        return BERLIN, (time(9,0), time(17,30))
    if t.endswith(".PA"):
        return PARIS, (time(9,0), time(17,30))
    if t.endswith(".AS"):
        return AMSTERDAM, (time(9,0), time(17,30))
    if t.endswith(".MC"):
        return MADRID, (time(9,0), time(17,30))
    return NY, (time(9,30), time(16,0))


def market_status(ticker: str, now=None) -> Dict:
    tz, (op, cl) = _asset_tz_session(ticker)
    now = now or datetime.now(tz)
    weekday = now.weekday() < 5
    local_t = now.timetz().replace(tzinfo=None)
    is_open = weekday and op <= local_t < cl
    pre = weekday and (local_t >= (time(4,0) if tz == NY else time(7,0))) and local_t < op
    post = weekday and local_t >= cl and local_t < (time(20,0) if tz == NY else time(19,0))
    if is_open:
        status = "REGULAR SESSION"
        next_event = cl
    elif pre:
        status = "PRE-MARKET"
        next_event = op
    elif post:
        status = "AFTER-HOURS"
        next_event = op
    else:
        status = "CLOSED"
        next_event = op
    return {"status":status,"timezone":str(tz),"open":op,"close":cl,"is_open":is_open,"is_pre":pre,"is_post":post,"now":now,"next_event":next_event}


def latest_premarket(ticker: str):
    # Extended-hours pre-market is implemented for US-listed assets.
    # EU venues fall back to previous close because their pre-open auction is not
    # represented consistently by Yahoo Finance intraday data.
    if _asset_tz_session(ticker)[0] != NY:
        return pd.DataFrame()
    df = fetch(ticker, "3d", "5m", prepost=True)
    local = df.index.tz_convert(NY)
    minutes = local.hour*60+local.minute
    pre_mask = (minutes >= 240) & (minutes < 570)
    return df.loc[pre_mask].copy()


def latest_regular(ticker: str):
    df = fetch(ticker, "5d", "5m", prepost=False)
    return intraday_regular(df, ticker)


def _daily_context(ticker: str):
    d = daily_features(fetch(ticker, "2y", "1d"))
    row = d.iloc[-1]
    return {
        "df": d,
        "price": safe_float(row["Close"]),
        "ret1": safe_float(row["ret1"]),
        "ret5": safe_float(row["ret5"]),
        "ret20": safe_float(row["ret20"]),
        "vol20": safe_float(row["vol20"]),
        "sma20": safe_float(row["sma20"]),
        "sma50": safe_float(row["sma50"]),
        "rsi": safe_float(row["rsi14"],50),
        "atr_pct": safe_float(row["atr14"]/row["Close"],0.01),
    }


def premarket_analysis(ticker: str) -> Dict:
    t = ticker.strip().upper()
    ctx = _daily_context(t)
    mkt = market_snapshot()
    try:
        pre = latest_premarket(t)
    except Exception:
        pre = pd.DataFrame()
    indicative = safe_float(pre["Close"].iloc[-1], ctx["price"]) if not pre.empty else ctx["price"]
    gap = safe_pct(indicative, ctx["price"])
    pre_ret = safe_float(pre["Close"].pct_change().dropna().iloc[-1]) if len(pre) > 1 else 0.0
    vol_ratio = 1.0
    if not pre.empty:
        v = clean_series(pre["Volume"]).fillna(0)
        base = v.rolling(20,min_periods=5).mean().iloc[-1]
        vol_ratio = safe_float(v.iloc[-1] / base) if safe_float(base) else 1.0

    score = 50.0; drivers=[]
    def add(points, txt):
        nonlocal score
        p=safe_float(points); score += p
        if abs(p)>=3: drivers.append((abs(p),txt))
    add(np.clip(gap*250,-15,15),"gap pre-market")
    add(np.clip(pre_ret*1200,-10,10),"momentum pre-market")
    add(np.clip(ctx["ret5"]*100,-10,10),"trend 5 giorni")
    add(np.clip(ctx["ret20"]*50,-8,8),"trend 20 giorni")
    add(np.clip((ctx["rsi"]-50)*0.10,-5,5),"RSI")
    add(np.clip(mkt["QQQ"]["ret1"]*80,-6,6),"Nasdaq / QQQ")
    add(np.clip(mkt["SPY"]["ret1"]*70,-5,5),"S&P 500")
    add(np.clip(np.nan_to_num((vol_ratio-1)*5),-5,5),"volume pre-market")
    score=float(np.clip(score,1,99))
    p_up=float(np.clip(0.5+(score-50)/115,0.02,0.98))
    expected=float(np.clip((p_up-0.5)*max(ctx["atr_pct"],0.005)*2,-0.10,0.10))
    confidence=float(np.clip(0.50+abs(p_up-0.5)*0.9,0.50,0.95))
    signal="PRE-BUY" if p_up>=0.65 else "PRE-SELL" if p_up<=0.35 else "WAIT"
    return {
        "ticker":t,"signal":signal,"score":round(score,1),"p_up":p_up,"p_down":1-p_up,
        "expected_return":expected,"confidence":confidence,"prev_close":ctx["price"],
        "indicative":indicative,"gap":gap,"drivers":[x[1] for x in sorted(drivers,reverse=True)[:6]],
        "data_ok":bool(ctx["df"].shape[0]>=60),"pre_bars":len(pre),"daily_points":len(ctx["df"]),
    }


def intraday_state(ticker: str) -> Dict:
    status = market_status(ticker)
    try:
        reg = latest_regular(ticker)
        x = intraday_features(reg)
        local = x.index.tz_convert(NY)
        today = local.date[-1]
        today_x = x.loc[local.date == today]
        if today_x.empty:
            return {"status":"WAIT_FOR_OPEN","bars":0}
        row=today_x.iloc[-1]
        return {
            "status":"LIVE" if status["is_open"] else status["status"],
            "bars":len(today_x),"open":safe_float(today_x["Open"].iloc[0]),
            "current":safe_float(row["Close"]),"high":safe_float(today_x["High"].max()),
            "low":safe_float(today_x["Low"].min()),"ret5m":safe_float(row["ret5m"]),
            "ret15m":safe_float(row["ret15m"]),"ret30m":safe_float(row["ret30m"]),
            "ret60m":safe_float(row["ret60m"]),"vwap":safe_float(row["vwap"]),
            "vwap_dist":safe_float(row["vwap_dist"]),"volume_ratio":safe_float(row["volume_ratio"],1),
            "or_high":safe_float(row["or_high"]),"or_low":safe_float(row["or_low"]),
            "or_pos":safe_float(row["or_pos"],0.5),"updated":str(x.index[-1]),
        }
    except Exception as exc:
        return {"status":"DATA_ERROR","bars":0,"error":str(exc)}


def confirm_open(ticker: str, pre: Dict) -> Dict:
    state=intraday_state(ticker)
    if state.get("bars",0)==0:
        return {"status":"WAIT_FOR_OPEN","signal":"WAIT","message":"Aspetta l'apertura della sessione regolare."}
    if state["bars"] < 1:
        return {"status":"WAIT_FOR_OPEN","signal":"WAIT","message":"Prima barra non disponibile."}
    bullish = state["current"] > state["open"] and state["current"] >= state["vwap"]
    bearish = state["current"] < state["open"] and state["current"] <= state["vwap"]
    # Require the 5-minute bar to be complete before strong confirmation.
    if state["bars"] == 1:
        signal = pre["signal"]
        st = "CONFIRMING"
    else:
        if pre["signal"] == "PRE-BUY" and bullish:
            signal="ENTER BUY"; st="CONFIRMED"
        elif pre["signal"] == "PRE-SELL" and bearish:
            signal="ENTER SELL"; st="CONFIRMED"
        elif pre["signal"] in ("PRE-BUY","PRE-SELL"):
            signal="WAIT / INVALIDATED"; st="INVALIDATED"
        else:
            signal="WAIT"; st="WAIT"
    return {"status":st,"signal":signal,"message":f"Barre sessione odierna: {state['bars']}",**state}


def trade_plan(pre: Dict, confirm: Dict, capital=10000, risk_pct=0.01) -> Dict:
    base=safe_float(confirm.get("current"),safe_float(pre.get("indicative")))
    atr_proxy=max(abs(safe_float(pre.get("expected_return")))*0.75,0.006)
    side=None
    if confirm.get("status") == "CONFIRMED" and confirm.get("signal")=="ENTER BUY":
        side="LONG"
    elif confirm.get("status") == "CONFIRMED" and confirm.get("signal")=="ENTER SELL":
        side="SHORT"
    elif pre.get("signal")=="PRE-BUY":
        side="LONG"
    elif pre.get("signal")=="PRE-SELL":
        side="SHORT"
    if side is None:
        return {"status":"NO TRADE","action":"NON ENTRARE","entry":base,"stop":base,"target":base,"rr":0.0,"shares":0}
    risk_per_share=base*atr_proxy*1.20
    if side=="LONG":
        entry=base; stop=base-risk_per_share; target=base+risk_per_share*2.0
        action="ENTER LONG AFTER CONFIRMATION" if confirm.get("status")!="CONFIRMED" else "ENTER LONG"
    else:
        entry=base; stop=base+risk_per_share; target=base-risk_per_share*2.0
        action="ENTER SHORT AFTER CONFIRMATION" if confirm.get("status")!="CONFIRMED" else "ENTER SHORT"
    rr=abs(target-entry)/max(abs(entry-stop),1e-9)
    risk_amount=max(0,safe_float(capital)*safe_float(risk_pct))
    shares=int(max(0,math.floor(risk_amount/max(abs(entry-stop),1e-9)))) if risk_amount else 0
    return {"status":"READY" if rr>=1.5 else "NO TRADE","action":action,"entry":entry,"stop":stop,"target":target,"rr":rr,"shares":shares,"risk_amount":risk_amount}


def _make_ml_frame(d: pd.DataFrame, horizon_days: int):
    x=daily_features(d).copy()
    feature_cols=["ret1","ret3","ret5","ret10","ret20","vol5","vol20","dist_sma20","dist_sma50","rsi14","volume_z","range_pct"]
    for col in feature_cols:
        x[col]=pd.to_numeric(x[col],errors="coerce")
    x["future_ret"]=x["Close"].shift(-horizon_days)/x["Close"]-1
    x["target"]=(x["future_ret"]>0).astype(int)
    x=x.dropna(subset=feature_cols+['future_ret']).copy()
    return x, feature_cols


def train_medium_model(ticker: str, horizon: str) -> Dict:
    days=5 if horizon=="WEEK" else 20
    d=fetch(ticker,"5y","1d")
    frame,features=_make_ml_frame(d,days)
    if len(frame)<150:
        raise RuntimeError("Storico insufficiente per il modello ML")
    split=max(int(len(frame)*0.8),100)
    train=frame.iloc[:split]; test=frame.iloc[split:]
    Xtr=train[features]; ytr=train["target"]
    Xte=test[features]; yte=test["target"]
    clf=HistGradientBoostingClassifier(max_iter=180,learning_rate=0.05,max_depth=3,random_state=42)
    reg=HistGradientBoostingRegressor(max_iter=180,learning_rate=0.05,max_depth=3,loss="squared_error",random_state=42)
    clf.fit(Xtr,ytr); reg.fit(Xtr,train["future_ret"])
    p=clf.predict_proba(Xte)[:,1]
    pred=(p>=0.5).astype(int)
    accuracy=safe_float(accuracy_score(yte,pred),0.5)
    brier=safe_float(brier_score_loss(yte,p),0.25)
    mae=safe_float(mean_absolute_error(test["future_ret"],reg.predict(Xte)),0.1)
    latest=frame.iloc[-1][features].to_frame().T
    p_up=safe_float(clf.predict_proba(latest)[0,1],0.5)
    expected=safe_float(reg.predict(latest)[0],0.0)
    return {"signal":"BUY" if p_up>=0.65 else "SELL" if p_up<=0.35 else "HOLD","p_up":p_up,"expected_return":expected,"accuracy":accuracy,"brier":brier,"mae":mae,"train_rows":len(train),"test_rows":len(test)}


def news(ticker: str, limit=10) -> List[Dict]:
    out=[]
    try:
        y=yf.Ticker(ticker)
        for n in (y.news or [])[:limit]:
            c=n.get("content",{}) if isinstance(n,dict) else {}
            title=c.get("title") or n.get("title") or "News"
            publisher=(c.get("provider") or {}).get("displayName") or n.get("publisher") or "Source"
            url=(c.get("clickThroughUrl") or {}).get("url") if isinstance(c.get("clickThroughUrl"),dict) else n.get("link")
            out.append({"title":title,"publisher":publisher,"url":url,"published":c.get("pubDate")})
    except Exception:
        pass
    if len(out)<limit:
        try:
            feed=feedparser.parse(f"https://news.google.com/rss/search?q={ticker}+stock&hl=en-US&gl=US&ceid=US:en")
            for e in feed.entries[:limit-len(out)]:
                out.append({"title":e.get("title","News"),"publisher":e.get("source",{}).get("title","Google News") if hasattr(e.get("source"),"get") else "Google News","url":e.get("link"),"published":e.get("published")})
        except Exception:
            pass
    return out[:limit]


def init_db():
    conn=sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS paper_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT, ticker TEXT, horizon TEXT, side TEXT, signal TEXT,
        entry REAL, stop REAL, target REAL, shares INTEGER, price_exit REAL,
        pnl REAL, status TEXT, notes TEXT)""")
    conn.commit(); conn.close()


def log_signal(ticker: str, horizon: str, side: str, signal: str, entry, stop, target, shares, notes=""):
    init_db(); conn=sqlite3.connect(DB_PATH)
    conn.execute("INSERT INTO paper_trades(created_at,ticker,horizon,side,signal,entry,stop,target,shares,status,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                 (datetime.utcnow().isoformat(),ticker,horizon,side,signal,safe_float(entry),safe_float(stop),safe_float(target),int(shares),"OPEN",notes))
    conn.commit(); conn.close()


def recent_paper_trades(limit=30)->pd.DataFrame:
    init_db(); conn=sqlite3.connect(DB_PATH)
    df=pd.read_sql_query("SELECT * FROM paper_trades ORDER BY id DESC LIMIT ?",conn,params=(limit,)); conn.close(); return df


def analyze_asset(ticker: str, capital=10000, risk_pct=0.01) -> Dict:
    pre=premarket_analysis(ticker)
    confirm=confirm_open(ticker,pre)
    plan=trade_plan(pre,confirm,capital,risk_pct)
    try: week=train_medium_model(ticker,"WEEK")
    except Exception as e: week={"signal":"N/A","error":str(e),"p_up":0.5,"expected_return":0.0}
    try: month=train_medium_model(ticker,"MONTH")
    except Exception as e: month={"signal":"N/A","error":str(e),"p_up":0.5,"expected_return":0.0}
    return {"ticker":ticker,"clock":market_status(ticker),"pre":pre,"confirm":confirm,"plan":plan,"week":week,"month":month,"news":news(ticker),"generated_at":datetime.now(NY).isoformat()}


def scanner(universe: List[str], max_assets: int=15)->pd.DataFrame:
    rows=[]
    for t in universe[:max_assets]:
        try:
            a=analyze_asset(t)
            rows.append({
                "ticker":t,
                "DAY":a["pre"]["signal"],"DAY_prob":round(a["pre"]["p_up"]*100,1),"DAY_exp":round(a["pre"]["expected_return"]*100,2),
                "WEEK":a["week"]["signal"],"WEEK_prob":round(a["week"]["p_up"]*100,1),"WEEK_exp":round(a["week"]["expected_return"]*100,2),
                "MONTH":a["month"]["signal"],"MONTH_prob":round(a["month"]["p_up"]*100,1),"MONTH_exp":round(a["month"]["expected_return"]*100,2),
            })
        except Exception:
            continue
    return pd.DataFrame(rows)

