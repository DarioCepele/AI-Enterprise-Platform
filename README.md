# Laboratorio AG-UI

Un'interfaccia agentica completa da cui partire: chat in streaming, piano di
lavoro ed event inspector alimentati da un solo stream SSE in protocollo AG-UI,
un agente che interroga **sottoagenti via A2A** e una memoria conversazionale
che sopravvive ai riavvii.

E' pensato per essere **forkato**: si clona, si cambiano delle variabili, si
scrivono i propri agenti. Cosa toccare sta in "[Come si forka](#come-si-forka)";
cosa manca di proposito, in "[Cosa non c'e'](#cosa-non-ce-e-perche)".

Il tab LOG usa un secondo canale: `GET /logs?cursor=<opaco>`, interrogato
durante la run e una volta alla fine. Il polling si ferma a riposo; cambiare tab
conserva cronologia e cursore.

Design: [`docs/specs/2026-09-07-agui-lab-design.md`](docs/specs/2026-09-07-agui-lab-design.md)

## Come si forka

Quattro repo fratelli, nessuno che li contiene: si clonano tutti e quattro nella
stessa cartella, perche' il compose li costruisce da percorsi fratelli e i test
di contratto cercano `../demo-infra/contracts`.

**Quello che si cambia**, in ordine di quanto si nota:

1. **Le variabili d'ambiente.** Nome del prodotto, lingua delle risposte, scope,
   modelli, indirizzi. Sono tutte nella tabella qui sotto e in `.env.example`;
   nessuna richiede di toccare il codice.
2. **Le istruzioni del master**, in `demo-master-agent/src/demo/agents/master.py`.
   E' il prompt che dice come lavora il tuo agente: il nome e la lingua ci
   arrivano gia' dalla configurazione.
3. **Le skill**, in `demo-master-agent/src/demo/skills/`. Formato Agent Skills:
   una cartella, un `SKILL.md`, frontmatter YAML. `comparison` e' un esempio.
4. **I tool**, in `demo-master-agent/src/demo/tools/`. `ui_table` e il piano di
   lavoro sono il vocabolario dell'interfaccia; il resto e' tuo.
5. **Il copy dell'interfaccia**, in `demo-frontend/lib/runtime-config.ts` per
   nome e claim, nei componenti per il resto.

**Quello che si tiene** e' l'ossatura: lo stream AG-UI e il reducer che lo
consuma, il client A2A col ciclo di vita del task, il servizio di memoria con
riassunti e ricerca semantica, i contratti fra i repo, le sonde, le migrazioni.

**Quello che si butta**: `demo-knowledge-agent` e' un **esempio** di sottoagente
A2A -- corpus di tre documenti su tre linguaggi. Serve a mostrare come si scrive
un agente remoto che risponde con artefatti strutturati e sa fermarsi a chiedere
un chiarimento. Il tuo sottoagente prendera' il suo posto, o non ce ne sara'
nessuno: `DEMO_SUBAGENTS=` vuoto e il master non espone tool di sottoagente.

## Cosa non c'e', e perche'

**L'autenticazione.** Rimandata per scelta, ma le giunture sono aperte e sono
tre:

- `demo-master-agent/src/demo/server/scope.py` -- `scope_of_request` decide a
  quale scope appartiene una richiesta. Oggi restituisce lo scope configurato, e
  legge l'intestazione **solo** se `DEMO_SCOPE_HEADER` la dichiara fidata: un
  valore che nessuno ha verificato e' una richiesta del client, non
  un'identita'. Chi aggiunge OIDC sostituisce questa funzione, e nient'altro;
- `create_app(scope_resolver=...)` la accetta iniettata, quindi la sostituzione
  non richiede di modificare l'app;
- la card A2A dichiara gia' i propri `security_schemes`, e il token di servizio
  della card estesa e' isolato in `demo-knowledge-agent/src/knowledge/extended.py`.

**Una UI di amministrazione.** Ritenzione e reindicizzazione sono endpoint,
pensati per un job schedulato.

**Un registry di agenti.** Il master conosce i sottoagenti per configurazione.
Il perche' e cosa servirebbe stanno in §11 della spec.

## Limiti dichiarati

