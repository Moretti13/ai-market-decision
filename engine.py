from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import yfinance as yf

from config import ALL_UNIVERSE, BENCHMARKS


# -----------------------------
# Robust Yahoo Finance access
# -----------------------------

def _clean_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    x = df.copy()
    if isinstance(x.columns, pd.MultiIndex):
        # history() normally returns flat columns; keep this as a defensive fallback.
        x.columns = [c[0] if isinstance(c, tuple) else c for c in x.columns]
    wanted = [c for c in ['Open', 'High', 'Low', 'Close', 'Adj Close', 'Volume'] if c in x.columns]
    x = x[wanted].copy()
    x = x.dropna(how='all')
    x.index = pd.to_datetime(x.index)
    return x


def fetch_history(ticker: str, period: str = '1y', interval: str = '1d', prepost: bool = False) -> pd.DataFrame:
    # Ticker.history is generally more resilient than a direct download call when Yahoo changes its response format.
    last_error = None
    for attempt in range(2):
        try:
            t = yf.Ticker(ticker)
            df = t.history(period=period, interval=interval, auto_adjust=False, prepost=prepost)
            df = _clean_ohlcv(df)
            if not df.empty:
                return df
        except Exception as exc:
            last_error = exc
    raise ValueError(f'Dati non disponibili per {ticker}: {last_error}')


