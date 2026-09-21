# Migrazione V7.2 → V7.3

Carica nella root del repository i file della patch V7.3 e sostituisci quelli esistenti.

File principali modificati:

- `app.py`
- `scanner.py`
- `config.py`
- `db.py`
- `tests/test_core.py`

File nuovi:

- `PATCH_NOTES_V7_3.md`
- `TELEGRAM_SETUP.md`

Non cambiare Python: resta su 3.12. Dopo il commit, attendi il redeploy Streamlit.

La configurazione Telegram non va inserita nel codice: usa Streamlit Secrets.
