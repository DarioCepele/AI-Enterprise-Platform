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

## MongoDB

Istanza di persistenza per la memoria conversazionale dell'agente. Vive nello
stesso compose degli altri servizi ma è indipendente: si alza da sola e non è
un `depends_on` di nessuno.

```bash
docker compose up -d mongo
docker compose ps mongo          # atteso: Up (healthy)
```

Le credenziali arrivano dal `.env` (`MONGO_INITDB_ROOT_USERNAME`,
`MONGO_INITDB_ROOT_PASSWORD`, `MONGO_INITDB_DATABASE`); in `.env.example` ci
sono solo segnaposto. Vengono lette **solo al primo avvio**, quando il volume è
vuoto: per cambiarle davvero serve ricreare il volume.

I dati stanno nel volume nominato `demo-infra_mongo-data`, non in una cartella
del repo. Sopravvivono a `docker compose down`; per azzerarli serve
`docker compose down -v` (oppure `docker volume rm demo-infra_mongo-data`).

Dall'host la porta è pubblicata **solo su loopback**, quindi il DB non è
raggiungibile dalla rete locale:

```bash
mongosh "mongodb://<user>:<password>@127.0.0.1:${MONGO_HOST_PORT}/<db>?authSource=admin"
```

`MONGO_HOST_PORT` esiste perché su una macchina con un mongod nativo la 27017 è
già occupata e il container non partirebbe; in quel caso basta metterla a 27018.
Dagli altri container di compose l'indirizzo è invece `mongo:27017`, sempre con
`authSource=admin`.

Verifica rapida della connettività, senza scrivere la password a riga di comando:

```bash
docker compose exec mongo sh -c 'mongosh --quiet -u "$MONGO_INITDB_ROOT_USERNAME" \
  -p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin \
  --eval "db.adminCommand({ping:1})"'
```

### Limiti da conoscere prima di costruirci sopra

È un **nodo singolo, senza replica set**. Due conseguenze che pesano sul design
di un layer di memoria:

- **niente transazioni multi-documento**: `session.startTransaction()` fallisce.
  Scrivere un turno di conversazione deve stare in un solo documento, o
  tollerare scritture parziali.
- **niente change streams**: `db.collection.watch()` non è disponibile, quindi
  nessuna notifica push sui cambi. Chi vuole reagire alle scritture deve fare
  polling.

Entrambi richiedono un replica set, anche a singolo nodo (`--replSet` più
`rs.initiate()`), che comporta oplog e una configurazione in più: non è stato
introdotto perché per una demo locale il costo supera il beneficio.

## Redis

Memoria a breve termine del servizio di memoria: la coda calda delle
conversazioni. Requisiti opposti a quelli di Mongo — latenza bassa e scadenza
automatica invece di durata e storia completa — per questo è un servizio a sé e
non un'altra collezione.

```bash
docker compose up -d redis
docker compose ps redis          # atteso: Up (healthy)
```

Password obbligatoria dal `.env` (`REDIS_PASSWORD`), porta su loopback
(`REDIS_HOST_PORT`, default 6379). **Nessun volume, ed è voluto**: quello che
vive qui deve essere sempre ricostruibile da Mongo. Se perdere Redis perdesse
dati, sarebbe il posto sbagliato dove tenerli.

L'immagine è `redis:8.2.3`, non `redis-stack`: da Redis 8 il Query Engine sta
nella distribuzione open source (AGPLv3) e redis-stack non è più mantenuta.
Verificato sull'istanza: `FT.CREATE` e `VADD` rispondono, quindi la ricerca
vettoriale è disponibile qui senza aggiungere un terzo datastore.

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
