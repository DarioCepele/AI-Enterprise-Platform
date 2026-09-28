# Changelog

Il formato segue [Keep a Changelog](https://keepachangelog.com/it/1.1.0/), le
versioni [Semantic Versioning](https://semver.org/lang/it/).

## [Non rilasciato]

### Da sapere prima di aggiornare

- **Variabili del master con prefisso `MASTER_`.** I vecchi `DEMO_*` funzionano
  ancora: all'avvio un avviso li elenca, e verranno rimossi.
- **Un segreto solo per le notifiche push**, `PUSH_SECRET` nel `.env` del
  compose, al posto di `DEMO_PUSH_SECRET` e `PROCESS_PUSH_SECRET`.
- **Un ruolo e un database per servizio**: `master`, `memory`, `processes`,
  `agents`, creati a ogni `up` dal job `postgres-init`, con le password
  `MASTER_DB_PASSWORD`, `MEMORY_DB_PASSWORD`, `PROCESS_DB_PASSWORD` e
  `AGENTS_DB_PASSWORD`. Il superutente resta solo al job. Il progetto compose
  si chiama `agent-platform`, e il volume `agent-platform_postgres-data`.
- **Webhook dei sottoagenti** su `/a2a/push/{scope}/{thread}/{agente}`, con un
  token firmato per scope, thread e agente. Le notifiche dei task registrati
  prima dell'aggiornamento non vengono più consegnate.
- **`start_process` chiede un'approvazione** prima di partire
  (`MASTER_TOOLS_REQUIRING_APPROVAL`; vuoto per nessuna approvazione).
- **Il modello finto si accende con `FAKE_MODEL`** nel `.env`, per tutti gli
  agenti insieme.
- **Il pacchetto Python del master si chiama `master_agent`**, non più `demo`:
  `uv run python -m master_agent`, e gli import dei fork da aggiornare.
- **L'header dello scope non è creduto finché non lo configuri**
  (`PROCESS_SCOPE_HEADER`, `MASTER_SCOPE_HEADER`): va impostato solo dietro un
  proxy che lo scrive, mai dal browser.
- **Frontend**: nome predefinito *Agent Platform*, nessun badge predefinito,
  interfaccia in inglese.

### Aggiunto

- Approvazioni umane con gli interrupt di AG-UI: la card nel frontend, la
  risposta che riprende il run, il canale vocale che annulla e avvisa. Una
  risposta che non si può applicare fallisce chiusa.
- `packages/platform-core`, il codice che i servizi condividono: osservabilità,
  migrazioni con lock, controllo degli URL contro l'SSRF, notifiche push, store
  A2A, MCP, CORS, limiti sulle richieste, segreti, modello finto.
- Tenancy per richiesta, rispettata da ogni tool.
- Task e webhook A2A durevoli su Postgres.
- Upload condivisi fra repliche, su Postgres.
- `GET /transparency`: modello, provider e comportamento dell'agente, senza
  credenziali né indirizzi interni.
- Estensioni senza modificare la piattaforma: `MASTER_INSTRUCTIONS_FILE`,
  `MASTER_SKILLS_DIRS`, `MASTER_TOOL_FACTORIES`.
- Frontend: Content Security Policy con nonce e header di hardening;
  `API_CREDENTIALS`, e `MASTER_CORS_CREDENTIALS`/`PROCESS_CORS_CREDENTIALS`
  nei servizi, per un proxy a cookie davanti.
- Modalità senza modello anche per i sottoagenti (`*_FAKE_CLIENT`): la
  piattaforma parte e risponde senza credenziali.
- Kubernetes: NetworkPolicy, PodDisruptionBudget, StatefulSet per il servizio
  dei processi, Ingress generico, `secret.example.yaml`, overlay k3s.
- CI: azioni fissate per commit e token in sola lettura; platform-core e voce
  nella matrice; lint e build del frontend; manifest validati con kubeconform;
  shellcheck; smoke test del compose; rilascio su GHCR con SBOM e provenance;
  Dependabot.
- `LICENSE` (Apache-2.0), `SECURITY.md`, `CONTRIBUTING.md`, questo file.

### Corretto

- Lo scope dei thread non veniva mai applicato: l'adapter AG-UI passa al
  resolver il corpo della richiesta, non la richiesta.
- I download di video e pagine potevano raggiungere la rete interna; i
  sottoagenti potevano notificare qualunque URL.
- I fatti ricordati arrivavano al modello come istruzioni di sistema.
- Tutti i servizi usavano lo stesso superutente Postgres.
- Il WebSocket della voce accettava qualunque origine.
- Un'approvazione configurata per un agente passava a tutti gli agenti creati
  dopo nello stesso processo.
- Dopo una richiesta vocale che chiedeva un'approvazione, la sessione rifiutava
  ogni turno successivo; un turno fallito restava in silenzio.
- Senza chiave API i sottoagenti non partivano, e con loro tutto lo stack.
- Il build delle immagini in CI non trovava `platform-core`.
- Il test sul video dal vivo poteva fare una chiamata a pagamento con un
  semplice `pytest`.
- L'immagine della voce scaricava un modello spaCy a runtime, impossibile con
  il filesystem in sola lettura; torch portava con sé le librerie CUDA.
- Il wheel del master non si costruiva.

### Rimosso

- `postgres/init/01-databases.sh`, sostituito da `postgres/provision.sh`.
- Il documento di passaggio fra sessioni e una fixture duplicata.

## Aggiornare da una versione precedente

1. **Il `.env`.** Ripartire da `demo-infra/.env.example`: le variabili dei
   segreti e dei database sono nuove.
2. **I dati.** Per un'installazione di prova conviene ripartire da zero. Per
   portarli con sé, lo schema vecchio aveva tre database (`agente`, `memoria`,
   `processi`) posseduti dal superutente, tabelle comprese: rinominarli non
   basta. Si esportano senza proprietari e si importano come ruolo del
   servizio, che ne diventa il proprietario:

   ```bash
   # con il vecchio stack acceso (progetto compose `demo-infra`)
   docker compose -p demo-infra exec -T postgres \
     pg_dump -U "$VECCHIO_UTENTE" --no-owner --no-privileges agente > master.sql
   # lo stesso per memoria -> memory.sql e processi -> processes.sql

   # nuovo stack acceso una volta (crea ruoli e database vuoti), poi:
   docker compose exec -T -e PGPASSWORD="$MASTER_DB_PASSWORD" postgres \
     psql -U master_svc -d master < master.sql
   # lo stesso per memory (memory_svc) e processes (process_svc)
   ```

   Le tabelle delle migrazioni viaggiano con i dati: all'avvio i servizi non
   riapplicano quello che c'è già.
