# AI Market Decision V7.3 Radar — Patch notes

V7.3 mantiene la base V7.2 Stability e aggiunge il radar operativo richiesto, senza trasformare lo scanner in un sistema di ordini automatici.

## Novità principali

- **Opportunity Score anche sui WAIT/HOLD**: lo scanner non mostra più `0` a tutti i setup non ancora entrati.
- **BUY WATCH / SELL WATCH**: segnala i titoli vicini alle soglie dure del modello.
- **Motivo leggibile**: per ogni riga mostra probabilità, edge richiesto, qualità modello ed eventuale event risk.
- **Scanner per singolo orizzonte**: DAY, WEEK e MONTH vengono calcolati solo quando richiesti; questo riduce CPU e chiamate dati.
- **Radar automatico DAY**: opzionale, ogni 15/30/60 minuti mentre l'app Streamlit è sveglia e il ticker principale è in PRE-MARKET o REGULAR SESSION.
- **Telegram automatico**: invia solo i candidati sopra lo score scelto. Le notifiche WATCH possono essere incluse/escluse.
- **Anti-spam**: lo stesso ticker/stato/orizzonte viene notificato una sola volta al giorno; se passa da WATCH a un segnale più forte può arrivare una nuova notifica.
- **Ricalcolo asset**: resta separato dal radar. Intervallo 15 minuti come default UI per contenere il consumo CPU.
- **Telegram test + guida in-app**: token e chat id restano esclusivamente nei Streamlit Secrets.

## Importante sul funzionamento 24/7

Il Radar V7.3 usa i fragment di Streamlit. Quindi funziona automaticamente mentre l'app/sessione è sveglia. Streamlit Community Cloud può mettere l'app in sleep: per un radar realmente 24/7 servirà in seguito un worker cloud separato o un servizio schedulato.

## Alert non significa profitto garantito

`Opportunity Score` è un punteggio di priorità, non una probabilità di profitto. `WATCH` significa che il setup è vicino alle soglie: prima di operare va aperto il ticker e verificata la conferma DAY/5m, oltre a stop, target e rischio.
