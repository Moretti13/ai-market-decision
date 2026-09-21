# Configurazione Telegram — V7.3

1. Apri Telegram e cerca `@BotFather`.
2. Invia `/newbot` e segui la procedura. BotFather ti darà un `TELEGRAM_BOT_TOKEN`.
3. Apri la chat con il nuovo bot e inviagli un messaggio qualsiasi.
4. Recupera il tuo `TELEGRAM_CHAT_ID` tramite `getUpdates` dell'API Telegram oppure tramite un servizio/bot che mostri il chat id.
5. In Streamlit Cloud apri **Manage app → Settings → Secrets**.
6. Inserisci:

```toml
TELEGRAM_BOT_TOKEN = "IL_TUO_TOKEN"
TELEGRAM_CHAT_ID = "IL_TUO_CHAT_ID"
```

7. Salva i Secrets, riavvia l'app se necessario e usa **Sistema → Test Telegram**.
8. Vai in **Scanner → Radar automatico + Telegram**, abilita il radar, scegli 15 minuti e lo score minimo.

## Sicurezza

Non salvare mai il token Telegram nel repository GitHub, in `secrets.example.toml`, negli screenshot pubblici o nei messaggi di chat. Se il token viene esposto, rigeneralo con BotFather.
