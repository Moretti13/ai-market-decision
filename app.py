import json
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from config import APP_NAME, ALL_UNIVERSE, DEFAULTS
from engine import analyze_asset, scanner, log_signal, recent_paper_trades

st.set_page_config(page_title=APP_NAME, page_icon="📈", layout="wide")

st.title("📈 AI Market Decision V5")
st.caption("PRE-MARKET → CONFERMA OPEN → ENTRY → RICALCOLO → EXIT | DAY • WEEK • MONTH")

with st.sidebar:
    st.header("Impostazioni")
    ticker = st.text_input("Asset / ticker", "NVDA").strip().upper()
    capital = st.number_input("Capitale di riferimento", min_value=100.0, value=DEFAULTS["capital"], step=500.0)
    risk_pct = st.number_input("Rischio massimo per operazione (%)", min_value=0.1, max_value=5.0, value=DEFAULTS["risk_pct"] * 100, step=0.1) / 100
    auto = st.checkbox("Ricalcolo automatico ogni 5 minuti", True)
    if auto:
        st_autorefresh(interval=DEFAULTS["refresh_seconds"] * 1000, key="v5_refresh")
    refresh = st.button("🔄 Ricalcola adesso", type="primary")
    st.divider()
    st.caption("Prototipo quantitativo: non è consulenza finanziaria e non esegue ordini.")

if "asset_result" not in st.session_state or refresh or st.session_state.get("ticker") != ticker:
    try:
        with st.spinner(f"Analisi multi-orizzonte di {ticker}..."):
            st.session_state.asset_result = analyze_asset(ticker, capital, risk_pct)
            st.session_state.ticker = ticker
    except Exception as exc:
        st.error(f"Errore nei dati/modelli: {exc}")
        st.stop()

r = st.session_state.asset_result
pre, conf, plan, clock = r["pre"], r["confirm"], r["plan"], r["clock"]

st.subheader(f"{ticker} — {clock['status']}")
st.write(f"Timezone: **{clock['timezone']}** · Orario mercato: **{clock['now'].strftime('%Y-%m-%d %H:%M:%S')}**")

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("DAY", pre["signal"], f"Score {pre['score']}/100")
c2.metric("Prob. rialzo", f"{pre['p_up']*100:.1f}%")
c3.metric("Rendimento atteso", f"{pre['expected_return']*100:.2f}%")
c4.metric("Confidence", f"{pre['confidence']*100:.1f}%")
c5.metric("Gap indicativo", f"{pre['gap']*100:.2f}%")

st.markdown("### 🎯 DAY — quando entrare")
if clock["status"] == "PRE-MARKET":
    st.info(f"**PRE-MARKET:** {pre['signal']}. Preparazione per l'apertura alle **{clock['open'].strftime('%H:%M')}** ({clock['timezone']}).")
elif clock["is_open"]:
    st.success("🟢 **MERCATO APERTO** — il sistema sta usando la sessione regolare per la conferma.")
else:
    st.warning(f"**{clock['status']}** — nessuna nuova entrata della sessione regolare.")

p1, p2, p3, p4 = st.columns(4)
p1.metric("Operazione", plan["action"])
p2.metric("Entry trigger", f"{plan['entry']:.2f}")
p3.metric("Stop", f"{plan['stop']:.2f}")
p4.metric("Target", f"{plan['target']:.2f}")
st.write(f"**Risk/Reward:** {plan['rr']:.2f} · **Size indicativa:** {plan['shares']} quote · **Rischio teorico:** {plan['risk_amount']:.2f}")

st.markdown("### Apertura / conferma 5m")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Stato", conf.get("status", "WAIT"))
c2.metric("Segnale", conf.get("signal", "WAIT"))
c3.metric("Prezzo", f"{conf.get('current', pre['indicative']):.2f}")
c4.metric("Da apertura", f"{conf.get('ret_from_open', 0)*100:.2f}%")

if conf.get("status") == "CONFIRMED":
    st.success(f"✅ **{conf['signal']}** — confermato dalla sessione regolare.")
