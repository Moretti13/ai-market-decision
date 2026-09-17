# Checklist deploy

- [ ] I file Python sono alla root del repository.
- [ ] `requirements.txt` è alla root.
- [ ] Streamlit Cloud usa Python 3.12.
- [ ] Entrypoint: `app.py`.
- [ ] Secrets non sono nel GitHub.
- [ ] `DATABASE_URL` impostato se si desidera persistenza oltre il filesystem locale.
- [ ] Telegram opzionale: `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`.
- [ ] Dopo il primo deploy: provare NVDA, SPY, AAPL.
- [ ] Verificare che DAY, WEEK, MONTH siano tutti visibili.
- [ ] Verificare che il market clock segni correttamente PRE-MARKET / REGULAR / CLOSED.
- [ ] Aprire il tab Backtest e verificare che il walk-forward parta.
- [ ] Aprire Journal e verificare salvataggio paper trade.
- [ ] Per uso reale: sostituire Yahoo Finance con un provider professionale, aggiungere feed eventi/macro point-in-time e monitoraggio affidabilità.
