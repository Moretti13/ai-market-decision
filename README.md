# AI Market Decision V3

Questa versione aggiunge al prototipo:

- DAY / WEEK / MONTH separati
- piano operativo per ogni orizzonte
- indicazione esplicita di **quando entrare** nel DAY
- entry trigger, stop, target e risk/reward
- ricalcolo automatico del DAY in Streamlit
- scanner TOP BUY / TOP SELL per 3 orizzonti
- accesso ai dati con `Ticker.history()` per maggiore robustezza rispetto al prototipo precedente
- news per asset
- nessun invio ordini al broker

## Avvio locale

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Deploy

Il progetto è pronto per essere caricato nello stesso repository GitHub usato per il deploy Streamlit.

## Nota

I segnali attuali sono ancora un motore quantitativo euristico. Per una versione da usare seriamente va aggiunto un dataset storico point-in-time, training ML, walk-forward validation, calibrazione delle probabilità, regime detection e backtest con costi/slippage.
