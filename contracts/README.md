# Contratti fra i repo

Quattro repo che si parlano, nessuno che li contiene. Questa cartella tiene i
**campioni versionati** di ciò che passa da uno all'altro, e i test di ogni repo
li caricano da qui invece di ripetersi a vicenda una copia che diverge.

Serve a una cosa sola: **rendere rumorosa una rottura che oggi sarebbe
silenziosa.** Rinominare l'artefatto del sottoagente da `scheda` a `briefing` ha
toccato knowledge agent, master agent e frontend. È passata liscia solo perché i
tre repo erano aperti nella stessa sessione. In un fork, chi cambia il
produttore non vede il consumatore.

## Come è fatto un contratto

```json
{
  "contract": "a2a/briefing",
  "version": 1,
  "produced_by": ["demo-knowledge-agent"],
  "consumed_by": ["demo-master-agent"],
  "description": "…",
  "sample": { }
}
```

`consumed_by` non è decorazione: è quello che il test stampa quando fallisce,
così chi ha rotto il contratto legge subito **chi** ha appena rotto.

## Cosa verificano i test

Le chiavi, non i valori. Un campione con `similarity: 0.83` non impone quel
numero; impone che il campo si chiami `similarity` e che ci sia. I valori
servono a rendere il campione leggibile e usabile come fixture nei test.

| Contratto | Chi lo produce | Chi lo consuma |
|---|---|---|
| `a2a/briefing` | knowledge agent | master agent |
| `agui/tool-result-briefing` | master agent | frontend |
| `agui/tool-result-ui-table` | master agent | frontend |
| `agui/shared-state` | master agent | frontend, servizio di memoria |
| `memory/snapshot` | servizio di memoria | master agent |
| `memory/search` | servizio di memoria | master agent |
| `memory/write-results` | servizio di memoria | master agent |

## Come lo trovano i repo

Ogni repo cerca questa cartella in `../demo-infra/contracts`, cioè dove sta
quando i quattro repo sono cloni fratelli. Si può spostare con la variabile
`AGUI_LAB_CONTRACTS`.

Se la cartella non c'è, i test di contratto **falliscono** con l'istruzione per
sistemare. Non si saltano: un test di contratto che si salta da solo quando la
controparte manca è esattamente il silenzio che questa cartella esiste per
togliere.

## Cambiare un contratto

1. Cambia il campione **e** alza `version`.
2. Fai passare i test in tutti i repo elencati in `produced_by` e `consumed_by`
   — nello stesso giro di lavoro, non "poi".
3. Se il cambiamento non è retrocompatibile, dillo nel messaggio di commit: chi
   aggiorna un fork legge quello.
