from __future__ import annotations

import time as time_module
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

try:
    import feedparser
except Exception:
    feedparser = None
import numpy as np
import pandas as pd
try:
    import yfinance as yf
except Exception:
    yf = None

from config import BENCHMARKS, MODEL_FEATURES

NY = ZoneInfo("America/New_York")
ROME = ZoneInfo("Europe/Rome")
BERLIN = ZoneInfo("Europe/Berlin")
PARIS = ZoneInfo("Europe/Paris")
AMSTERDAM = ZoneInfo("Europe/Amsterdam")
MADRID = ZoneInfo("Europe/Madrid")


class DataError(RuntimeError):
    pass


def safe_float(value, default: float = 0.0) -> float:
    try:
        value = float(value)
        return value if np.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def clean_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = [c[0] if isinstance(c, tuple) else c for c in out.columns]
    return out


def normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = _flatten_columns(df)
    idx = pd.to_datetime(out.index, errors="coerce")
    valid = ~idx.isna()
    out = out.loc[valid].copy()
    idx = idx[valid]
    if getattr(idx, "tz", None) is None:
        idx = idx.tz_localize("UTC")
    else:
        idx = idx.tz_convert("UTC")
    out.index = idx
    keep = [c for c in ["Open", "High", "Low", "Close", "Adj Close", "Volume"] if c in out.columns]
    out = out[keep]
    for col in ["Open", "High", "Low", "Close", "Adj Close", "Volume"]:
        if col in out.columns:
            out[col] = clean_numeric(out[col])
    if "Close" not in out.columns:
        return pd.DataFrame()
    return out.sort_index().dropna(subset=["Close"])


_CACHE: Dict[Tuple[str, str, str, bool], Tuple[float, pd.DataFrame]] = {}
_CACHE_TTL = {
    "1d": 300.0,
    "5m": 30.0,
    "15m": 60.0,
    "1h": 120.0,
}


