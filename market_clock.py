from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
ROME = ZoneInfo("Europe/Rome")
BERLIN = ZoneInfo("Europe/Berlin")
PARIS = ZoneInfo("Europe/Paris")
AMSTERDAM = ZoneInfo("Europe/Amsterdam")
MADRID = ZoneInfo("Europe/Madrid")

try:
    import pandas_market_calendars as mcal
except Exception:
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


def _is_session_day(local_date, ticker: str) -> bool:
    if local_date.weekday() >= 5:
        return False
    if mcal is None:
        return True
    try:
        calendar_name = "NYSE" if session_hours(ticker)[0] == NY else "XETR" if ticker.upper().endswith(".DE") else "SIX" if ticker.upper().endswith(".MI") else "NYSE"
        calendar = mcal.get_calendar(calendar_name)
        schedule = calendar.schedule(start_date=local_date, end_date=local_date)
        return not schedule.empty
    except Exception:
        return True


def market_status(ticker: str, now: datetime | None = None) -> dict:
    tz, (op, cl) = session_hours(ticker)
    now = now.astimezone(tz) if now else datetime.now(tz)
    local_time = now.timetz().replace(tzinfo=None)
    is_day = _is_session_day(now.date(), ticker)
    pre_start = time(4, 0) if tz == NY else time(7, 0)
    post_end = time(20, 0) if tz == NY else time(19, 0)

    if is_day and op <= local_time < cl:
        status = "REGULAR SESSION"
        is_open, is_pre, is_post = True, False, False
        next_event = "CLOSE"
    elif is_day and pre_start <= local_time < op:
        status = "PRE-MARKET"
        is_open, is_pre, is_post = False, True, False
        next_event = "OPEN"
    elif is_day and cl <= local_time < post_end:
        status = "AFTER-HOURS"
        is_open, is_pre, is_post = False, False, True
        next_event = "NEXT OPEN"
    else:
        status = "CLOSED"
        is_open = is_pre = is_post = False
        next_event = "NEXT SESSION"

    return {
        "status": status,
        "timezone": tz,
        "now": now,
        "open": op,
        "close": cl,
        "is_open": is_open,
        "is_pre": is_pre,
        "is_post": is_post,
        "next_event": next_event,
    }
