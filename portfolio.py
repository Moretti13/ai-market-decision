from __future__ import annotations

from datetime import datetime
from typing import Dict, List

import pandas as pd

from data_layer import latest_regular, quote_snapshot, safe_float
from db import close_position_once, open_positions, update_position_mark
from market_clock import market_status


def position_pnl(side: str, quantity: int, entry: float, price: float) -> float:
    mult = 1.0 if str(side).upper() == "LONG" else -1.0
    return mult * int(quantity) * (float(price) - float(entry))


def _day_position_is_stale(row, status: dict) -> bool:
    """Return True when a DAY position survived beyond its intended cash session."""
    if str(row.get("horizon", "")).upper() != "DAY":
        return False
    now = status.get("now")
    if now is None:
        return False
    if status.get("is_post"):
        return True
    if status.get("is_session_day") and not status.get("is_open") and not status.get("is_pre"):
        try:
            local_time = now.timetz().replace(tzinfo=None)
            close_time = status.get("close")
            if close_time is not None and local_time >= close_time:
                return True
        except Exception:
            pass

    # Recovery path: if the prior close cycle was missed, never carry a DAY
    # paper trade into a later session.
    try:
        created = datetime.fromisoformat(str(row.get("created_at")))
        if created.tzinfo is not None:
            created = created.astimezone(status.get("timezone"))
        return created.date() < now.date()
    except Exception:
        return False


def _intraday_level_exit(row) -> tuple[str, float] | None:
    """Detect a DAY stop/target touch on completed 5m bars after the paper entry.

    The partial 5m bar containing the entry timestamp is skipped unless the entry
    is exactly on a 5m boundary, avoiding a false trigger from price action that
    happened before the paper trade existed. If stop and target are both touched
    in one bar, STOP wins conservatively because OHLC cannot reveal hit order.
    """
    if str(row.get("horizon", "")).upper() != "DAY":
        return None
    stop = safe_float(row.get("stop"), 0.0)
    target = safe_float(row.get("target"), 0.0)
    if stop <= 0 and target <= 0:
        return None
    bars = latest_regular(str(row["ticker"]))
    if bars is None or bars.empty:
        return None

    try:
        created = pd.Timestamp(row.get("created_at"))
        if created.tzinfo is None:
            created = created.tz_localize("UTC")
        else:
            created = created.tz_convert("UTC")
        start = created.ceil("5min")
        idx = bars.index
        if idx.tz is None:
            idx = idx.tz_localize("UTC")
            bars = bars.copy()
            bars.index = idx
        else:
            start = start.tz_convert(idx.tz)
        eligible = bars.loc[bars.index >= start]
    except Exception:
        return None

    side = str(row.get("side", "")).upper()
    for _, bar in eligible.iterrows():
        high = safe_float(bar.get("High"), 0.0)
        low = safe_float(bar.get("Low"), 0.0)
        if high <= 0 or low <= 0:
            continue
        if side == "LONG":
            stop_hit = stop > 0 and low <= stop
            target_hit = target > 0 and high >= target
        else:
            stop_hit = stop > 0 and high >= stop
            target_hit = target > 0 and low <= target
        if stop_hit:
            return "STOP", float(stop)
        if target_hit:
            return "TARGET", float(target)
    return None


def monitor_open_positions(auto_close_levels: bool = True, close_at_session_end: bool = True) -> Dict:
    positions = open_positions()
    events: List[Dict] = []
    errors: List[str] = []
    if positions.empty:
        return {"updated": 0, "closed": 0, "events": [], "errors": []}
    updated = closed = 0
    for _, row in positions.iterrows():
        try:
            q = quote_snapshot(str(row["ticker"]))
            price = safe_float(q.get("price"))
            if price <= 0:
                raise RuntimeError("Prezzo live non disponibile")
            pnl = position_pnl(row["side"], int(row["quantity"]), float(row["entry"]), price)
            update_position_mark(int(row["id"]), price, pnl)
            updated += 1

            reason = None
            exit_price = price
            side = str(row["side"]).upper()
            stop = safe_float(row.get("stop"), 0.0)
            target = safe_float(row.get("target"), 0.0)

            # DAY paper positions need touch detection, not only a 15-minute
            # snapshot, otherwise a stop/target hit between worker cycles can
            # disappear before the next mark.
            level_touch = _intraday_level_exit(row) if auto_close_levels else None
            if level_touch is not None:
                reason, exit_price = level_touch
            elif auto_close_levels and side == "LONG":
                if stop > 0 and price <= stop:
                    reason, exit_price = "STOP", price
                elif target > 0 and price >= target:
                    reason, exit_price = "TARGET", price
            elif auto_close_levels and side == "SHORT":
                if stop > 0 and price >= stop:
                    reason, exit_price = "STOP", price
                elif target > 0 and price <= target:
                    reason, exit_price = "TARGET", price

            if reason is None and close_at_session_end and str(row.get("horizon", "")).upper() == "DAY":
                status = market_status(str(row["ticker"]))
                if _day_position_is_stale(row, status):
                    reason, exit_price = "SESSION_END", price

            exit_pnl = position_pnl(row["side"], int(row["quantity"]), float(row["entry"]), exit_price)
            if reason and close_position_once(int(row["id"]), exit_price, exit_pnl, reason):
                closed += 1
                events.append({
                    "id": int(row["id"]),
                    "ticker": str(row["ticker"]),
                    "horizon": str(row.get("horizon", "")),
                    "side": side,
                    "quantity": int(row["quantity"]),
                    "reason": reason,
                    "price": exit_price,
                    "pnl": exit_pnl,
                })
        except Exception as exc:
            errors.append(f"{row.get('ticker')}: {exc}")
    return {"updated": updated, "closed": closed, "events": events, "errors": errors[:10]}