def fetch(ticker: str, period: str = "1y", interval: str = "1d", prepost: bool = False, force: bool = False) -> pd.DataFrame:
    ticker = ticker.strip().upper()
    key = (ticker, period, interval, prepost)
    ttl = _CACHE_TTL.get(interval, 120.0)
    now = time_module.time()
    if not force and key in _CACHE and (now - _CACHE[key][0] < ttl):
        return _CACHE[key][1].copy()

    if yf is None:
        raise DataError("yfinance non installato: installa requirements.txt")
    errors: List[str] = []
    try:
        hist = yf.Ticker(ticker).history(
            period=period,
            interval=interval,
            prepost=prepost,
            auto_adjust=False,
            actions=False,
        )
        out = normalize_ohlcv(hist)
        if not out.empty:
            _CACHE[key] = (now, out)
            return out.copy()
        errors.append("Ticker.history vuoto")
    except Exception as exc:
        errors.append(f"Ticker.history: {exc}")

    try:
        hist = yf.download(
            ticker,
            period=period,
            interval=interval,
            prepost=prepost,
            auto_adjust=False,
            progress=False,
            threads=False,
            actions=False,
        )
        out = normalize_ohlcv(hist)
        if not out.empty:
            _CACHE[key] = (now, out)
            return out.copy()
        errors.append("yf.download vuoto")
    except Exception as exc:
        errors.append(f"yf.download: {exc}")

    raise DataError(f"Dati non disponibili per {ticker}. {' | '.join(errors)}")


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    c = clean_numeric(close)
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    high = clean_numeric(df["High"])
    low = clean_numeric(df["Low"])
    close = clean_numeric(df["Close"])
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.rolling(n, min_periods=max(3, n // 3)).mean().bfill()


def daily_features(df: pd.DataFrame) -> pd.DataFrame:
    x = normalize_ohlcv(df)
    if x.empty or len(x) < 30:
        return pd.DataFrame()
    close = clean_numeric(x["Close"])
    ret = close.pct_change()
    x["ret1"] = close.pct_change(1)
    x["ret3"] = close.pct_change(3)
    x["ret5"] = close.pct_change(5)
    x["ret10"] = close.pct_change(10)
    x["ret20"] = close.pct_change(20)
    x["vol5"] = ret.rolling(5, min_periods=3).std()
    x["vol20"] = ret.rolling(20, min_periods=10).std()
    x["sma10"] = close.rolling(10, min_periods=5).mean()
    x["sma20"] = close.rolling(20, min_periods=10).mean()
    x["sma50"] = close.rolling(50, min_periods=20).mean()
    x["sma200"] = close.rolling(200, min_periods=50).mean()
    x["rsi14"] = rsi(close)
    x["atr14"] = atr(x)
    x["atr_pct"] = (x["atr14"] / close).replace([np.inf, -np.inf], np.nan)
    volume = clean_numeric(x.get("Volume", pd.Series(index=x.index, dtype=float))).fillna(0)
    vmean = volume.rolling(20, min_periods=5).mean()
    vstd = volume.rolling(20, min_periods=5).std().replace(0, np.nan)
    x["volume_z"] = ((volume - vmean) / vstd).fillna(0)
    x["dist_sma20"] = (close / x["sma20"] - 1).replace([np.inf, -np.inf], np.nan)
    x["dist_sma50"] = (close / x["sma50"] - 1).replace([np.inf, -np.inf], np.nan)
    x["range_pct"] = ((clean_numeric(x["High"]) - clean_numeric(x["Low"])) / close).replace([np.inf, -np.inf], np.nan)
    x["gap"] = (clean_numeric(x["Open"]) / close.shift(1) - 1).replace([np.inf, -np.inf], np.nan)
    return x.replace([np.inf, -np.inf], np.nan).dropna(subset=["ret1", "vol20", "sma20", "rsi14", "atr_pct"])


def benchmark_features(start: pd.Timestamp, end: Optional[pd.Timestamp] = None) -> pd.DataFrame:
    frames = []
    for name, ticker in BENCHMARKS.items():
        try:
            raw = fetch(ticker, "3y", "1d")
            d = daily_features(raw)
            if d.empty:
                continue
            cols = d[["ret1", "ret5"]].rename(columns={"ret1": f"bench_{name.lower()}_ret1", "ret5": f"bench_{name.lower()}_ret5"})
            if name in {"VIX", "TNX", "DXY", "OIL", "GOLD", "BTC"}:
                cols = cols[[c for c in cols.columns if c.endswith("_ret1")]]
            frames.append(cols)
        except Exception:
            continue
    if not frames:
        return pd.DataFrame(index=pd.date_range(start=start, end=end or start, freq="D"))
    out = pd.concat(frames, axis=1).sort_index()
    return out.loc[(out.index >= start) & (end is None or out.index <= end)].ffill().fillna(0)


def add_market_features(d: pd.DataFrame) -> pd.DataFrame:
    if d.empty:
        return d
    bench = benchmark_features(d.index.min(), d.index.max())
    if bench.empty:
        for feature in MODEL_FEATURES:
            if feature.startswith("bench_"):
                d[feature] = 0.0
        return d
    out = d.copy()
    aligned = bench.reindex(out.index).ffill().fillna(0)
    for col in aligned.columns:
        out[col] = aligned[col]
    required_bench = [c for c in MODEL_FEATURES if c.startswith("bench_")]
    for col in required_bench:
        if col not in out:
            out[col] = 0.0
    return out


def latest_previous_close(ticker: str) -> float:
    d = daily_features(fetch(ticker, "6mo", "1d"))
    return safe_float(d["Close"].iloc[-1])


def regular_session_df(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    from market_clock import session_hours
    if df.empty:
        return df
    tz, (op, cl) = session_hours(ticker)
    local = df.index.tz_convert(tz)
    minutes = local.hour * 60 + local.minute
    opm = op.hour * 60 + op.minute
    clm = cl.hour * 60 + cl.minute
    return df.loc[(minutes >= opm) & (minutes < clm)].copy()


def premarket_df(ticker: str) -> pd.DataFrame:
    tz, _ = session_hours(ticker)
    if tz != NY:
        return pd.DataFrame()
    df = fetch(ticker, "3d", "5m", prepost=True)
    local = df.index.tz_convert(NY)
    minutes = local.hour * 60 + local.minute
    return df.loc[(minutes >= 240) & (minutes < 570)].copy()


def latest_regular(ticker: str) -> pd.DataFrame:
    return regular_session_df(fetch(ticker, "10d", "5m", prepost=False), ticker)


def intraday_features(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    x = normalize_ohlcv(df)
    x = regular_session_df(x, ticker)
    if x.empty:
        return x
    close = clean_numeric(x["Close"])
    volume = clean_numeric(x.get("Volume", pd.Series(index=x.index, dtype=float))).fillna(0)
    x["ret5m"] = close.pct_change(1)
    x["ret15m"] = close.pct_change(3)
    x["ret30m"] = close.pct_change(6)
    x["ret60m"] = close.pct_change(12)
    pv = (close * volume).groupby(x.index.tz_convert(NY).date).cumsum()
    vv = volume.groupby(x.index.tz_convert(NY).date).cumsum().replace(0, np.nan)
    x["vwap"] = (pv / vv).fillna(close)
    x["vwap_dist"] = (close / x["vwap"] - 1).replace([np.inf, -np.inf], np.nan).fillna(0)
    x["range"] = (clean_numeric(x["High"]) - clean_numeric(x["Low"]))
    vm = volume.rolling(20, min_periods=5).mean().replace(0, np.nan)
    x["volume_ratio"] = (volume / vm).replace([np.inf, -np.inf], np.nan).fillna(1)
    dates = x.index.tz_convert(NY).date
    x["session_date"] = dates
    x["bar_num"] = x.groupby("session_date").cumcount() + 1
    or_high = x.groupby("session_date")["High"].transform(lambda s: safe_float(s.head(6).max(), np.nan))
    or_low = x.groupby("session_date")["Low"].transform(lambda s: safe_float(s.head(6).min(), np.nan))
    x["or_high"] = or_high
    x["or_low"] = or_low
    denom = (x["or_high"] - x["or_low"]).replace(0, np.nan)
    x["or_pos"] = ((close - x["or_low"]) / denom).replace([np.inf, -np.inf], np.nan).fillna(0.5)
    return x.replace([np.inf, -np.inf], np.nan).fillna(0)


def news(ticker: str, limit: int = 12) -> List[Dict]:
    results: List[Dict] = []
    try:
        items = yf.Ticker(ticker).news or []
        for item in items[:limit]:
            content = item.get("content", {}) if isinstance(item, dict) else {}
            title = content.get("title") or item.get("title") or "News"
            provider = content.get("provider")
            publisher = (provider.get("displayName") if isinstance(provider, dict) else None) or item.get("publisher") or "Source"
            url_obj = content.get("clickThroughUrl")
            if isinstance(url_obj, dict):
                url = url_obj.get("url")
            else:
                url = item.get("link")
            published = content.get("pubDate") or item.get("providerPublishTime") or item.get("published")
            results.append({"title": str(title), "publisher": str(publisher), "url": url, "published": published})
    except Exception:
        pass

    if len(results) < limit and feedparser is not None:
        try:
            query = quote_plus(f"{ticker} stock")
            feed = feedparser.parse(f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en")
            for entry in feed.entries[: limit - len(results)]:
                source = entry.get("source")
                publisher = source.get("title") if hasattr(source, "get") else "Google News"
                results.append({"title": entry.get("title", "News"), "publisher": publisher or "Google News", "url": entry.get("link"), "published": entry.get("published")})
        except Exception:
            pass
    return results[:limit]


def simple_sentiment(text: str) -> float:
    positive = {
        "beat", "beats", "strong", "growth", "upgrade", "surge", "profit", "record", "bullish",
        "positive", "raises", "raise", "buyback", "outperform", "approval", "wins", "contract",
    }
    negative = {
        "miss", "misses", "weak", "decline", "downgrade", "drop", "loss", "warning", "bearish",
        "negative", "cuts", "cut", "sell", "lawsuit", "recall", "investigation", "tariff",
    }
    words = {w.strip(".,:;!?()[]{}\"").lower() for w in str(text).split()}
    pos = sum(w in positive for w in words)
    neg = sum(w in negative for w in words)
    if pos + neg == 0:
        return 0.0
    return float((pos - neg) / (pos + neg))


def news_context(ticker: str, limit: int = 12) -> Dict:
    items = news(ticker, limit)
    scores = [simple_sentiment(i.get("title", "")) for i in items]
    score = float(np.mean(scores)) if scores else 0.0
    return {"items": items, "sentiment": score, "count": len(items)}


def current_fundamentals(ticker: str) -> Dict:
    fields = [
        "shortName", "sector", "industry", "marketCap", "trailingPE", "forwardPE", "pegRatio",
        "priceToSalesTrailing12Months", "returnOnEquity", "returnOnAssets", "profitMargins",
        "operatingMargins", "grossMargins", "debtToEquity", "revenueGrowth", "earningsGrowth",
        "freeCashflow", "totalCash", "totalDebt", "dividendYield",
    ]
    out: Dict = {}
    try:
        info = yf.Ticker(ticker).get_info()
        for field in fields:
            val = info.get(field)
            if val is not None and val != "":
                out[field] = val
    except Exception as exc:
        out["error"] = str(exc)
    return out


def data_health(ticker: str) -> Dict:
    health = {"ticker": ticker, "daily_bars": 0, "intraday_bars": 0, "premarket_bars": 0, "status": "UNKNOWN", "warnings": []}
    try:
        daily = fetch(ticker, "2y", "1d")
        health["daily_bars"] = len(daily)
    except Exception as exc:
        health["warnings"].append(f"Daily: {exc}")
    try:
        intra = latest_regular(ticker)
        health["intraday_bars"] = len(intra)
    except Exception as exc:
        health["warnings"].append(f"Intraday: {exc}")
    try:
        pre = premarket_df(ticker)
        health["premarket_bars"] = len(pre)
    except Exception as exc:
        health["warnings"].append(f"Premarket: {exc}")
    health["status"] = "OK" if health["daily_bars"] >= 150 and not health["warnings"] else "PARTIAL" if health["daily_bars"] >= 60 else "ERROR"
    return health
