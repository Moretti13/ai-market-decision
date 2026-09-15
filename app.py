import json
from datetime import datetime

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from config import ALL_UNIVERSE
from engine import analyze_asset, scan_market, make_price_chart

st.set_page_config(page_title='AI Market Decision V3', layout='wide')

st.title('AI Market Decision V3')
st.caption('Decision support • DAY / WEEK / MONTH • trade timing • market scanner')

with st.sidebar:
    st.header('Asset')
    asset = st.text_input('Ticker', value='NVDA').strip().upper()
    st.divider()
    st.header('DAY')
    auto = st.checkbox('Ricalcolo automatico', value=True)
    every = st.slider('Intervallo (secondi)', 60, 600, 300, 60)
    if auto:
        st_autorefresh(interval=every * 1000, key='day_refresh')
    st.divider()
    st.caption('Il prototipo non invia ordini al broker. I segnali sono sperimentali e non garantiscono risultati finanziari.')


def render_trade_card(r, title):
    sig = r['signal']
    if sig == 'BUY':
        icon = '🟢'
    elif sig == 'SELL':
        icon = '🔴'
    else:
        icon = '🟡'
    st.subheader(f'{icon} {title} — {sig}')
    c1, c2, c3, c4 = st.columns(4)
    c1.metric('Score', f"{r['score']}/100")
    c2.metric('Prob. rialzo', f"{r['p_up']*100:.1f}%")
    c3.metric('Rendimento atteso', f"{r['expected_return']*100:.2f}%")
    c4.metric('Confidence', f"{r['confidence']*100:.1f}%")

    plan = r['plan']
    st.markdown(f"**Operazione:** `{plan['action']}`")
    st.markdown(f"**Quando entrare:** `{plan['condition']}`")
    p1, p2, p3, p4 = st.columns(4)
    p1.metric('Entry trigger', f"{plan['entry']:.2f}")
    p2.metric('Stop', f"{plan['stop']:.2f}")
    p3.metric('Target', f"{plan['target']:.2f}")
    p4.metric('Risk / Reward', f"{plan['rr']:.2f}")
    st.write('**Driver:** ' + ', '.join(r['drivers']) if r['drivers'] else '**Driver:** dati insufficienti')


if asset:
    st.header(f'Analisi: {asset}')
    try:
        with st.spinner('Aggiornamento dati...'):
            result = analyze_asset(asset)

        tabs = st.tabs(['DAY', 'WEEK', 'MONTH', 'NEWS'])
        with tabs[0]:
            r = result['horizons']['DAY']
            render_trade_card(r, 'DAY')
            st.info(f"Timing suggerito: **{r['entry_timing']}** • ultimo dato intraday: {r['updated']}")
            st.plotly_chart(make_price_chart(asset), use_container_width=True)
        with tabs[1]:
            render_trade_card(result['horizons']['WEEK'], 'WEEK')
        with tabs[2]:
            render_trade_card(result['horizons']['MONTH'], 'MONTH')
        with tabs[3]:
            news = result.get('news', [])
            if not news:
                st.warning('Nessuna news restituita dal provider.')
            for n in news:
                st.write(f"**{n['title']}** — {n['publisher']}")
                if n.get('url'):
                    st.write(n['url'])

        st.download_button('Scarica analisi JSON', json.dumps(result, indent=2, ensure_ascii=False), f'{asset}_analysis.json', 'application/json')
    except Exception as exc:
        st.error(f'Impossibile analizzare {asset}: {exc}')

st.divider()
st.header('AI Market Scanner')
st.write('Classifica gli asset per DAY / WEEK / MONTH usando rendimento atteso e confidence. I ranking sono di supporto e non sono certificazioni di performance future.')

scan_size = st.slider('Asset da scandagliare', 5, min(30, len(ALL_UNIVERSE)), 12)
if st.button('🔎 Scansiona mercato', type='primary') or 'scan_df' not in st.session_state:
    with st.spinner('Scansione in corso...'):
        st.session_state.scan_df = scan_market(max_assets=scan_size)

df = st.session_state.get('scan_df', pd.DataFrame())
if not df.empty:
    for horizon in ['DAY', 'WEEK', 'MONTH']:
        st.subheader(horizon)
        buy = df[df[horizon] == 'BUY'].sort_values(f'{horizon} rank', ascending=False).head(5)
        sell = df[df[horizon] == 'SELL'].sort_values(f'{horizon} rank', ascending=True).head(5)
        c1, c2 = st.columns(2)
        with c1:
            st.markdown('### 🟢 Top BUY')
            cols = ['ticker', horizon + ' score', horizon + ' exp.', horizon + ' conf.']
            st.dataframe(buy[cols] if not buy.empty else pd.DataFrame(), use_container_width=True, hide_index=True)
        with c2:
            st.markdown('### 🔴 Top SELL')
            st.dataframe(sell[cols] if not sell.empty else pd.DataFrame(), use_container_width=True, hide_index=True)
else:
    st.info('Premi “Scansiona mercato” per avviare lo scanner.')
