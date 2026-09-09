# Laboratorio AG-UI

Demo locale di un'interfaccia agentica: chat in streaming, piano di lavoro ed
event inspector alimentati da un solo stream SSE in protocollo AG-UI, un agente
che interroga un **sottoagente via A2A** e una memoria conversazionale che
sopravvive ai riavvii.

Il tab LOG usa un secondo canale: `GET /logs?cursor=<int>`, interrogato durante
la run e una volta alla fine. Il polling si ferma a riposo; cambiare tab conserva
cronologia e cursore.

Design: [`docs/specs/2026-09-07-agui-lab-design.md`](docs/specs/2026-09-07-agui-lab-design.md)

## Repo

| Repo | Ruolo |
|---|---|
| `demo-master-agent` | agente principale, endpoint AG-UI su SSE |
| `demo-knowledge-agent` | sottoagente di knowledge base, esposto via A2A |
| `demo-memory-service` | memoria delle conversazioni: transcript, riassunti, fatti, ricordi |
| `demo-frontend` | interfaccia Next.js |
| `demo-infra` | compose, documentazione (questo repo) |

I repo devono stare nella stessa cartella padre: `compose.yaml` li costruisce da
percorsi fratelli.

## I servizi in piedi

| Servizio | Porta sull'host | A cosa serve |
|---|---|---|
| `frontend` | 3000 | l'interfaccia |
| `master-agent` | 8000 | AG-UI su SSE, `/logs` |
| `memory-service` | — | memoria conversazionale, interna |
| `knowledge-agent` | — | sottoagente A2A, interno |
| `mongo` | loopback | transcript e fatti duraturi |
| `redis` | loopback | coda calda e ricordi cercabili |

Memoria e sottoagente **non pubblicano porte**: li raggiunge solo il master
agent dalla rete di compose. L'unico modo serio di dire "servizio interno" è non
esporlo.

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
e messaggio. Il piano appartiene al thread e sopravvive al riavvio dell'agente;
i log invece sono ancora del processo, senza isolamento per thread.

La risposta finale e' resa come Markdown mentre arriva; il pulsante `interrompi`
chiude la run in corso senza segnalare un errore. Nell'inspector gli eventi
consecutivi dello stesso tipo stanno in una riga sola con il conteggio: il
contatore in alto resta quello degli eventi, e il payload compare aprendo la riga.

## Il sottoagente A2A

Il master non fa tutto da solo: per le domande sui linguaggi interroga il
[knowledge agent](../demo-knowledge-agent/README.md), un processo separato che
parla **A2A**. Due interrogazioni chieste nello stesso turno partono insieme —
MAF esegue le tool call di un turno con `asyncio.gather`.

Provalo con:

> Interroga la knowledge base su Go e su Rust riguardo alla concorrenza, poi
> confrontali in una tabella. Fai prima un piano.

Nella timeline compaiono due voci `knowledge`, e finché la prima è `in corso`
mentre l'altra è già `concluso` stai guardando il parallelismo mentre accade.
Nell'inspector il filtro **sottoagenti** mostra `SUBAGENT_STARTED ×2` seguito da
`SUBAGENT_FINISHED ×2`.

Misure di una run vera: 352 e 492 aggiornamenti dal sottoagente, 13,2 s e 39,1 s,
con la prima chiusa mentre la seconda era a metà. In serie sarebbero stati
oltre 50 s.

Due default disattivano lo streaming A2A **in silenzio** — card non fetchata
lato client, `stream=False` lato server — e una versione di protocollo
sbagliata nella card lo rompe con un `MethodNotFoundError` che non spiega
niente. Tutti e tre sono documentati in §5.5-5.7 della spec e nel README del
knowledge agent.

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

## I log operativi con piu' di una replica

Il tab LOG legge `GET /logs` con un cursore. Il collettore in RAM di ogni
processo resta, ma quando `DEMO_REDIS_URI` e' configurato le righe finiscono
anche in uno stream Redis condiviso, e l'endpoint legge da li': due repliche
raccontano una storia sola invece di meta' ciascuna.

