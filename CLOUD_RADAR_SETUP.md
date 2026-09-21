# V7.4 Cloud Radar — configurazione una volta sola

Questa versione separa il Radar dalla dashboard Streamlit. Il worker gira su GitHub Actions, quindi il PC e l'iPhone possono essere spenti.

## 1. Carica la patch
Carica tutti i file della patch nella root del repository, inclusa la cartella `.github/workflows`. Il file essenziale è `.github/workflows/cloud_radar.yml`.

## 2. GitHub Secrets
Nel repository: **Settings → Secrets and variables → Actions → Secrets → New repository secret**. Crea:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Usa gli stessi valori già inseriti nei Secrets di Streamlit. Non inserirli nel codice.

`DATABASE_URL` è opzionale. Senza database cloud, il workflow mantiene deduplica e modelli tramite la cache di GitHub Actions. Con PostgreSQL/Supabase, aggiungi anche `DATABASE_URL` come repository secret e usa lo stesso valore su Streamlit per condividere lo storico.

## 3. Variabili opzionali
In **Settings → Secrets and variables → Actions → Variables** puoi creare:
- `RADAR_ASSETS` = `5`
- `RADAR_WORKERS` = `2`
- `RADAR_ALERT_SCORE` = `75`
- `RADAR_NOTIFY_WATCH` = `true`
- `RADAR_MAX_ALERTS` = `3`
- `RADAR_ENTRY_CUTOFF_ET` = `15:30`
- `RADAR_CAPITAL` = `10000`
- `RADAR_RISK_PCT` = `0.01`
- `RADAR_TICKERS` = `NVDA,AMD,AVGO,MSFT,AMZN` (opzionale; se assente usa l'universo del progetto)

Se non crei le variabili, il worker usa i valori di default della V7.4.

## 4. Test manuale
Apri **Actions → AI Market Decision - Cloud Radar → Run workflow**. Lascia attivi `force_run` e `send_summary`, quindi avvia. Dopo il completamento riceverai un riepilogo Telegram di test. Fuori orario il test non invia alert operativi WATCH/ENTRY: invia solo il riepilogo.

## 5. Funzionamento automatico
Il workflow parte ogni 15 minuti (04:07–15:22 America/New_York, lunedì-venerdì). Il worker applica inoltre un cutoff nuovi ingressi DAY alle 15:30 ET. Il worker verifica comunque calendario e sessione; nei festivi non esegue scansioni live. In sessione regolare, un candidato viene ricontrollato con barre 5m prima di inviare un **ENTRY CONFIRMED**.

## Cosa significa l'alert
- `BUY/SELL WATCH`: setup interessante, ma non è un ordine.
- `ENTRY CONFIRMED`: il setup ha superato anche la conferma intraday 5m e viene inviato con Entry/Stop/Target/Size paper. Rimane un segnale probabilistico e non garantisce profitto.

## Nota GitHub Actions
Le esecuzioni pianificate non sono real-time garantite e possono subire ritardi. Il workflow è volutamente sfalsato dal minuto 00 per ridurre la probabilità di congestione.
