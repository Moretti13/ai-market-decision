# Deploy Checklist V7.1 Performance

## GitHub

Carica i file V7.1 nella **root** del repository, sostituendo quelli V7 con lo stesso nome. `app.py` deve restare direttamente nella root.

File principali da aggiornare:

- `app.py`
- `config.py`
- `data_layer.py`
- `model_engine.py`
- `signal_engine.py`
- `scanner.py`
- `news_engine.py`
- `events_engine.py`
- `macro_engine.py`
- `regime_engine.py`
- `requirements.txt`
- `README.md`

Gli altri file V7 possono restare invariati.

## Streamlit

- Branch: `main`
- Main file: `app.py`
- Python: **3.12**

Dopo il commit Streamlit dovrebbe ridistribuire automaticamente. Se non lo fa, usa Reboot app.

## Primo test

1. Disattiva temporaneamente il refresh automatico se Streamlit è ancora sotto throttle.
2. Apri NVDA e attendi il primo training.
3. Premi `Ricalcola ora`: non deve riaddestrare i modelli se l'ultima barra daily completata non è cambiata.
4. In `Data health / modello` controlla `DAY cache`, `WEEK cache`, `MONTH cache`: dopo il primo training dovresti vedere `MEMORY` o `DISK` nei ricalcoli successivi.
5. Prova AAPL e SPY.
6. Riattiva refresh automatico a 5 minuti.
7. Scanner: iniziare con 3–5 asset.
8. Backtest: eseguirlo manualmente, non durante lo scanner.

## Come verificare che il cambiamento di mercato venga ancora considerato

Durante la sessione il prezzo/VWAP/momentum/volume devono continuare ad aggiornarsi. Il campo `trained_at` può restare uguale: è corretto. Il segnale DAY può cambiare anche senza nuovo training perché cambia l'inference live.

## Nota cache

La cache su disco è locale al container Streamlit. Dopo un redeploy/sleep profondo la piattaforma può ricreare il filesystem e il primo caricamento può riaddestrare i modelli. Non è un errore.
