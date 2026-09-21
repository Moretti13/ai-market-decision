from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
from sqlalchemy import Column, Double, Integer, MetaData, String, Table, create_engine, event, insert, select, update
from sqlalchemy.engine import Engine
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


def open_position(ticker: str, horizon: str, side: str, quantity: int, entry: float, stop: float | None, target: float | None, notes: str = "") -> int:
    init_db()
    now = _utcnow()
    with engine().begin() as conn:
        res = conn.execute(insert(paper_positions_v7).values(
            created_at=now, updated_at=now, ticker=ticker.upper(), horizon=horizon.upper(), side=side.upper(),
            quantity=int(quantity), entry=float(entry), stop=stop, target=target, status="OPEN",
            last_price=float(entry), unrealized_pnl=0.0, notes=notes,
        ))
        try:
            return int(res.inserted_primary_key[0])
        except Exception:
            return 0


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


def close_position(position_id: int, exit_price: float, realized_pnl: float, reason: str = "MANUAL") -> None:
    init_db()
    now = _utcnow()
    with engine().begin() as conn:
        conn.execute(
            update(paper_positions_v7)
            .where(paper_positions_v7.c.id == int(position_id))
            .values(
                status="CLOSED", exit_price=float(exit_price), realized_pnl=float(realized_pnl),
                unrealized_pnl=0.0, last_price=float(exit_price), closed_at=now, updated_at=now,
                exit_reason=reason,
            )
        )
