from __future__ import annotations

import json
from datetime import timedelta

import pandas as pd
import streamlit as st

from alerts import send_telegram, telegram_configured
from config import ALL_UNIVERSE, APP_BUILD, APP_NAME, APP_VERSION, DEFAULTS
from data_layer import current_fundamentals
from db import (
    all_positions,
    close_position,
    event_exists,
    open_position,
    open_positions,
    prediction_history,
    prediction_metrics,
    recent_events,
    record_event_once,
)
from market_clock import market_status
from model_engine import clear_model_cache, walk_forward_backtest
from portfolio import monitor_open_positions, position_pnl
from scanner import scanner
from signal_engine import analyze_asset
from verification import store_analysis_predictions, verify_matured_predictions

st.set_page_config(page_title=APP_NAME, page_icon="📈", layout="wide", initial_sidebar_state="expanded")


@st.cache_data(ttl=50, show_spinner=False)
def cached_analysis(ticker: str, capital: float, risk_pct: float, nonce: int = 0):
    return analyze_asset(ticker, capital, risk_pct, include_fundamentals=False)


@st.cache_data(ttl=3600, show_spinner=False)
def cached_verification(nonce: int = 0):
    return verify_matured_predictions()


@st.cache_data(ttl=21600, show_spinner=False)
def cached_fundamentals(ticker: str):
    return current_fundamentals(ticker)


@st.cache_data(ttl=900, show_spinner=False)
def cached_scanner(max_assets: int, horizon: str, bucket: int):
    # DAY-only radar scans are intentionally light. WEEK/MONTH are calculated only
    # when that view is explicitly requested from the scanner UI.
    return scanner(ALL_UNIVERSE, max_assets=max_assets, horizons=(horizon,))


def notify_scanner_candidates(df: pd.DataFrame, horizon: str, threshold: float, include_watch: bool = True) -> dict:
    result = {"eligible": 0, "sent": 0, "skipped": 0, "failed": 0}
    if not isinstance(df, pd.DataFrame) or df.empty or not telegram_configured():
        return result
    horizon = str(horizon).upper()
    signal_col = horizon
    score_col = f"{horizon}_score"
    reason_col = f"{horizon}_reason"
    prob_col = f"{horizon}_prob"
    exp_col = f"{horizon}_exp"
    day_key = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d")
    for _, row in df.iterrows():
        state = str(row.get(signal_col, "WAIT")).upper()
        score = float(row.get(score_col, 0.0) or 0.0)
        if state in {"WAIT", "HOLD", "N/A"} or score < float(threshold):
            continue
        if "WATCH" in state and not include_watch:
            continue
        result["eligible"] += 1
        ticker_row = str(row.get("ticker", "")).upper()
        event_key = f"V73|RADAR|{day_key}|{horizon}|{ticker_row}|{state}"
        if event_exists(event_key):
            result["skipped"] += 1
            continue
        message = (
            f"📡 AI Market Decision V7.3 RADAR\n"
            f"{ticker_row} · {horizon} · {state}\n"
            f"Opportunity score: {score:.1f}/100\n"
            f"P(up): {float(row.get(prob_col, 50.0)):.1f}%\n"
            f"Rend. atteso: {float(row.get(exp_col, 0.0)):+.2f}%\n"
            f"Event risk: {row.get('event_risk', 'NORMAL')}\n"
            f"Regime: {row.get('regime', 'UNKNOWN')}\n"
            f"Motivo: {row.get(reason_col, '—')}\n"
            f"Apri il ticker nell'app e verifica la conferma 5m prima di operare."
        )
        if send_telegram(message):
            record_event_once(
                event_key, ticker_row, horizon, state, 0.0, 0.0, 0.0,
                notes=f"Radar score {score:.1f}; {row.get(reason_col, '')}",
            )
            result["sent"] += 1
        else:
            result["failed"] += 1
    return result


def pct(v) -> str:
    try:
        return f"{float(v) * 100:.2f}%"
    except Exception:
        return "—"


def num(v, digits: int = 2) -> str:
    try:
        return f"{float(v):.{digits}f}"
    except Exception:
        return "—"


