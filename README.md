# AI Market Decision V7.1 Performance

V7.1 è l'aggiornamento prestazionale della V7 per ridurre CPU e tempi di attesa su Streamlit Community Cloud senza congelare il mercato.

## Cosa cambia davvero

- **Training separato dall'inference**: DAY/WEEK/MONTH non vengono riaddestrati a ogni refresh.
- **Retraining intelligente**: un modello viene riaddestrato quando cambia l'ultima barra daily completata usata dal modello.
- **Dati live sempre aggiornabili**: gap, prezzo, barre 5m, VWAP, momentum e volume continuano a cambiare il segnale DAY durante la sessione.
- **Cache modelli in memoria + disco locale**: i modelli già addestrati vengono riutilizzati nello stesso ambiente Streamlit. Se il container viene ricreato, il primo caricamento può richiedere un nuovo training.
- **News/eventi cache 5 min**, **regime cache 10 min**, **macro cache 15 min**, **FRED cache 6h**, **fondamentali on-demand 6h**.
- **Niente doppio download intraday** nella stessa analisi.
- **ATR riutilizzato** dal contesto già calcolato, evitando un secondo calcolo dello storico.
- **Scanner più prudente sulle risorse**: default 5 asset, 1 worker; il primo scan scalda la cache, i successivi sono più rapidi.
- **Backtest resta manuale** e non parte durante il normale refresh.
- **WAIT più pulito**: Entry/Stop/Target vengono mostrati come non disponibili invece di ripetere il prezzo corrente.
- **Refresh automatico minimo 5 minuti** per evitare carico inutile.

## Perché il mercato continua a essere considerato

Il modello storico non viene congelato per sempre. Viene riaddestrato quando arriva nuova informazione daily completata. Durante la giornata, invece, la decisione DAY viene ricalcolata con input live: gap, prezzo, VWAP, momentum, volume, stato della sessione, regime ed eventi/news in cache breve.

In pratica: **training raro, inference frequente**.

## Fonte dati

La build usa Yahoo Finance tramite `yfinance`. È adatta al collaudo e al paper trading, ma non equivale a un feed professionale con SLA e streaming garantito.

## Installazione / Streamlit

Consigliato Python **3.12**.

```bash
pip install -r requirements.txt
python healthcheck.py
python -m unittest discover -s tests -v
streamlit run app.py
```

Su Streamlit Cloud:

- repository: il tuo repository GitHub;
- branch: `main`;
- main file: `app.py`;
- Python: `3.12`.

## Test consigliato

1. NVDA: attendi il primo caricamento.
2. Premi `Ricalcola ora`: il secondo passaggio deve essere sensibilmente più rapido.
3. AAPL e SPY: il primo caricamento crea le rispettive cache.
4. Lascia il refresh automatico a 5 minuti.
5. Scanner: inizialmente 3–5 asset.
6. Backtest: avvialo solo manualmente.
7. Paper trading prima di qualsiasi uso con capitale reale.

## Verifiche del pacchetto

- compilazione Python completa;
- import di tutti i moduli core;
- 8/8 test offline superati;
- modello persistente con cache memory/disk;
- nessun retraining dovuto al semplice refresh intraday;
- Python target 3.12.

V7.1 è la build consigliata per il test dell'architettura attuale. Non garantisce rendimenti e non invia ordini al broker.
