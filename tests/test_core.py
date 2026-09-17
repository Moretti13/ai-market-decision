import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data_layer import daily_features, safe_float  # noqa: E402
from market_clock import session_hours  # noqa: E402


def synthetic_ohlcv(n=260):
    idx = pd.date_range("2024-01-02", periods=n, freq="B", tz="UTC")
    base = 100 + np.cumsum(np.sin(np.arange(n) / 14) + 0.2)
    close = pd.Series(base, index=idx)
    open_ = close.shift(1).fillna(close.iloc[0]) * 1.001
    high = pd.concat([open_, close], axis=1).max(axis=1) * 1.01
    low = pd.concat([open_, close], axis=1).min(axis=1) * 0.99
    volume = pd.Series(1_000_000, index=idx)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume})


def test_daily_features_not_empty():
    d = daily_features(synthetic_ohlcv())
    assert not d.empty
    for col in ["ret1", "ret5", "ret20", "rsi14", "atr14", "atr_pct"]:
        assert col in d.columns
        assert np.isfinite(d[col].tail(20)).all()


def test_safe_float():
    assert safe_float("3.5") == 3.5
    assert safe_float(float("nan"), 7) == 7


def test_session_hours_us():
    tz, (op, cl) = session_hours("NVDA")
    assert str(tz) == "America/New_York"
    assert (op.hour, op.minute) == (9, 30)
    assert (cl.hour, cl.minute) == (16, 0)
