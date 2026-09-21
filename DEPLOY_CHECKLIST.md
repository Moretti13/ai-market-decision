# Deploy Checklist V7.3 Radar

## GitHub

Carica la patch V7.3 nella **root** del repository e sostituisci i file con lo stesso nome.

File principali aggiornati:

- `app.py`
- `scanner.py`
- `config.py`
- `db.py`
- `tests/test_core.py`

File nuovi:

- `PATCH_NOTES_V7_3.md`
- `TELEGRAM_SETUP.md`
- `MIGRATION_FROM_V7_2.md`

## Streamlit

- Branch: `main`
- Main file: `app.py`
- Python: **3.12**

Dopo il commit attendi il redeploy automatico. Se necessario usa **Manage app → Reboot app**.

## Test base

1. Controlla che il titolo mostri **AI Market Decision V7.3 Radar**.
2. Prova NVDA con `Ricalcola ora`.
3. Apri Scanner e fai una scansione DAY di 5 asset.
4. Verifica che alcuni WAIT possano diventare `BUY WATCH` / `SELL WATCH` e che la colonna `reason` spieghi il perché.
5. Lascia lo `Score alert` a **75** per il primo paper test.
6. Configura Telegram in **Sistema** e usa `Test Telegram`.
7. Abilita `Radar automatico`, 5 asset, ogni **15 min**.
8. Il radar deve rimanere in standby fuori da premarket/sessione regolare e ripartire automaticamente nella finestra live.
9. Gli alert ripetuti dello stesso ticker/stato nello stesso giorno devono essere deduplicati.

## Impostazione consigliata

- Ricalcolo asset: 15 min se attivo
- Radar DAY: 15 min
- Numero asset iniziale: 5
- Score alert: 75/100
- Notifica WATCH: ON durante il paper test
- Backtest: manuale

## Nota 24/7

Il Radar V7.3 gira finché Streamlit è sveglio. Per un vero monitor 24/7 indipendente dall'app servirà un worker cloud separato in una fase successiva.
