from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable, List, Sequence

import numpy as np
import pandas as pd

from config import DEFAULTS
from events_engine import event_context
from model_engine import train_medium_model
from regime_engine import market_regime
from signal_engine import premarket_analysis


def _clip01(value: float) -> float:
    return float(np.clip(float(value), 0.0, 1.0))


def _opportunity_detail(
    signal: str,
    p_up: float,
    exp: float,
    quality: float,
    min_edge: float,
    event_risk: str = "NORMAL",
) -> dict:
    """Score a setup even when the model has not crossed the hard trade threshold.

    The score is deliberately *not* a predicted return. It is a prioritisation score
    combining directional probability, expected edge and model quality. A WATCH is
    shown only when the setup is close enough to the hard signal threshold.
    """
    raw_signal = str(signal or "WAIT").upper()
    p_up = float(np.clip(float(p_up), 0.0, 1.0))
    exp = float(exp)
    quality = _clip01(quality)
    min_edge = max(float(min_edge), 1e-6)

    buy_prob_strength = _clip01((p_up - 0.50) / max(DEFAULTS["buy_prob"] - 0.50, 1e-6))
    sell_prob_strength = _clip01((0.50 - p_up) / max(0.50 - DEFAULTS["sell_prob"], 1e-6))
    buy_edge_strength = _clip01(max(exp, 0.0) / min_edge)
    sell_edge_strength = _clip01(max(-exp, 0.0) / min_edge)

    buy_score = 100.0 * (0.50 * buy_prob_strength + 0.30 * buy_edge_strength + 0.20 * quality)
    sell_score = 100.0 * (0.50 * sell_prob_strength + 0.30 * sell_edge_strength + 0.20 * quality)

    if "BUY" in raw_signal:
        side = "BUY"
        score = buy_score
    elif "SELL" in raw_signal:
        side = "SELL"
        score = sell_score
    elif buy_score >= sell_score:
        side = "BUY"
        score = buy_score
    else:
        side = "SELL"
        score = sell_score

    risk = str(event_risk or "NORMAL").upper()
    if risk in {"HIGH", "CRITICAL"}:
        score *= 0.72
    elif risk in {"ELEVATED", "MEDIUM"}:
        score *= 0.88
    score = float(np.clip(score, 0.0, 100.0))

    watch_threshold = float(DEFAULTS.get("scanner_watch_score", 60.0))
    if raw_signal in {"PRE-BUY", "BUY", "ENTER BUY"}:
        display_signal = raw_signal
    elif raw_signal in {"PRE-SELL", "SELL", "ENTER SELL"}:
        display_signal = raw_signal
    elif score >= watch_threshold:
        display_signal = f"{side} WATCH"
    else:
        display_signal = "WAIT" if raw_signal in {"WAIT", "N/A"} else "HOLD"

    if side == "BUY":
        prob_ok = p_up >= DEFAULTS["buy_prob"]
        edge_ok = exp >= min_edge
        prob_req = DEFAULTS["buy_prob"] * 100
        prob_status = "OK" if prob_ok else f"< {prob_req:.0f}%"
        edge_status = "OK" if edge_ok else f"< +{min_edge*100:.2f}%"
        prob_text = f"P(up) {p_up*100:.1f}% {prob_status}"
        edge_text = f"edge {exp*100:+.2f}% {edge_status}"
    else:
        p_down = 1.0 - p_up
        prob_ok = p_up <= DEFAULTS["sell_prob"]
        edge_ok = exp <= -min_edge
        prob_req = (1.0 - DEFAULTS["sell_prob"]) * 100
        prob_status = "OK" if prob_ok else f"< {prob_req:.0f}%"
        edge_status = "OK" if edge_ok else f"> -{min_edge*100:.2f}%"
        prob_text = f"P(down) {p_down*100:.1f}% {prob_status}"
        edge_text = f"edge {exp*100:+.2f}% {edge_status}"

    reason_parts = [prob_text, edge_text, f"qualità {quality*100:.0f}%"]
    if risk != "NORMAL":
        reason_parts.append(f"event risk {risk}")
    reason = " · ".join(reason_parts)

    return {
        "score": score,
        "display_signal": display_signal,
        "candidate_side": side,
        "reason": reason,
        "raw_signal": raw_signal,
    }


