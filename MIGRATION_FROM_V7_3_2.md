# Migrazione V7.3.2 → V7.4

1. Carica la patch nella root del repository.
2. Verifica che `.github/workflows/cloud_radar.yml` sia presente.
3. Aggiungi i due Telegram secrets anche in GitHub Actions.
4. Esegui manualmente il workflow una volta.
5. Se il test arriva su Telegram, il PC può essere spento: le scansioni programmate girano su GitHub Actions.
