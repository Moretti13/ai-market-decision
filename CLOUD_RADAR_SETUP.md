# V7.4.1 Cloud Radar — configurazione operativa

Il Radar gira indipendentemente dalla dashboard Streamlit. Il worker GitHub Actions gestisce scansione, paper entry/exit, registro persistente e Telegram.

## 1. GitHub Secrets obbligatori
Nel repository: **Settings → Secrets and variables → Actions → Secrets**.

Crea:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `DATABASE_URL`

`DATABASE_URL` deve puntare a PostgreSQL persistente (per esempio Supabase/Neon/Postgres gestito). Nel worker cloud non viene più accettato SQLite/cache GitHub come registro operativo: scheduler slot, deduplica, paper trade e storico devono sopravvivere ai runner effimeri.

Per vedere lo stesso storico nella dashboard Streamlit, configura lo stesso `DATABASE_URL` anche nei Secrets di Streamlit.

## 2. Variabili opzionali
In **Settings → Secrets and variables → Actions → Variables** puoi creare:
- `RADAR_ASSETS` = `5`
- `RADAR_WORKERS` = `2`
- `RADAR_ALERT_SCORE` = `75`
- `RADAR_NOTIFY_WATCH` = `true`
- `RADAR_MAX_ALERTS` = `3`
- `RADAR_ENTRY_CUTOFF_ET` = `15:30`
- `RADAR_CAPITAL` = `10000`
- `RADAR_RISK_PCT` = `0.01`
- `RADAR_TICKERS` = `NVDA,AMD,AVGO,MSFT,AMZN`

Se non sono presenti, il worker usa i default del progetto.

## 3. Scheduler 15 minuti con recupero
Per ogni slot logico da 15 minuti ci sono due trigger GitHub Actions:
- primario: minuto 07/22/37/52;
- backup: minuto 12/27/42/57.

Entrambi vengono mappati allo stesso slot persistente nel database. Se il primario ha completato, il backup termina senza rilanciare lo scanner. Se il primario fallisce o resta bloccato, il backup può riprendere quello slot. I trigger continuano fino a dopo la chiusura USA per consentire l'uscita `SESSION_END`.

Questo riduce sensibilmente i buchi dovuti a ritardi/fallimenti e impedisce doppie aperture grazie alle chiavi persistenti. GitHub Actions resta comunque un servizio schedulato best-effort: non è corretto considerarlo un clock con SLA hard real-time.

## 4. Cutoff DAY 15:30 ET
Alle **15:30 America/New_York** vengono bloccati soltanto i **nuovi ingressi DAY**. Il worker non interrompe la gestione delle posizioni già aperte: continua a controllare stop, target e fine sessione.

Anche una esecuzione manuale `force_run` non può creare un nuovo paper trade dopo il cutoff; può soltanto eseguire diagnostica/riepilogo.

## 5. Paper trading automatico
Quando un setup DAY passa a `ENTRY CONFIRMED`:
1. viene creata automaticamente una paper position;
2. la chiave del segnale rende l'apertura idempotente;
3. ENTRY/Stop/Target/Size vengono salvati nel database;
4. Telegram invia l'ENTRY senza alterare l'anti-spam esistente.

Le posizioni DAY vengono chiuse automaticamente su:
- `STOP`;
- `TARGET`;
- `SESSION_END`.

Se un ciclo di chiusura viene perso, il controllo successivo non mantiene intenzionalmente una posizione DAY overnight.

## 6. Registro e metriche
Il database mantiene:
- posizioni paper aperte/chiuse;
- eventi immutabili ENTRY/EXIT;
- P/L realizzato e non realizzato;
- win rate;
- max drawdown sul P/L cumulato;
- storico completo nella tab Paper Position Tracker.

Gli EXIT Telegram derivano dal ledger persistente: se un invio fallisce, il worker può ritentarlo senza riaprire o richiudere il trade.

## 7. Test manuale
Apri **Actions → AI Market Decision - Cloud Radar → Run workflow** e lascia attivi `force_run` e `send_summary`.

Fuori orario il test può calcolare un riepilogo, ma non crea ENTRY operativi. Prima del merge verifica che:
- il workflow veda `DATABASE_URL`;
- il riepilogo Telegram arrivi;
- nel database vengano create le nuove tabelle al primo run.
