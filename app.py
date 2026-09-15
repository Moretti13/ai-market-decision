import json
import streamlit as st
from config import APP_NAME, ALL_UNIVERSE
from engine import analyze_asset, scan_market

st.set_page_config(page_title=APP_NAME, layout="wide")
st.title("AI Market Decision Prototype")
st.caption("DAY • WEEK • MONTH | Asset Analyzer + AI Market Scanner")

with st.sidebar:
    st.header("Input")
    asset=st.text_input("Asset / ticker", "MSFT").strip().upper()
    scan_size=st.slider("Asset nello scanner", 5, min(30,len(ALL_UNIVERSE)), 12)
    go=st.button("Analizza / ricalcola", type="primary")
    scan=st.button("Scansiona il mercato")

if go or "result" not in st.session_state:
    try:
        with st.spinner(f"Analizzo {asset}..."):
            st.session_state.result=analyze_asset(asset)
    except Exception as e:
        st.error(f"Impossibile analizzare {asset}: {e}")
        st.info("Prova un ticker come NVDA, AAPL, MSFT, SAP.DE, ENI.MI o ASML.AS.")

if "result" in st.session_state and isinstance(st.session_state.result,dict):
    r=st.session_state.result
    cols=st.columns(3)
    for col,h in zip(cols,["DAY","WEEK","MONTH"]):
        x=r["horizons"][h]
        icon="🟢" if x["signal"]=="BUY" else "🔴" if x["signal"]=="SELL" else "🟡"
        with col:
            st.subheader(f"{icon} {h}")
            st.metric("Segnale",x["signal"],f"Score {x['score']}/100")
            st.metric("Prob. rialzo",f"{x['p_up']*100:.1f}%")
            st.metric("Rendimento atteso",f"{x['expected_return']*100:.2f}%")
            st.metric("Confidence",f"{x['confidence']*100:.1f}%")
            st.write("**Driver**")
            for d in x["drivers"]: st.write("• "+d)
    st.subheader("News recenti")
    for n in r.get("news",[]):
        line=f"**{n['title']}** — {n['publisher']}"
        if n.get("url"): line += f"  \n{n['url']}"
        st.write(line)
    st.download_button("Scarica JSON",json.dumps(r,ensure_ascii=False,indent=2),file_name=f"{asset}_analysis.json")

if scan:
    with st.spinner("Scansione mercato..."):
        df=scan_market(ALL_UNIVERSE,scan_size)
    st.subheader("AI Market Scanner")
    t1,t2,t3=st.tabs(["DAY","WEEK","MONTH"])
    for tab, sig, score in [(t1,"day_signal","day_score"),(t2,"week_signal","week_score"),(t3,"month_signal","month_score")]:
        with tab:
            buy=df[df[sig]=="BUY"].sort_values(score,ascending=False).head(10)
            sell=df[df[sig]=="SELL"].sort_values(score,ascending=True).head(10)
            a,b=st.columns(2)
            with a: st.write("🟢 TOP BUY"); st.dataframe(buy,use_container_width=True,hide_index=True)
            with b: st.write("🔴 TOP SELL"); st.dataframe(sell,use_container_width=True,hide_index=True)
