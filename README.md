# AI Market Decision V5

Versione evoluta del prototipo: l'obiettivo è rendere il workflow DAY realmente operativo e mantenere WEEK/MONTH separati.

## Cosa fa

### DAY
- market clock e distinzione pre-market / regular / after-hours
- analisi pre-market
- PRE-BUY / PRE-SELL / WAIT
- piano operativo con entry, stop, target, R/R e position sizing indicativo
- conferma dopo l'apertura usando le barre regolari a 5 minuti
- ricalcolo automatico ogni 5 minuti
- invalidazione del segnale quando l'apertura non conferma
- protezione completa contro `NaN`/dati mancanti

### WEEK / MONTH
- modelli `HistGradientBoostingClassifier` + `HistGradientBoostingRegressor`
- dataset storico point-in-time a livello daily
- holdout temporale finale 20% per accuracy/Brier/MAE
- segnale BUY / SELL / HOLD

### Market Scanner
- scansione iniziale di azioni/ETF USA + Europa
- TOP BUY e TOP SELL per DAY/WEEK/MONTH

### News
- Yahoo Finance
- fallback Google News RSS

### Paper Trading
- diario SQLite locale
- registrazione dei segnali confermati
- entry/stop/target/size

## Installazione

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

## Deploy su Streamlit Cloud

Sostituisci i file del repository GitHub con quelli di questa cartella e fai Commit. Streamlit Cloud ricostruirà l'app.

## Limiti attuali

- il feed dati resta Yahoo Finance, quindi non è un feed professionale tick-by-tick
- Google News RSS può essere intermittente
- il journal SQLite su Streamlit Cloud non è un database persistente affidabile durante rebuild/restart
- il modello DAY resta quantitativo/euristico; WEEK/MONTH hanno un primo ML baseline ma non sono ancora validati con walk-forward completo
- non c'è esecuzione automatica di ordini

## Roadmap per il sistema production

1. database PostgreSQL/TimescaleDB/Supabase
2. market/news feeds professionali
3. dataset point-in-time completo
4. walk-forward DAY/WEEK/MONTH
5. probability calibration
6. regime model
7. options/volatility/flow data
8. paper trading persistente
9. alert push/email
10. eventuale integrazione broker solo dopo validazione