def render_model_box(title: str, dat: dict):
    st.markdown(f"#### {title}")
    if dat.get("signal") == "N/A":
        st.error(dat.get("error", "Modello non disponibile"))
        return
    a, b, c, d = st.columns(4)
    a.metric("Segnale", dat.get("signal", "HOLD"))
    b.metric("P(up)", pct(dat.get("p_up", 0.5)))
    c.metric("Rendimento atteso", pct(dat.get("expected_return", 0.0)))
    d.metric("Qualità modello", pct(dat.get("quality_score", 0.0)))
    st.caption(
        f"Accuracy {pct(dat.get('accuracy', 0.0))} · AUC {num(dat.get('auc', 0.5), 3)} · "
        f"Brier {num(dat.get('brier', 0.25), 3)} · MAE {pct(dat.get('mae', 0.0))} · dati fino a {dat.get('data_asof', '—')} · "
        f"cache {dat.get('cache_source', '—')}"
    )


def render_analysis(r: dict):
    ticker = r["ticker"]
    pre, confirm, plan, clock = r["pre"], r["confirm"], r["plan"], r["clock"]
    st.subheader(f"{ticker} — {clock['status']}")
    st.caption(
        f"As of {clock['now'].strftime('%Y-%m-%d %H:%M:%S %Z')} · sessione {clock['open'].strftime('%H:%M')}–{clock['close'].strftime('%H:%M')} · "
        f"gap source: {pre.get('gap_source', '—')}"
    )

    c = st.columns(7)
    c[0].metric("DAY", pre.get("signal", "WAIT"), f"Score {num(pre.get('score', 50), 1)}/100")
    c[1].metric("P(up)", pct(pre.get("p_up", 0.5)))
    c[2].metric("Rend. atteso", pct(pre.get("expected_return", 0.0)))
    c[3].metric("Confidence", pct(pre.get("confidence", 0.0)))
    c[4].metric("Gap", pct(pre.get("gap", 0.0)))
    c[5].metric("Event risk", pre.get("event_risk", "NORMAL"))
    c[6].metric("Regime", r.get("regime", {}).get("regime", "UNKNOWN"))

    st.markdown("### 🎯 DAY — ingresso operativo")
    if clock["is_pre"]:
        st.info(f"**{pre['signal']}** · {plan['trigger']} · apertura alle {clock['open'].strftime('%H:%M')}.")
    elif clock["is_open"]:
        status = confirm.get("status", "WAIT")
        if status == "CONFIRMED":
            st.success(f"✅ **{confirm.get('signal')}** — {confirm.get('message')}")
        elif status == "WATCH":
            st.info(f"👀 **{confirm.get('signal')}** — {confirm.get('message')}")
        elif status == "CONFIRMING":
            st.info(f"⏳ **CONFIRMING** — {confirm.get('message')}")
        elif status == "INVALIDATED":
            st.warning(f"⚠️ **INVALIDATED** — {confirm.get('message')}")
        else:
            st.info(f"**{status}** — {confirm.get('message', 'Nessun ingresso.')}")
    else:
        reason = clock.get("closed_reason")
        next_open = clock.get("next_open")
        suffix = f" · prossima apertura {next_open.strftime('%Y-%m-%d %H:%M %Z')}" if next_open else ""
        if clock.get("is_post"):
            st.warning(f"After-hours: nessun nuovo ingresso DAY; il sistema prepara la prossima sessione{suffix}.")
        else:
            reason_txt = f" ({reason.lower()})" if reason else ""
            st.warning(f"Mercato chiuso{reason_txt}: nessun nuovo ingresso intraday adesso{suffix}.")

    p = st.columns(6)
    p[0].metric("Azione", plan.get("action", "NON ENTRARE"))
    p[1].metric("Entry", num(plan.get("entry")))
    p[2].metric("Stop", num(plan.get("stop")))
    p[3].metric("Target", num(plan.get("target")))
    p[4].metric("R/R", num(plan.get("rr")))
    p[5].metric("Size", str(plan.get("shares", 0)))
    st.write(
        f"**Rischio teorico:** {num(plan.get('risk_amount'))} · **Finestra ingresso:** {plan.get('entry_window')} · "
        f"**Validità:** {plan.get('validity')}"
    )
    st.caption(f"Driver: {' · '.join(pre.get('drivers') or ['nessun driver forte'])}")

    st.markdown("### 🕐 Conferma 5 minuti")
    q = st.columns(7)
    q[0].metric("Stato", confirm.get("status", "WAIT"))
    q[1].metric("Segnale", confirm.get("signal", "WAIT"))
    q[2].metric("Prezzo", num(confirm.get("current", pre.get("indicative"))))
    q[3].metric("VWAP", num(confirm.get("vwap", pre.get("indicative"))))
    q[4].metric("15m", pct(confirm.get("ret15m", 0.0)))
    vol_ratio = confirm.get("volume_ratio")
    q[5].metric("Vol ratio", num(vol_ratio) if confirm.get("volume_available", vol_ratio is not None) else "N/D")
    q[6].metric("Barre 5m", str(confirm.get("bars", 0)))
    if not confirm.get("volume_available", vol_ratio is not None) and clock.get("is_open"):
        st.caption("⚠️ Volume 5m Yahoo momentaneamente non affidabile: il filtro volume viene ignorato, non interpretato come 0.")

    st.markdown("### 📆 WEEK / MONTH")
    w, m = st.columns(2)
    with w:
        render_model_box("WEEK", r["week"])
    with m:
        render_model_box("MONTH", r["month"])

    st.markdown("### 🌍 Regime + macro")
    regime, macro = r.get("regime", {}), r.get("macro", {})
    a, b, c, d, e = st.columns(5)
    a.metric("Regime", regime.get("regime", "UNKNOWN"))
    b.metric("Regime score", num(regime.get("score", 0), 0))
    c.metric("VIX", num(regime.get("vix", macro.get("vix", 0))))
    d.metric("10Y", num(macro.get("tnx", 0)))
    e.metric("Macro risk", macro.get("risk_label", "NEUTRAL"))
    st.caption(
        f"SPY 20d {pct(regime.get('spy20', 0))} · QQQ 20d {pct(regime.get('qqq20', 0))} · "
        f"IWM 20d {pct(regime.get('iwm20', 0))} · DXY 5d {pct(macro.get('dxy_change_5d', 0))} · Oil 5d {pct(macro.get('oil_change_5d', 0))}"
    )

    st.markdown("### ⚡ Eventi / news intelligence")
    events = r.get("events", {})
    earnings = events.get("earnings", {})
    a, b, c, d = st.columns(4)
    a.metric("Event risk", events.get("event_risk", "NORMAL"))
    b.metric("Catalyst", events.get("catalyst", "NONE"))
    c.metric("News sentiment", num(events.get("news", {}).get("sentiment", 0), 3))
    d.metric("Prossimi earnings", earnings.get("next_earnings") or "N/D")
    news = r.get("news", [])
    if news:
        for item in news[:8]:
            title = item.get("title", "News")
            publisher = item.get("publisher", "Source")
            cats = ", ".join(item.get("categories", []))
            score = item.get("sentiment", 0.0)
            url = item.get("url")
            line = f"**{title}** — {publisher} · {cats} · sentiment {score:+.2f}"
            st.markdown(line + (f" · [Apri]({url})" if url else ""))
    else:
        st.caption("Nessuna news disponibile in questo aggiornamento.")

    with st.expander("🧾 Fondamentali correnti"):
        fund_key = f"fundamentals_{ticker}"
        if st.button("Carica fondamentali", key=f"load_fund_{ticker}"):
            st.session_state[fund_key] = cached_fundamentals(ticker)
        f = st.session_state.get(fund_key, {})
        if f and "error" not in f:
            cols = [
                "shortName", "sector", "industry", "marketCap", "trailingPE", "forwardPE",
                "profitMargins", "returnOnEquity", "revenueGrowth", "earningsGrowth",
                "debtToEquity", "freeCashflow", "averageVolume", "beta",
            ]
            st.dataframe(pd.DataFrame([{k: f[k] for k in cols if k in f}]), use_container_width=True, hide_index=True)
        elif f.get("error"):
            st.caption(f.get("error"))
        else:
            st.caption("Caricamento on-demand per non rallentare il segnale live. Premi il pulsante solo quando vuoi consultare i fondamentali.")

    with st.expander("🩺 Data health / modello"):
        h = r.get("health", {})
        st.write({
            "status": h.get("status"), "provider": h.get("provider"),
            "daily_bars": h.get("daily_bars"), "intraday_bars": h.get("intraday_bars"),
            "premarket_bars": h.get("premarket_bars"), "daily_last": h.get("daily_last"),
            "intraday_last": h.get("intraday_last"), "volume_status": h.get("volume_status"),
            "recent_volume_valid_pct": h.get("recent_volume_valid_pct"), "warnings": h.get("warnings"),
        })
        st.write({
            "DAY accuracy": pre.get("model_accuracy"), "DAY AUC": pre.get("model_auc"),
            "DAY Brier": pre.get("model_brier"), "DAY quality": pre.get("quality_score"),
            "DAY data_asof": pre.get("model_data_asof"),
            "DAY trained_at": pre.get("model_trained_at"),
            "DAY cache": pre.get("model_cache_source"),
            "WEEK trained_at": r.get("week", {}).get("trained_at"),
            "WEEK cache": r.get("week", {}).get("cache_source"),
            "MONTH trained_at": r.get("month", {}).get("trained_at"),
            "MONTH cache": r.get("month", {}).get("cache_source"),
        })
        st.caption("Fonte attuale: Yahoo Finance/yfinance, adatta al test del prototipo ma non equivalente a un feed professionale con SLA.")

    if plan.get("status") == "READY" and confirm.get("status") == "CONFIRMED":
        event_key = f"V732|{ticker}|DAY|{clock['now'].date()}|{confirm.get('signal')}"
        if record_event_once(
            event_key, ticker, "DAY", confirm.get("signal", ""),
            plan.get("entry", 0.0), plan.get("stop", 0.0), plan.get("target", 0.0),
            notes="V7.3.2 confirmed DAY signal",
        ):
            send_telegram(
                f"AI Market Decision V7.3.2\n{ticker} DAY\n{confirm.get('signal')}\n"
                f"Entry {plan.get('entry', 0):.2f}\nStop {plan.get('stop', 0):.2f}\nTarget {plan.get('target', 0):.2f}\n"
                f"P(up) {pre.get('p_up', .5)*100:.1f}%\nEvent risk {pre.get('event_risk')}"
            )