Quello che degrada, invece di rompersi, quando manca un pezzo:

| Se manca | Cosa succede |
|---|---|
| `DEMO_MEMORY_SERVICE_URL` | la conversazione vive in RAM e muore col processo |
| `DEMO_REDIS_URI` | i log operativi restano per replica, e con due repliche il tab LOG ne mostra meta'; le notifiche push non si deduplicano |
| `MEMORY_REDIS_URI` | niente coda calda, niente ricerca semantica, lock di compattazione solo di processo |
| `MEMORY_SUMMARY_MODEL` | i turni fuori finestra escono dal contesto senza riassunto, e non si imparano fatti duraturi |
| `MEMORY_EMBEDDING_MODEL` | `cerca_nei_ricordi` non trova niente e lo dichiara |
| `KNOWLEDGE_SERVICE_TOKEN` | la card estesa non e' accessibile: il modello interroga il sottoagente senza sapere cosa contiene |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | nessuna traccia esportata, tutto il resto uguale |
| `PROCESS_POSTGRES_DSN` (irraggiungibile) | il servizio dei processi non parte: non c'e' una modalita' in memoria, sarebbe un motore che dice di essere durevole e non lo e' |
| `PROCESS_AGENTS` | i passi `agent` e `open_goal` falliscono col nome dell'agente cercato; i processi di soli `tool` e `decision` girano lo stesso |
| `PROCESS_URL` (frontend) | il tab **Istanze** dice che non c'e' un servizio dei processi, invece di mostrare un errore |
| `DEMO_PROCESS_SERVICE_URL` (master) | l'agente non ha i tool per avviare o leggere un processo: non li vede proprio, quindi non prova a usarli |
| `OPENAI_API_KEY` sul process-service | fallisce solo `open_goal`: e' l'unico posto dove quel servizio parla con un modello |
| `ANALYSIS_SERVICE_TOKEN` | come per il knowledge agent: card estesa non accessibile |

E i default che valgono per un tenant solo: `DEMO_DEFAULT_SCOPE` e' una
costante, `DEMO_SCOPE_HEADER` e' vuoto, la ritenzione e' spenta.

## Variabili d'ambiente

Generata da chi le legge, con `tools/env_table.py`: una tabella scritta a mano
e' una tabella che mente al secondo cambiamento.

<!-- env-table:start -->

### master agent

| Variable | Default | What it decides |
|---|---|---|
| `OPENAI_BASE_URL` | `https://openrouter.ai/api/v1` | Where the model lives. Any OpenAI-compatible endpoint. |
| `OPENAI_API_KEY` | *(empty)* | Credential for that endpoint. Required unless the fake client is on. |
| `OPENAI_CHAT_COMPLETION_MODEL` | `anthropic/claude-sonnet-5` | Model the master agent talks to. |
| `DEMO_FAKE_CLIENT` | `False` | Deterministic answers without a model. For tests and offline work. |
| `DEMO_ALLOWED_ORIGINS` | `http://localhost:3000, http://127.0.0.1:3000, http://localhost:3001, http://127.0.0.1:3001` | Comma-separated origins allowed by CORS. |
| `DEMO_PRODUCT_NAME` | `AG-UI Lab` | What the agent calls itself in its own instructions. |
| `DEMO_PRODUCT_LANGUAGE` | `Italian` | Language the agent answers in. |
| `DEMO_DEFAULT_SCOPE` | `local-laboratory` | Authorization boundary used when nothing else says otherwise. |
| `DEMO_SCOPE_HEADER` | *(empty)* | Header carrying the scope, read **only** when this is set: naming it means something in front has verified it. |
| `DEMO_JSON_LOGS` | `False` | Structured logs for a collector instead of the readable line. |
| `DEMO_MEMORY_SERVICE_URL` | *(empty)* | Memory service. Without it the conversation lives in RAM and dies with the process. |
| `DEMO_REDIS_URI` | *(empty)* | Shared logs, deduplicated notifications. Without it both are per replica. |
| `DEMO_KNOWLEDGE_AGENT_URL` | *(empty)* | One subagent, the short way. Ignored when DEMO_SUBAGENTS is set. |
| `DEMO_KNOWLEDGE_SERVICE_TOKEN` | *(empty)* | Service token of that subagent, for its extended card. |
| `DEMO_SUBAGENTS` | *(empty)* | Subagents as JSON: [{"name":"x","url":"http://...","token":""}]. |
| `DEMO_PUBLIC_URL` | *(empty)* | How a subagent reaches this agent back, for push notifications. |
| `DEMO_PROCESS_SERVICE_URL` | *(empty)* | Where durable processes live. Empty: the agent cannot start one. |
| `DEMO_SUBAGENT_WAIT_SECONDS` | `60.0` | How long a turn waits before letting the outcome arrive by notification. |

