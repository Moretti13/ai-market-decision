from __future__ import annotations

import json
import os
import time as time_module
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from config import DEFAULTS
from market_clock import market_status
from radar_engine import run_cloud_radar
from radar_ledger import export_trade_ledger, send_daily_summary

NY = ZoneInfo("America/New_York")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int, minimum: int = 1, maximum: int = 120) -> int:
    raw = os.getenv(name)
    try:
        value = int(float(raw)) if raw is not None and str(raw).strip() != "" else int(default)
    except Exception:
        value = int(default)
    return max(minimum, min(maximum, value))


def _parse_hhmm(raw: str, fallback: str) -> time:
    value = (raw or fallback).strip() or fallback
    try:
        hh, mm = [int(x) for x in value.split(":", 1)]
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return time(hh, mm)
    except Exception:
        pass
    hh, mm = [int(x) for x in fallback.split(":", 1)]
    return time(hh, mm)


def _window_end(now_et: datetime) -> datetime | None:
    """Return the end of the current long-running GitHub block.

    The workflow starts only a few times per day. Once a block starts, this
    daemon owns the 15-minute cadence itself instead of relying on dozens of
    GitHub cron events.
    """
    pre_end = _parse_hhmm(
        os.getenv("RADAR_DAEMON_PREMARKET_END", ""),
        str(DEFAULTS.get("radar_daemon_premarket_end", "09:27")),
    )
    regular_end = _parse_hhmm(
        os.getenv("RADAR_DAEMON_REGULAR_END", ""),
        str(DEFAULTS.get("radar_daemon_regular_end", "15:22")),
    )

    t = now_et.timetz().replace(tzinfo=None)
    if time(4, 0) <= t < time(9, 30):
        return datetime.combine(now_et.date(), pre_end, tzinfo=NY)
    if time(9, 30) <= t < time(15, 30):
        return datetime.combine(now_et.date(), regular_end, tzinfo=NY)
    return None


def _seconds_until(target: datetime) -> float:
    return max(0.0, (target - datetime.now(target.tzinfo)).total_seconds())


def run_once(*, force_run: bool = False, send_summary: bool = False) -> dict:
    result = run_cloud_radar(force_run=force_run, send_summary=send_summary)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str), flush=True)
    try:
        export_trade_ledger()
    except Exception as exc:
        print(f"ledger export failed: {type(exc).__name__}: {exc}", flush=True)
    return result


def run_window() -> int:
    interval_min = _env_int(
        "RADAR_INTERVAL_MINUTES",
        int(DEFAULTS.get("radar_daemon_interval_minutes", 15)),
        5,
        60,
    )
    now_et = datetime.now(NY)
    clock = market_status("SPY", now_et)

    if not clock.get("is_session_day", False):
        # Holiday/weekend: one lightweight reconciliation and exit. Do not keep
        # a GitHub runner sleeping for hours on a closed-market day.
        run_once(force_run=False, send_summary=False)
        return 0

    # Dedicated post-close cycle: reconcile any remaining DAY paper positions,
    # export the ledger and send one daily P/L summary.
    if clock.get("is_post") or now_et.timetz().replace(tzinfo=None) >= time(16, 0):
        run_once(force_run=False, send_summary=False)
        send_daily_summary(now_et)
        return 0

    end = _window_end(now_et)
    if end is None:
        # A delayed schedule can land just before/after a window. We still do
        # one safe cycle so missed starts do not silently disappear.
        run_once(force_run=False, send_summary=False)
        return 0

    next_run = datetime.now(NY)
    cycles = 0
    while True:
        now_et = datetime.now(NY)
        if now_et >= end:
            break

        if now_et >= next_run:
            run_once(force_run=False, send_summary=False)
            cycles += 1
            # Advance from the planned slot, not completion time, so a slow scan
            # does not accumulate timing drift.
            next_run = next_run + timedelta(minutes=interval_min)
            while next_run <= datetime.now(NY):
                next_run = next_run + timedelta(minutes=interval_min)

        sleep_for = min(30.0, _seconds_until(next_run), _seconds_until(end))
        if sleep_for <= 0:
            continue
        time_module.sleep(sleep_for)

    print(f"V7.5 daemon window complete; cycles={cycles}; end={end.isoformat()}", flush=True)
    export_trade_ledger()
    return 0


def main() -> int:
    mode = (os.getenv("RADAR_DAEMON_MODE", "once") or "once").strip().lower()
    if mode == "window":
        return run_window()

    force = _env_bool("RADAR_FORCE_RUN", False)
    summary = _env_bool("RADAR_SEND_SUMMARY", False)
    result = run_once(force_run=force, send_summary=summary)
    if summary and not result.get("summary_sent", False):
        return 3
    if result.get("errors") and result.get("scanned", 0) == 0 and not result.get("paper_checked", 0):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
