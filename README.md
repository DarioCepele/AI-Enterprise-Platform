# Laboratorio AG-UI

Demo locale di un'interfaccia agentica: chat in streaming, stato condiviso ed
event inspector, tutti alimentati da un solo stream SSE in protocollo AG-UI.
Il tab LOG usa un secondo canale: `GET /logs?cursor=<int>`, interrogato durante
la run e una volta alla fine. Il polling si ferma a riposo; cambiare tab conserva
cronologia e cursore.

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

## Modelli per il piano di lavoro

Serve un modello con **tool-calling reale**: scrivere nel testo che sta creando
un piano non aggiorna `shared.plan`. Il modello deve chiamare `todo_write`, poi
`todo_set_status`, oltre a `load_skill` e `ui_table` quando richiesti.

Profili verificati nelle prove del laboratorio:

| Servizio | Modello | Configurazione |
|---|---|---|
| OpenRouter | `qwen/qwen3.8-27b` | `OPENAI_BASE_URL=https://openrouter.ai/api/v1` |
| LM Studio | `google/gemma-4-12b` | `OPENAI_BASE_URL=http://localhost:1234/v1` in sviluppo nativo |

Nel container, per LM Studio sull'host usare `http://host.docker.internal:1234/v1`.
Configurare `OPENAI_CHAT_COMPLETION_MODEL` con l'identificativo del modello e
`OPENAI_API_KEY` nel proprio `.env`; non versionare le credenziali. Gemma 4 12B
ha emesso chiamate `ui_table` complete e snapshot di stato nelle prove precedenti:
non va considerato incapace di chiamare tool.

Per provare il flusso in `http://localhost:3000`:

> Confronta Python e Go su tipizzazione, concorrenza e gestione degli errori.
> Fai prima un piano di lavoro.

Il piano deve avanzare durante la run, mentre la timeline mostra ragionamento,
tool e tabella. Nell'Inspector si filtrano gli eventi; LOG mostra orario, sorgente
e messaggio. Il backend conserva un solo piano per processo: due schede del browser
lo condividono. I log sono anch'essi del processo, senza isolamento per thread.

La risposta finale e' resa come Markdown mentre arriva; il pulsante `interrompi`
chiude la run in corso senza segnalare un errore. Nell'inspector gli eventi
consecutivi dello stesso tipo stanno in una riga sola con il conteggio: il
contatore in alto resta quello degli eventi, e il payload compare aprendo la riga.

## Test

```bash
cd ../demo-master-agent && uv run pytest -v
cd ../demo-frontend && npm test
```

`node_modules` contiene binari specifici della piattaforma: se alterni Windows e
WSL sulla stessa cartella, rilancia `npm install` dopo ogni cambio.

## Stato

Tappa 1 completata. Tappa 2 completata: piano di lavoro, skill, tabelle,
timeline, filtri e log; flusso verificato nel browser con qwen/qwen3.8-27b.
Tappa 3 (sottoagenti A2A) da fare.
