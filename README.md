# AI Market Decision V7.4 Cloud Radar

V7.4 mantiene tutte le funzioni V7.3.2 (DAY/WEEK/MONTH, conferma 5m, Yahoo volume fix, Scanner/Opportunity Score, backtest, verifica, paper positions e Telegram) e aggiunge un Radar Cloud indipendente dalla sessione Streamlit.

## Novità principale
- `cloud_radar.py`: un ciclo DAY autonomo.
- `.github/workflows/cloud_radar.yml`: GitHub Actions avvia il Radar ogni 15 minuti durante premarket/sessione USA.
- Il PC e il browser possono essere spenti.
- I candidati vengono prima classificati dallo Scanner e, in regular session, ricontrollati con conferma intraday 5m.
- `ENTRY CONFIRMED` include Entry, Stop, Target, R/R e size paper.
- WATCH e ENTRY sono deduplicati.
- SQLite + Actions cache mantiene stato e modelli senza database esterno; PostgreSQL/Supabase resta opzionale e consigliato per storico condiviso con Streamlit.
- Nessun nuovo ingresso DAY dopo il cutoff predefinito 15:30 ET.

## Installazione
Vedi `CLOUD_RADAR_SETUP.md`.

## Nota
Il Radar segnala setup che superano i filtri probabilistici; non identifica operazioni garantite e non invia ordini al broker. Prima dell'uso reale servono paper trading e verifica statistica dei risultati.
