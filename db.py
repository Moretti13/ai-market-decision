from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
from sqlalchemy import Column, Double, Integer, MetaData, String, Table, create_engine, insert, select, text
from sqlalchemy.engine import Engine

DB_FILE = Path(__file__).with_name("paper_trades.db")


def _secret(name: str, default: Optional[str] = None) -> Optional[str]:
    val = os.getenv(name)
    if val:
        return val
    try:
        import streamlit as st
        val = st.secrets.get(name)
        if val:
            return str(val)
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
    return create_engine(url, pool_pre_ping=True, future=True)


metadata = MetaData()
paper_trades = Table(
    "paper_trades",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("created_at", String(64), nullable=False),
    Column("ticker", String(32), nullable=False),
    Column("horizon", String(16), nullable=False),
    Column("side", String(16), nullable=False),
    Column("signal", String(32), nullable=False),
    Column("entry", Double),
    Column("stop", Double),
    Column("target", Double),
    Column("shares", Integer),
    Column("price_exit", Double),
    Column("pnl", Double),
    Column("status", String(16), nullable=False),
    Column("notes", String(1024)),
)
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


def init_db() -> None:
    eng = engine()
    metadata.create_all(eng)


def log_signal(ticker: str, horizon: str, side: str, signal: str, entry: float, stop: float, target: float, shares: int, notes: str = "") -> None:
    init_db()
    created = datetime.now(timezone.utc).isoformat()
    with engine().begin() as conn:
        conn.execute(insert(paper_trades).values(
            created_at=created, ticker=ticker, horizon=horizon, side=side, signal=signal,
            entry=entry, stop=stop, target=target, shares=int(shares), status="OPEN", notes=notes,
        ))


def record_event_once(event_key: str, ticker: str, horizon: str, signal: str, entry: float, stop: float, target: float, notes: str = "") -> bool:
    init_db()
    created = datetime.now(timezone.utc).isoformat()
    try:
        with engine().begin() as conn:
            conn.execute(insert(signal_events).values(
                event_key=event_key, created_at=created, ticker=ticker, horizon=horizon, signal=signal,
                entry=entry, stop=stop, target=target, notes=notes,
            ))
        return True
    except Exception:
        return False


def recent_paper_trades(limit: int = 30) -> pd.DataFrame:
    init_db()
    query = select(paper_trades).order_by(paper_trades.c.id.desc()).limit(int(limit))
    with engine().connect() as conn:
        return pd.read_sql(query, conn)


def recent_events(limit: int = 50) -> pd.DataFrame:
    init_db()
    query = select(signal_events).order_by(signal_events.c.id.desc()).limit(int(limit))
    with engine().connect() as conn:
        return pd.read_sql(query, conn)
