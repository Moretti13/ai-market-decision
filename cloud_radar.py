from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from radar_engine import run_cloud_radar


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def main() -> int:
    force = _bool("RADAR_FORCE_RUN", False)
    summary = _bool("RADAR_SEND_SUMMARY", False)
    result = run_cloud_radar(force_run=force, send_summary=summary)
    payload = json.dumps(result, indent=2, ensure_ascii=False, default=str)
    print(payload)
    state_dir = Path(os.getenv("RADAR_STATE_DIR", ".radar_state"))
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "last_run.json").write_text(payload, encoding="utf-8")
    # Data errors should be visible in Actions, but one problematic ticker should
    # not disable the whole scheduled radar if the rest of the scan completed.
    if summary and not result.get("summary_sent", False):
        print("Telegram test summary was requested but was not delivered.")
        return 3
    if result.get("errors") and result.get("scanned", 0) == 0:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
