from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from db import claim_radar_slot, database_url, finish_radar_slot
from radar_engine import run_cloud_radar

NY = ZoneInfo("America/New_York")


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _scheduler_slot_key(now: datetime | None = None) -> str:
    """Map redundant 5-minute wake-ups to one logical 15-minute ET slot."""
    now_et = (now or datetime.now(NY)).astimezone(NY)
    expr = str(os.getenv("GITHUB_EVENT_SCHEDULE", "") or "").strip()
    offset = 0
    if expr:
        try:
            offset = int(expr.split()[0].split(",")[0])
        except Exception:
            offset = 0
    shifted = now_et - timedelta(minutes=max(0, min(14, offset)))
    slot_minute = (shifted.minute // 15) * 15
    slot = shifted.replace(minute=slot_minute, second=0, microsecond=0)
    return f"V74|CLOUD_SLOT|{slot.strftime('%Y-%m-%dT%H:%M%z')}"


def _persistent_db_required() -> None:
    if not _bool("RADAR_REQUIRE_PERSISTENT_DB", False):
        return
    configured = str(os.getenv("DATABASE_URL", "") or "").strip()
    resolved = database_url()
    if not configured or resolved.startswith("sqlite"):
        raise RuntimeError(
            "Cloud Radar richiede DATABASE_URL PostgreSQL persistente; "
            "SQLite/cache GitHub non e' un registro trade affidabile."
        )


def main() -> int:
    force = _bool("RADAR_FORCE_RUN", False)
    summary = _bool("RADAR_SEND_SUMMARY", False)
    scheduled = bool(str(os.getenv("GITHUB_EVENT_SCHEDULE", "") or "").strip())
    slot_key = _scheduler_slot_key() if scheduled else None

    try:
        _persistent_db_required()
    except Exception as exc:
        print(str(exc))
        return 4

    claimed = False
    if slot_key:
        claimed = claim_radar_slot(slot_key, stale_minutes=12)
        if not claimed:
            print(json.dumps({
                "status": "SKIPPED_DUPLICATE_SLOT",
                "slot_key": slot_key,
                "reason": "slot gia completato o un tentativo e ancora attivo",
            }, ensure_ascii=False))
            return 0

    try:
        result = run_cloud_radar(force_run=force, send_summary=summary)
        payload = json.dumps(result, indent=2, ensure_ascii=False, default=str)
        print(payload)
        state_dir = Path(os.getenv("RADAR_STATE_DIR", ".radar_state"))
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "last_run.json").write_text(payload, encoding="utf-8")

        code = 0
        if summary and not result.get("summary_sent", False):
            print("Telegram test summary was requested but was not delivered.")
            code = 3
        elif result.get("failed", 0) or result.get("exit_failed", 0):
            # Mark the slot failed so a later recovery wake-up can retry unsent ENTRY/WATCH/EXIT alerts.
            code = 2
        elif result.get("errors") and result.get("scanned", 0) == 0 and not result.get("positions_updated", 0):
            code = 2

        if slot_key and claimed:
            finish_radar_slot(slot_key, ok=(code == 0), error="" if code == 0 else payload[-1000:])
        return code
    except Exception as exc:
        if slot_key and claimed:
            try:
                finish_radar_slot(slot_key, ok=False, error=f"{type(exc).__name__}: {exc}")
            except Exception:
                pass
        raise


if __name__ == "__main__":
    raise SystemExit(main())
