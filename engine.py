from __future__ import annotations
from datetime import datetime, timezone
from typing import Dict, List
import numpy as np
import pandas as pd
import yfinance as yf


def clean_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        raise ValueError("Nessun dato ricevuto da Yahoo Finance")
    if isinstance(df.columns, pd.MultiIndex):
        # Keep OHLCV columns for a single ticker.
        lvl0 = list(df.columns.get_level_values(0))
        if set(["Open","High","Low","Close","Volume"]).issubset(set(lvl0)):
            df = df.copy()
            df.columns = df.columns.get_level_values(0)
        else:
            df = df.copy()
            df.columns = [c[-1] if isinstance(c, tuple) else c for c in df.columns]
    df = df.copy()
    cols = [c for c in ["Open","High","Low","Close","Volume"] if c in df.columns]
    if len(cols) < 4:
        raise ValueError(f"Colonne OHLCV non disponibili: {list(df.columns)}")
    df = df[cols].dropna(subset=["Close"])
    df.index = pd.to_datetime(df.index)
    return df


def fetch_history(ticker: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
    # Ticker.history is more robust than the older multi-download path.
    obj = yf.Ticker(ticker)
    df = obj.history(period=period, interval=interval, auto_adjust=False)
    return clean_ohlcv(df)


def rsi(series: pd.Series, n: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100/(1+rs)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev = df["Close"].shift(1)
    tr = pd.concat([(df["High"]-df["Low"]),(df["High"]-prev).abs(),(df["Low"]-prev).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def add_daily_features(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    c = x["Close"]
    x["ret1"] = c.pct_change(1)
    x["ret5"] = c.pct_change(5)
    x["ret10"] = c.pct_change(10)
    x["ret20"] = c.pct_change(20)
    x["vol20"] = c.pct_change().rolling(20).std() * np.sqrt(252)
    x["sma20"] = c.rolling(20).mean()
    x["sma50"] = c.rolling(50).mean()
    x["rsi14"] = rsi(c,14)
    x["atr14"] = atr(x,14)
    x["volz"] = (x["Volume"]-x["Volume"].rolling(20).mean()) / x["Volume"].rolling(20).std()
    x["dist20"] = c/x["sma20"]-1
    x["dist50"] = c/x["sma50"]-1
    return x.dropna()


def add_intraday_features(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    c=x["Close"]
    x["ret5"] = c.pct_change(1)
    x["ret15"] = c.pct_change(3)
    x["ret30"] = c.pct_change(6)
    x["ret60"] = c.pct_change(12)
    x["vwap"] = (c*x["Volume"]).cumsum()/x["Volume"].replace(0,np.nan).cumsum()
    x["vwapdist"] = c/x["vwap"]-1
    x["volratio"] = x["Volume"]/x["Volume"].rolling(20).mean().replace(0,np.nan)
    x["date"] = x.index.date
    x["bar"] = x.groupby("date").cumcount()
    # Opening range = first 30 minutes for 5m data.
    g = x.groupby("date", group_keys=False)
    x["orhigh"] = g["High"].transform(lambda s: s.head(6).max())
    x["orlow"] = g["Low"].transform(lambda s: s.head(6).min())
    x["orpos"] = (c-x["orlow"])/(x["orhigh"]-x["orlow"]).replace(0,np.nan)
    return x.dropna()


def market_ret(symbol: str) -> tuple[float,float]:
    try:
        d=add_daily_features(fetch_history(symbol,"3mo","1d"))
        z=d.iloc[-1]
        return float(z["ret1"]), float(z["ret5"])
    except Exception:
        return 0.0,0.0


def score_day(ticker: str) -> Dict:
    intr = add_intraday_features(fetch_history(ticker,"5d","5m"))
    daily = add_daily_features(fetch_history(ticker,"1y","1d"))
    a=intr.iloc[-1]; d=daily.iloc[-1]
    us1,us5=market_ret("SPY"); tech1,tech5=market_ret("QQQ")
    score=50.0; drivers=[]
    def add(v,text):
        nonlocal score
        score += float(v)
        if abs(v)>=3: drivers.append((abs(v), text))
    add(np.clip(a["ret15"]*1500,-12,12),"momentum 15m")
    add(np.clip(a["ret60"]*800,-10,10),"momentum 60m")
    add(np.clip(a["vwapdist"]*1000,-10,10),"prezzo rispetto al VWAP")
    add(np.clip((a["volratio"]-1)*7,-7,7),"volume relativo")
    add(np.clip(d["ret5"]*100,-8,8),"trend 5 giorni")
    add(np.clip(d["ret20"]*60,-7,7),"trend 20 giorni")
    add(np.clip(d["dist20"]*80,-6,6),"trend SMA20")
    add(np.clip(us1*80,-5,5),"S&P 500")
    add(np.clip(tech1*60,-5,5),"Nasdaq/QQQ")
    add(np.clip((d["rsi14"]-50)*0.10,-4,4),"RSI")
    score=float(np.clip(score,1,99))
    signal="BUY" if score>=65 else "SELL" if score<=35 else "HOLD"
    atrpct=max(float(d["atr14"]/d["Close"]),0.005)
    exp=float(np.clip((score-50)/50*atrpct*1.75,-0.06,0.06))
    p_up=float(np.clip(0.50+(score-50)/100,0.01,0.99))
    return {"ticker":ticker,"horizon":"DAY","signal":signal,"score":round(score,1),"p_up":p_up,"p_down":1-p_up,"expected_return":exp,"confidence":float(0.50+abs(score-50)/100),"price":float(a["Close"]),"vwap":float(a["vwap"]),"rsi":float(d["rsi14"]),"drivers":[t for _,t in sorted(drivers,reverse=True)[:5]],"updated":str(intr.index[-1])}


def score_medium(ticker: str, horizon: str) -> Dict:
    ddf=add_daily_features(fetch_history(ticker,"1y","1d")); d=ddf.iloc[-1]
    us1,us5=market_ret("SPY"); tech1,tech5=market_ret("QQQ")
    score=50.0; drivers=[]
    def add(v,text):
        nonlocal score
        score += float(v)
        if abs(v)>=3: drivers.append((abs(v),text))
    add(np.clip(d["ret5"]*100,-12,12),"momentum 5 giorni")
    add(np.clip(d["ret20"]*55,-10,10),"momentum 20 giorni")
    add(np.clip(d["ret10"]*70,-8,8),"momentum 10 giorni")
    add(np.clip(d["dist20"]*70,-8,8),"trend SMA20")
    add(np.clip(d["dist50"]*45,-7,7),"trend SMA50")
    add(np.clip((d["rsi14"]-50)*0.12,-6,6),"RSI")
    add(np.clip(us5*35,-5,5),"mercato")
    add(np.clip(tech5*30,-5,5),"tech market")
    score=float(np.clip(score,1,99))
    signal="BUY" if score>=65 else "SELL" if score<=35 else "HOLD"
    base=max(float(d["atr14"]/d["Close"]),0.008 if horizon=="WEEK" else 0.012)
    mult=3 if horizon=="WEEK" else 6
    exp=float(np.clip((score-50)/50*base*mult,-0.20,0.20))
    p_up=float(np.clip(0.50+(score-50)/110,0.02,0.98))
    return {"ticker":ticker,"horizon":horizon,"signal":signal,"score":round(score,1),"p_up":p_up,"p_down":1-p_up,"expected_return":exp,"confidence":float(0.50+abs(score-50)/90),"price":float(d["Close"]),"rsi":float(d["rsi14"]),"drivers":[t for _,t in sorted(drivers,reverse=True)[:5]],"updated":str(ddf.index[-1])}


def safe_news(ticker: str) -> List[Dict]:
    try:
        news = yf.Ticker(ticker).news
    except Exception:
        return []
    out=[]
    for n in (news or [])[:8]:
        c=n.get("content",{}) if isinstance(n,dict) else {}
        out.append({"title": c.get("title") or n.get("title") or "News", "publisher": (c.get("provider") or {}).get("displayName",""), "url": ((c.get("clickThroughUrl") or {}).get("url") if isinstance(c.get("clickThroughUrl"),dict) else None)})
    return out


def analyze_asset(ticker:str)->Dict:
    ticker=ticker.strip().upper()
    return {"ticker":ticker,"timestamp_utc":datetime.now(timezone.utc).isoformat(),"horizons":{"DAY":score_day(ticker),"WEEK":score_medium(ticker,"WEEK"),"MONTH":score_medium(ticker,"MONTH")},"news":safe_news(ticker)}


def scan_market(universe:List[str],max_assets:int)->pd.DataFrame:
    rows=[]
    for t in universe[:max_assets]:
        try:
            a=score_day(t); w=score_medium(t,"WEEK"); m=score_medium(t,"MONTH")
            rows.append({"ticker":t,"day_signal":a["signal"],"day_score":a["score"],"day_expected":a["expected_return"],"week_signal":w["signal"],"week_score":w["score"],"week_expected":w["expected_return"],"month_signal":m["signal"],"month_score":m["score"],"month_expected":m["expected_return"]})
        except Exception as e:
            rows.append({"ticker":t,"day_signal":"ERROR","day_score":np.nan,"day_expected":np.nan,"week_signal":"ERROR","week_score":np.nan,"week_expected":np.nan,"month_signal":"ERROR","month_score":np.nan,"month_expected":np.nan})
    return pd.DataFrame(rows)
