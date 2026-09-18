# Migrazione V6 → V7

1. Fai una copia del repository V6 se vuoi poter tornare indietro.
2. Elimina/sostituisci i vecchi file Python con quelli V7.
3. Aggiungi i nuovi moduli: `news_engine.py`, `macro_engine.py`, `regime_engine.py`, `events_engine.py`, `verification.py`, `portfolio.py`.
4. Sostituisci `requirements.txt`.
5. Mantieni i tuoi Secrets Streamlit: `DATABASE_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` restano compatibili.
6. Il database V7 usa nuove tabelle per prediction history e paper positions, quindi non dipende dalla struttura V6 del vecchio journal.
7. Redeploy/reboot e testa NVDA/AAPL/SPY prima di usare scanner o posizioni.