st.title(f"📈 {APP_NAME}")
st.caption("DAY + WEEK + MONTH · modelli persistenti · live inference · news/eventi · macro/regime · scanner · walk-forward · paper positions")

with st.sidebar:
    st.header("Impostazioni")
    ticker = st.text_input("Asset / ticker", "NVDA", help="Esempi: NVDA, AAPL, SPY, ASML.AS, ENEL.MI").strip().upper()
    capital = st.number_input("Capitale di riferimento", min_value=100.0, value=float(DEFAULTS["capital"]), step=500.0)
    risk_pct = st.number_input(
        "Rischio massimo per operazione (%)", min_value=0.1, max_value=5.0,
        value=float(DEFAULTS["risk_pct"] * 100), step=0.1,
    ) / 100
    if risk_pct > 0.02:
        st.warning("Per il paper test stai usando un rischio >2% per operazione: è un'impostazione aggressiva.")
    auto = st.toggle("Ricalcolo automatico asset", value=False, help="Se attivo, V7.3.2 ricalcola il ticker selezionato solo in premarket/sessione regolare. 15 minuti è l’impostazione più leggera.")
    refresh_minutes = st.selectbox("Intervallo asset", [5, 10, 15], index=2, disabled=not auto)
    if st.button("🔄 Ricalcola ora", type="primary"):
        st.session_state["refresh_nonce"] = int(st.session_state.get("refresh_nonce", 0)) + 1
        st.session_state.pop("analysis_key", None)
    st.divider()
    st.caption(f"{APP_BUILD} · supporto decisionale/paper trading; nessun ordine viene inviato al broker.")

