# AI Market Decision V7.3 Radar

V7.3 estende V7.2 Stability con uno scanner più utile e un radar automatico opzionale.

## Cosa aggiunge

- Opportunity Score 0–100 anche per setup ancora in WAIT/HOLD.
- Stati `BUY WATCH` e `SELL WATCH` per i setup vicini alle soglie.
- Colonna `reason` con spiegazione del mancato ingresso o dei requisiti già soddisfatti.
- Scanner calcolato solo sull'orizzonte selezionato per ridurre CPU.
- Radar DAY automatico ogni 15/30/60 minuti.
- Alert Telegram automatici sopra una soglia score configurabile.
- Deduplicazione alert: stesso ticker/stato/orizzonte una sola volta al giorno.
- Ricalcolo automatico del ticker principale separato dal radar.

## Configurazione consigliata per il test

- Python 3.12
- Ricalcolo asset: OFF oppure 15 minuti
- Radar: 5 asset, DAY, ogni 15 minuti
- Score alert: 75/100
- Notifica WATCH: ON durante il paper test
- Backtest: solo manuale
- Paper trading prima di capitale reale

## Telegram

Configurare in Streamlit Secrets:

```toml
TELEGRAM_BOT_TOKEN = "..."
TELEGRAM_CHAT_ID = "..."
```

Vedi `TELEGRAM_SETUP.md`.

## Limitazione importante

Il radar automatico gira nei fragment Streamlit mentre l'app è sveglia. Community Cloud può sospendere l'app; un monitor 24/7 richiederà un worker cloud separato in una fase successiva.

## Test del pacchetto

```bash
python healthcheck.py
python -m unittest discover -s tests -v
streamlit run app.py
```

Build verificata offline con compilazione completa e 11 test automatici. I test live di Yahoo Finance/Telegram vanno eseguiti sul deploy Streamlit perché richiedono rete e credenziali.

V7.3 è un sistema di supporto decisionale/paper trading: non invia ordini al broker e non garantisce profitti.