def _scan_one(ticker: str, shared_regime: dict, horizons: Sequence[str]) -> dict:
    try:
        horizons = {str(h).upper() for h in horizons}
        events = event_context(ticker)
        row = {
            "ticker": ticker,
            "regime": shared_regime.get("regime", "UNKNOWN"),
            "event_risk": events.get("event_risk", "NORMAL"),
            "catalyst": events.get("catalyst", "NONE"),
            "news": round(events.get("news", {}).get("sentiment", 0.0), 3),
        }

        if "DAY" in horizons:
            pre = premarket_analysis(ticker, events=events, regime=shared_regime)
            detail = _opportunity_detail(
                pre.get("signal", "WAIT"),
                pre.get("p_up", 0.5),
                pre.get("expected_return", 0.0),
                pre.get("quality_score", pre.get("confidence", 0.4)),
                DEFAULTS["day_min_edge"],
                events.get("event_risk", "NORMAL"),
            )
            row.update({
                "DAY": detail["display_signal"],
                "DAY_raw": detail["raw_signal"],
                "DAY_side": detail["candidate_side"],
                "DAY_prob": round(pre.get("p_up", 0.5) * 100, 1),
                "DAY_exp": round(pre.get("expected_return", 0.0) * 100, 2),
                "DAY_score": round(detail["score"], 1),
                "DAY_reason": detail["reason"],
                "DAY_quality": round(pre.get("quality_score", pre.get("confidence", 0.0)) * 100, 1),
            })
        else:
            row.update({"DAY": "N/A", "DAY_raw": "N/A", "DAY_side": "", "DAY_prob": 50.0, "DAY_exp": 0.0, "DAY_score": 0.0, "DAY_reason": "Non calcolato", "DAY_quality": 0.0})

        for horizon, edge_key in (("WEEK", "week_min_edge"), ("MONTH", "month_min_edge")):
            if horizon not in horizons:
                row.update({
                    horizon: "N/A", f"{horizon}_raw": "N/A", f"{horizon}_side": "",
                    f"{horizon}_prob": 50.0, f"{horizon}_exp": 0.0, f"{horizon}_score": 0.0,
                    f"{horizon}_reason": "Non calcolato", f"{horizon}_quality": 0.0,
                })
                continue
            try:
                model = train_medium_model(ticker, horizon)
            except Exception as exc:
                model = {"signal": "N/A", "p_up": 0.5, "expected_return": 0.0, "quality_score": 0.0, "error": str(exc)}
            detail = _opportunity_detail(
                model.get("signal", "N/A"),
                model.get("p_up", 0.5),
                model.get("expected_return", 0.0),
                model.get("quality_score", 0.0),
                DEFAULTS[edge_key],
                events.get("event_risk", "NORMAL"),
            )
            row.update({
                horizon: detail["display_signal"],
                f"{horizon}_raw": detail["raw_signal"],
                f"{horizon}_side": detail["candidate_side"],
                f"{horizon}_prob": round(model.get("p_up", 0.5) * 100, 1),
                f"{horizon}_exp": round(model.get("expected_return", 0.0) * 100, 2),
                f"{horizon}_score": round(detail["score"], 1),
                f"{horizon}_reason": detail["reason"],
                f"{horizon}_quality": round(model.get("quality_score", 0.0) * 100, 1),
            })
        return row
    except Exception as exc:
        return {"ticker": ticker, "ERROR": str(exc)}


def scanner(
    universe: Iterable[str],
    max_assets: int = 10,
    workers: int | None = None,
    horizons: Sequence[str] = ("DAY", "WEEK", "MONTH"),
) -> pd.DataFrame:
    tickers = list(dict.fromkeys([str(t).upper() for t in universe if str(t).strip()]))[: int(max_assets)]
    if not tickers:
        return pd.DataFrame()
    wanted = tuple(dict.fromkeys(str(h).upper() for h in horizons if str(h).upper() in {"DAY", "WEEK", "MONTH"}))
    if not wanted:
        wanted = ("DAY",)

    rows: List[dict] = []
    regime = market_regime()
    max_workers = min(int(workers or DEFAULTS["scanner_workers"]), max(1, len(tickers)))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_scan_one, t, regime, wanted): t for t in tickers}
        for fut in as_completed(futures):
            try:
                row = fut.result()
            except Exception as exc:
                row = {"ticker": futures[fut], "ERROR": str(exc)}
            if "ERROR" not in row:
                rows.append(row)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    score_cols = [f"{h}_score" for h in wanted if f"{h}_score" in df.columns]
    df["BEST_score"] = df[score_cols].max(axis=1) if score_cols else 0.0
    primary = f"{wanted[0]}_score" if wanted else "BEST_score"
    return df.sort_values(["BEST_score", primary], ascending=[False, False]).reset_index(drop=True)
