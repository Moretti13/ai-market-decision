# V7.4 Cloud Radar

- Worker DAY indipendente da Streamlit (`cloud_radar.py`).
- GitHub Actions ogni 15 minuti durante premarket/sessione USA.
- Funziona con PC e browser spenti.
- Conferma 5m prima di `ENTRY CONFIRMED`.
- Alert Telegram WATCH e ENTRY CONFIRMED.
- Deduplica persistente tramite SQLite + Actions cache; PostgreSQL/Supabase opzionale.
- Cache persistente dei modelli DAY per evitare retraining ad ogni run.
- Concurrency guard per evitare scansioni sovrapposte.
- Worker separato con `requirements_worker.txt`.
- Radar Streamlit mantenuto come fallback.
