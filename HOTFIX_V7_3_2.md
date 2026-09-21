# V7.3.2 Yahoo Data Hotfix

Questa hotfix nasce dal caso osservato in sessione regolare con `Vol ratio = 0.00`.

## Cosa corregge

- Non interpreta più un volume Yahoo mancante/zero come un vero rapporto volume pari a zero.
- Esclude la candela 5 minuti ancora in formazione dalla conferma operativa.
- Se una barra 5m completata ha volume mancante, prova una sola volta il feed Yahoo con `prepost=True` e ripara esclusivamente il campo Volume allo stesso timestamp.
- Non inventa volume: se entrambe le richieste non forniscono un dato valido, mostra `N/D` e il filtro volume viene ignorato in modo neutrale.
- Il calcolo del volume ratio usa la mediana delle precedenti 20 barre positive e non include la barra corrente nel proprio benchmark.
- Data Health ora espone `volume_status` e percentuale di barre recenti con volume valido.
- Mantiene integralmente la hotfix database V7.3.1, Radar e Telegram.

## Limite che resta

Questa patch rende l'uso di Yahoo più robusto, ma non trasforma Yahoo Finance in un feed professionale con SLA. Per uso reale continuativo, il data provider professionale resta il passo successivo dopo il paper test.

## Verifica

- Compilazione Python: OK
- Unit test: 14/14 OK
- Test aggiunti: zero-volume non diventa 0.00; riparazione volume non modifica i prezzi.
