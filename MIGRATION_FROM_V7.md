# Migrazione V7 -> V7.1 Performance

Non serve cambiare repository, database o secrets.

1. Carica i file V7.1 nella root del repository GitHub.
2. Conferma il commit su `main`.
3. Lascia Streamlit su Python 3.12.
4. Attendi il redeploy.
5. Testa NVDA una prima volta.
6. Ricalcola NVDA: controlla che la cache modello passi a `MEMORY` o `DISK`.
7. Poi prova AAPL e SPY.

La V7.1 mantiene DAY/WEEK/MONTH, scanner, backtest, verifica previsioni, paper positions, DB e Telegram. La differenza principale è che separa il training dai refresh live.
