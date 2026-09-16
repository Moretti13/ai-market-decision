# AI Market Decision V5.1

Correzione della V5 con:

- fix `KeyError: 'shares'`
- niente `nan` nelle metriche principali
- piano operativo non mostrato quando il segnale è WAIT
- calcolo size azioni solo quando esiste un'operazione
- Market Clock: PRE-MARKET / REGULAR SESSION / AFTER-HOURS / CLOSED
- conferma dei primi dati della sessione regolare
- ricalcolo automatico ogni 5 minuti
- DAY / WEEK / MONTH
- scanner.

## Aggiornamento del repository

Sostituisci nel repository GitHub:

`app.py`
`engine.py`
`config.py`
`requirements.txt`
`README.md`

Poi fai `Commit changes`.

Streamlit Community Cloud ricostruirà l'app.

## Nota

Questo è ancora un prototipo euristico. Prima di usare capitale reale servono dataset point-in-time, modelli ML addestrati, backtest walk-forward, costi di transazione e paper trading.
