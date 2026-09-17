from __future__ import annotations

import json
from datetime import timedelta

import pandas as pd
import streamlit as st

from alerts import send_telegram
from config import ALL_UNIVERSE, APP_NAME, APP_VERSION, DEFAULTS
from db import log_signal, recent_events, recent_paper_trades, record_event_once
from model_engine import walk_forward_backtest
from scanner import scanner
from signal_engine import analyze_asset

@st.cache_data(ttl=240, show_spinner=False)
def cached_analysis(ticker: str, capital: float, risk_pct: float, refresh_nonce: int = 0):
    return analyze_asset(ticker, capital, risk_pct)

st.set_page_config(page_title=APP_NAME, page_icon="📈", layout="wide", initial_sidebar_state="expanded")

st.title("📈 AI Market Decision V6")
st.caption("DAY + WEEK + MONTH · PRE-MARKET → CONFERMA 5m → ENTRY → RICALCOLO → EXIT")

with st.sidebar:
    st.header("Impostazioni")
    ticker = st.text_input("Asset / ticker", "NVDA", help="Esempi: NVDA, AAPL, SPY, ASML.AS, ENEL.MI").strip().upper()
    capital = st.number_input("Capitale di riferimento", min_value=100.0, value=float(DEFAULTS["capital"]), step=500.0)
    risk_pct = st.number_input("Rischio massimo per operazione (%)", min_value=0.1, max_value=5.0, value=DEFAULTS["risk_pct"] * 100, step=0.1) / 100
    auto = st.toggle("Ricalcolo automatico", value=True)
    refresh_minutes = st.selectbox("Intervallo", [1, 5, 10], index=1, disabled=not auto)
    force_refresh = st.button("🔄 Ricalcola ora", type="primary")
    if force_refresh:
        st.session_state["manual_refresh_nonce"] = int(st.session_state.get("manual_refresh_nonce", 0)) + 1
    st.divider()
    st.caption("Uso previsto: supporto decisionale quantitativo e paper trading. Nessun ordine viene inviato al broker.")

cache_key = f"analysis::{ticker}::{capital:.2f}::{risk_pct:.5f}"
if force_refresh or st.session_state.get("analysis_key") != cache_key:
    try:
        with st.spinner(f"Calcolo di {ticker}: DAY / WEEK / MONTH..."):
            result = cached_analysis(ticker, capital, risk_pct, int(st.session_state.get("manual_refresh_nonce", 0)))
        st.session_state["analysis"] = result
        st.session_state["analysis_key"] = cache_key
    except Exception as exc:
        st.error(f"Errore di analisi: {exc}")
        st.stop()
else:
    result = st.session_state["analysis"]

# Auto-refresh only the main live panel; Streamlit 1.63+ supports independent fragments.
@st.fragment(run_every=timedelta(minutes=refresh_minutes) if auto else None)
def live_panel():
    current = cached_analysis(ticker, capital, risk_pct, int(st.session_state.get("manual_refresh_nonce", 0))) if auto else result
    st.session_state["analysis"] = current
    render_analysis(current)


