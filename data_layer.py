from __future__ import annotations

import time as time_module
import threading
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote_plus

import numpy as np
import pandas as pd

try:
    import feedparser
except Exception:
    feedparser = None

try:
    import yfinance as yf
except Exception:
    yf = None

from config import BENCHMARKS, MODEL_FEATURES


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


def normalize_ohlcv(df: pd.DataFrame, daily: bool = False) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = _flatten_columns(df)
    original_index = pd.to_datetime(out.index, errors="coerce")
    valid = ~original_index.isna()
    out = out.loc[valid].copy()
    original_index = original_index[valid]

    if daily:
        # Preserve the exchange calendar date before converting timezone. This avoids
        # shifting European daily bars to the previous UTC date.
        dates = [ts.date() for ts in original_index]
        idx = pd.DatetimeIndex(pd.to_datetime(dates), tz="UTC")
    else:
        idx = original_index
        if getattr(idx, "tz", None) is None:
            idx = idx.tz_localize("UTC")
        else:
            idx = idx.tz_convert("UTC")
    out.index = idx

    keep = [c for c in ["Open", "High", "Low", "Close", "Adj Close", "Volume"] if c in out.columns]
    out = out[keep]
    for col in keep:
        out[col] = clean_numeric(out[col])
    if "Close" not in out.columns:
        return pd.DataFrame()
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out.dropna(subset=["Close"])


_CACHE: Dict[Tuple[str, str, str, bool], Tuple[float, pd.DataFrame]] = {}
_CACHE_TTL = {"1d": 1800.0, "5m": 35.0, "15m": 60.0, "30m": 90.0, "1h": 120.0}


def clear_cache() -> None:
    global _BENCH_CACHE
    _CACHE.clear()
    _NEWS_RAW_CACHE.clear() if "_NEWS_RAW_CACHE" in globals() else None
    _FUND_CACHE.clear() if "_FUND_CACHE" in globals() else None
    _EARNINGS_CACHE.clear() if "_EARNINGS_CACHE" in globals() else None
    _HEALTH_CACHE.clear() if "_HEALTH_CACHE" in globals() else None
    _BENCH_CACHE = None


