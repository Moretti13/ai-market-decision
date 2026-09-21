from __future__ import annotations

APP_NAME = "AI Market Decision V7.4 Cloud Radar"
APP_VERSION = "7.4.0"
APP_BUILD = "V7.4-CLOUD-RADAR"

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
    "IWM": "IWM",
    "VIX": "^VIX",
    "TNX": "^TNX",
    "DXY": "DX-Y.NYB",
    "OIL": "CL=F",
    "GOLD": "GC=F",
    "TLT": "TLT",
    "BTC": "BTC-USD",
}

DEFAULTS = {
    "capital": 10_000.0,
    "risk_pct": 0.01,
    "max_capital_fraction": 0.25,
    "slippage_bps": 5.0,
    "commission_bps": 3.0,
    "refresh_seconds": 300,
    "scanner_assets": 5,
    "scanner_workers": 1,
    "scanner_watch_score": 60.0,
    "scanner_alert_score": 75.0,
    "radar_interval_minutes": 15,
    "radar_assets": 5,
    "radar_confirm_floor": 60.0,
    "radar_max_alerts": 3,
    "radar_entry_cutoff_et": "15:30",
    "buy_prob": 0.64,
    "sell_prob": 0.36,
    "day_min_edge": 0.0035,
    "week_min_edge": 0.010,
    "month_min_edge": 0.025,
    "min_rr": 1.5,
    "target_rr": 2.0,
    "day_confirm_bars": 2,
    "day_opening_range_bars": 6,
    "prediction_verify_limit": 40,
}

# Features intentionally use information available at or before the prediction timestamp.
MODEL_FEATURES = [
    "ret1", "ret3", "ret5", "ret10", "ret20", "ret60",
    "vol5", "vol20", "vol60",
    "dist_sma20", "dist_sma50", "dist_sma200",
    "ema20_slope", "ema50_slope",
    "rsi14", "macd", "macd_signal", "bb_z",
    "volume_z", "range_pct", "atr_pct", "dist_52w_high",
    "bench_spy_ret1", "bench_spy_ret5", "bench_qqq_ret1", "bench_qqq_ret5",
    "bench_iwm_ret5", "bench_vix_ret1", "bench_tnx_ret1", "bench_dxy_ret1",
    "bench_oil_ret1", "bench_gold_ret1", "bench_tlt_ret1", "bench_btc_ret1",
]

DAY_FEATURES = MODEL_FEATURES + ["gap"]

HORIZON_DAYS = {"DAY": 0, "WEEK": 5, "MONTH": 20}
