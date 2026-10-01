from __future__ import annotations

import json
import os
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from alerts import send_telegram
from config import DEFAULTS
from data_layer import fetch, safe_float
from db import all_positions, close_position, open_position, open_positions, recent_events, update_position_mark

NY = ZoneInfo("America/New_York")


def _state_dir() -> Path:
    path = Path(os.getenv("RADAR_STATE_DIR", ".radar_state"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _utc_ts(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _net_pnl(side: str, quantity: int, entry: float, exit_price: float) -> tuple[float, float, float]:
    q = max(0, int(quantity or 0))
    entry = float(entry or 0.0)
    exit_price = float(exit_price or 0.0)
    gross = (entry - exit_price) * q if str(side).upper() == "SHORT" else (exit_price - entry) * q
    friction_bps = float(DEFAULTS.get("commission_bps", 0.0)) + float(DEFAULTS.get("slippage_bps", 0.0))
    costs = (abs(entry) + abs(exit_price)) * q * friction_bps / 10_000.0
    return float(gross - costs), float(gross), float(costs)


def _unrealized_pnl(side: str, quantity: int, entry: float, mark: float) -> float:
    q = max(0, int(quantity or 0))
    return float((float(entry) - float(mark)) * q) if str(side).upper() == "SHORT" else float((float(mark) - float(entry)) * q)


def _exit_message(trade: dict) -> str:
    reason = str(trade.get("reason", "EXIT"))
    emoji = "🎯" if "TARGET" in reason else "🛑" if "STOP" in reason else "⏰"
    return (
        f"{emoji} AI Market Decision V7.5 — PAPER EXIT\n"
        f"{trade.get('ticker', '')} · {trade.get('side', '')}\n"
        f"Motivo: {reason}\n"
        f"Entry: {float(trade.get('entry', 0.0)):.2f}\n"
        f"Exit: {float(trade.get('exit', 0.0)):.2f}\n"
        f"Qty: {int(trade.get('quantity', 0) or 0)}\n"
        f"P/L netto paper: {float(trade.get('net_pnl', 0.0)):+.2f}\n"
        "Risultato simulato: include una stima prudente di costi/slippage."
    )


def register_confirmed_trade(detail: dict, event_key: str, score: float) -> dict:
    ticker = str(detail.get("ticker", "")).upper()
    plan = detail.get("plan", {}) or {}
    if not ticker or plan.get("status") != "READY":
        return {"opened": False, "reason": "PLAN_NOT_READY"}

    existing = open_positions()
    if not existing.empty:
        mask = (existing["ticker"].astype(str).str.upper() == ticker) & (existing["horizon"].astype(str).str.upper() == "DAY")
        if bool(mask.any()):
            row = existing.loc[mask].iloc[0]
            return {"opened": False, "reason": "ALREADY_OPEN", "position_id": int(row["id"])}

    qty = int(plan.get("shares", 0) or 0)
    entry = safe_float(plan.get("entry"), 0.0)
    stop = safe_float(plan.get("stop"), 0.0)
    target = safe_float(plan.get("target"), 0.0)
    if qty <= 0 or entry <= 0 or stop <= 0 or target <= 0:
        return {"opened": False, "reason": "INVALID_PLAN"}

    pid = open_position(
        ticker,
        "DAY",
        str(plan.get("side") or "LONG"),
        qty,
        entry,
        stop,
        target,
        notes=f"Cloud Radar V7.5; source={event_key}; score={float(score):.1f}",
    )
    export_trade_ledger()
    return {"opened": bool(pid), "reason": "OPENED" if pid else "DB_OPEN_FAILED", "position_id": int(pid or 0)}


def _first_exit_from_bars(side: str, stop: float, target: float, bars: pd.DataFrame) -> tuple[float | None, str | None, str | None]:
    side = str(side).upper()
    for ts, row in bars.iterrows():
        high = safe_float(row.get("High"), np.nan)
        low = safe_float(row.get("Low"), np.nan)
        if not np.isfinite(high) or not np.isfinite(low):
            continue
        if side == "SHORT":
            stop_hit = high >= stop
            target_hit = low <= target
        else:
            stop_hit = low <= stop
            target_hit = high >= target
        if stop_hit and target_hit:
            return float(stop), "STOP_SAME_BAR", str(ts)
        if stop_hit:
            return float(stop), "STOP", str(ts)
        if target_hit:
            return float(target), "TARGET", str(ts)
    return None, None, None


def reconcile_open_positions(*, now_et: datetime | None = None, notify: bool = True) -> dict:
    now_et = now_et.astimezone(NY) if now_et else datetime.now(NY)
    opened = open_positions()
    result = {"checked": int(len(opened)), "closed": [], "open": int(len(opened)), "errors": []}
    if opened.empty:
        export_trade_ledger()
        return result

    for _, pos in opened.iterrows():
        try:
            pid = int(pos["id"])
            ticker = str(pos["ticker"]).upper()
            side = str(pos["side"]).upper()
            qty = int(pos["quantity"] or 0)
            entry = safe_float(pos["entry"], 0.0)
            stop = safe_float(pos["stop"], 0.0)
            target = safe_float(pos["target"], 0.0)
            created = _utc_ts(pos["created_at"])
            entry_et = created.tz_convert(NY)

            bars = fetch(ticker, period="5d", interval="5m", prepost=False, force=True)
            bars = bars[bars.index >= created.floor("5min")].copy() if not bars.empty else bars
            if bars.empty:
                result["errors"].append(f"{ticker}: no bars after entry timestamp")
                continue

            exit_price, reason, hit_at = _first_exit_from_bars(side, stop, target, bars)
            if exit_price is None:
                bars_et = bars.index.tz_convert(NY)
                mask = np.array([d == entry_et.date() for d in bars_et.date], dtype=bool)
                entry_day_bars = bars.loc[mask]
                should_eod = now_et.date() > entry_et.date() or (now_et.date() == entry_et.date() and now_et.time() >= time(15, 55))
                if should_eod and not entry_day_bars.empty:
                    exit_price = safe_float(entry_day_bars["Close"].iloc[-1], 0.0)
                    reason = "EOD"
                    hit_at = str(entry_day_bars.index[-1])

            if exit_price is not None and exit_price > 0:
                net, gross, costs = _net_pnl(side, qty, entry, exit_price)
                close_position(pid, exit_price, net, reason or "EXIT")
                trade = {
                    "position_id": pid, "ticker": ticker, "side": side, "quantity": qty,
                    "entry": entry, "exit": float(exit_price), "reason": reason or "EXIT",
                    "hit_at": hit_at, "gross_pnl": gross, "estimated_costs": costs, "net_pnl": net,
                }
                result["closed"].append(trade)
                if notify:
                    send_telegram(_exit_message(trade))
            else:
                mark = safe_float(bars["Close"].iloc[-1], entry)
                update_position_mark(pid, mark, _unrealized_pnl(side, qty, entry, mark))
        except Exception as exc:
            result["errors"].append(f"{pos.get('ticker', '?')}: {type(exc).__name__}: {exc}")

    result["open"] = int(len(open_positions()))
    export_trade_ledger()
    return result


def paper_metrics() -> dict:
    df = all_positions(5000)
    if df.empty:
        return {"trades": 0, "closed": 0, "open": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "net_pnl": 0.0, "profit_factor": 0.0, "max_drawdown": 0.0}
    closed = df[df["status"].astype(str).str.upper() == "CLOSED"].copy()
    open_count = int((df["status"].astype(str).str.upper() == "OPEN").sum())
    if closed.empty:
        return {"trades": int(len(df)), "closed": 0, "open": open_count, "wins": 0, "losses": 0, "win_rate": 0.0, "net_pnl": 0.0, "profit_factor": 0.0, "max_drawdown": 0.0}
    pnl = pd.to_numeric(closed["realized_pnl"], errors="coerce").fillna(0.0)
    wins = int((pnl > 0).sum())
    losses = int((pnl < 0).sum())
    gross_win = float(pnl[pnl > 0].sum())
    gross_loss = float(-pnl[pnl < 0].sum())
    ordered = closed.assign(_pnl=pnl).sort_values(["closed_at", "id"], na_position="last")
    equity = ordered["_pnl"].cumsum()
    peak = equity.cummax().clip(lower=0.0)
    drawdown = peak - equity
    return {
        "trades": int(len(df)), "closed": int(len(closed)), "open": open_count,
        "wins": wins, "losses": losses, "win_rate": float(wins / max(1, wins + losses)),
        "net_pnl": float(pnl.sum()),
        "profit_factor": float(gross_win / gross_loss) if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0),
        "max_drawdown": float(drawdown.max()) if not drawdown.empty else 0.0,
        "avg_win": float(pnl[pnl > 0].mean()) if wins else 0.0,
        "avg_loss": float(pnl[pnl < 0].mean()) if losses else 0.0,
    }


def export_trade_ledger() -> dict:
    state = _state_dir()
    positions = all_positions(5000)
    events = recent_events(5000)
    if not positions.empty:
        positions.sort_values("id").to_csv(state / "trade_ledger.csv", index=False)
    else:
        pd.DataFrame(columns=["id", "ticker", "status", "entry", "exit_price", "realized_pnl"]).to_csv(state / "trade_ledger.csv", index=False)
    if not events.empty:
        events.sort_values("id").to_csv(state / "signal_events.csv", index=False)
    else:
        pd.DataFrame(columns=["id", "event_key", "ticker", "signal"]).to_csv(state / "signal_events.csv", index=False)
    metrics = paper_metrics()
    (state / "performance_summary.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
    return metrics


def daily_summary_message(now_et: datetime | None = None) -> str:
    now_et = now_et.astimezone(NY) if now_et else datetime.now(NY)
    df = all_positions(5000)
    metrics = paper_metrics()
    today_closed = pd.DataFrame()
    if not df.empty:
        closed = df[df["status"].astype(str).str.upper() == "CLOSED"].copy()
        if not closed.empty:
            closed["_closed_et"] = pd.to_datetime(closed["closed_at"], utc=True, errors="coerce").dt.tz_convert(NY)
            today_closed = closed[closed["_closed_et"].dt.date == now_et.date()].copy()
    today_pnl = float(pd.to_numeric(today_closed.get("realized_pnl", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum()) if not today_closed.empty else 0.0
    return "\n".join([
        "📊 AI Market Decision V7.5 — DAILY PAPER SUMMARY",
        f"Data ET: {now_et.date().isoformat()}",
        f"Trade chiusi oggi: {len(today_closed)}",
        f"P/L netto paper oggi: {today_pnl:+.2f}",
        f"Cumulativo: {metrics['net_pnl']:+.2f}",
        f"Win rate: {metrics['win_rate']*100:.1f}% ({metrics['wins']}W/{metrics['losses']}L)",
        f"Profit factor: {metrics['profit_factor']:.2f}",
        f"Max drawdown paper: {metrics['max_drawdown']:.2f}",
        f"Posizioni ancora aperte: {metrics['open']}",
        "Statistiche paper, non rendimento garantito o risultato reale di broker.",
    ])


def send_daily_summary(now_et: datetime | None = None) -> bool:
    export_trade_ledger()
    return bool(send_telegram(daily_summary_message(now_et)))
