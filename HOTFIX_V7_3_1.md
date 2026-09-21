# V7.3.1 DB Hotfix

Corregge l'errore SQLite `table signal_events already exists` osservato dopo il passaggio a V7.3 Radar.

## Causa
Il main script Streamlit e il fragment Radar possono inizializzare il database quasi nello stesso momento. Con SQLite, il normale controllo `create_all(checkfirst=True)` può subire una race condition: entrambi vedono la tabella come assente e uno dei due tenta di crearla dopo che l'altro l'ha già creata.

## Correzioni
- inizializzazione schema protetta da lock;
- `CREATE TABLE IF NOT EXISTS` per ogni tabella;
- init eseguita una sola volta per URL database nel processo;
- SQLite `busy_timeout=30s`;
- SQLite WAL per ridurre conflitti tra letture/scritture;
- test concorrente con 24 inizializzazioni su 8 thread.

Non cambia i modelli, i segnali, lo scanner o le soglie di trading.
