from __future__ import annotations

from datetime import datetime
from typing import Dict, List

from data_layer import quote_snapshot, safe_float
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
            side = str(row["side"]).upper()
            stop = safe_float(row.get("stop"), 0.0)
            target = safe_float(row.get("target"), 0.0)
            if auto_close_levels and side == "LONG":
                if stop > 0 and price <= stop:
                    reason = "STOP"
                elif target > 0 and price >= target:
                    reason = "TARGET"
            elif auto_close_levels and side == "SHORT":
                if stop > 0 and price >= stop:
                    reason = "STOP"
                elif target > 0 and price <= target:
                    reason = "TARGET"

            if reason is None and close_at_session_end and str(row.get("horizon", "")).upper() == "DAY":
                status = market_status(str(row["ticker"]))
                if _day_position_is_stale(row, status):
                    reason = "SESSION_END"

            if reason and close_position_once(int(row["id"]), price, pnl, reason):
                closed += 1
                events.append({
                    "id": int(row["id"]),
                    "ticker": str(row["ticker"]),
                    "horizon": str(row.get("horizon", "")),
                    "side": side,
                    "quantity": int(row["quantity"]),
                    "reason": reason,
                    "price": price,
                    "pnl": pnl,
                })
        except Exception as exc:
            errors.append(f"{row.get('ticker')}: {exc}")
    return {"updated": updated, "closed": closed, "events": events, "errors": errors[:10]}