def render_analysis(r: dict):
    pre, confirm, plan, clock = r["pre"], r["confirm"], r["plan"], r["clock"]
    st.subheader(f"{ticker} — {clock['status']}")
    st.write(f"As of **{clock['now'].strftime('%Y-%m-%d %H:%M:%S %Z')}** · sessione {clock['open'].strftime('%H:%M')}–{clock['close'].strftime('%H:%M')}")

    c = st.columns(6)
    c[0].metric("DAY", pre["signal"], f"Score {pre['score']:.1f}/100")
    c[1].metric("P(up)", f"{pre['p_up']*100:.1f}%")
    c[2].metric("Rend. atteso", f"{pre['expected_return']*100:.2f}%")
    c[3].metric("Confidence", f"{pre['confidence']*100:.1f}%")
    c[4].metric("Gap", f"{pre['gap']*100:.2f}%")
    c[5].metric("Regime", pre["regime"]["regime"])

    st.markdown("### 🎯 DAY — ingresso operativo")
    if clock["status"] == "PRE-MARKET":
        st.info(f"**{pre['signal']}** — non entrare alla cieca all'apertura. Trigger: **{plan['trigger']}**. Apertura alle **{clock['open'].strftime('%H:%M')}**.")
    elif clock["is_open"]:
        if confirm["status"] == "CONFIRMED":
            st.success(f"✅ **{confirm['signal']}** — {confirm['message']}")
        elif confirm["status"] == "CONFIRMING":
            st.info(f"⏳ **CONFIRMING** — {confirm['message']}")
        elif confirm["status"] == "INVALIDATED":
            st.warning("⚠️ **SEGNALE INVALIDATO** — non entrare sulla previsione pre-market.")
        else:
            st.info(f"⏳ **{confirm['status']}** — {confirm['message']}")
    else:
        st.warning("Mercato chiuso: nessun nuovo ingresso DAY fino alla prossima sessione.")

    p = st.columns(5)
    p[0].metric("Azione", plan["action"])
    p[1].metric("Entry", f"{plan['entry']:.2f}")
    p[2].metric("Stop", f"{plan['stop']:.2f}")
    p[3].metric("Target", f"{plan['target']:.2f}")
    p[4].metric("R/R", f"{plan['rr']:.2f}")
    st.write(f"**Size indicativa:** {plan['shares']} quote · **Rischio teorico:** {plan['risk_amount']:.2f} · {plan['validity']}")
    st.caption(f"Driver: {' · '.join(pre['drivers']) if pre['drivers'] else 'nessun driver forte'}")

    st.markdown("### 🕐 Conferma apertura 5m")
    q = st.columns(6)
    q[0].metric("Stato", confirm.get("status", "WAIT"))
    q[1].metric("Segnale", confirm.get("signal", "WAIT"))
    q[2].metric("Prezzo", f"{confirm.get('current', pre['indicative']):.2f}")
    q[3].metric("Da open", f"{(confirm.get('current', pre['indicative']) / max(confirm.get('open', pre['indicative']), 1e-9) - 1)*100:.2f}%")
    q[4].metric("VWAP", f"{confirm.get('vwap', pre['indicative']):.2f}")
    q[5].metric("Barre 5m", str(confirm.get("bars", 0)))

    st.markdown("### 📆 WEEK / MONTH")
    w, m = st.columns(2)
    for box, title, dat in [(w, "WEEK", r["week"]), (m, "MONTH", r["month"])]:
        with box:
            st.markdown(f"#### {title}")
            if dat.get("signal") == "N/A":
                st.error(dat.get("error", "Modello non disponibile"))
            else:
                a, b, c = st.columns(3)
                a.metric("Segnale", dat["signal"])
                b.metric("P(up)", f"{dat['p_up']*100:.1f}%")
                c.metric("Rendimento atteso", f"{dat['expected_return']*100:.2f}%")
                st.caption(f"Accuracy holdout {dat['accuracy']*100:.1f}% · Brier {dat['brier']:.3f} · MAE {dat['mae']*100:.2f}% · dati fino a {dat['data_asof']}")

    st.markdown("### 🌍 Contesto mercato")
    mc = r["regime"]
    a, b, c, d = st.columns(4)
    a.metric("Regime", mc["regime"])
    b.metric("SPY 5d", f"{mc['spy5']*100:.2f}%")
    c.metric("QQQ 5d", f"{mc['qqq5']*100:.2f}%")
    d.metric("VIX", f"{mc['vix']:.2f}")

    st.markdown("### 🧾 Fondamentali correnti")
    f = r.get("fundamentals", {})
    if f and "error" not in f:
        cols = ["shortName", "sector", "industry", "marketCap", "trailingPE", "forwardPE", "profitMargins", "returnOnEquity", "revenueGrowth", "earningsGrowth", "debtToEquity", "freeCashflow"]
        table = {k: f[k] for k in cols if k in f}
        st.dataframe(pd.DataFrame([table]), use_container_width=True, hide_index=True)
    else:
        st.caption("Dati fondamentali correnti non disponibili in questo aggiornamento.")

    st.markdown("### 📰 News recenti")
    if r["news"]:
        for item in r["news"][:8]:
            title, publisher, url = item.get("title", "News"), item.get("publisher", "Source"), item.get("url")
            if url:
                st.markdown(f"**{title}** — {publisher} · [Apri]({url})")
            else:
                st.write(f"**{title}** — {publisher}")
    else:
        st.caption("Nessuna news disponibile.")

    health = r["health"]
    with st.expander("🩺 Data health"):
        st.write({"status": health["status"], "daily_bars": health["daily_bars"], "intraday_bars": health["intraday_bars"], "premarket_bars": health["premarket_bars"], "warnings": health["warnings"]})
        st.caption("Fonte di mercato nel prototipo: Yahoo Finance tramite yfinance. Per uso professionale/live serve una fonte market-data con SLA e feed adeguati.")

    if plan["status"] == "READY" and confirm.get("status") == "CONFIRMED":
        event_key = f"{ticker}|DAY|{clock['now'].date()}|{confirm.get('signal')}"
        if record_event_once(event_key, ticker, "DAY", confirm.get("signal", ""), plan["entry"], plan["stop"], plan["target"], notes="V6 confirmed signal"):
            send_telegram(f"AI Market Decision\n{ticker} DAY\n{confirm.get('signal')}\nEntry {plan['entry']:.2f}\nStop {plan['stop']:.2f}\nTarget {plan['target']:.2f}\nP(up) {pre['p_up']*100:.1f}%")

