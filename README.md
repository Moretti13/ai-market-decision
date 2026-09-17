# AI Market Decision V6

Prototype quantitativo Streamlit per **DAY + WEEK + MONTH**.

## Cosa fa
- **DAY**: modello storico open→close + gap overnight, news, regime; dopo l'apertura usa conferma su barre 5m e VWAP.
- **WEEK**: modello ML separato su orizzonte di circa 5 sessioni.
- **MONTH**: modello ML separato su orizzonte di circa 20 sessioni.
- **Scanner**: analisi parallela su un universo di titoli/ETF.
- **Backtest**: walk-forward sullo storico.
- **Paper journal**: SQLite locale o PostgreSQL/Supabase tramite `DATABASE_URL`.
- **Telegram**: alert opzionali tramite `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID`.
- **Auto-refresh**: `st.fragment(run_every=...)`, senza componenti aggiuntivi.

## Fonte dati
Il prototipo usa Yahoo Finance tramite `yfinance`. La documentazione del progetto yfinance specifica che Yahoo Finance non è un feed professionale con SLA e che l'uso è destinato soprattutto a ricerca/uso personale. Per una versione realmente professionale bisogna sostituire il layer dati con un provider market-data con feed live e diritti d'uso adeguati.

## Deploy Streamlit Cloud
1. Metti `app.py`, `config.py`, `data_layer.py`, `market_clock.py`, `model_engine.py`, `signal_engine.py`, `scanner.py`, `db.py`, `alerts.py` e `requirements.txt` **alla root del repository**.
2. In Streamlit Community Cloud scegli Python **3.12** e l'entrypoint `app.py`.
3. Se vuoi persistenza cloud, inserisci `DATABASE_URL` nei Secrets, usando una connessione PostgreSQL/Supabase.
4. Per Telegram aggiungi anche `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID`.

## Secrets
Esempio:

```toml
DATABASE_URL = "postgresql://USER:PASSWORD@HOST:5432/DB"
TELEGRAM_BOT_TOKEN = "..."
TELEGRAM_CHAT_ID = "..."
```

Non committare mai `secrets.toml` nel repository.

## Importante
Questo software è un **supporto decisionale**, non una garanzia di profitto. Le probabilità sono output del modello; non sono certezze. Prima dell'uso con denaro reale è necessario eseguire paper trading e verificare risultati out-of-sample e costi reali.
