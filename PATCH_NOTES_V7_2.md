# AI Market Decision V7.2 Stability — Patch notes

Questa patch parte da V7.1 Performance e non cambia l'architettura dei modelli ML.
I modelli persistenti V7.1 restano compatibili e non viene forzato un retraining solo per il cambio versione.

## Correzioni

- Riconoscimento sessione reso più esplicito: WEEKEND / HOLIDAY / BEFORE EXTENDED HOURS / AFTER EXTENDED HOURS.
- Il premarket USA resta 04:00–09:30 ET **solo nei giorni di sessione validi**.
- Aggiunta la prossima apertura (`next_open`) al contesto di mercato.
- `confirm_open()` non scarica più barre 5m quando la sessione regolare è chiusa.
- `data_health()` non scarica più sia intraday sia premarket quando non servono.
- `quote_snapshot()` usa direttamente il daily close nei giorni completamente chiusi, evitando chiamate 5m inutili.
- Auto-refresh OFF di default.
- Anche se l'utente abilita l'auto-refresh, V7.2 lo esegue solo in PRE-MARKET o REGULAR SESSION; fuori da queste finestre viene sospeso.
- Paper Position Tracker non interroga più i prezzi ad ogni rerun dell'intera pagina: aggiornamento manuale tramite pulsante dedicato.
- Messaggio chiusura mercato più chiaro, con motivo e prossima apertura.
- Avviso UI quando il rischio per operazione supera il 2%.
- Versione UI aggiornata a V7.2 Stability.

## Nota importante sul test del 19/09/2026

Il 19 settembre 2026 è sabato. Alle 06:25 ET SPY deve risultare `CLOSED`, non `PRE-MARKET`.
Il premarket 04:00–09:30 ET vale nei normali giorni di sessione; il test automatico V7.2 verifica sia il sabato chiuso sia il venerdì 18/09/2026 alle 06:25 ET come `PRE-MARKET`.

## Test eseguiti

- `py_compile`: OK
- unit test core/database: 10/10 OK
- test sessione SPY 19/09/2026 06:25 ET: CLOSED / WEEKEND
- test sessione SPY 18/09/2026 06:25 ET: PRE-MARKET
- test che `confirm_open` non invochi il fetch intraday a mercato chiuso: OK
