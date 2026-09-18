# Deploy Checklist V7

## 1. GitHub

Carica nella root del repository:

- `app.py`
- `config.py`
- `data_layer.py`
- `news_engine.py`
- `macro_engine.py`
- `regime_engine.py`
- `events_engine.py`
- `model_engine.py`
- `signal_engine.py`
- `scanner.py`
- `verification.py`
- `portfolio.py`
- `db.py`
- `alerts.py`
- `healthcheck.py`
- `requirements.txt`
- `runtime.txt`
- `.python-version`
- `.streamlit/config.toml`
- `tests/`

Non creare `ai-market-decision/ai_market_decision_v7/app.py`: `app.py` deve restare direttamente nella root.

## 2. Streamlit

Impostazioni app:

- Branch: `main`
- Main file path: `app.py`
- Python: preferibilmente `3.12`

Dopo il commit, fai reboot/redeploy.

## 3. Primo avvio

Nei log cerca:

- installazione dipendenze completata;
- nessun `Traceback`;
- server Streamlit avviato.

Se il deploy usa Python 3.14, i range nel `requirements.txt` permettono a pip/uv di scegliere release compatibili. Se hai la possibilità di selezionare 3.12, usa 3.12 per coerenza con i test del progetto.

## 4. Test funzionale

Esegui in ordine:

1. `NVDA`: verifica DAY/WEEK/MONTH.
2. `AAPL`: verifica un secondo titolo.
3. `SPY`: verifica ETF/benchmark.
4. Scanner su 5 asset.
5. Backtest DAY con 20 finestre.
6. Backtest WEEK con 20 finestre.
7. Tab Verifica previsioni: controlla che lo storico venga salvato.
8. Posizioni: registra una paper position e verifica mark-to-market.

## 5. Database persistente (consigliato dopo il test base)

Aggiungi nei Secrets:

```toml
DATABASE_URL = "postgresql://USER:PASSWORD@HOST:5432/DB"
```

## 6. Telegram (opzionale)

```toml
TELEGRAM_BOT_TOKEN = "..."
TELEGRAM_CHAT_ID = "..."
```

Poi usa il pulsante `Test Telegram` nella tab Sistema.

## 7. Prima del denaro reale

Non saltare questa fase: accumula paper trades e previsioni maturate, verifica accuracy/edge out-of-sample e confronta i risultati con costi/slippage reali. La qualità del dato Yahoo Finance è sufficiente per testare il flusso, non per dichiarare esecuzione istituzionale.