def fetch(
    ticker: str,
    period: str = "1y",
    interval: str = "1d",
    prepost: bool = False,
    force: bool = False,
) -> pd.DataFrame:
    ticker = ticker.strip().upper()
    if not ticker:
        raise DataError("Ticker vuoto")
    key = (ticker, period, interval, prepost)
    now = time_module.time()
    ttl = _CACHE_TTL.get(interval, 120.0)
    if not force and key in _CACHE and now - _CACHE[key][0] < ttl:
        return _CACHE[key][1].copy()
    if yf is None:
        raise DataError("yfinance non installato: esegui l'installazione da requirements.txt")

    errors: List[str] = []
    for attempt in range(2):
        try:
            hist = yf.Ticker(ticker).history(
                period=period,
                interval=interval,
                prepost=prepost,
                auto_adjust=False,
                actions=False,
                timeout=15,
            )
            out = normalize_ohlcv(hist, daily=(interval == "1d"))
            if not out.empty:
                _CACHE[key] = (now, out)
                return out.copy()
            errors.append("Ticker.history vuoto")
        except Exception as exc:
            errors.append(f"Ticker.history[{attempt+1}]: {exc}")
            time_module.sleep(0.15 * (attempt + 1))

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
            timeout=20,
        )
        out = normalize_ohlcv(hist, daily=(interval == "1d"))
        if not out.empty:
            _CACHE[key] = (now, out)
            return out.copy()
        errors.append("yf.download vuoto")
    except Exception as exc:
        errors.append(f"yf.download: {exc}")
    raise DataError(f"Dati non disponibili per {ticker}. {' | '.join(errors[-4:])}")


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    c = clean_numeric(close)
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    high, low, close = clean_numeric(df["High"]), clean_numeric(df["Low"]), clean_numeric(df["Close"])
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=max(3, n // 3)).mean().bfill()


def daily_features(df: pd.DataFrame) -> pd.DataFrame:
    x = normalize_ohlcv(df, daily=True)
    if x.empty or len(x) < 40:
        return pd.DataFrame()
    close = clean_numeric(x["Close"])
    ret = close.pct_change()
    for n in [1, 3, 5, 10, 20, 60]:
        x[f"ret{n}"] = close.pct_change(n)
    for n in [5, 20, 60]:
        x[f"vol{n}"] = ret.rolling(n, min_periods=max(3, n // 2)).std()

    for n in [20, 50, 200]:
        x[f"sma{n}"] = close.rolling(n, min_periods=max(10, n // 3)).mean()
        x[f"dist_sma{n}"] = (close / x[f"sma{n}"] - 1).replace([np.inf, -np.inf], np.nan)
    x["ema20"] = close.ewm(span=20, adjust=False).mean()
    x["ema50"] = close.ewm(span=50, adjust=False).mean()
    x["ema20_slope"] = x["ema20"].pct_change(5)
    x["ema50_slope"] = x["ema50"].pct_change(10)
    x["rsi14"] = rsi(close)

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    x["macd"] = (ema12 - ema26) / close.replace(0, np.nan)
    signal = (ema12 - ema26).ewm(span=9, adjust=False).mean()
    x["macd_signal"] = signal / close.replace(0, np.nan)

    mid = close.rolling(20, min_periods=10).mean()
    std = close.rolling(20, min_periods=10).std().replace(0, np.nan)
    x["bb_z"] = ((close - mid) / std).replace([np.inf, -np.inf], np.nan)
    x["atr14"] = atr(x)
    x["atr_pct"] = (x["atr14"] / close).replace([np.inf, -np.inf], np.nan)

    volume = clean_numeric(x.get("Volume", pd.Series(index=x.index, dtype=float))).fillna(0)
    vmean = volume.rolling(20, min_periods=5).mean()
    vstd = volume.rolling(20, min_periods=5).std().replace(0, np.nan)
    x["volume_z"] = ((volume - vmean) / vstd).replace([np.inf, -np.inf], np.nan).fillna(0)
    x["range_pct"] = ((clean_numeric(x["High"]) - clean_numeric(x["Low"])) / close).replace([np.inf, -np.inf], np.nan)
    x["gap"] = (clean_numeric(x["Open"]) / close.shift(1) - 1).replace([np.inf, -np.inf], np.nan)
    rolling_high = clean_numeric(x["High"]).rolling(252, min_periods=60).max()
    x["dist_52w_high"] = (close / rolling_high - 1).replace([np.inf, -np.inf], np.nan)

    required = ["ret1", "ret20", "vol20", "rsi14", "atr_pct", "dist_sma20"]
    return x.replace([np.inf, -np.inf], np.nan).dropna(subset=required)


_BENCH_CACHE: Tuple[float, pd.DataFrame] | None = None
_BENCH_LOCK = threading.Lock()


def benchmark_features(start: pd.Timestamp, end: Optional[pd.Timestamp] = None) -> pd.DataFrame:
    global _BENCH_CACHE
    now = time_module.time()
    if _BENCH_CACHE is not None and now - _BENCH_CACHE[0] < 900:
        full = _BENCH_CACHE[1]
    else:
        # Scanner threads share one benchmark build instead of hammering the provider.
        with _BENCH_LOCK:
            now = time_module.time()
            if _BENCH_CACHE is not None and now - _BENCH_CACHE[0] < 900:
                full = _BENCH_CACHE[1]
            else:
                frames = []
                for name, ticker in BENCHMARKS.items():
                    try:
                        d = daily_features(fetch(ticker, "5y", "1d"))
                        if d.empty:
                            continue
                        wanted = {"ret1": f"bench_{name.lower()}_ret1"}
                        if name in {"SPY", "QQQ", "IWM"}:
                            wanted["ret5"] = f"bench_{name.lower()}_ret5"
                        cols = d[list(wanted)].rename(columns=wanted)
                        frames.append(cols)
                    except Exception:
                        continue
                full = pd.concat(frames, axis=1).sort_index() if frames else pd.DataFrame()
                _BENCH_CACHE = (now, full)
    if full.empty:
        return pd.DataFrame()
    s = pd.Timestamp(start).tz_convert("UTC") if pd.Timestamp(start).tzinfo else pd.Timestamp(start, tz="UTC")
    e0 = end if end is not None else full.index.max()
    e = pd.Timestamp(e0).tz_convert("UTC") if pd.Timestamp(e0).tzinfo else pd.Timestamp(e0, tz="UTC")
    return full.loc[(full.index >= s) & (full.index <= e)].copy()


def add_market_features(d: pd.DataFrame) -> pd.DataFrame:
    if d.empty:
        return d
    out = d.copy()
    bench = benchmark_features(out.index.min(), out.index.max())
    if not bench.empty:
        aligned = bench.reindex(out.index).ffill().fillna(0)
        for col in aligned.columns:
            out[col] = aligned[col]
    for col in [c for c in MODEL_FEATURES if c.startswith("bench_")]:
        if col not in out.columns:
            out[col] = 0.0
    return out


def regular_session_df(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    from market_clock import session_hours
    if df.empty:
        return df
    tz, (op, cl) = session_hours(ticker)
    local = df.index.tz_convert(tz)
    minutes = local.hour * 60 + local.minute
    opm, clm = op.hour * 60 + op.minute, cl.hour * 60 + cl.minute
    return df.loc[(minutes >= opm) & (minutes < clm)].copy()


def premarket_df(ticker: str) -> pd.DataFrame:
    from market_clock import NY, session_hours
    tz, _ = session_hours(ticker)
    if tz != NY:
        return pd.DataFrame()
    df = fetch(ticker, "5d", "5m", prepost=True)
    if df.empty:
        return df
    local = df.index.tz_convert(NY)
    minutes = local.hour * 60 + local.minute
    return df.loc[(minutes >= 240) & (minutes < 570)].copy()


def _repair_intraday_volume(primary: pd.DataFrame, fallback: pd.DataFrame) -> pd.DataFrame:
    """Repair obviously-missing Yahoo 5m volume without changing valid primary prices.

    Yahoo occasionally returns a recent 5m row with Volume=0/missing while an
    equivalent pre/post-enabled request contains the volume.  We only patch the
    Volume field at matching timestamps and never fabricate volume.
    """
    if primary is None or primary.empty:
        return fallback.copy() if fallback is not None else pd.DataFrame()
    out = primary.copy()
    if fallback is None or fallback.empty or "Volume" not in out.columns or "Volume" not in fallback.columns:
        return out
    alt = fallback.reindex(out.index)
    base_vol = clean_numeric(out["Volume"])
    alt_vol = clean_numeric(alt["Volume"])
    replace_mask = (base_vol.isna() | (base_vol <= 0)) & (alt_vol > 0)
    if replace_mask.any():
        out.loc[replace_mask, "Volume"] = alt_vol.loc[replace_mask]
    return out


def latest_regular(ticker: str) -> pd.DataFrame:
    primary = regular_session_df(fetch(ticker, "10d", "5m", prepost=False), ticker)
    if primary.empty or "Volume" not in primary.columns:
        return primary

    # Trigger a second Yahoo request only when *completed* recent bars have
    # missing volume. A zero-volume currently-forming candle is simply ignored
    # by intraday_state and should not cause another provider call.
    recent_frame = primary
    try:
        from market_clock import market_status
        status = market_status(ticker)
        now_utc = pd.Timestamp(status["now"])
        if now_utc.tzinfo is None:
            now_utc = now_utc.tz_localize(status["timezone"])
        now_utc = now_utc.tz_convert("UTC")
        completed = (primary.index + pd.Timedelta(minutes=5)) <= (now_utc - pd.Timedelta(seconds=2))
        if completed.any():
            recent_frame = primary.loc[completed]
    except Exception:
        pass
    recent = clean_numeric(recent_frame["Volume"]).tail(4)
    needs_volume_fallback = bool(len(recent) and (recent.isna().any() or (recent <= 0).any()))
    if not needs_volume_fallback:
        return primary
    try:
        alt = regular_session_df(fetch(ticker, "10d", "5m", prepost=True), ticker)
        return _repair_intraday_volume(primary, alt)
    except Exception:
        return primary


def intraday_features(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    from market_clock import session_hours
    if df.empty:
        return pd.DataFrame()
    x = regular_session_df(normalize_ohlcv(df), ticker)
    if x.empty:
        return x
    tz, _ = session_hours(ticker)
    close = clean_numeric(x["Close"])
    volume = clean_numeric(x.get("Volume", pd.Series(index=x.index, dtype=float)))
    # Zero volume on a liquid 5m bar is frequently a provider/incomplete-bar
    # issue. Keep it as unavailable instead of turning it into a misleading
    # 0.00 ratio. No synthetic volume is invented.
    positive_volume = volume.where(volume > 0)
    x["volume_valid"] = (positive_volume.notna()).astype(int)
    for bars, name in [(1, "ret5m"), (3, "ret15m"), (6, "ret30m"), (12, "ret60m")]:
        x[name] = close.pct_change(bars)
    local_dates = x.index.tz_convert(tz).date
    vwap_volume = positive_volume.fillna(0)
    pv = (close * vwap_volume).groupby(local_dates).cumsum()
    vv = vwap_volume.groupby(local_dates).cumsum().replace(0, np.nan)
    x["vwap"] = (pv / vv).fillna(close)
    x["vwap_dist"] = (close / x["vwap"] - 1).replace([np.inf, -np.inf], np.nan).fillna(0)

    # Compare the current completed bar only with prior positive-volume bars.
    # shift(1) avoids diluting the ratio by including the current bar in its
    # own baseline.  Median is more robust to opening-volume spikes.
    baseline = positive_volume.shift(1).rolling(20, min_periods=5).median().replace(0, np.nan)
    x["volume_ratio"] = (positive_volume / baseline).replace([np.inf, -np.inf], np.nan)
    x["session_date"] = local_dates
    x["bar_num"] = x.groupby("session_date").cumcount() + 1
    x["session_open"] = x.groupby("session_date")["Open"].transform("first")
    x["session_ret"] = (close / x["session_open"] - 1).replace([np.inf, -np.inf], np.nan).fillna(0)
    x["session_high"] = x.groupby("session_date")["High"].cummax()
    x["session_low"] = x.groupby("session_date")["Low"].cummin()
    x["or_high"] = x.groupby("session_date")["High"].transform(lambda s: s.iloc[:6].max())
    x["or_low"] = x.groupby("session_date")["Low"].transform(lambda s: s.iloc[:6].min())
    denom = (x["or_high"] - x["or_low"]).replace(0, np.nan)
    x["or_pos"] = ((close - x["or_low"]) / denom).replace([np.inf, -np.inf], np.nan).fillna(0.5)
    x = x.replace([np.inf, -np.inf], np.nan)
    # Keep volume_ratio as NaN when Yahoo did not provide trustworthy volume;
    # the confirmation engine will then ignore volume instead of treating 0 as
    # a bearish/low-volume observation.
    preserve_nan = x["volume_ratio"].copy()
    x = x.fillna(0)
    x["volume_ratio"] = preserve_nan
    return x


_NEWS_RAW_CACHE: Dict[Tuple[str, int], Tuple[float, List[Dict]]] = {}
_FUND_CACHE: Dict[str, Tuple[float, Dict]] = {}
_EARNINGS_CACHE: Dict[str, Tuple[float, Dict]] = {}
_HEALTH_CACHE: Dict[str, Tuple[float, Dict]] = {}


def raw_news(ticker: str, limit: int = 20) -> List[Dict]:
    ticker = ticker.strip().upper()
    key = (ticker, int(limit))
    now = time_module.time()
    cached = _NEWS_RAW_CACHE.get(key)
    if cached and now - cached[0] < 300:
        return [dict(x) for x in cached[1]]
    if yf is None:
        return []
    results: List[Dict] = []
    try:
        items = yf.Ticker(ticker).news or []
        for item in items[:limit]:
            content = item.get("content", {}) if isinstance(item, dict) else {}
            title = content.get("title") or item.get("title") or "News"
            provider = content.get("provider")
            publisher = (provider.get("displayName") if isinstance(provider, dict) else None) or item.get("publisher") or "Source"
            url_obj = content.get("clickThroughUrl")
            url = url_obj.get("url") if isinstance(url_obj, dict) else item.get("link")
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
                results.append({
                    "title": entry.get("title", "News"),
                    "publisher": publisher or "Google News",
                    "url": entry.get("link"),
                    "published": entry.get("published"),
                })
        except Exception:
            pass
    final = results[:limit]
    _NEWS_RAW_CACHE[key] = (now, [dict(x) for x in final])
    return final


def current_fundamentals(ticker: str) -> Dict:
    ticker = ticker.strip().upper()
    now = time_module.time()
    cached = _FUND_CACHE.get(ticker)
    if cached and now - cached[0] < 21600:
        return dict(cached[1])
    fields = [
        "shortName", "sector", "industry", "marketCap", "trailingPE", "forwardPE", "pegRatio",
        "priceToSalesTrailing12Months", "returnOnEquity", "returnOnAssets", "profitMargins",
        "operatingMargins", "grossMargins", "debtToEquity", "revenueGrowth", "earningsGrowth",
        "freeCashflow", "totalCash", "totalDebt", "dividendYield", "averageVolume", "beta",
    ]
    out: Dict = {}
    if yf is None:
        return {"error": "yfinance non disponibile"}
    try:
        info = yf.Ticker(ticker).get_info() or {}
        for field in fields:
            val = info.get(field)
            if val is not None and val != "":
                out[field] = val
    except Exception as exc:
        out["error"] = str(exc)
    _FUND_CACHE[ticker] = (now, dict(out))
    return out


def earnings_context(ticker: str) -> Dict:
    ticker = ticker.strip().upper()
    now_ts = time_module.time()
    cached = _EARNINGS_CACHE.get(ticker)
    if cached and now_ts - cached[0] < 7200:
        return dict(cached[1])
    result = {"next_earnings": None, "days_to_earnings": None, "source": "Yahoo Finance"}
    if yf is None:
        return result
    now = pd.Timestamp.now(tz="UTC")
    try:
        cal = yf.Ticker(ticker).calendar
        if isinstance(cal, dict):
            candidates = cal.get("Earnings Date") or cal.get("EarningsDate")
            if candidates is not None:
                if not isinstance(candidates, (list, tuple)):
                    candidates = [candidates]
                parsed = [pd.to_datetime(x, utc=True, errors="coerce") for x in candidates]
                parsed = [x for x in parsed if not pd.isna(x) and x >= now - pd.Timedelta(days=1)]
                if parsed:
                    nxt = min(parsed)
                    result["next_earnings"] = nxt.isoformat()
                    result["days_to_earnings"] = int(np.floor((nxt - now).total_seconds() / 86400))
                    _EARNINGS_CACHE[ticker] = (now_ts, dict(result))
                    return result
    except Exception:
        pass
    try:
        dates = yf.Ticker(ticker).get_earnings_dates(limit=8)
        if dates is not None and not dates.empty:
            idx = pd.to_datetime(dates.index, utc=True, errors="coerce")
            future = idx[idx >= now - pd.Timedelta(days=1)]
            if len(future):
                nxt = min(future)
                result["next_earnings"] = nxt.isoformat()
                result["days_to_earnings"] = int(np.floor((nxt - now).total_seconds() / 86400))
    except Exception:
        pass
    _EARNINGS_CACHE[ticker] = (now_ts, dict(result))
    return result


def quote_snapshot(ticker: str) -> Dict:
    from market_clock import market_status
    status = market_status(ticker)

    # 5m data is useful during extended/regular trading, but querying it on a
    # weekend/holiday wastes API calls and CPU. Fall back directly to the last
    # completed daily close when the exchange is fully closed.
    if status.get("is_pre") or status.get("is_open") or status.get("is_post"):
        try:
            intra = fetch(ticker, "5d", "5m", prepost=True)
            if not intra.empty:
                row = intra.iloc[-1]
                return {"price": safe_float(row["Close"]), "asof": str(intra.index[-1]), "source": "5m", "market_status": status["status"]}
        except Exception:
            pass
    daily = fetch(ticker, "1mo", "1d")
    if daily.empty:
        raise DataError(f"Nessun prezzo per {ticker}")
    return {"price": safe_float(daily["Close"].iloc[-1]), "asof": str(daily.index[-1]), "source": "1d", "market_status": status["status"]}


def data_health(ticker: str, force: bool = False, live: bool | None = None) -> Dict:
    ticker = ticker.strip().upper()
    now = time_module.time()
    cached = _HEALTH_CACHE.get(ticker)
    if not force and cached and now - cached[0] < 300:
        return dict(cached[1])

    from market_clock import market_status
    clock = market_status(ticker)
    if live is None:
        live = bool(clock.get("is_open") or clock.get("is_pre"))

    health = {
        "ticker": ticker,
        "provider": "Yahoo Finance / yfinance",
        "daily_bars": 0,
        "intraday_bars": 0,
        "premarket_bars": 0,
        "daily_last": None,
        "intraday_last": None,
        "volume_status": "NOT_CHECKED",
        "recent_volume_valid_pct": None,
        "status": "UNKNOWN",
        "warnings": [],
        "live_checks": bool(live),
    }
    try:
        daily = fetch(ticker, "7y", "1d")
        health["daily_bars"] = len(daily)
        health["daily_last"] = str(daily.index[-1]) if len(daily) else None
    except Exception as exc:
        health["warnings"].append(f"Daily: {exc}")

    # Avoid two extra 5m downloads whenever the market is fully closed. During
    # live operation query only the relevant feed: premarket before the open,
    # regular bars once the session is open.
    if live and clock.get("is_open"):
        try:
            intra = latest_regular(ticker)
            health["intraday_bars"] = len(intra)
            health["intraday_last"] = str(intra.index[-1]) if len(intra) else None
            if len(intra) and "Volume" in intra.columns:
                recent_vol = clean_numeric(intra["Volume"]).tail(12)
                valid = recent_vol > 0
                pct_valid = float(valid.mean()) if len(valid) else 0.0
                health["recent_volume_valid_pct"] = pct_valid
                if pct_valid >= 0.90:
                    health["volume_status"] = "OK"
                elif pct_valid >= 0.60:
                    health["volume_status"] = "PARTIAL"
                    health["warnings"].append("Volume 5m parzialmente disponibile da Yahoo")
                else:
                    health["volume_status"] = "UNAVAILABLE"
                    health["warnings"].append("Volume 5m non affidabile da Yahoo; filtro volume ignorato")
        except Exception as exc:
            health["warnings"].append(f"Intraday: {exc}")
    elif live and clock.get("is_pre"):
        try:
            pre = premarket_df(ticker)
            health["premarket_bars"] = len(pre)
            health["intraday_last"] = str(pre.index[-1]) if len(pre) else None
        except Exception as exc:
            health["warnings"].append(f"Premarket: {exc}")

    if health["daily_bars"] >= 180 and not health["warnings"]:
        health["status"] = "OK"
    elif health["daily_bars"] >= 80:
        health["status"] = "PARTIAL"
    else:
        health["status"] = "ERROR"
    _HEALTH_CACHE[ticker] = (now, dict(health))
    return health