nonce = int(st.session_state.get("refresh_nonce", 0))
cache_key = f"{ticker}|{capital:.2f}|{risk_pct:.5f}|{nonce}"
if st.session_state.get("analysis_key") != cache_key:
    try:
        with st.spinner(f"Analisi {ticker}: live + modelli DAY/WEEK/MONTH..."):
            result = cached_analysis(ticker, capital, risk_pct, nonce)
        st.session_state["analysis"] = result
        st.session_state["analysis_key"] = cache_key
        store_analysis_predictions(result)
    except Exception as exc:
        st.error(f"Analisi non disponibile: {exc}")
        st.stop()
else:
    result = st.session_state["analysis"]

# Automatic outcome verification runs at most once per hour because the function is cached.
try:
    st.session_state["auto_verify_result"] = cached_verification(0)
except Exception as exc:
    st.session_state["auto_verify_result"] = {"checked": 0, "evaluated": 0, "errors": [str(exc)]}


live_auto = bool(auto and (result.get("clock", {}).get("is_pre") or result.get("clock", {}).get("is_open")))
if auto and not live_auto:
    st.sidebar.caption("Auto-refresh in attesa: fuori da premarket/sessione regolare farà solo un controllo leggero dell'orario e ripartirà automaticamente quando il mercato entra nella finestra live.")


