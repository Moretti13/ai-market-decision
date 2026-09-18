from __future__ import annotations

from typing import Dict, List

from data_layer import quote_snapshot, safe_float
from db import close_position, open_positions, update_position_mark


def position_pnl(side: str, quantity: int, entry: float, price: float) -> float:
    mult = 1.0 if str(side).upper() == "LONG" else -1.0
    return mult * int(quantity) * (float(price) - float(entry))


def monitor_open_positions(auto_close_levels: bool = True) -> Dict:
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
            if reason:
                close_position(int(row["id"]), price, pnl, reason)
                closed += 1
                events.append({"id": int(row["id"]), "ticker": row["ticker"], "reason": reason, "price": price, "pnl": pnl})
        except Exception as exc:
            errors.append(f"{row.get('ticker')}: {exc}")
    return {"updated": updated, "closed": closed, "events": events, "errors": errors[:10]}
