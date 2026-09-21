from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Iterable

import pandas as pd

from alerts import send_telegram, telegram_configured
from config import ALL_UNIVERSE, DEFAULTS
from db import event_exists, record_event_once
from market_clock import market_status
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


def run_cloud_radar(*, force_run: bool = False, send_summary: bool = False) -> dict:
    """Run one independent cloud-radar cycle.

    Scheduled environments can call this every 15 minutes. The worker only does
    DAY calculations, then performs the 5-minute confirmation on candidates near
    the alert threshold. No Streamlit session is required.
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
        "errors": [],
    }
    if not live and not force_run:
        result["reason"] = f"Mercato non live: {clock.get('status', 'CLOSED')} / {clock.get('closed_reason') or ''}".strip()
        return result

    # Avoid opening new DAY trades too close to the US cash close. The default
    # cutoff is 15:30 ET and can be changed with RADAR_ENTRY_CUTOFF_ET=HH:MM.
    if live and clock.get("is_open") and not force_run:
        cutoff_raw = os.getenv("RADAR_ENTRY_CUTOFF_ET", str(DEFAULTS.get("radar_entry_cutoff_et", "15:30")))
        try:
            hh, mm = [int(x) for x in cutoff_raw.split(":", 1)]
            now_local = clock.get("now")
            if now_local is not None and (now_local.hour, now_local.minute) >= (hh, mm):
                result["reason"] = f"Cutoff nuovi ingressi DAY raggiunto ({hh:02d}:{mm:02d} ET)"
                return result
        except Exception:
            pass

    if not telegram_configured():
        result["errors"].append("Telegram non configurato nel worker cloud")
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

    # Confirm top candidates only. This keeps the 15-minute worker light while
    # still requiring live 5m confirmation before sending an ENTRY alert.
    candidates = df[df["DAY_score"] >= min(alert_score, confirm_floor)].copy()
    candidates = candidates.sort_values("DAY_score", ascending=False)
    result["eligible"] = int(len(candidates))

    allow_candidate_alerts = live

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
                status = _send_once(
                    key, ticker, str(confirm.get("signal", "ENTRY CONFIRMED")), _confirmed_message(detail, score),
                    entry=plan.get("entry"), stop=plan.get("stop"), target=plan.get("target"),
                    notes=f"Cloud Radar confirmed; score {score:.1f}",
                )
                if status == "sent":
                    result["sent"] += 1
                    result["confirmed"] += 1
                elif status == "duplicate":
                    result["duplicates"] += 1
                else:
                    result["failed"] += 1
                continue

            # Premarket or not-yet-confirmed regular-session candidate.
            if allow_candidate_alerts and notify_watch and score >= alert_score and display not in {"WAIT", "HOLD", "N/A"}:
                state = display
                # During regular hours PRE-BUY/PRE-SELL without confirmation is a WATCH.
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

    if send_summary:
        top = df.head(min(3, len(df)))
        lines = [
            "🧪 AI Market Decision V7.4 — Cloud Radar test",
            f"Mercato: {clock.get('status', 'UNKNOWN')}",
            f"Asset analizzati: {result['scanned']}",
            f"Alert inviati: {result['sent']} (confirmed {result['confirmed']}, watch {result['watch']})",
        ]
        for _, row in top.iterrows():
            lines.append(f"{row.get('ticker')}: {row.get('DAY')} · {float(row.get('DAY_score', 0)):.1f}/100")
        if result["errors"]:
            lines.append(f"Errori: {len(result['errors'])}")
        send_telegram("\n".join(lines))
    return result
