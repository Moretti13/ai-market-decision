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
from data_layer import daily_features, safe_float  # noqa: E402
from market_clock import session_hours  # noqa: E402
from model_engine import _backtest_frame, _train_predict  # noqa: E402
from news_engine import score_title  # noqa: E402
from signal_engine import _confirmation_logic  # noqa: E402
from portfolio import position_pnl  # noqa: E402


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

    def test_market_hours(self):
        tz, (op, cl) = session_hours("NVDA")
        self.assertEqual(str(tz), "America/New_York")
        self.assertEqual((op.hour, op.minute), (9, 30))
        self.assertEqual((cl.hour, cl.minute), (16, 0))

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


class DatabaseTests(unittest.TestCase):
    def test_database_roundtrip(self):
        import db
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "test.db"
            old = os.environ.get("DATABASE_URL")
            os.environ["DATABASE_URL"] = f"sqlite:///{path}"
            db.reset_engine_cache()
            try:
                db.init_db()
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
