import json
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from config import APP_NAME, ALL_UNIVERSE
from engine import analyze, scanner

st.set_page_config(page_title=APP_NAME, layout="wide")

st.title("AI Market Decision V5.1")
st.caption("PRE-MARKET → CONFERMA OPEN → ENTRY → RICALCOLO → EXIT | DAY • WEEK • MONTH")

with st.sidebar:
    st.header("Impostazioni")
    ticker = st.text_input("Asset / ticker", "NVDA").strip().upper()
    capital = st.number_input("Capitale di riferimento", min_value=100.0, value=10000.0, step=500.0)
    risk_pct = st.number_input("Rischio massimo per operazione (%)", min_value=0.1, max_value=10.0, value=1.0, step=0.1)
    auto = st.checkbox("Ricalcolo automatico ogni 5 minuti", True)
    if auto:
        st_autorefresh(interval=300000, key="v51_refresh")
    refresh = st.button("🔄 Ricalcola adesso", type="primary")
    st.divider()
    st.info("Prototipo quantitativo/euristico. Non costituisce consulenza finanziaria.")

if "result" not in st.session_state or refresh or st.session_state.get("ticker") != ticker:
    try:
        with st.spinner(f"Analisi {ticker}..."):
            st.session_state.result = analyze(ticker, capital, risk_pct)
            st.session_state.ticker = ticker
    except Exception as e:
        st.error(f"Errore nell'analisi di {ticker}: {e}")
        st.stop()

r = st.session_state.result
day = r["day"]
conf = r["confirmation"]
plan = r["plan"]

st.header(f"{ticker} — {r['session']}")

c1,c2,c3,c4,c5 = st.columns(5)
c1.metric("DAY", day["signal"], f"Score {day['score']}/100")
c2.metric("Prob. rialzo", f"{day['p_up']*100:.1f}%")
c3.metric("Rendimento atteso", f"{day['expected_return']*100:.2f}%")
c4.metric("Confidence", f"{day['confidence']*100:.1f}%")
c5.metric("Gap indicativo", f"{day['gap']*100:.2f}%")

st.subheader("🎯 DAY — quando entrare")

if r["session"] == "REGULAR SESSION":
    st.success("🟢 MERCATO APERTO — il sistema usa la sessione regolare per la conferma.")
elif r["session"] == "PRE-MARKET":
    st.info("🟡 PRE-MARKET — prepara il piano, ma aspetta la conferma all'apertura.")
elif r["session"] == "AFTER-HOURS":
    st.warning("🟠 AFTER-HOURS — nessuna nuova entrata DAY viene autorizzata.")
else:
    st.info("⚪ MERCATO CHIUSO — nessuna nuova entrata DAY.")

pc1,pc2,pc3,pc4 = st.columns(4)
action = plan["action"]
pc1.metric("Operazione", action)

if plan["side"] == "NONE":
    pc2.metric("Entry trigger", "—")
    pc3.metric("Stop", "—")
    pc4.metric("Target", "—")
    st.warning("NON ENTRARE: il segnale è WAIT oppure i dati non consentono un piano operativo.")
else:
    pc2.metric("Entry trigger", f"{plan['entry']:.2f}")
    pc3.metric("Stop", f"{plan['stop']:.2f}")
    pc4.metric("Target", f"{plan['target']:.2f}")

if plan["side"] != "NONE":
    q1,q2,q3 = st.columns(3)
    q1.metric("Risk / Reward", f"{plan['rr']:.2f}")
    q2.metric("Size indicativa", f"{plan['shares']} azioni")
    q3.metric("Rischio teorico", f"{plan['risk_amount']:.2f}")

st.write(f"**Validità:** {plan['validity']}")
st.write("**Driver:** " + (" · ".join(day["drivers"]) if day["drivers"] else "nessun driver dominante"))

st.subheader("⏱️ Conferma apertura / Intraday")
cc1,cc2,cc3 = st.columns(3)
cc1.metric("Stato", conf.get("status","WAIT"))
cc2.metric("Segnale", conf.get("signal","WAIT"))
if "ret_from_open" in conf:
    cc3.metric("Da apertura", f"{conf['ret_from_open']*100:.2f}%")
else:
    cc3.metric("Da apertura", "—")
st.caption(conf.get("message",""))

if conf.get("status") == "CONFIRMED":
    st.success(f"✅ {conf.get('signal')}")
elif conf.get("status") == "INVALIDATED":
    st.error(f"⛔ {conf.get('signal')}")
elif conf.get("status") == "CONFIRMING":
    st.info("⏳ In attesa della conferma della prima barra regular-session.")
else:
    st.info("Nessuna entrata confermata in questo momento.")

st.subheader("📅 WEEK / MONTH")
w,m = st.columns(2)
with w:
    st.markdown("### WEEK")
    st.metric(r["week"]["signal"], f"Score {r['week']['score']}")
    st.write(f"Prob. rialzo: {r['week']['p_up']*100:.1f}%")
    st.write(f"Rendimento atteso: {r['week']['expected_return']*100:.2f}%")
with m:
    st.markdown("### MONTH")
    st.metric(r["month"]["signal"], f"Score {r['month']['score']}")
    st.write(f"Prob. rialzo: {r['month']['p_up']*100:.1f}%")
    st.write(f"Rendimento atteso: {r['month']['expected_return']*100:.2f}%")

st.download_button(
    "⬇️ Esporta piano JSON",
    data=json.dumps(r, ensure_ascii=False, indent=2).encode("utf-8"),
    file_name=f"{ticker}_trade_plan_v51.json",
    mime="application/json",
)

st.divider()
st.subheader("🔎 AI Market Scanner")
scan_n = st.slider("Asset da scansionare", 10, min(40, len(ALL_UNIVERSE)), 15)
if st.button("🚀 Scansiona mercato"):
    with st.spinner("Scansione del mercato..."):
        df = scanner(ALL_UNIVERSE, scan_n)
    if df.empty:
        st.warning("Nessun risultato.")
    else:
        t1,t2,t3 = st.tabs(["DAY","WEEK","MONTH"])
        with t1:
            st.markdown("### TOP BUY")
            st.dataframe(df[df["day"]=="BUY"].sort_values("day_score", ascending=False), use_container_width=True, hide_index=True)
            st.markdown("### TOP SELL")
            st.dataframe(df[df["day"]=="SELL"].sort_values("day_score"), use_container_width=True, hide_index=True)
        with t2:
            st.markdown("### TOP BUY")
            st.dataframe(df[df["week"]=="BUY"].sort_values("week_score", ascending=False), use_container_width=True, hide_index=True)
            st.markdown("### TOP SELL")
            st.dataframe(df[df["week"]=="SELL"].sort_values("week_score"), use_container_width=True, hide_index=True)
        with t3:
            st.markdown("### TOP BUY")
            st.dataframe(df[df["month"]=="BUY"].sort_values("month_score", ascending=False), use_container_width=True, hide_index=True)
            st.markdown("### TOP SELL")
            st.dataframe(df[df["month"]=="SELL"].sort_values("month_score"), use_container_width=True, hide_index=True)

st.caption(f"Ultimo aggiornamento: {r['timestamp']}")