### memory service

| Variable | Default | What it decides |
|---|---|---|
| `MEMORY_MONGO_URI` | `mongodb://127.0.0.1:27017` | Durable transcripts. Required. |
| `MEMORY_MONGO_DATABASE` | `demo_memory` | Database name inside that Mongo. |
| `MEMORY_REDIS_URI` | `redis://127.0.0.1:6379/0` | Hot tail, semantic index, compaction lock. Required. |
| `MEMORY_BUCKET_SIZE` | `50` | Messages per bucket document. |
| `MEMORY_RETENTION_DAYS` | `0` | Days of inactivity after which a thread is forgotten. 0 = never. |
| `MEMORY_JSON_LOGS` | `False` | Structured logs for a collector instead of the readable line. |
| `MEMORY_HOT_TAIL_SECONDS` | `1800` | How long the cached tail of a conversation survives. |
| `MEMORY_HOT_TAIL_MESSAGES` | `100` | How many messages that tail keeps. |
| `MEMORY_SUMMARY_MODEL` | *(empty)* | Model for summaries. Empty means no compaction and no durable facts. |
| `MEMORY_SUMMARY_BASE_URL` | `https://openrouter.ai/api/v1` | Endpoint of the model that summarizes and extracts facts. |
| `MEMORY_SUMMARY_API_KEY` | *(empty)* | Credential for that endpoint. |
| `MEMORY_EMBEDDING_MODEL` | *(empty)* | Embedding model. Empty means no semantic search. |
| `MEMORY_DROP_REASONING` | `True` | Whether past reasoning leaves the rebuilt context. |
| `MEMORY_KEEP_TOOL_RESULTS` | `4` | How many recent tool results keep their content. |
| `MEMORY_MAX_CONTEXT_MESSAGES` | `60` | Window handed back to the agent, in messages. |
| `MEMORY_MAX_FACTS` | `30` | How many durable facts are injected into a context. |

### process service

| Variable | Default | What it decides |
|---|---|---|
| `PROCESS_POSTGRES_DSN` | `postgresql://127.0.0.1:5432/processes` | Where instances live. Durable execution needs real transactions. |
| `PROCESS_DEFINITIONS_PATH` | `processes` | Folder of process definitions, loaded once at startup. |
| `PROCESS_DEFAULT_SCOPE` | `local-laboratory` | Authorization boundary used when the header says nothing. |
| `PROCESS_SCOPE_HEADER` | `X-Process-Scope` | Header carrying the scope of the caller. |
| `PROCESS_JSON_LOGS` | `False` | Structured logs for a collector instead of the readable line. |
| `PROCESS_POOL_MIN_SIZE` | `1` | Connections kept open. |
| `PROCESS_POOL_MAX_SIZE` | `10` | Connections at most. |
| `PROCESS_PORT` | `8300` | Where the service listens when started locally. |
| `PROCESS_ALLOWED_ORIGINS` | `('http://localhost:3000', 'http://127.0.0.1:3000', 'http://localhost:3001', 'http://127.0.0.1:3001')` | Which pages may read this API from a browser. Comma-separated. |
| `PROCESS_PUBLIC_URL` | `http://localhost:8300` | How a remote agent reaches this service back, for notifications. |
| `PROCESS_PUSH_SECRET` | `laboratory-without-a-secret` | Signs notification tokens. Change it: the default is public. |
| `PROCESS_AGENTS` | `{}` | Agents a step may delegate to, as JSON: {"knowledge": "http://..."}. |

### frontend

Read at request time, so the same image serves any environment. The
`NEXT_PUBLIC_*` names still work as the build-time fallback.

