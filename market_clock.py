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
    if session_hours(ticker)[0] == NY:
        return "NYSE"
    if ticker.upper().endswith(".DE"):
        return "XETR"
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


def _session_open_close_for_date(ticker: str, d: date) -> tuple[time, time]:
    tz, (default_open, default_close) = session_hours(ticker)
    name = _calendar_name(ticker)
    if mcal is not None and name:
        try:
            schedule = mcal.get_calendar(name).schedule(start_date=d, end_date=d)
            if not schedule.empty:
                open_dt = schedule.iloc[0]["market_open"].to_pydatetime().astimezone(tz)
                close_dt = schedule.iloc[0]["market_close"].to_pydatetime().astimezone(tz)
                return open_dt.time().replace(tzinfo=None), close_dt.time().replace(tzinfo=None)
        except Exception:
            pass
    return default_open, default_close


def market_status(ticker: str, now: datetime | None = None) -> dict:
    """Return current exchange-session state.

    US pre-market is treated as 04:00-09:30 ET on valid session days only.
    Weekends and exchange holidays remain CLOSED even if the clock time falls
    inside the normal pre-market window.
    """
    tz, _ = session_hours(ticker)
    now = now.astimezone(tz) if now else datetime.now(tz)
    local_time = now.timetz().replace(tzinfo=None)
    is_day = _is_session_day(now.date(), ticker)
    op, cl = _session_open_close_for_date(ticker, now.date()) if is_day else session_hours(ticker)[1]

    pre_start = time(4, 0) if tz == NY else time(7, 0)
    post_end = time(20, 0) if tz == NY else time(19, 0)

    closed_reason = None
    if is_day and op <= local_time < cl:
        status, is_open, is_pre, is_post, next_event = "REGULAR SESSION", True, False, False, "CLOSE"
    elif is_day and pre_start <= local_time < op:
        status, is_open, is_pre, is_post, next_event = "PRE-MARKET", False, True, False, "OPEN"
    elif is_day and cl <= local_time < post_end:
        status, is_open, is_pre, is_post, next_event = "AFTER-HOURS", False, False, True, "NEXT OPEN"
    else:
        status, is_open, is_pre, is_post = "CLOSED", False, False, False
        if not is_day:
            closed_reason = "WEEKEND" if now.date().weekday() >= 5 else "HOLIDAY"
            next_event = "NEXT SESSION"
        elif local_time < pre_start:
            closed_reason = "BEFORE EXTENDED HOURS"
            next_event = "PRE-MARKET" if tz == NY else "OPEN"
        else:
            closed_reason = "AFTER EXTENDED HOURS"
            next_event = "NEXT SESSION"

    if is_day and local_time < op:
        next_date = now.date()
    elif is_day and local_time < cl:
        next_date = now.date()
    else:
        next_date = next_session_date(ticker, now.date(), 1)

    next_open, _ = _session_open_close_for_date(ticker, next_date)
    next_open_dt = datetime.combine(next_date, next_open, tzinfo=tz)
    next_pre_dt = None
    if tz == NY:
        next_pre_dt = datetime.combine(next_date, time(4, 0), tzinfo=tz)

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
        "closed_reason": closed_reason,
        "next_session_date": next_date,
        "next_open": next_open_dt,
        "next_pre": next_pre_dt,
    }