live_panel()

st.divider()
tabs = st.tabs(["🔎 Scanner", "📊 Backtest", "📒 Journal", "ℹ️ Info"])

with tabs[0]:
    st.subheader("AI Market Scanner")
    scan_n = st.slider("Numero di asset", 5, min(20, len(ALL_UNIVERSE)), DEFAULTS["scanner_assets"])
    if st.button("🚀 Scansiona mercato", type="primary"):
        with st.spinner("Scansione parallela DAY / WEEK / MONTH..."):
            st.session_state["scan_df"] = scanner(ALL_UNIVERSE, scan_n)
    df = st.session_state.get("scan_df")
    if isinstance(df, pd.DataFrame) and not df.empty:
        for title, signal_col, prob_col, exp_col in [
            ("DAY", "DAY", "DAY_prob", "DAY_exp"),
            ("WEEK", "WEEK", "WEEK_prob", "WEEK_exp"),
            ("MONTH", "MONTH", "MONTH_prob", "MONTH_exp"),
        ]:
            st.markdown(f"#### {title}")
            view = df[["ticker", signal_col, prob_col, exp_col, "regime", "news"]].sort_values([prob_col, exp_col], ascending=[False, False])
            st.dataframe(view, use_container_width=True, hide_index=True)
            st.caption("Ordinamento per probabilità del modello e rendimento atteso; non rappresenta una garanzia di performance futura.")
    else:
        st.info("Premi 'Scansiona mercato'.")

with tabs[1]:
    st.subheader("Walk-forward Backtest")
    horizon = st.selectbox("Orizzonte", ["WEEK", "MONTH"])
    if st.button("▶️ Esegui backtest"):
        try:
            with st.spinner("Walk-forward in corso..."):
                bt = walk_forward_backtest(ticker, horizon, max_folds=60)
            st.session_state["backtest"] = bt
        except Exception as exc:
            st.error(f"Backtest non disponibile: {exc}")
    bt = st.session_state.get("backtest")
    if bt:
        a, b, c, d = st.columns(4)
        a.metric("Ritorno cumulato", f"{bt['cumulative_return']*100:.2f}%")
        b.metric("Max drawdown", f"{bt['max_drawdown']*100:.2f}%")
        c.metric("Win rate", f"{bt['win_rate']*100:.1f}%")
        d.metric("Profit factor", f"{bt['profit_factor']:.2f}" if bt['profit_factor'] != float('inf') else "∞")
        st.write(bt)

with tabs[2]:
    st.subheader("Paper Trading Journal")
    if st.button("📝 Registra il piano corrente come PAPER TRADE"):
        p = st.session_state["analysis"]["plan"]
        if p["status"] == "READY":
            log_signal(ticker, "DAY", p["side"], st.session_state["analysis"]["confirm"].get("signal", ""), p["entry"], p["stop"], p["target"], p["shares"], notes="V6 manual paper trade")
            st.success("Paper trade registrato.")
        else:
            st.warning("Nessun piano pronto da registrare.")
    trades = recent_paper_trades(50)
    st.dataframe(trades, use_container_width=True, hide_index=True)
    st.markdown("#### Eventi segnale")
    st.dataframe(recent_events(50), use_container_width=True, hide_index=True)

with tabs[3]:
    st.subheader("Come leggere il sistema")
    st.markdown(
        """
        **DAY** usa un modello open→close sulle giornate storiche, il gap/overnight, il contesto di mercato, le news e, quando il mercato è aperto, una conferma intraday su barre 5m e VWAP.\n\n        **WEEK** e **MONTH** sono modelli separati: non vengono sostituiti dal segnale DAY.\n\n        La probabilità è una stima del modello, non una probabilità matematica certa dell'esito. Il backtest è walk-forward e va trattato come diagnostica, non come promessa di rendimento.\n\n        Per gli short il conto/broker deve consentire la vendita allo scoperto; in caso contrario usa i segnali SELL come uscita/avoid, non come apertura short.
        """
    )
    st.info("Il prototipo non manda ordini al broker. Gli alert Telegram sono opzionali e vengono inviati solo per un evento DAY confermato, una volta per sessione/segnale.")

payload = json.dumps(st.session_state["analysis"], default=str, ensure_ascii=False, indent=2)
st.download_button("⬇️ Esporta analisi JSON", data=payload.encode("utf-8"), file_name=f"{ticker}_analysis_v6.json", mime="application/json")
st.caption(f"AI Market Decision V{APP_VERSION} · aggiornamento automatico {refresh_minutes} min quando attivo")
