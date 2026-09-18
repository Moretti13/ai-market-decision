from __future__ import annotations

import argparse
import compileall
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CORE_MODULES = [
    "config", "market_clock", "data_layer", "news_engine", "macro_engine", "regime_engine",
    "events_engine", "model_engine", "signal_engine", "scanner", "db", "verification", "portfolio", "alerts",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", metavar="TICKER", help="Esegue anche un test dati live (richiede rete/yfinance).")
    args = parser.parse_args()

    ok = compileall.compile_dir(str(ROOT), quiet=1)
    print(f"[compile] {'OK' if ok else 'FAIL'}")
    for name in CORE_MODULES:
        try:
            importlib.import_module(name)
            print(f"[import] {name}: OK")
        except Exception as exc:
            ok = False
            print(f"[import] {name}: FAIL -> {exc}")

    if args.live and ok:
        try:
            from data_layer import fetch
            ticker = args.live.upper()
            d = fetch(ticker, "1mo", "1d", force=True)
            print(f"[live] {ticker}: {len(d)} daily bars, last={d.index[-1] if len(d) else None}")
            ok = ok and not d.empty
        except Exception as exc:
            ok = False
            print(f"[live] FAIL -> {exc}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
