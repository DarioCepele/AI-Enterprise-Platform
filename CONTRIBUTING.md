# Contribuire

## Cosa serve

- Python 3.12 e [uv](https://docs.astral.sh/uv/)
- Node 22
- Docker con Compose v2
- facoltativi: `kubectl` e [kubeconform](https://github.com/yannh/kubeconform)
  per i manifest

## Un servizio Python

```bash
cd demo-master-agent
uv sync
uv run pytest -q
uv run ruff check .
uv run mypy src
```

Nessun test chiama un modello a pagamento: lanciali con `OPENAI_API_KEY=`
vuota. L'unico test che lo fa parte solo con `MASTER_LIVE_TESTS=1`.

I test che hanno bisogno di Postgres si saltano quando manca il suo indirizzo.
Per farli girare basta un Postgres qualsiasi con pgvector:

```bash
docker run -d --name platform-test-pg -e POSTGRES_PASSWORD=test \
  -p 127.0.0.1:55432:5432 pgvector/pgvector:pg17

export MASTER_POSTGRES_DSN=postgresql://postgres:test@127.0.0.1:55432/master
export MEMORY_POSTGRES_DSN=postgresql://postgres:test@127.0.0.1:55432/memory
export PROCESS_POSTGRES_DSN=postgresql://postgres:test@127.0.0.1:55432/processes
export PLATFORM_TEST_POSTGRES_DSN=postgresql://postgres:test@127.0.0.1:55432/postgres
```

Ogni suite si crea un database suo (`master_test`, ...) accanto a quello
indicato, e non tocca quello vero.

`packages/platform-core` è il codice che tutti i servizi condividono:
osservabilità, migrazioni, controllo degli URL, notifiche push, store A2A, MCP,
CORS, il modello finto. Cambiarlo fa girare in CI tutte le suite.

## Il frontend

```bash
cd demo-frontend
npm ci
npx tsc --noEmit && npm run lint && npm test && npm run build
```

## Tutto insieme

```bash
cd demo-infra
tools/smoke.sh
```

Avvia il core con il modello finto, sotto un nome di progetto e un volume suoi,
controlla che risponda come lo usa l'interfaccia, e lo spegne.

## Configurazione

Una variabile nuova è un campo nei `Settings` del servizio, con la sua nota in
`FIELD_NOTES`. Per il frontend è una voce in `lib/runtime-env.json`: i test
controllano che il JSON corrisponda a quello che la pagina legge davvero. Poi:

```bash
cd demo-infra
python tools/env_table.py
```

Rigenera le tabelle del README. La CI fallisce se non sono aggiornate.

## Contratti

I messaggi scambiati fra servizi stanno in `demo-infra/contracts`, ognuno con
chi lo produce e chi lo consuma. Cambiarne uno fa girare i test dei
consumatori, che sono quelli che si rompono.

## Pull request

- Una modifica per pull request, con i test che la dimostrano.
- La CI verde: `python`, `frontend`, `infra`, `images` e `smoke`.
- Se cambia un comportamento o una variabile, una riga in `CHANGELOG.md` sotto
  *Non rilasciato*.
- Le dipendenze le aggiorna Dependabot. Le azioni della CI sono fissate per
  commit, non per tag.

## Rilasci

```bash
git tag v1.2.3 && git push origin v1.2.3
```

Il workflow `release` pubblica le immagini su GitHub Container Registry, ognuna
con SBOM e provenance allegati.
