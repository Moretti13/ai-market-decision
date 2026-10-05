# AI Market Decision V7.5 Reliability Patch

Questa patch cambia solo infrastruttura e misurazione. Non abbassa le soglie del modello e non trasforma i segnali in ordini reali.

## 1. Scheduler 15 minuti più affidabile

La V7.4 chiedeva a GitHub di avviare decine di cron separati al giorno. GitHub può ritardare o perdere eventi schedulati.

La V7.5 usa tre soli avvii cloud per giornata USA:
- 04:07 ET: blocco premarket
- 09:37 ET: blocco regular session
- 16:05 ET: riconciliazione EOD + riepilogo

Dentro i primi due blocchi, `radar_daemon.py` mantiene autonomamente la cadenza di 15 minuti. Quindi, una volta partito il job, non dipende più da un nuovo cron GitHub ogni 15 minuti.

Questo è molto più robusto per il paper test, ma non è un feed con SLA: l'avvio iniziale di un job GitHub può ancora subire ritardi. Per denaro reale andrà poi usato un worker cloud dedicato.

## 2. Cutoff 15:30 ET corretto

Se la variabile GitHub `RADAR_ENTRY_CUTOFF_ET` era presente ma vuota, la V7.4 saltava silenziosamente il cutoff.

La V7.5 tratta stringa vuota come valore mancante e usa sempre il default `15:30`.

Il blocco regular termina alle 15:22 ET, quindi l'ultima scansione è volutamente prima del cutoff.

## 3. Registro automatico dei trade paper e P/L

Quando arriva `ENTRY CONFIRMED`:
- viene registrata automaticamente una paper position;
- Telegram e ledger sono separati: un temporaneo errore Telegram non fa perdere il campione statistico;
- il sistema verifica sulle barre 5 minuti se viene toccato stop o target;
- se stop e target risultano entrambi toccati nella stessa barra 5m, viene assunto STOP (ipotesi conservativa);
- se nessuno dei due viene toccato, la posizione DAY viene chiusa a fine giornata;
- P/L stimato include commissioni + slippage configurati;
- una stessa conferma non può aprire due paper trade.

A ogni ciclo vengono esportati in `.radar_state/`:
- `trade_ledger.csv`
- `signal_events.csv`
- `performance_summary.json`
- `last_run.json`

Ogni workflow GitHub carica questi file anche come artifact con retention di 30 giorni.

## 4. Riepilogo Telegram EOD

Alle 16:05 ET viene inviato un solo riepilogo giornaliero con:
- trade chiusi;
- P/L paper del giorno;
- P/L cumulativo;
- win rate;
- profit factor;
- max drawdown;
- eventuali posizioni ancora aperte.

## Test prima del merge

La PR V7.5 include:
- compilazione Python;
- test core esistenti;
- test specifico cutoff vuoto -> 15:30;
- test conservativo stop/target stessa barra;
- test del percorso ENTRY CONFIRMED con ledger mockato.

## Importante

V7.5 resta paper trading/supporto decisionale. Il P/L è simulato sui dati disponibili e non comprende tutti i possibili effetti di esecuzione reale, gap, liquidità o fill del broker.