elif conf.get("status") == "INVALIDATED":
    st.warning("⚠️ Il segnale pre-market è stato invalidato: **NON ENTRARE** sulla previsione iniziale.")
elif conf.get("status") == "WAIT_FOR_OPEN":
    st.info("⏳ Attendi apertura e prima barra da 5 minuti.")
else:
    st.info("⏳ In attesa della conferma dei primi 5 minuti.")

st.write("**Driver:** " + " · ".join(pre["drivers"]))

if plan["status"] == "READY" and conf.get("status") == "CONFIRMED":
    if st.button("📝 Registra come PAPER TRADE"):
        log_signal(ticker, "DAY", plan["side"], conf.get("signal", ""), plan["entry"], plan["stop"], plan["target"], plan["shares"], notes="V5 confermato")
        st.success("Paper trade registrato nel diario.")

st.markdown("### 📆 WEEK / MONTH — baseline ML")
w, m = st.columns(2)
for box, title, dat in [(w, "WEEK", r["week"]), (m, "MONTH", r["month"])]:
    with box:
        st.markdown(f"#### {title}")
        if dat.get("signal") == "N/A":
            st.error(dat.get("error", "Modello non disponibile"))
        else:
            a, b, c = st.columns(3)
            a.metric("Segnale", dat["signal"])
            b.metric("Prob. rialzo", f"{dat['p_up']*100:.1f}%")
            c.metric("Rendimento atteso", f"{dat['expected_return']*100:.2f}%")
            st.caption(f"Holdout accuracy {dat.get('accuracy',0)*100:.1f}% · Brier {dat.get('brier',0):.3f} · MAE {dat.get('mae',0)*100:.2f}%")

st.markdown("### 📰 News recenti")
for item in r["news"]:
    title = item.get("title", "News")
    pub = item.get("publisher", "")
    url = item.get("url")
    if url:
        st.markdown(f"**{title}** — {pub}  \\n{url}")
    else:
        st.write(f"**{title}** — {pub}")

st.divider()
st.subheader("🔎 AI Market Scanner")
scan_n = st.slider("Asset da scansionare", 5, min(20, len(ALL_UNIVERSE)), 10)
if st.button("🚀 Scansiona mercato"):
    with st.spinner("Scansione in corso..."):
        st.session_state.scan_df = scanner(ALL_UNIVERSE, scan_n)

if "scan_df" in st.session_state and not st.session_state.scan_df.empty:
    df = st.session_state.scan_df
    tabs = st.tabs(["DAY", "WEEK", "MONTH"])
    for tab, sig, prob, exp in [
        (tabs[0], "DAY", "DAY_prob", "DAY_exp"),
        (tabs[1], "WEEK", "WEEK_prob", "WEEK_exp"),
        (tabs[2], "MONTH", "MONTH_prob", "MONTH_exp"),
    ]:
        with tab:
            buy = df[df[sig].isin(["BUY", "PRE-BUY"])].sort_values(prob, ascending=False).head(10)
            sell = df[df[sig].isin(["SELL", "PRE-SELL"])].sort_values(prob, ascending=True).head(10)
            left, right = st.columns(2)
            with left:
                st.markdown("#### 🟢 TOP BUY")
                st.dataframe(buy, use_container_width=True, hide_index=True)
            with right:
                st.markdown("#### 🔴 TOP SELL")
                st.dataframe(sell, use_container_width=True, hide_index=True)

st.divider()
st.subheader("📒 Paper Trading Journal")
trades = recent_paper_trades(30)
if trades.empty:
    st.info("Nessun paper trade registrato.")
else:
    st.dataframe(trades, use_container_width=True, hide_index=True)

payload = {"ticker": ticker, "clock": str(clock), "result": r}
st.download_button("⬇️ Esporta analisi JSON", data=json.dumps(payload, default=str, ensure_ascii=False, indent=2).encode("utf-8"), file_name=f"{ticker}_analysis_v5.json", mime="application/json")

st.caption("V5: market clock, pre-market, conferma 5m, ricalcolo automatico, ML baseline WEEK/MONTH, scanner, news e paper journal.")
