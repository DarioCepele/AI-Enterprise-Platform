# Laboratorio AG-UI

Demo locale di un'interfaccia agentica: chat in streaming, stato condiviso ed
event inspector, tutti alimentati da un solo stream SSE in protocollo AG-UI.

Design: [`docs/specs/2026-09-07-agui-lab-design.md`](docs/specs/2026-09-07-agui-lab-design.md)

## Repo

| Repo | Ruolo |
|---|---|
| `demo-master-agent` | agente principale, endpoint AG-UI su SSE |
| `demo-frontend` | interfaccia Next.js |
| `demo-infra` | compose, documentazione (questo repo) |
| `demo-knowledge-agent` | sottoagente A2A — tappa 3, non ancora presente |

I repo devono stare nella stessa cartella padre: `compose.yaml` li costruisce da
percorsi fratelli.

## Avvio con Docker

```bash
cp .env.example .env        # inserire la propria API key
docker compose up --build   # http://localhost:3000
```

## Avvio in sviluppo

Più rapido per iterare, niente rebuild di immagini:

```bash
cd ../demo-master-agent && uv run python -m demo    # :8000
cd ../demo-frontend && npm run dev                  # :3000
```

Senza LLM e senza rete:

```bash
cd ../demo-master-agent && DEMO_FAKE_CLIENT=true uv run python -m demo
```

In questa modalità l'agente **non chiama tool**: `FakeStreamingChatClient` non
eredita da `FunctionInvocationLayer`, quindi `ui_table` non parte mai. È atteso.
La catena dei tool si verifica con i test o con un LLM vero.

## Test

```bash
cd ../demo-master-agent && uv run pytest -v
cd ../demo-frontend && npm test
```

`node_modules` contiene binari specifici della piattaforma: se alterni Windows e
WSL sulla stessa cartella, rilancia `npm install` dopo ogni cambio.

## Stato

Tappa 1 (walking skeleton) completata. Mancano il piano di lavoro, le skill e la
tabella comparativa (tappa 2), e i sottoagenti A2A (tappa 3).