Il cursore e' **opaco**: con lo stream porta una posizione piu' il numero di
sequenza globale, senza porta il numero di sequenza locale. Il client lo
rimanda com'e'. `dropped` dice quante righe sono uscite dal buffer fra due
letture, cosi' un buco non si legge come continuita'.

Senza Redis non si rompe niente: ogni replica mostra le proprie righe, e lo
dichiara nei log all'avvio.

## Sonde, log e tracce

`/health/live` dice se il processo e' bloccato e non chiede niente a nessuno:
una sonda di liveness che interrogasse i database trasformerebbe un guasto in
un loop di riavvii. `/health/ready` dice se il servizio puo' servire, ed e'
quella che il compose usa per `service_healthy`.

Cosa conta come dipendenza cambia per servizio: per il master il servizio di
memoria si' e i sottoagenti no -- uno giu' degrada un turno e il tool lo
dichiara, mentre senza memoria ogni thread ripartirebbe da zero in silenzio.
Per la memoria, Mongo e' obbligatorio e Redis no: e' una cache, e senza si e'
piu' lenti, non incapaci.

Con `JSON_LOGS=true` le righe diventano oggetti con servizio, logger e thread
del turno. Con `OTEL_EXPORTER_OTLP_ENDPOINT` le tracce attraversano i tre
servizi; senza, non si esporta niente e non si rompe niente.

## Immagini e deploy

Le immagini di base sono pinnate per **digest**: un tag si sposta, un digest
no, e due build della stessa riga devono partire dalla stessa base. Nessun
processo gira come root, e i tre servizi Python reggono
`readOnlyRootFilesystem` -- chiamano uvicorn dal venv, perche' `uv run` vuole
una cache scrivibile.

Il frontend legge la propria configurazione **a runtime**: la stessa immagine
serve staging e produzione cambiando `AGUI_URL` e `PRODUCT_NAME`, senza
ricostruire niente. Prima quelle variabili finivano nel bundle a build time.

I manifest Kubernetes stanno in `deploy/`, con due repliche di default sul
master agent e sul servizio di memoria: non e' dimensionamento, e' la sonda che
fa emergere subito una regressione dello stato di processo. Mongo e Redis
restano fuori, e il perche' e' scritto in `deploy/README.md`.

## Contratti fra i repo

`contracts/` tiene i campioni versionati di cio' che passa da un repo all'altro:
l'artefatto A2A del sottoagente, lo stato condiviso AG-UI, le risposte dell'API
di memoria. Ogni repo li carica dai propri test invece di tenerne una copia che
diverge, e quando un campione non torna il fallimento nomina il contratto, la
differenza e **chi lo consuma**.

Esiste per una rottura vera: rinominare l'artefatto da `scheda` a `briefing` ha
toccato tre repo, ed e' passata liscia solo perche' erano aperti nella stessa
sessione. In un fork non lo sono.

Dettagli e regole di modifica in `contracts/README.md`.

## Test

```bash
cd ../demo-master-agent && uv run pytest        # 140
cd ../demo-knowledge-agent && uv run pytest     # 39
cd ../demo-memory-service && uv run pytest      # 97, contro Mongo e Redis veri
cd ../demo-frontend && npm test                 # 121
```

I test del servizio di memoria girano contro i database veri (`docker compose
up -d mongo redis`): il modello a bucket senza transazioni si regge su garanzie
del server che un finto non riproduce. Gli altri sono offline.

`node_modules` contiene binari specifici della piattaforma: se alterni Windows e
WSL sulla stessa cartella, rilancia `npm install` dopo ogni cambio.

## Stato

Tappe 1, 2 e 3 completate e verificate nel browser: walking skeleton, flusso del
video (piano, skill, tabelle, filtri, log) e sottoagenti A2A in parallelo.

Costruito oltre il piano iniziale: memoria conversazionale durevole con
riassunti, fatti duraturi e ricerca semantica dei ricordi, piu' la telemetria
del contesto a ogni chiamata al modello.

I task futuri stanno in §11 della spec: registry degli agenti con ricerca
semantica, decadimento dei fatti, reindicizzazione dei ricordi, update dei
sottoagenti rilanciati nello stream.
