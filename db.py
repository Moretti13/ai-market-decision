from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
from sqlalchemy import Column, Double, Integer, MetaData, String, Table, create_engine, event, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable

DB_FILE = Path(os.getenv("MARKET_DB_PATH", str(Path.home() / ".ai_market_decision" / "market_decision_v7.db")))
DB_FILE.parent.mkdir(parents=True, exist_ok=True)
_ENGINE_CACHE: dict[str, Engine] = {}
_INIT_LOCK = threading.RLock()
_INITIALIZED_URLS: set[str] = set()


def _secret(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.getenv(name)
    if value:
        return value
    try:
        import streamlit as st
        value = st.secrets.get(name)
        if value:
            return str(value)
    except Exception:
        pass
    return default


def database_url() -> str:
    return _secret("DATABASE_URL", f"sqlite:///{DB_FILE}") or f"sqlite:///{DB_FILE}"


def engine() -> Engine:
    url = database_url()
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://") and "+psycopg" not in url:
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    if url not in _ENGINE_CACHE:
        kwargs = {"pool_pre_ping": True, "future": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        eng = create_engine(url, **kwargs)
        if url.startswith("sqlite"):
            @event.listens_for(eng, "connect")
            def _sqlite_pragmas(dbapi_connection, _connection_record):
                cursor = dbapi_connection.cursor()
                try:
                    cursor.execute("PRAGMA busy_timeout=30000")
                    cursor.execute("PRAGMA journal_mode=WAL")
                finally:
                    cursor.close()
        _ENGINE_CACHE[url] = eng
    return _ENGINE_CACHE[url]


def reset_engine_cache() -> None:
    global _ENGINE_CACHE, _INITIALIZED_URLS
    with _INIT_LOCK:
        for eng in _ENGINE_CACHE.values():
            try:
                eng.dispose()
            except Exception:
                pass
        _ENGINE_CACHE = {}
        _INITIALIZED_URLS = set()


metadata = MetaData()

signal_events = Table(
    "signal_events",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("event_key", String(255), nullable=False, unique=True),
    Column("created_at", String(64), nullable=False),
    Column("ticker", String(32), nullable=False),
    Column("horizon", String(16), nullable=False),
    Column("signal", String(32), nullable=False),
    Column("entry", Double),
    Column("stop", Double),
    Column("target", Double),
    Column("notes", String(1024)),
)

prediction_history_v7 = Table(
    "prediction_history_v7",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("event_key", String(255), nullable=False, unique=True),
    Column("created_at", String(64), nullable=False),
    Column("ticker", String(32), nullable=False),
    Column("horizon", String(16), nullable=False),
    Column("signal", String(32), nullable=False),
    Column("p_up", Double),
    Column("p_down", Double),
    Column("expected_return", Double),
    Column("reference_price", Double),
    Column("data_asof", String(64)),
    Column("prediction_date", String(16), nullable=False),
    Column("target_date", String(16), nullable=False),
    Column("target_days", Integer, nullable=False),
    Column("target_type", String(32), nullable=False),
    Column("quality_score", Double),
    Column("model_version", String(32), nullable=False),
    Column("actual_return", Double),
    Column("actual_direction", Integer),
    Column("correct", Integer),
    Column("evaluated_at", String(64)),
)

paper_positions_v7 = Table(
    "paper_positions_v7",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("created_at", String(64), nullable=False),
    Column("updated_at", String(64), nullable=False),
    Column("ticker", String(32), nullable=False),
    Column("horizon", String(16), nullable=False),
    Column("side", String(16), nullable=False),
    Column("quantity", Integer, nullable=False),
    Column("entry", Double, nullable=False),
    Column("stop", Double),
    Column("target", Double),
    Column("status", String(16), nullable=False),
    Column("last_price", Double),
    Column("unrealized_pnl", Double),
    Column("exit_price", Double),
    Column("realized_pnl", Double),
    Column("closed_at", String(64)),
    Column("exit_reason", String(64)),
    Column("notes", String(1024)),
)

paper_trade_events_v7 = Table(
    "paper_trade_events_v7",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("event_key", String(255), nullable=False, unique=True),
    Column("created_at", String(64), nullable=False),
    Column("event_type", String(16), nullable=False),
    Column("position_id", Integer, nullable=False),
    Column("ticker", String(32), nullable=False),
    Column("horizon", String(16), nullable=False),
    Column("side", String(16), nullable=False),
    Column("quantity", Integer, nullable=False),
    Column("price", Double, nullable=False),
    Column("stop", Double),
    Column("target", Double),
    Column("pnl", Double),
    Column("reason", String(64)),
    Column("notes", String(1024)),
)

radar_run_slots_v7 = Table(
    "radar_run_slots_v7",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("slot_key", String(128), nullable=False, unique=True),
    Column("started_at", String(64), nullable=False),
    Column("finished_at", String(64)),
    Column("status", String(16), nullable=False),
    Column("attempt", Integer, nullable=False),
    Column("error", String(1024)),
)



def init_db() -> None:
    """Create schema once per database URL, safely across Streamlit reruns/threads.

    V7.3 can run the main app and Radar fragment close together. SQLAlchemy's
    normal check-before-create can race on SQLite: two callers can both see a
    missing table and then one receives ``table already exists``. Using a
    process lock plus ``CREATE TABLE IF NOT EXISTS`` makes initialization
    idempotent and avoids that startup race.
    """
    eng = engine()
    key = str(eng.url)
    if key in _INITIALIZED_URLS:
        return
    with _INIT_LOCK:
        if key in _INITIALIZED_URLS:
            return
        with eng.begin() as conn:
            for table in metadata.sorted_tables:
                conn.execute(CreateTable(table, if_not_exists=True))
        _INITIALIZED_URLS.add(key)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_event_once(event_key: str, ticker: str, horizon: str, signal: str, entry: float, stop: float, target: float, notes: str = "") -> bool:
    init_db()
    try:
        with engine().begin() as conn:
            conn.execute(insert(signal_events).values(
                event_key=event_key, created_at=_utcnow(), ticker=ticker, horizon=horizon, signal=signal,
                entry=entry, stop=stop, target=target, notes=notes,
            ))
        return True
    except Exception:
        return False


def event_exists(event_key: str) -> bool:
    init_db()
    query = select(signal_events.c.id).where(signal_events.c.event_key == str(event_key)).limit(1)
    with engine().connect() as conn:
        return conn.execute(query).first() is not None


def recent_events(limit: int = 50) -> pd.DataFrame:
    init_db()
    query = select(signal_events).order_by(signal_events.c.id.desc()).limit(int(limit))
    with engine().connect() as conn:
        return pd.read_sql(query, conn)


def log_prediction_once(**values) -> bool:
    init_db()
    payload = dict(values)
    payload.setdefault("created_at", _utcnow())
    try:
        with engine().begin() as conn:
            conn.execute(insert(prediction_history_v7).values(**payload))
        return True
    except Exception:
        return False


def unresolved_predictions(limit: int = 50) -> pd.DataFrame:
    init_db()
    query = (
        select(prediction_history_v7)
        .where(prediction_history_v7.c.evaluated_at.is_(None))
        .order_by(prediction_history_v7.c.id.asc())
        .limit(int(limit))
    )
    with engine().connect() as conn:
        return pd.read_sql(query, conn)


def mark_prediction_evaluated(prediction_id: int, actual_return: float, actual_direction: int, correct: int) -> None:
    init_db()
    with engine().begin() as conn:
        conn.execute(
            update(prediction_history_v7)
            .where(prediction_history_v7.c.id == int(prediction_id))
            .values(
                actual_return=float(actual_return),
                actual_direction=int(actual_direction),
                correct=int(correct),
                evaluated_at=_utcnow(),
            )
        )


def prediction_history(limit: int = 200) -> pd.DataFrame:
    init_db()
    query = select(prediction_history_v7).order_by(prediction_history_v7.c.id.desc()).limit(int(limit))
    with engine().connect() as conn:
        return pd.read_sql(query, conn)


def prediction_metrics() -> dict:
    df = prediction_history(2000)
    if df.empty:
        return {"evaluated": 0, "accuracy": 0.0, "avg_actual_return": 0.0}
    ev = df[df["evaluated_at"].notna()].copy()
    if ev.empty:
        return {"evaluated": 0, "accuracy": 0.0, "avg_actual_return": 0.0}
    return {
        "evaluated": int(len(ev)),
        "accuracy": float(pd.to_numeric(ev["correct"], errors="coerce").fillna(0).mean()),
        "avg_actual_return": float(pd.to_numeric(ev["actual_return"], errors="coerce").fillna(0).mean()),
    }


def _insert_position(conn, ticker: str, horizon: str, side: str, quantity: int, entry: float, stop: float | None, target: float | None, notes: str, now: str) -> int:
    res = conn.execute(insert(paper_positions_v7).values(
        created_at=now, updated_at=now, ticker=ticker.upper(), horizon=horizon.upper(), side=side.upper(),
        quantity=int(quantity), entry=float(entry), stop=stop, target=target, status="OPEN",
        last_price=float(entry), unrealized_pnl=0.0, notes=notes,
    ))
    try:
        return int(res.inserted_primary_key[0])
    except Exception:
        return 0


def _insert_trade_event(
    conn,
    *,
    event_key: str,
    event_type: str,
    position_id: int,
    ticker: str,
    horizon: str,
    side: str,
    quantity: int,
    price: float,
    stop: float | None = None,
    target: float | None = None,
    pnl: float | None = None,
    reason: str = "",
    notes: str = "",
    created_at: str | None = None,
) -> None:
    conn.execute(insert(paper_trade_events_v7).values(
        event_key=str(event_key),
        created_at=created_at or _utcnow(),
        event_type=str(event_type).upper(),
        position_id=int(position_id),
        ticker=str(ticker).upper(),
        horizon=str(horizon).upper(),
        side=str(side).upper(),
        quantity=int(quantity),
        price=float(price),
        stop=None if stop is None else float(stop),
        target=None if target is None else float(target),
        pnl=None if pnl is None else float(pnl),
        reason=str(reason or ""),
        notes=str(notes or ""),
    ))


def open_position(ticker: str, horizon: str, side: str, quantity: int, entry: float, stop: float | None, target: float | None, notes: str = "") -> int:
    init_db()
    now = _utcnow()
    with engine().begin() as conn:
        pid = _insert_position(conn, ticker, horizon, side, quantity, entry, stop, target, notes, now)
        if pid:
            _insert_trade_event(
                conn,
                event_key=f"POSITION|{pid}|ENTRY",
                event_type="ENTRY",
                position_id=pid,
                ticker=ticker,
                horizon=horizon,
                side=side,
                quantity=quantity,
                price=entry,
                stop=stop,
                target=target,
                reason="MANUAL",
                notes=notes,
                created_at=now,
            )
        return pid


def open_position_once(event_key: str, ticker: str, horizon: str, side: str, quantity: int, entry: float, stop: float | None, target: float | None, notes: str = "") -> tuple[int, bool]:
    """Open an automatic paper position exactly once for a persistent signal key."""
    init_db()
    key = str(event_key)
    with engine().connect() as conn:
        row = conn.execute(
            select(paper_trade_events_v7.c.position_id)
            .where(paper_trade_events_v7.c.event_key == key)
            .limit(1)
        ).first()
        if row is not None:
            return int(row[0]), False

    now = _utcnow()
    try:
        with engine().begin() as conn:
            pid = _insert_position(conn, ticker, horizon, side, quantity, entry, stop, target, notes, now)
            if not pid:
                raise RuntimeError("Unable to obtain paper position id")
            _insert_trade_event(
                conn,
                event_key=key,
                event_type="ENTRY",
                position_id=pid,
                ticker=ticker,
                horizon=horizon,
                side=side,
                quantity=quantity,
                price=entry,
                stop=stop,
                target=target,
                reason="ENTRY_CONFIRMED",
                notes=notes,
                created_at=now,
            )
        return pid, True
    except IntegrityError:
        with engine().connect() as conn:
            row = conn.execute(
                select(paper_trade_events_v7.c.position_id)
                .where(paper_trade_events_v7.c.event_key == key)
                .limit(1)
            ).first()
        return (int(row[0]), False) if row is not None else (0, False)


def open_positions() -> pd.DataFrame:
    init_db()
    query = select(paper_positions_v7).where(paper_positions_v7.c.status == "OPEN").order_by(paper_positions_v7.c.id.desc())
    with engine().connect() as conn:
        return pd.read_sql(query, conn)


def all_positions(limit: int = 200) -> pd.DataFrame:
    init_db()
    query = select(paper_positions_v7).order_by(paper_positions_v7.c.id.desc()).limit(int(limit))
    with engine().connect() as conn:
        return pd.read_sql(query, conn)


def update_position_mark(position_id: int, last_price: float, unrealized_pnl: float) -> None:
    init_db()
    with engine().begin() as conn:
        conn.execute(
            update(paper_positions_v7)
            .where(paper_positions_v7.c.id == int(position_id))
            .values(last_price=float(last_price), unrealized_pnl=float(unrealized_pnl), updated_at=_utcnow())
        )


def close_position_once(position_id: int, exit_price: float, realized_pnl: float, reason: str = "MANUAL") -> bool:
    """Close a position once and persist an immutable EXIT event in the same transaction."""
    init_db()
    pid = int(position_id)
    now = _utcnow()
    with engine().begin() as conn:
        row = conn.execute(
            select(paper_positions_v7).where(paper_positions_v7.c.id == pid).limit(1)
        ).mappings().first()
        if not row or str(row.get("status", "")).upper() != "OPEN":
            return False
        res = conn.execute(
            update(paper_positions_v7)
            .where(paper_positions_v7.c.id == pid)
            .where(paper_positions_v7.c.status == "OPEN")
            .values(
                status="CLOSED", exit_price=float(exit_price), realized_pnl=float(realized_pnl),
                unrealized_pnl=0.0, last_price=float(exit_price), closed_at=now, updated_at=now,
                exit_reason=str(reason),
            )
        )
        if not res.rowcount:
            return False
        _insert_trade_event(
            conn,
            event_key=f"POSITION|{pid}|EXIT",
            event_type="EXIT",
            position_id=pid,
            ticker=row["ticker"],
            horizon=row["horizon"],
            side=row["side"],
            quantity=int(row["quantity"]),
            price=float(exit_price),
            stop=row.get("stop"),
            target=row.get("target"),
            pnl=float(realized_pnl),
            reason=str(reason),
            notes=str(row.get("notes") or ""),
            created_at=now,
        )
    return True


def close_position(position_id: int, exit_price: float, realized_pnl: float, reason: str = "MANUAL") -> None:
    close_position_once(position_id, exit_price, realized_pnl, reason)


def trade_events(event_type: str | None = None, limit: int = 200) -> pd.DataFrame:
    init_db()
    query = select(paper_trade_events_v7)
    if event_type:
        query = query.where(paper_trade_events_v7.c.event_type == str(event_type).upper())
    query = query.order_by(paper_trade_events_v7.c.id.desc()).limit(int(limit))
    with engine().connect() as conn:
        return pd.read_sql(query, conn)


def trade_metrics(limit: int = 5000) -> dict:
    df = all_positions(limit)
    if df.empty:
        return {
            "trades": 0, "wins": 0, "win_rate": 0.0,
            "realized_pnl": 0.0, "unrealized_pnl": 0.0, "total_pnl": 0.0,
            "max_drawdown": 0.0,
        }

    open_df = df[df["status"].astype(str).str.upper() == "OPEN"].copy()
    closed = df[df["status"].astype(str).str.upper() == "CLOSED"].copy()
    unrealized = float(pd.to_numeric(open_df.get("unrealized_pnl"), errors="coerce").fillna(0.0).sum()) if not open_df.empty else 0.0
    if closed.empty:
        return {
            "trades": 0, "wins": 0, "win_rate": 0.0,
            "realized_pnl": 0.0, "unrealized_pnl": unrealized, "total_pnl": unrealized,
            "max_drawdown": 0.0,
        }

    closed["realized_pnl"] = pd.to_numeric(closed["realized_pnl"], errors="coerce").fillna(0.0)
    closed["_closed_at"] = pd.to_datetime(closed["closed_at"], errors="coerce", utc=True)
    closed = closed.sort_values(["_closed_at", "id"], ascending=True)
    pnl = closed["realized_pnl"]
    cumulative = pnl.cumsum()
    running_peak = cumulative.cummax().clip(lower=0.0)
    drawdown = running_peak - cumulative
    wins = int((pnl > 0).sum())
    realized = float(pnl.sum())
    return {
        "trades": int(len(closed)),
        "wins": wins,
        "win_rate": float(wins / len(closed)),
        "realized_pnl": realized,
        "unrealized_pnl": unrealized,
        "total_pnl": realized + unrealized,
        "max_drawdown": float(drawdown.max()) if not drawdown.empty else 0.0,
    }


def claim_radar_slot(slot_key: str, stale_minutes: int = 12) -> bool:
    """Claim one logical 15-minute cloud slot, allowing failed/stale retries."""
    init_db()
    key = str(slot_key)
    now = datetime.now(timezone.utc)
    now_s = now.isoformat()
    try:
        with engine().begin() as conn:
            conn.execute(insert(radar_run_slots_v7).values(
                slot_key=key,
                started_at=now_s,
                finished_at=None,
                status="RUNNING",
                attempt=1,
                error=None,
            ))
        return True
    except IntegrityError:
        pass

    with engine().begin() as conn:
        row = conn.execute(
            select(radar_run_slots_v7).where(radar_run_slots_v7.c.slot_key == key).limit(1)
        ).mappings().first()
        if not row:
            return False
        status = str(row.get("status") or "").upper()
        if status == "DONE":
            return False
        stale = status == "FAILED"
        if not stale:
            try:
                started = datetime.fromisoformat(str(row.get("started_at")))
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                stale = (now - started).total_seconds() >= max(1, int(stale_minutes)) * 60
            except Exception:
                stale = True
        if not stale:
            return False
        conn.execute(
            update(radar_run_slots_v7)
            .where(radar_run_slots_v7.c.id == int(row["id"]))
            .values(
                started_at=now_s,
                finished_at=None,
                status="RUNNING",
                attempt=int(row.get("attempt") or 0) + 1,
                error=None,
            )
        )
    return True


def finish_radar_slot(slot_key: str, *, ok: bool, error: str = "") -> None:
    init_db()
    with engine().begin() as conn:
        conn.execute(
            update(radar_run_slots_v7)
            .where(radar_run_slots_v7.c.slot_key == str(slot_key))
            .values(
                finished_at=_utcnow(),
                status="DONE" if ok else "FAILED",
                error=None if ok else str(error or "")[:1024],
            )
        )
