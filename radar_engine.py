from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Iterable

import pandas as pd

from alerts import send_telegram, send_telegram_detailed, telegram_configured
from config import ALL_UNIVERSE, DEFAULTS
from db import event_exists, open_position_once, record_event_once, trade_events
from market_clock import market_status
from portfolio import monitor_open_positions
from scanner import scanner
from signal_engine import analyze_day_fast


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int, minimum: int = 1, maximum: int = 500) -> int:
    try:
        value = int(float(os.getenv(name, str(default))))
    except Exception:
        value = int(default)
    return max(minimum, min(maximum, value))


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except Exception:
        value = float(default)
    return max(minimum, min(maximum, value))


def configured_universe() -> list[str]:
    raw = os.getenv("RADAR_TICKERS", "").strip()
    if raw:
        values = [x.strip().upper() for x in raw.replace(";", ",").split(",") if x.strip()]
        return list(dict.fromkeys(values))
    return list(ALL_UNIVERSE)


def _event_key(ticker: str, state: str, side: str = "") -> str:
    # One notification per state/side/ticker/session day. A later transition from
    # WATCH to ENTRY CONFIRMED is a different key and therefore can notify again.
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"V74|CLOUD|{day}|DAY|{ticker.upper()}|{state.upper()}|{side.upper()}"


def _fmt_price(value) -> str:
    try:
        if value is None:
            return "—"
        return f"{float(value):.2f}"
    except Exception:
        return "—"


def _send_once(event_key: str, ticker: str, signal: str, message: str, *, entry=0.0, stop=0.0, target=0.0, notes="") -> str:
    if event_exists(event_key):
        return "duplicate"
    if not send_telegram(message):
        return "failed"
    record_event_once(event_key, ticker, "DAY", signal, float(entry or 0.0), float(stop or 0.0), float(target or 0.0), notes=notes)
    return "sent"


def _watch_message(row: pd.Series, state: str) -> str:
    return (
        "📡 AI Market Decision V7.4 CLOUD RADAR\n"
        f"{row.get('ticker', '')} · DAY · {state}\n"
        f"Opportunity score: {float(row.get('DAY_score', 0.0)):.1f}/100\n"
        f"P(up): {float(row.get('DAY_prob', 50.0)):.1f}%\n"
        f"Rend. atteso: {float(row.get('DAY_exp', 0.0)):+.2f}%\n"
        f"Qualità modello: {float(row.get('DAY_quality', 0.0)):.1f}%\n"
        f"Event risk: {row.get('event_risk', 'NORMAL')}\n"
        f"Regime: {row.get('regime', 'UNKNOWN')}\n"
        f"Motivo: {row.get('DAY_reason', '—')}\n"
        "WATCH non è un ordine: apri il ticker e attendi/conferma il setup 5m."
    )


def _confirmed_message(detail: dict, score: float) -> str:
    plan = detail.get("plan", {})
    confirm = detail.get("confirm", {})
    pre = detail.get("pre", {})
    events = detail.get("events", {})
    return (
        "✅ AI Market Decision V7.4 — ENTRY CONFIRMED\n"
        f"{detail.get('ticker', '')} · DAY · {confirm.get('signal', '')}\n"
        f"Opportunity score: {score:.1f}/100\n"
        f"P(up): {float(pre.get('p_up', 0.5))*100:.1f}%\n"
        f"Confidence: {float(pre.get('confidence', 0.0))*100:.1f}%\n"
        f"Entry: {_fmt_price(plan.get('entry'))}\n"
        f"Stop: {_fmt_price(plan.get('stop'))}\n"
        f"Target: {_fmt_price(plan.get('target'))}\n"
        f"R/R: {float(plan.get('rr', 0.0)):.2f}\n"
        f"Size paper: {int(plan.get('shares', 0) or 0)}\n"
        f"Event risk: {events.get('event_risk', 'NORMAL')}\n"
        f"5m bars: {int(confirm.get('bars', 0) or 0)} · Volume: {confirm.get('volume_status', 'N/D')}\n"
        "Segnale probabilistico, non profitto garantito. Verifica il prezzo prima di qualsiasi operazione."
    )


def _entry_cutoff_reached(clock: dict) -> tuple[bool, str]:
    """Return whether new US DAY entries are blocked by the ET cutoff."""
    cutoff_raw = os.getenv("RADAR_ENTRY_CUTOFF_ET", str(DEFAULTS.get("radar_entry_cutoff_et", "15:30")))
    try:
        hh, mm = [int(x) for x in cutoff_raw.split(":", 1)]
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError("invalid cutoff")
    except Exception:
        hh, mm = 15, 30
    now_local = clock.get("now")
    if now_local is None:
        return False, f"{hh:02d}:{mm:02d}"
    reached = bool(clock.get("is_open") and (now_local.hour, now_local.minute) >= (hh, mm))
    return reached, f"{hh:02d}:{mm:02d}"