def rsi(series: pd.Series, n: int = 14) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    gain = up.ewm(alpha=1/n, adjust=False).mean()
    loss = down.ewm(alpha=1/n, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev = df['Close'].shift(1)
    tr = pd.concat([
        df['High'] - df['Low'],
        (df['High'] - prev).abs(),
        (df['Low'] - prev).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def add_daily_features(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    c = x['Close']
    x['ret_1d'] = c.pct_change()
    x['ret_3d'] = c.pct_change(3)
    x['ret_5d'] = c.pct_change(5)
    x['ret_10d'] = c.pct_change(10)
    x['ret_20d'] = c.pct_change(20)
    x['vol_20d'] = c.pct_change().rolling(20).std() * np.sqrt(252)
    x['sma20'] = c.rolling(20).mean()
    x['sma50'] = c.rolling(50).mean()
    x['rsi14'] = rsi(c, 14)
    x['atr14'] = atr(x, 14)
    x['volume_mean20'] = x['Volume'].rolling(20).mean()
    x['volume_z'] = (x['Volume'] - x['volume_mean20']) / x['Volume'].rolling(20).std()
    x['dist_sma20'] = c / x['sma20'] - 1
    x['dist_sma50'] = c / x['sma50'] - 1
    return x.dropna()


def add_intraday_features(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    c = x['Close']
    x['ret_5m'] = c.pct_change(1)
    x['ret_15m'] = c.pct_change(3)
    x['ret_30m'] = c.pct_change(6)
    x['ret_60m'] = c.pct_change(12)
    x['vol_ratio'] = x['Volume'] / x['Volume'].rolling(20).mean().replace(0, np.nan)
    # VWAP reset for each local date. Yahoo returns exchange-local timestamps for most symbols.
    x['session_date'] = x.index.date
    typical = (x['High'] + x['Low'] + x['Close']) / 3
    x['_pv'] = typical * x['Volume']
    x['cum_pv'] = x.groupby('session_date')['_pv'].cumsum()
    x['cum_vol'] = x.groupby('session_date')['Volume'].cumsum()
    x['vwap'] = x['cum_pv'] / x['cum_vol'].replace(0, np.nan)
    x['vwap_dist'] = c / x['vwap'] - 1
    x['bar_num'] = x.groupby('session_date').cumcount()
    return x.dropna(subset=['ret_15m', 'vwap_dist'])


def _market_context() -> Dict[str, Dict[str, float]]:
    result: Dict[str, Dict[str, float]] = {}
    for name, symbol in BENCHMARKS.items():
        try:
            d = add_daily_features(fetch_history(symbol, period='3mo', interval='1d'))
            last = d.iloc[-1]
            result[name] = {
                'ret_1d': float(last['ret_1d']),
                'ret_5d': float(last['ret_5d']),
                'rsi': float(last['rsi14']),
            }
        except Exception:
            result[name] = {'ret_1d': 0.0, 'ret_5d': 0.0, 'rsi': 50.0}
    return result


def _signal(score: float) -> str:
    if score >= 66:
        return 'BUY'
    if score <= 34:
        return 'SELL'
    return 'HOLD'


def _direction_probability(score: float) -> float:
    return float(np.clip(0.5 + (score - 50) / 100, 0.02, 0.98))


def _trade_plan(signal: str, price: float, atr_value: float, expected_return: float, confidence: float,
                stage: str, p_up: float) -> Dict:
    atr_pct = max((atr_value / price) if price else 0.01, 0.003)
    # The plan is for simulation/decision support. It is deliberately conservative about entry confirmation.
    if signal == 'BUY':
        entry_trigger = price * (1 + min(0.0025, atr_pct * 0.25)) if stage != 'PRE_OPEN' else price
        stop = price - 1.25 * atr_value
        target = price + max(1.75 * atr_value, abs(expected_return) * price * 1.20)
        action = 'ENTER LONG'
        condition = 'OPEN OR BREAKOUT CONFIRMATION'
    elif signal == 'SELL':
        entry_trigger = price * (1 - min(0.0025, atr_pct * 0.25)) if stage != 'PRE_OPEN' else price
        stop = price + 1.25 * atr_value
        target = price - max(1.75 * atr_value, abs(expected_return) * price * 1.20)
        action = 'EXIT / AVOID LONG'
        condition = 'SELL ON BREAKDOWN / WEAK OPEN'
    else:
        entry_trigger = price
        stop = price - 1.0 * atr_value
        target = price + 1.0 * atr_value
        action = 'WAIT'
        condition = 'WAIT FOR CONFIRMATION'

    # Do not signal immediate entry from a low-confidence model.
    if confidence < 0.62 or abs(p_up - 0.5) < 0.10:
        action = 'WAIT'
        condition = 'NO ENTRY — INSUFFICIENT CONFIRMATION'

    rr = abs(target - entry_trigger) / max(abs(stop - entry_trigger), 1e-9)
    return {
        'action': action,
        'condition': condition,
        'entry': float(entry_trigger),
        'stop': float(stop),
        'target': float(target),
        'rr': float(rr),
    }


def score_day(ticker: str) -> Dict:
    intraday = add_intraday_features(fetch_history(ticker, period='5d', interval='5m', prepost=True))
    daily = add_daily_features(fetch_history(ticker, period='1y', interval='1d'))
    mkt = _market_context()

    last_i = intraday.iloc[-1]
    last_d = daily.iloc[-1]
    price = float(last_i['Close'])
    atr_value = float(last_d['atr14'])
    score = 50.0
    drivers = []

    def add(points: float, text: str):
        nonlocal score
        score += float(points)
        if abs(points) >= 3.5:
            drivers.append((points, text))

    add(np.clip(last_i['ret_15m'] * 1100, -12, 12), 'momentum 15m')
    add(np.clip(last_i['ret_60m'] * 600, -9, 9), 'momentum 60m')
    add(np.clip(last_i['vwap_dist'] * 900, -10, 10), 'prezzo vs VWAP')
    add(np.clip((last_i['vol_ratio'] - 1) * 9, -8, 8), 'volume relativo')
    add(np.clip(last_d['ret_5d'] * 90, -8, 8), 'trend 5 giorni')
    add(np.clip(last_d['ret_20d'] * 45, -7, 7), 'trend 20 giorni')
    add(np.clip(mkt['SPY']['ret_1d'] * 75, -6, 6), 'S&P 500')
    add(np.clip(mkt['QQQ']['ret_1d'] * 65, -6, 6), 'Nasdaq/QQQ')
    add(np.clip((50 - last_d['rsi14']) * 0.07, -4, 4), 'RSI')

    score = float(np.clip(score, 1, 99))
    signal = _signal(score)
    p_up = _direction_probability(score)
    p_down = 1 - p_up
    atr_pct = max(atr_value / price, 0.005)
    expected = float(np.clip((score - 50) / 50 * atr_pct * 1.60, -0.08, 0.08))
    confidence = float(np.clip(0.55 + abs(score - 50) / 105, 0.55, 0.95))

    now = datetime.now().astimezone()
    stage = 'LIVE' if 0 <= now.hour <= 23 else 'PRE_OPEN'
    plan = _trade_plan(signal, price, atr_value, expected, confidence, stage, p_up)

    # Timing message for DAY: entry only after confirmation unless the model is very strong pre-open.
    if signal == 'BUY' and confidence >= 0.72:
        entry_timing = 'APERTURA CON CONFERMA 5m'
    elif signal == 'SELL' and confidence >= 0.72:
        entry_timing = 'APERTURA DEBOLE / BREAKDOWN 5m'
    else:
        entry_timing = 'ATTENDI CONFERMA 5m'

    drivers = [x[1] for x in sorted(drivers, key=lambda z: abs(z[0]), reverse=True)[:6]]
    return {
        'ticker': ticker,
        'signal': signal,
        'score': round(score, 1),
        'p_up': round(p_up, 3),
        'p_down': round(p_down, 3),
        'expected_return': round(expected, 4),
        'confidence': round(confidence, 3),
        'price': round(price, 4),
        'atr': round(atr_value, 4),
        'entry_timing': entry_timing,
        'plan': plan,
        'drivers': drivers,
        'updated': str(intraday.index[-1]),
        'bars': int(len(intraday)),
    }


def score_medium(ticker: str, horizon: str) -> Dict:
    daily = add_daily_features(fetch_history(ticker, period='2y', interval='1d'))
    mkt = _market_context()
    last = daily.iloc[-1]
    price = float(last['Close'])
    atr_value = float(last['atr14'])
    score = 50.0
    drivers = []

    def add(points: float, text: str):
        nonlocal score
        score += float(points)
        if abs(points) >= 3.5:
            drivers.append((points, text))

    add(np.clip(last['ret_5d'] * 90, -11, 11), 'momentum 5 giorni')
    add(np.clip(last['ret_10d'] * 55, -9, 9), 'momentum 10 giorni')
    add(np.clip(last['ret_20d'] * 40, -8, 8), 'momentum 20 giorni')
    add(np.clip(last['dist_sma20'] * 65, -8, 8), 'trend SMA20')
    add(np.clip(last['dist_sma50'] * 45, -7, 7), 'trend SMA50')
    add(np.clip((last['rsi14'] - 50) * 0.11, -6, 6), 'RSI')
    add(np.clip(mkt['SPY']['ret_5d'] * 30, -5, 5), 'mercato')
    add(np.clip(mkt['QQQ']['ret_5d'] * 28, -5, 5), 'Nasdaq/QQQ')

    score = float(np.clip(score, 1, 99))
    signal = _signal(score)
    p_up = _direction_probability(score)
    p_down = 1 - p_up
    vol = max(atr_value / price, 0.008 if horizon == 'WEEK' else 0.012)
    mult = 2.7 if horizon == 'WEEK' else 5.5
    expected = float(np.clip((score - 50) / 50 * vol * mult, -0.30, 0.30))
    confidence = float(np.clip(0.54 + abs(score - 50) / 110, 0.54, 0.93))
    plan = _trade_plan(signal, price, atr_value, expected, confidence, horizon, p_up)
    drivers = [x[1] for x in sorted(drivers, key=lambda z: abs(z[0]), reverse=True)[:6]]
    return {
        'ticker': ticker,
        'horizon': horizon,
        'signal': signal,
        'score': round(score, 1),
        'p_up': round(p_up, 3),
        'p_down': round(p_down, 3),
        'expected_return': round(expected, 4),
        'confidence': round(confidence, 3),
        'price': round(price, 4),
        'atr': round(atr_value, 4),
        'plan': plan,
        'drivers': drivers,
        'updated': str(daily.index[-1]),
    }


def fetch_news(ticker: str, limit: int = 10) -> List[Dict]:
    try:
        items = yf.Ticker(ticker).news or []
    except Exception:
        items = []
    out = []
    for item in items[:limit]:
        c = item.get('content', {}) if isinstance(item, dict) else {}
        provider = c.get('provider', {}) if isinstance(c, dict) else {}
        click = c.get('clickThroughUrl', {}) if isinstance(c, dict) else {}
        out.append({
            'title': c.get('title') or item.get('title') or 'News',
            'publisher': provider.get('displayName') or item.get('publisher') or 'Yahoo Finance',
            'url': click.get('url') if isinstance(click, dict) else item.get('link'),
            'published': c.get('pubDate') or item.get('providerPublishTime'),
        })
    return out


def analyze_asset(ticker: str) -> Dict:
    ticker = ticker.strip().upper()
    day = score_day(ticker)
    week = score_medium(ticker, 'WEEK')
    month = score_medium(ticker, 'MONTH')
    return {
        'ticker': ticker,
        'timestamp_utc': datetime.now(timezone.utc).isoformat(),
        'horizons': {'DAY': day, 'WEEK': week, 'MONTH': month},
        'news': fetch_news(ticker),
    }


def scan_market(universe: Optional[List[str]] = None, max_assets: int = 15) -> pd.DataFrame:
    universe = universe or ALL_UNIVERSE
    rows = []
    for ticker in universe[:max_assets]:
        try:
            d = score_day(ticker)
            w = score_medium(ticker, 'WEEK')
            m = score_medium(ticker, 'MONTH')
            # Risk-adjusted ranking: expected return divided by ATR-derived risk proxy, penalized by low confidence.
            day_rank = (d['expected_return'] / max(d['confidence'], 0.50))
            week_rank = (w['expected_return'] / max(w['confidence'], 0.50))
            month_rank = (m['expected_return'] / max(m['confidence'], 0.50))
            rows.append({
                'ticker': ticker,
                'DAY': d['signal'], 'DAY score': d['score'], 'DAY exp.': d['expected_return'], 'DAY conf.': d['confidence'], 'DAY rank': day_rank,
                'WEEK': w['signal'], 'WEEK score': w['score'], 'WEEK exp.': w['expected_return'], 'WEEK conf.': w['confidence'], 'WEEK rank': week_rank,
                'MONTH': m['signal'], 'MONTH score': m['score'], 'MONTH exp.': m['expected_return'], 'MONTH conf.': m['confidence'], 'MONTH rank': month_rank,
            })
        except Exception:
            continue
    return pd.DataFrame(rows)


def make_price_chart(ticker: str, period: str = '3mo'):
    import plotly.graph_objects as go
    d = add_daily_features(fetch_history(ticker, period=period, interval='1d'))
    fig = go.Figure()
    fig.add_trace(go.Candlestick(x=d.index, open=d['Open'], high=d['High'], low=d['Low'], close=d['Close'], name=ticker))
    fig.add_trace(go.Scatter(x=d.index, y=d['sma20'], mode='lines', name='SMA20'))
    fig.add_trace(go.Scatter(x=d.index, y=d['sma50'], mode='lines', name='SMA50'))
    fig.update_layout(height=500, margin=dict(l=10,r=10,t=30,b=10), xaxis_rangeslider_visible=False)
    return fig
