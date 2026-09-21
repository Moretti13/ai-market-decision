from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import MODEL_FEATURES  # noqa: E402
from data_layer import daily_features, intraday_features, _repair_intraday_volume, safe_float  # noqa: E402
from market_clock import market_status, session_hours  # noqa: E402
from model_engine import _backtest_frame, _train_predict  # noqa: E402
from news_engine import score_title  # noqa: E402
from signal_engine import _confirmation_logic, confirm_open  # noqa: E402
from scanner import _opportunity_detail  # noqa: E402
from portfolio import position_pnl  # noqa: E402
from radar_engine import _event_key, configured_universe  # noqa: E402


def synthetic_ohlcv(n=500):
    rng = np.random.default_rng(42)
    idx = pd.date_range("2023-01-02", periods=n, freq="B", tz="UTC")
    r = 0.0004 + rng.normal(0, 0.012, n)
    close = pd.Series(100 * np.cumprod(1 + r), index=idx)
    open_ = close.shift(1).fillna(close.iloc[0]) * (1 + rng.normal(0, 0.003, n))
    high = pd.concat([open_, close], axis=1).max(axis=1) * (1 + rng.uniform(0.001, 0.012, n))
    low = pd.concat([open_, close], axis=1).min(axis=1) * (1 - rng.uniform(0.001, 0.012, n))
    volume = pd.Series(rng.integers(700_000, 2_000_000, n), index=idx)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume})