def _exit_message(row: pd.Series) -> str:
    return (
        "🧾 AI Market Decision V7.4 — PAPER EXIT\n"
        f"{row.get('ticker', '')} · {row.get('side', '')} · {row.get('reason', 'EXIT')}\n"
        f"Qty: {int(row.get('quantity', 0) or 0)}\n"
        f"Exit: {_fmt_price(row.get('price'))}\n"
        f"P/L: {float(row.get('pnl', 0.0) or 0.0):+.2f}\n"
        f"Position ID: {int(row.get('position_id', 0) or 0)}"
    )


def _send_pending_exit_notifications(limit: int = 5000) -> tuple[int, int, int]:
    """Retry unsent EXIT notifications from the persistent trade ledger."""
    sent = failed = duplicates = 0
    df = trade_events("EXIT", limit=limit)
    if df.empty:
        return sent, failed, duplicates
    for _, row in df.iterrows():
        notify_key = f"V74|TRADE_NOTIFY|{row.get('event_key', '')}"
        if event_exists(notify_key):
            duplicates += 1
            continue
        if send_telegram(_exit_message(row)):
            ok = record_event_once(
                notify_key,
                str(row.get("ticker", "")),
                str(row.get("horizon", "POSITION")),
                f"EXIT {row.get('reason', '')}",
                float(row.get("price", 0.0) or 0.0),
                0.0,
                0.0,
                notes=f"position_id={int(row.get('position_id', 0) or 0)}; pnl={float(row.get('pnl', 0.0) or 0.0):.2f}",
            )
            if ok:
                sent += 1
            else:
                failed += 1
        else:
            failed += 1
    return sent, failed, duplicates