@st.fragment(run_every=timedelta(minutes=refresh_minutes) if auto else None)
def live_panel():
    status_now = market_status(ticker) if auto else result.get("clock", {})
    should_refresh = bool(auto and (status_now.get("is_pre") or status_now.get("is_open")))
    current = cached_analysis(ticker, capital, risk_pct, nonce) if should_refresh else result
    st.session_state["analysis"] = current
    try:
        store_analysis_predictions(current)
    except Exception:
        pass
    render_analysis(current)


live_panel()
st.divider()

tabs = st.tabs(["🔎 Scanner", "📊 Backtest", "✅ Verifica previsioni", "💼 Posizioni", "🛠️ Sistema"])

with tabs[0]:
    st.subheader("Market Scanner V7.3 — WATCH + Radar")
    c1, c2, c3 = st.columns(3)
    scan_n = c1.slider("Numero asset", 3, min(15, len(ALL_UNIVERSE)), int(DEFAULTS["scanner_assets"]))
    horizon_view = c2.selectbox("Vista", ["DAY", "WEEK", "MONTH"])
    side_view = c3.selectbox("Segnali", ["TUTTI", "BUY", "SELL", "WATCH"])

    if st.button("🚀 Scansiona mercato", type="primary"):
        st.session_state["scan_nonce_v73"] = int(st.session_state.get("scan_nonce_v73", 0)) + 1
        nonce_scan = int(st.session_state["scan_nonce_v73"])
        with st.spinner(f"Scansione {horizon_view} controllata..."):
            st.session_state["scan_df_v7"] = cached_scanner(scan_n, horizon_view, nonce_scan)

    st.markdown("#### 📡 Radar automatico + Telegram")
    r1, r2, r3, r4 = st.columns(4)
    radar_enabled = r1.toggle("Radar automatico", value=False, help="Scansiona DAY automaticamente mentre l'app Streamlit è sveglia.")
    radar_interval = r2.selectbox("Ogni", [15, 30, 60], index=0, format_func=lambda x: f"{x} min", disabled=not radar_enabled)
    radar_threshold = r3.slider("Score alert", 60, 95, int(DEFAULTS.get("scanner_alert_score", 75)), disabled=not radar_enabled)
    radar_watch = r4.toggle("Notifica WATCH", value=True, disabled=not radar_enabled)

    if radar_enabled and not telegram_configured():
        st.warning("Radar attivo, ma Telegram non è configurato: la scansione funziona, le notifiche no. Configuralo nella scheda Sistema.")

    clock_for_radar = market_status(ticker) if radar_enabled else result.get("clock", {})
    radar_live = bool(clock_for_radar.get("is_pre") or clock_for_radar.get("is_open"))
    if radar_enabled and not radar_live:
        st.caption("Radar in attesa fuori dalla finestra live del ticker principale: ogni intervallo controlla solo l'orario e riparte automaticamente quando entra in premarket/sessione regolare.")

    @st.fragment(run_every=timedelta(minutes=radar_interval) if radar_enabled else None)
    def radar_worker():
        if not radar_enabled:
            return
        status_now = market_status(ticker)
        if not (status_now.get("is_pre") or status_now.get("is_open")):
            st.caption(f"Radar standby · {status_now.get('status', 'CLOSED')} · nessuna scansione dati eseguita.")
            return
        seconds = max(900, int(radar_interval) * 60)
        bucket = int(pd.Timestamp.now(tz="UTC").timestamp() // seconds)
        with st.spinner(f"Radar DAY: scansione di {scan_n} asset..."):
            radar_df = cached_scanner(scan_n, "DAY", bucket)
        st.session_state["scan_df_v7"] = radar_df
        alert_result = notify_scanner_candidates(radar_df, "DAY", radar_threshold, radar_watch)
        st.caption(
            f"Radar aggiornato {pd.Timestamp.now(tz='UTC').strftime('%H:%M UTC')} · "
            f"candidati {alert_result['eligible']} · notifiche nuove {alert_result['sent']} · già notificate {alert_result['skipped']}"
        )

    radar_worker()

    df = st.session_state.get("scan_df_v7")
    if isinstance(df, pd.DataFrame) and not df.empty:
        sig, prob, exp, score = horizon_view, f"{horizon_view}_prob", f"{horizon_view}_exp", f"{horizon_view}_score"
        reason = f"{horizon_view}_reason"
        view = df.copy()
        # A dataframe may come from the DAY-only automatic radar while the user is
        # viewing WEEK/MONTH. In that case ask for a manual scan of that horizon.
        if sig not in view.columns or view[sig].astype(str).eq("N/A").all():
            st.info(f"Il Radar automatico calcola DAY per contenere CPU. Premi 'Scansiona mercato' per calcolare {horizon_view}.")
        else:
            if side_view == "BUY":
                view = view[view[sig].astype(str).str.contains("BUY")]
            elif side_view == "SELL":
                view = view[view[sig].astype(str).str.contains("SELL")]
            elif side_view == "WATCH":
                view = view[view[sig].astype(str).str.contains("WATCH")]
            cols = ["ticker", sig, prob, exp, score, reason, "event_risk", "catalyst", "regime", "news"]
            cols = [c for c in cols if c in view.columns]
            st.dataframe(view[cols].sort_values(score, ascending=False), use_container_width=True, hide_index=True)
            st.caption("Opportunity Score = priorità del setup in base a probabilità, edge atteso, qualità ed event risk. WATCH significa 'vicino alle soglie', non ordine di ingresso né profitto garantito.")
    else:
        st.info("Premi 'Scansiona mercato' oppure abilita il Radar automatico.")

with tabs[1]:
    st.subheader("Walk-forward Backtest")
    c1, c2 = st.columns(2)
    horizon_bt = c1.selectbox("Orizzonte backtest", ["DAY", "WEEK", "MONTH"])
    folds = c2.slider("Numero massimo finestre", 20, 100, 60, 10)
    if st.button("▶️ Esegui backtest"):
        try:
            with st.spinner("Walk-forward in corso..."):
                st.session_state["backtest_v7"] = walk_forward_backtest(ticker, horizon_bt, max_folds=folds)
        except Exception as exc:
            st.error(f"Backtest non disponibile: {exc}")
    bt = st.session_state.get("backtest_v7")
    if bt:
        a, b, c, d, e = st.columns(5)
        a.metric("Ritorno cumulato", pct(bt.get("cumulative_return", 0)))
        b.metric("Max drawdown", pct(bt.get("max_drawdown", 0)))
        c.metric("Win rate", pct(bt.get("win_rate", 0)))
        pf = bt.get("profit_factor", 0)
        d.metric("Profit factor", "∞" if pf == float("inf") else num(pf))
        e.metric("Trade", str(bt.get("trades", 0)))
        st.write(bt)

with tabs[2]:
    st.subheader("Storico + verifica automatica")
    if st.button("🔍 Verifica ora le previsioni maturate"):
        st.session_state["verify_nonce"] = int(st.session_state.get("verify_nonce", 0)) + 1
        st.session_state["verify_result"] = cached_verification(int(st.session_state["verify_nonce"]))
    vr = st.session_state.get("verify_result") or st.session_state.get("auto_verify_result")
    if vr:
        st.info(f"Ultima verifica: controllate {vr.get('checked', 0)} · valutate {vr.get('evaluated', 0)}")
        if vr.get("errors"):
            st.warning(vr["errors"])
    metrics = prediction_metrics()
    a, b, c = st.columns(3)
    a.metric("Previsioni valutate", metrics.get("evaluated", 0))
    b.metric("Accuracy segnali valutati", pct(metrics.get("accuracy", 0)))
    c.metric("Rendimento reale medio target", pct(metrics.get("avg_actual_return", 0)))
    hist = prediction_history(200)
    st.dataframe(hist, use_container_width=True, hide_index=True)
    st.caption("La verifica confronta il segnale salvato con il rendimento realizzato al target temporale. WAIT/HOLD è considerato corretto solo se il movimento resta sotto la soglia dell'orizzonte.")

with tabs[3]:
    st.subheader("Paper Position Tracker")
    st.caption("Per ridurre chiamate dati/CPU, il monitor paper non interroga i prezzi ad ogni rerun della pagina. Aggiornalo quando vuoi controllare stop/target.")
    if st.button("🔄 Aggiorna posizioni paper / stop-target", key="refresh_paper_positions"):
        monitor = monitor_open_positions(auto_close_levels=True)
        st.session_state["paper_monitor"] = monitor
        for ev in monitor.get("events", []):
            key = f"V73|POSITION|{ev['id']}|{ev['reason']}"
            if record_event_once(key, ev["ticker"], "POSITION", ev["reason"], ev["price"], 0, 0, notes=f"PnL {ev['pnl']:.2f}"):
                send_telegram(f"AI Market Decision V7.3\n{ev['ticker']} PAPER POSITION\n{ev['reason']}\nPrice {ev['price']:.2f}\nPnL {ev['pnl']:.2f}")
    monitor = st.session_state.get("paper_monitor", {"updated": 0, "closed": 0, "events": [], "errors": []})
    if monitor.get("updated") or monitor.get("closed"):
        st.info(f"Paper tracker: aggiornate {monitor.get('updated', 0)} · chiuse {monitor.get('closed', 0)}")
    if monitor.get("errors"):
        st.warning(monitor["errors"])

    st.markdown("#### Apri dal piano DAY corrente")
    plan = st.session_state["analysis"].get("plan", {})
    if plan.get("status") == "READY":
        st.write({k: plan.get(k) for k in ["side", "entry", "stop", "target", "shares", "risk_amount"]})
        if st.button("➕ Registra piano come PAPER POSITION"):
            pid = open_position(
                ticker, "DAY", plan["side"], int(plan["shares"]), float(plan["entry"]),
                float(plan["stop"]), float(plan["target"]), notes="V7.3 current DAY plan",
            )
            st.success(f"Paper position registrata (ID {pid}).")
    else:
        st.info("Il piano corrente non è READY: nessuna posizione suggerita da registrare.")

    with st.expander("Aggiungi posizione manuale"):
        mticker = st.text_input("Ticker posizione", ticker, key="mticker").strip().upper()
        mside = st.selectbox("Lato", ["LONG", "SHORT"], key="mside")
        mhorizon = st.selectbox("Orizzonte", ["DAY", "WEEK", "MONTH"], key="mhorizon")
        mq = st.number_input("Quantità", 1, 1_000_000, 1, key="mq")
        me = st.number_input("Entry", min_value=0.0001, value=max(0.0001, float(plan.get("entry", 100.0) or 100.0)), key="me")
        ms = st.number_input("Stop", min_value=0.0, value=max(0.0, float(plan.get("stop", 0.0) or 0.0)), key="ms")
        mt = st.number_input("Target", min_value=0.0, value=max(0.0, float(plan.get("target", 0.0) or 0.0)), key="mt")
        if st.button("Salva posizione manuale"):
            pid = open_position(mticker, mhorizon, mside, int(mq), float(me), float(ms) or None, float(mt) or None, notes="V7.3 manual paper position")
            st.success(f"Posizione {pid} salvata.")

    open_df = open_positions()
    st.markdown("#### Aperte")
    st.dataframe(open_df, use_container_width=True, hide_index=True)
    if not open_df.empty:
        ids = [int(x) for x in open_df["id"].tolist()]
        selected = st.selectbox("ID da chiudere manualmente", ids)
        row = open_df.loc[open_df["id"] == selected].iloc[0]
        exit_price = st.number_input("Prezzo uscita", min_value=0.0001, value=max(0.0001, float(row.get("last_price") or row["entry"])), key="exit_price")
        if st.button("Chiudi posizione selezionata"):
            pnl = position_pnl(row["side"], int(row["quantity"]), float(row["entry"]), float(exit_price))
            close_position(selected, float(exit_price), pnl, "MANUAL")
            st.success(f"Posizione {selected} chiusa. PnL paper: {pnl:.2f}")
    st.markdown("#### Storico")
    st.dataframe(all_positions(200), use_container_width=True, hide_index=True)

with tabs[4]:
    st.subheader("Sistema / deploy / alert")
    st.write({
        "app": APP_NAME,
        "version": APP_VERSION,
        "build": APP_BUILD,
        "telegram_configured": telegram_configured(),
        "data_source": "Yahoo Finance via yfinance",
        "database": "PostgreSQL se DATABASE_URL è configurato, altrimenti SQLite locale",
    })
    st.info("V7.3 aggiunge Opportunity Score/WATCH, Radar DAY automatico e alert Telegram deduplicati. Training e inference restano separati e il Radar DAY calcola un solo orizzonte per contenere CPU.")
    if st.button("🧠 Forza retraining modelli del ticker"):
        removed = clear_model_cache(ticker)
        cached_analysis.clear()
        st.session_state.pop("analysis_key", None)
        st.success(f"Cache modelli di {ticker} azzerata ({removed} file). Al prossimo ricalcolo verranno riaddestrati.")

    if st.button("📨 Test Telegram"):
        if send_telegram("AI Market Decision V7.3 — test alert OK"):
            st.success("Messaggio Telegram inviato.")
        else:
            st.warning("Telegram non configurato o invio fallito. Controlla TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID nei Secrets.")
    with st.expander("📲 Come configurare Telegram"): 
        st.markdown(
            """
            1. In Telegram apri **@BotFather**, crea un bot con `/newbot` e conserva il token.  
            2. Apri una chat con il bot e inviagli almeno un messaggio.  
            3. Recupera il tuo `chat_id` (puoi usare l'API `getUpdates` del bot oppure un bot dedicato che mostra il chat id).  
            4. In Streamlit Cloud vai in **Manage app → Settings → Secrets** e aggiungi:

            ```toml
            TELEGRAM_BOT_TOKEN = "il_tuo_token"
            TELEGRAM_CHAT_ID = "il_tuo_chat_id"
            ```

            Non mettere token o chat id nel repository GitHub. Dopo aver salvato i Secrets, premi **Test Telegram**.
            """
        )
    st.markdown(
        """
        **Prima dell'uso reale:** esegui paper trading, verifica le previsioni maturate, controlla backtest e costi, e confronta i segnali con dati live affidabili. V7.3 non invia ordini e non garantisce profitti.\n\n
        **Short:** ENTER SELL/SHORT richiede un conto che consenta la vendita allo scoperto; altrimenti interpreta SELL come uscita/avoid.\n\n
        **Persistenza:** su Streamlit Cloud usa PostgreSQL/Supabase tramite `DATABASE_URL`; il filesystem locale può essere ricreato nei redeploy.
        """
    )
    st.markdown("#### Eventi registrati")
    st.dataframe(recent_events(100), use_container_width=True, hide_index=True)

payload = json.dumps(st.session_state["analysis"], default=str, ensure_ascii=False, indent=2)
st.download_button(
    "⬇️ Esporta analisi JSON",
    data=payload.encode("utf-8"),
    file_name=f"{ticker}_analysis_v7_2.json",
    mime="application/json",
)
st.caption(f"AI Market Decision V{APP_VERSION} · {APP_BUILD} · refresh {refresh_minutes if auto else 'manuale'} min")
