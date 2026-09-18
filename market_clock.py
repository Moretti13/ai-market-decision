from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
ROME = ZoneInfo("Europe/Rome")
BERLIN = ZoneInfo("Europe/Berlin")
PARIS = ZoneInfo("Europe/Paris")
AMSTERDAM = ZoneInfo("Europe/Amsterdam")
MADRID = ZoneInfo("Europe/Madrid")

try:
    import pandas_market_calendars as mcal
except Exception:  # optional at import/test time
    mcal = None


def session_hours(ticker: str):
    t = ticker.upper()
    if t.endswith(".MI"):
        return ROME, (time(9, 0), time(17, 30))
    if t.endswith(".DE"):
        return BERLIN, (time(9, 0), time(17, 30))
    if t.endswith(".PA"):
        return PARIS, (time(9, 0), time(17, 30))
    if t.endswith(".AS"):
        return AMSTERDAM, (time(9, 0), time(17, 30))
    if t.endswith(".MC"):
        return MADRID, (time(9, 0), time(17, 30))
    return NY, (time(9, 30), time(16, 0))


def _calendar_name(ticker: str) -> str | None:
    # NYSE holidays/early closes are the most important for the default universe.
    if session_hours(ticker)[0] == NY:
        return "NYSE"
    if ticker.upper().endswith(".DE"):
        return "XETR"
    # For other European venues, fall back to weekday logic if the installed
    # calendar package does not expose an exact venue name.
    return None


def _is_session_day(local_date: date, ticker: str) -> bool:
    if local_date.weekday() >= 5:
        return False
    name = _calendar_name(ticker)
    if mcal is None or not name:
        return True
    try:
        calendar = mcal.get_calendar(name)
        schedule = calendar.schedule(start_date=local_date, end_date=local_date)
        return not schedule.empty
    except Exception:
        return True


def next_session_date(ticker: str, start: date, steps: int = 1) -> date:
    if steps <= 0:
        return start
    d = start
    found = 0
    while found < steps:
        d += timedelta(days=1)
        if _is_session_day(d, ticker):
            found += 1
    return d


def market_status(ticker: str, now: datetime | None = None) -> dict:
    tz, (default_open, default_close) = session_hours(ticker)
    now = now.astimezone(tz) if now else datetime.now(tz)
    local_time = now.timetz().replace(tzinfo=None)
    is_day = _is_session_day(now.date(), ticker)
    op, cl = default_open, default_close

    # Use the exchange calendar to respect US early closes when available.
    name = _calendar_name(ticker)
    if is_day and mcal is not None and name:
        try:
            schedule = mcal.get_calendar(name).schedule(start_date=now.date(), end_date=now.date())
            if not schedule.empty:
                open_dt = schedule.iloc[0]["market_open"].to_pydatetime().astimezone(tz)
                close_dt = schedule.iloc[0]["market_close"].to_pydatetime().astimezone(tz)
                op, cl = open_dt.time().replace(tzinfo=None), close_dt.time().replace(tzinfo=None)
        except Exception:
            pass

    pre_start = time(4, 0) if tz == NY else time(7, 0)
    post_end = time(20, 0) if tz == NY else time(19, 0)

    if is_day and op <= local_time < cl:
        status, is_open, is_pre, is_post, next_event = "REGULAR SESSION", True, False, False, "CLOSE"
    elif is_day and pre_start <= local_time < op:
        status, is_open, is_pre, is_post, next_event = "PRE-MARKET", False, True, False, "OPEN"
    elif is_day and cl <= local_time < post_end:
        status, is_open, is_pre, is_post, next_event = "AFTER-HOURS", False, False, True, "NEXT OPEN"
    else:
        status, is_open, is_pre, is_post, next_event = "CLOSED", False, False, False, "NEXT SESSION"

    return {
        "status": status,
        "timezone": tz,
        "timezone_name": str(tz),
        "now": now,
        "open": op,
        "close": cl,
        "is_open": is_open,
        "is_pre": is_pre,
        "is_post": is_post,
        "is_session_day": is_day,
        "next_event": next_event,
    }