def run_cloud_radar(*, force_run: bool = False, send_summary: bool = False) -> dict:
    """Run one independent cloud-radar cycle.

    Position management is always executed first. New DAY entries remain gated by
    the live session and the 15:30 ET cutoff, while exits continue through the
    cash close so stop/target/session-end rules cannot be skipped by the cutoff.
    """
    clock = market_status("SPY")
    live = bool(clock.get("is_pre") or clock.get("is_open"))
    result = {
        "status": clock.get("status", "UNKNOWN"),
        "live": live,
        "scanned": 0,
        "eligible": 0,
        "sent": 0,
        "duplicates": 0,
        "failed": 0,
        "confirmed": 0,
        "watch": 0,
        "paper_opened": 0,
        "paper_existing": 0,
        "positions_updated": 0,
        "positions_closed": 0,
        "exit_sent": 0,
        "exit_failed": 0,
        "errors": [],
    }

    # Paper lifecycle is independent from new-entry eligibility. This must happen
    # before any closed-market/cutoff early return.
    try:
        monitor = monitor_open_positions(auto_close_levels=True, close_at_session_end=True)
        result["positions_updated"] = int(monitor.get("updated", 0) or 0)
        result["positions_closed"] = int(monitor.get("closed", 0) or 0)
        result["errors"].extend(monitor.get("errors", []) or [])
    except Exception as exc:
        result["errors"].append(f"Paper monitor: {exc}")

    telegram_ok = telegram_configured()
    if telegram_ok:
        try:
            exit_sent, exit_failed, _ = _send_pending_exit_notifications()
            result["exit_sent"] = int(exit_sent)
            result["exit_failed"] = int(exit_failed)
        except Exception as exc:
            result["errors"].append(f"Exit Telegram: {exc}")
    else:
        result["errors"].append("Telegram non configurato nel worker cloud")

    cutoff_reached, cutoff_text = _entry_cutoff_reached(clock)
    allow_candidate_alerts = bool(live and not cutoff_reached)

    if not live and not force_run:
        result["reason"] = f"Mercato non live: {clock.get('status', 'CLOSED')} / {clock.get('closed_reason') or ''}".strip()
        return result

    # After 15:30 ET we still manage/close positions, but normal scheduled runs
    # skip the expensive scanner. Manual diagnostics can still scan, without
    # being allowed to create a new paper entry.
    if cutoff_reached and not force_run and not send_summary:
        result["reason"] = f"Cutoff nuovi ingressi DAY raggiunto ({cutoff_text} ET); gestione uscite ancora attiva"
        return result

    universe = configured_universe()
    max_assets = _env_int("RADAR_ASSETS", DEFAULTS.get("radar_assets", 5), 1, min(100, len(universe) or 1))
    workers = _env_int("RADAR_WORKERS", 2, 1, 4)
    alert_score = _env_float("RADAR_ALERT_SCORE", DEFAULTS.get("scanner_alert_score", 75.0), 50.0, 99.0)
    confirm_floor = _env_float("RADAR_CONFIRM_FLOOR", DEFAULTS.get("radar_confirm_floor", 60.0), 50.0, 99.0)
    notify_watch = _env_bool("RADAR_NOTIFY_WATCH", True)
    max_alerts = _env_int("RADAR_MAX_ALERTS", DEFAULTS.get("radar_max_alerts", 3), 1, 20)
    capital = _env_float("RADAR_CAPITAL", DEFAULTS.get("capital", 10_000.0), 100.0, 100_000_000.0)
    risk_pct = _env_float("RADAR_RISK_PCT", DEFAULTS.get("risk_pct", 0.01), 0.001, 0.10)

    df = scanner(universe, max_assets=max_assets, workers=workers, horizons=("DAY",))
    result["scanned"] = int(len(df))
    if df.empty:
        result["reason"] = "Scanner senza risultati"
        return result

    candidates = df[df["DAY_score"] >= min(alert_score, confirm_floor)].copy()
    candidates = candidates.sort_values("DAY_score", ascending=False)
    result["eligible"] = int(len(candidates))

    for _, row in candidates.iterrows():
        if result["sent"] >= max_alerts:
            break
        ticker = str(row.get("ticker", "")).upper()
        score = float(row.get("DAY_score", 0.0) or 0.0)
        display = str(row.get("DAY", "WAIT")).upper()
        try:
            detail = analyze_day_fast(ticker, capital=capital, risk_pct=risk_pct, include_health=False)
            confirm = detail.get("confirm", {})
            plan = detail.get("plan", {})
            if allow_candidate_alerts and confirm.get("status") == "CONFIRMED" and plan.get("status") == "READY":
                side = str(plan.get("side") or confirm.get("signal") or "")
                key = _event_key(ticker, "ENTRY_CONFIRMED", side)
                pid, created = open_position_once(
                    key,
                    ticker,
                    "DAY",
                    side,
                    int(plan.get("shares", 0) or 0),
                    float(plan.get("entry") or 0.0),
                    plan.get("stop"),
                    plan.get("target"),
                    notes=f"Cloud Radar confirmed; score {score:.1f}",
                )
                if pid <= 0:
                    result["failed"] += 1
                    result["errors"].append(f"{ticker}: apertura paper non riuscita")
                    continue
                if created:
                    result["paper_opened"] += 1
                else:
                    result["paper_existing"] += 1

                status = _send_once(
                    key,
                    ticker,
                    str(confirm.get("signal", "ENTRY CONFIRMED")),
                    _confirmed_message(detail, score) + f"\nPaper position ID: {pid}",
                    entry=plan.get("entry"),
                    stop=plan.get("stop"),
                    target=plan.get("target"),
                    notes=f"Cloud Radar confirmed; paper position {pid}; score {score:.1f}",
                )
                if status == "sent":
                    result["sent"] += 1
                    result["confirmed"] += 1
                elif status == "duplicate":
                    result["duplicates"] += 1
                else:
                    result["failed"] += 1
                continue

            if allow_candidate_alerts and telegram_ok and notify_watch and score >= alert_score and display not in {"WAIT", "HOLD", "N/A"}:
                state = display
                if clock.get("is_open") and state in {"PRE-BUY", "PRE-SELL"}:
                    state = "BUY WATCH" if "BUY" in state else "SELL WATCH"
                key = _event_key(ticker, state, str(row.get("DAY_side", "")))
                status = _send_once(
                    key, ticker, state, _watch_message(row, state),
                    notes=f"Cloud Radar watch; score {score:.1f}; {row.get('DAY_reason', '')}",
                )
                if status == "sent":
                    result["sent"] += 1
                    result["watch"] += 1
                elif status == "duplicate":
                    result["duplicates"] += 1
                else:
                    result["failed"] += 1
        except Exception as exc:
            result["errors"].append(f"{ticker}: {exc}")

    result["summary_requested"] = bool(send_summary)
    result["summary_sent"] = False
    if send_summary:
        top = df.head(min(3, len(df)))
        lines = [
            "🧪 AI Market Decision V7.4.1 — Cloud Radar test",
            f"Mercato: {clock.get('status', 'UNKNOWN')}",
            f"Asset analizzati: {result['scanned']}",
            f"Alert inviati: {result['sent']} (confirmed {result['confirmed']}, watch {result['watch']})",
            f"Paper: aperte {result['paper_opened']} · chiuse {result['positions_closed']}",
        ]
        if cutoff_reached:
            lines.append(f"Nuovi ingressi bloccati dal cutoff {cutoff_text} ET")
        for _, row in top.iterrows():
            lines.append(f"{row.get('ticker')}: {row.get('DAY')} · {float(row.get('DAY_score', 0)):.1f}/100")
        if result["errors"]:
            lines.append(f"Errori: {len(result['errors'])}")
        diag = send_telegram_detailed("\n".join(lines))
        result["summary_sent"] = bool(diag.get("ok"))
        result["telegram_status_code"] = diag.get("status_code")
        if not diag.get("ok"):
            result["errors"].append(f"Telegram summary failed: {diag.get('error') or 'unknown error'}")
    return result
