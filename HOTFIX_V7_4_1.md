# V7.4.1 Telegram diagnostic hotfix

Corregge due punti emersi nel primo test GitHub Actions:

- il worker ora rende visibile nei log l'esito reale dell'invio Telegram senza mostrare token o Chat ID;
- un test manuale con `send_summary=true` fallisce esplicitamente se Telegram non consegna il riepilogo, invece di risultare verde in silenzio;
- il workflow converte esplicitamente gli input booleani in `true`/`false`;
- la chiave cache include `github.run_attempt`, evitando l'avviso di cache gia presente durante i re-run.

Il motore dei segnali non viene modificato.