| Variable | Default | What it decides |
|---|---|---|
| `AGUI_URL` | `http://127.0.0.1:8000/agui` | Where the agent answers. The logs endpoint is derived from it. |
| `PRODUCT_NAME` | `AG-UI Lab` | Name shown in the header and in the tab. |
| `PRODUCT_TAGLINE` | `an agent at work` | Line under the name. |
| `PRODUCT_DESCRIPTION` | *(see lib/runtime-config.ts)* | Page description. |
| `PRODUCT_DISCLAIMER` | *(see lib/runtime-config.ts)* | Footer line. |
| `PRODUCT_LOCALE` | `en` | `lang` of the document. |
| `PRODUCT_MONOGRAM` | `a/` | The two characters in the badge. |
| `PRODUCT_BADGES` | `AG-UI,MAF 1.17,Next.js` | Comma-separated badges in the header. |

### knowledge agent

| Variable | Default | What it decides |
|---|---|---|
| `KNOWLEDGE_BASE_URL` | `http://localhost:8200/` | The url this agent declares in its own card. |
| `KNOWLEDGE_SERVICE_TOKEN` | *(empty)* | Token that unlocks the extended card. Empty means nobody gets it. |
| `KNOWLEDGE_JSON_LOGS` | `false` | Structured logs for a collector. |
| `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_CHAT_COMPLETION_MODEL` | *(as the master agent)* | The model this agent reads its corpus with. |

### analysis agent

| Variable | Default | What it decides |
|---|---|---|
| `ANALYSIS_BASE_URL` | `http://localhost:8400/` | The url this agent declares in its own card. |
| `ANALYSIS_SERVICE_TOKEN` | *(empty)* | Token that unlocks the extended card. Empty means nobody gets it. |
| `ANALYSIS_JSON_LOGS` | `false` | Structured logs for a collector. |
| `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_CHAT_COMPLETION_MODEL` | *(as the master agent)* | The model this agent reasons with. It computes with tools, not with the model. |

<!-- env-table:end -->

## Repo

| Repo | Ruolo |
|---|---|
| `demo-master-agent` | agente principale, endpoint AG-UI su SSE |
| `demo-knowledge-agent` | sottoagente di knowledge base, esposto via A2A |
| `demo-analysis-agent` | sottoagente che misura e confronta numeri, via A2A |
| `demo-memory-service` | memoria delle conversazioni: transcript, riassunti, fatti, ricordi |
| `demo-frontend` | interfaccia Next.js |
| `demo-process-service` | processi durevoli: definizioni versionate, istanze |
| `demo-infra` | compose, contratti, manifest, documentazione (questo repo) |

I repo devono stare nella stessa cartella padre: `compose.yaml` li costruisce da
percorsi fratelli.

## I servizi in piedi

| Servizio | Porta sull'host | A cosa serve |
|---|---|---|
| `frontend` | 3000 | l'interfaccia |
| `master-agent` | 8000 | AG-UI su SSE, `/logs` |
| `process-service` | loopback 8300 | processi durevoli, istanze su Postgres |
| `knowledge-agent` | loopback 8200 | sottoagente A2A: legge un corpus |
| `analysis-agent` | loopback 8400 | sottoagente A2A: misura e confronta |
| `memory-service` | — | memoria conversazionale, interna |
| `mongo` | loopback | transcript e fatti duraturi |
| `redis` | loopback | coda calda e ricordi cercabili |
| `postgres` | loopback | il libro mastro delle istanze |

La memoria **non pubblica porte**: la raggiunge solo il master agent dalla rete
di compose. Gli altri sono sul **loopback**, che non e' la stessa cosa di
esposti: servono a guardarli mentre si lavora -- e a misurarli, come fa
`tools/measure_fan_out.py` -- e restano irraggiungibili da fuori la macchina.

**Due sottoagenti, non uno.** Con un sottoagente solo l'instradamento non
esiste: qualunque domanda va all'unico che c'e'. `knowledge` legge quello che
qualcuno ha scritto, `analysis` lavora sui numeri che riceve nella richiesta;
sono due domini davvero diversi, ed entrambi sono **esempi da sostituire**.

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
