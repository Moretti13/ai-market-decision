from __future__ import annotations

APP_NAME = "AI Market Decision V6"
APP_VERSION = "6.0.0"

US_STOCKS = [
    "NVDA", "AMD", "AVGO", "MSFT", "AMZN", "META", "GOOGL", "AAPL", "TSLA", "NFLX",
    "JPM", "BAC", "XOM", "CVX", "LLY", "UNH", "COST", "WMT", "ORCL", "CRM",
    "INTC", "QCOM", "MU", "PLTR", "SMCI", "ARM", "GE", "CAT", "BA", "UBER",
]
EU_STOCKS = [
    "SAP.DE", "ASML.AS", "MC.PA", "TTE.PA", "SIE.DE", "AIR.PA", "SU.PA", "AI.PA",
    "SAN.MC", "ENEL.MI", "ISP.MI", "ENI.MI", "UCG.MI",
]
ETFS = ["QQQ", "SPY", "IWM", "XLK", "XLF", "XLE", "XLV", "SMH", "TLT", "GLD", "SLV"]

ALL_UNIVERSE = list(dict.fromkeys(US_STOCKS + EU_STOCKS + ETFS))

BENCHMARKS = {
    "SPY": "SPY",
    "QQQ": "QQQ",
    "VIX": "^VIX",
    "TNX": "^TNX",
    "DXY": "DX-Y.NYB",
    "OIL": "CL=F",
    "GOLD": "GC=F",
    "BTC": "BTC-USD",
}

DEFAULTS = {
    "capital": 10000.0,
    "risk_pct": 0.01,
    "max_capital_fraction": 0.25,
    "slippage_bps": 5.0,
    "commission_bps": 3.0,
    "refresh_seconds": 300,
    "scanner_assets": 10,
    "buy_prob": 0.65,
    "sell_prob": 0.35,
    "day_min_edge": 0.004,
    "week_min_edge": 0.010,
    "month_min_edge": 0.025,
    "min_rr": 1.5,
}

MODEL_FEATURES = [
    "ret1", "ret3", "ret5", "ret10", "ret20",
    "vol5", "vol20", "dist_sma20", "dist_sma50", "rsi14",
    "volume_z", "range_pct", "atr_pct",
    "bench_spy_ret1", "bench_spy_ret5", "bench_qqq_ret1", "bench_qqq_ret5",
    "bench_vix_ret1", "bench_tnx_ret1", "bench_dxy_ret1",
    "bench_oil_ret1", "bench_gold_ret1", "bench_btc_ret1",
]

DAY_FEATURES = MODEL_FEATURES + ["gap"]