class CoreTests(unittest.TestCase):
    def test_daily_features(self):
        d = daily_features(synthetic_ohlcv())
        self.assertFalse(d.empty)
        for col in ["ret1", "ret20", "ret60", "rsi14", "atr14", "macd", "bb_z", "dist_52w_high"]:
            self.assertIn(col, d.columns)
        self.assertTrue(np.isfinite(d["atr_pct"].tail(20)).all())

    def test_safe_float(self):
        self.assertEqual(safe_float("3.5"), 3.5)
        self.assertEqual(safe_float(float("nan"), 7), 7)

    def test_intraday_zero_volume_is_unavailable_not_zero_ratio(self):
        idx = pd.date_range("2026-09-21 13:30", periods=30, freq="5min", tz="UTC")
        close = pd.Series(np.linspace(100, 103, len(idx)), index=idx)
        df = pd.DataFrame({
            "Open": close.shift(1).fillna(close.iloc[0]),
            "High": close + 0.2,
            "Low": close - 0.2,
            "Close": close,
            "Volume": pd.Series([1000] * 29 + [0], index=idx),
        })
        out = intraday_features(df, "NVDA")
        self.assertEqual(int(out["volume_valid"].iloc[-1]), 0)
        self.assertTrue(pd.isna(out["volume_ratio"].iloc[-1]))
        self.assertGreater(float(out["volume_ratio"].iloc[-2]), 0)

    def test_intraday_volume_repair_only_replaces_missing_volume(self):
        idx = pd.date_range("2026-09-21 13:30", periods=3, freq="5min", tz="UTC")
        primary = pd.DataFrame({
            "Open": [100, 101, 102], "High": [101, 102, 103], "Low": [99, 100, 101],
            "Close": [100.5, 101.5, 102.5], "Volume": [1000, 0, 1200],
        }, index=idx)
        fallback = primary.copy()
        fallback["Close"] = [999, 999, 999]
        fallback["Volume"] = [900, 1100, 1000]
        out = _repair_intraday_volume(primary, fallback)
        self.assertEqual(float(out["Volume"].iloc[1]), 1100)
        self.assertEqual(float(out["Volume"].iloc[0]), 1000)
        self.assertEqual(float(out["Close"].iloc[1]), 101.5)


    def test_market_hours(self):
        tz, (op, cl) = session_hours("NVDA")
        self.assertEqual(str(tz), "America/New_York")
        self.assertEqual((op.hour, op.minute), (9, 30))
        self.assertEqual((cl.hour, cl.minute), (16, 0))


    def test_market_status_weekend_and_premarket(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        ny = ZoneInfo("America/New_York")
        weekend = market_status("SPY", datetime(2026, 9, 19, 6, 25, tzinfo=ny))
        self.assertEqual(weekend["status"], "CLOSED")
        self.assertEqual(weekend["closed_reason"], "WEEKEND")
        friday = market_status("SPY", datetime(2026, 9, 18, 6, 25, tzinfo=ny))
        self.assertEqual(friday["status"], "PRE-MARKET")
        self.assertTrue(friday["is_pre"])

    def test_confirm_open_does_not_fetch_intraday_when_closed(self):
        from unittest.mock import patch
        closed = {
            "status": "CLOSED", "is_open": False, "is_pre": False, "is_post": False,
            "closed_reason": "WEEKEND", "timezone": None, "now": pd.Timestamp("2026-09-19"),
            "open": pd.Timestamp("09:30").time(), "close": pd.Timestamp("16:00").time(),
            "is_session_day": False, "next_event": "NEXT SESSION",
        }
        pre = {"signal": "WAIT", "indicative": 100.0, "prev_close": 100.0}
        with patch("signal_engine.market_status", return_value=closed), patch("signal_engine.intraday_state") as intraday_mock:
            out = confirm_open("SPY", pre)
            self.assertEqual(out["status"], "CLOSED")
            intraday_mock.assert_not_called()

    def test_news_score(self):
        pos = score_title("Company beats earnings and raises guidance")
        neg = score_title("Company misses revenue and cuts guidance after warning")
        self.assertGreater(pos["sentiment"], 0)
        self.assertLess(neg["sentiment"], 0)
        self.assertIn("EARNINGS", pos["categories"])

    def test_confirmation_logic(self):
        state = {"bars": 4, "current": 103, "open": 100, "vwap": 101, "ret15m": 0.01, "session_ret": 0.03, "volume_ratio": 1.3, "or_pos": 0.8}
        out = _confirmation_logic("PRE-BUY", state)
        self.assertEqual(out["status"], "CONFIRMED")
        self.assertEqual(out["signal"], "ENTER BUY")

    def test_position_pnl(self):
        self.assertEqual(position_pnl("LONG", 10, 100, 105), 50)
        self.assertEqual(position_pnl("SHORT", 10, 100, 95), 50)

    def test_scanner_watch_score(self):
        near = _opportunity_detail("WAIT", 0.62, 0.0045, 0.60, 0.0035, "NORMAL")
        self.assertEqual(near["candidate_side"], "BUY")
        self.assertGreaterEqual(near["score"], 60)
        self.assertIn("WATCH", near["display_signal"])
        conflict = _opportunity_detail("WAIT", 0.65, -0.001, 0.50, 0.0035, "NORMAL")
        self.assertLess(conflict["score"], near["score"])

    def test_model_and_backtest_helpers(self):
        rng = np.random.default_rng(7)
        n = 430
        frame = pd.DataFrame(index=pd.date_range("2023-01-01", periods=n, freq="D", tz="UTC"))
        for col in MODEL_FEATURES:
            frame[col] = rng.normal(0, 1, n)
        latent = 0.015 * frame[MODEL_FEATURES[0]] - 0.012 * frame[MODEL_FEATURES[1]] + rng.normal(0, 0.01, n)
        frame["future_return"] = latent
        frame["target"] = (latent > 0).astype(int)
        latest = frame.iloc[[-1]].copy()
        out = _train_predict(frame.iloc[:-1], latest, MODEL_FEATURES)
        self.assertGreaterEqual(out["p_up"], 0)
        self.assertLessEqual(out["p_up"], 1)
        bt = _backtest_frame(frame, MODEL_FEATURES, "WEEK", max_folds=20)
        self.assertIn("cumulative_return", bt)
        self.assertGreaterEqual(bt["folds_tested"], 1)

    def test_cloud_radar_event_key_changes_by_state(self):
        a = _event_key("NVDA", "BUY WATCH", "BUY")
        b = _event_key("NVDA", "ENTRY_CONFIRMED", "LONG")
        self.assertNotEqual(a, b)
        self.assertIn("NVDA", a)

    def test_cloud_radar_custom_universe(self):
        old = os.environ.get("RADAR_TICKERS")
        os.environ["RADAR_TICKERS"] = "NVDA, AAPL, NVDA"
        try:
            self.assertEqual(configured_universe(), ["NVDA", "AAPL"])
        finally:
            if old is None:
                os.environ.pop("RADAR_TICKERS", None)
            else:
                os.environ["RADAR_TICKERS"] = old

    def test_cloud_radar_skips_when_market_not_live(self):
        from unittest.mock import patch
        from radar_engine import run_cloud_radar
        closed = {"status": "CLOSED", "is_pre": False, "is_open": False, "closed_reason": "WEEKEND"}
        with patch("radar_engine.market_status", return_value=closed), patch("radar_engine.scanner") as scan_mock:
            out = run_cloud_radar(force_run=False, send_summary=False)
            self.assertEqual(out["scanned"], 0)
            scan_mock.assert_not_called()

    def test_cloud_radar_confirmed_alert_path(self):
        from unittest.mock import patch
        from radar_engine import run_cloud_radar
        live = {"status": "REGULAR SESSION", "is_pre": False, "is_open": True, "closed_reason": None}
        frame = pd.DataFrame([{
            "ticker": "NVDA", "DAY": "PRE-BUY", "DAY_side": "BUY", "DAY_score": 88.0,
            "DAY_prob": 70.0, "DAY_exp": 0.8, "DAY_quality": 70.0, "DAY_reason": "ok",
            "event_risk": "NORMAL", "regime": "RISK-ON",
        }])
        detail = {
            "ticker": "NVDA",
            "confirm": {"status": "CONFIRMED", "signal": "ENTER BUY", "bars": 4, "volume_status": "OK"},
            "plan": {"status": "READY", "side": "LONG", "entry": 100.0, "stop": 98.0, "target": 104.0, "rr": 2.0, "shares": 10},
            "pre": {"p_up": 0.70, "confidence": 0.75},
            "events": {"event_risk": "NORMAL"},
        }
        with patch("radar_engine.market_status", return_value=live), \
             patch("radar_engine.telegram_configured", return_value=True), \
             patch("radar_engine.scanner", return_value=frame), \
             patch("radar_engine.analyze_day_fast", return_value=detail), \
             patch("radar_engine.event_exists", return_value=False), \
             patch("radar_engine.send_telegram", return_value=True), \
             patch("radar_engine.record_event_once", return_value=True):
            out = run_cloud_radar(force_run=False, send_summary=False)
            self.assertEqual(out["sent"], 1)
            self.assertEqual(out["confirmed"], 1)


class DatabaseTests(unittest.TestCase):
    def test_concurrent_database_init_is_idempotent(self):
        import concurrent.futures
        import db
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "concurrent.db"
            old = os.environ.get("DATABASE_URL")
            os.environ["DATABASE_URL"] = f"sqlite:///{path}"
            db.reset_engine_cache()
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                    futures = [pool.submit(db.init_db) for _ in range(24)]
                    for f in futures:
                        f.result(timeout=10)
                self.assertFalse(db.event_exists("not-present"))
            finally:
                db.reset_engine_cache()
                if old is None:
                    os.environ.pop("DATABASE_URL", None)
                else:
                    os.environ["DATABASE_URL"] = old

    def test_database_roundtrip(self):
        import db
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "test.db"
            old = os.environ.get("DATABASE_URL")
            os.environ["DATABASE_URL"] = f"sqlite:///{path}"
            db.reset_engine_cache()
            try:
                db.init_db()
                self.assertFalse(db.event_exists("scanner-test"))
                self.assertTrue(db.record_event_once("scanner-test", "NVDA", "DAY", "BUY WATCH", 0, 0, 0, "test"))
                self.assertTrue(db.event_exists("scanner-test"))
                pid = db.open_position("NVDA", "DAY", "LONG", 2, 100.0, 95.0, 110.0, "test")
                self.assertGreaterEqual(pid, 1)
                opened = db.open_positions()
                self.assertEqual(len(opened), 1)
                db.update_position_mark(pid, 105.0, 10.0)
                db.close_position(pid, 106.0, 12.0, "MANUAL")
                self.assertEqual(len(db.open_positions()), 0)
                ok = db.log_prediction_once(
                    event_key="test|NVDA|DAY", ticker="NVDA", horizon="DAY", signal="PRE-BUY",
                    p_up=0.7, p_down=0.3, expected_return=0.01, reference_price=100.0,
                    data_asof="2026-09-17", prediction_date="2026-09-18", target_date="2026-09-18",
                    target_days=0, target_type="OPEN_CLOSE", quality_score=0.5, model_version="7.0.0",
                )
                self.assertTrue(ok)
                self.assertEqual(len(db.unresolved_predictions()), 1)
            finally:
                db.reset_engine_cache()
                if old is None:
                    os.environ.pop("DATABASE_URL", None)
                else:
                    os.environ["DATABASE_URL"] = old


if __name__ == "__main__":
    unittest.main()
