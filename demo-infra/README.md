# La piattaforma: configurazione e infrastruttura

Un'interfaccia agentica completa da cui partire: chat in streaming, piano di
lavoro ed event inspector alimentati da un solo stream SSE in protocollo AG-UI,
un agente che interroga **sottoagenti via A2A** e una memoria conversazionale
che sopravvive ai riavvii.

E' pensato per essere **forkato**: si clona, si cambiano delle variabili, si
scrivono i propri agenti. Cosa toccare sta in "[Come si forka](#come-si-forka)";
cosa manca di proposito, in "[Cosa non c'e'](#cosa-non-ce-e-perche)". Questa
cartella contiene il compose, i [manifest Kubernetes](deploy/README.md), i
contratti fra i servizi e gli strumenti che li verificano.

Il tab LOG usa un secondo canale: `GET /logs?cursor=<opaco>`, interrogato
durante la run e una volta alla fine. Il polling si ferma a riposo; cambiare tab
conserva cronologia e cursore.

Design: [`docs/specs/2026-09-07-agui-lab-design.md`](docs/specs/2026-09-07-agui-lab-design.md)

## Come si forka

Un repository solo, con tutti i servizi dentro: il compose li costruisce da
percorsi fratelli, e `packages/platform-core` e' il codice che condividono.

**Quello che si cambia**, in ordine di quanto si nota -- e quasi niente
richiede di modificare il codice della piattaforma, cosi' un fork continua a
ricevere gli aggiornamenti con un merge:

1. **Le variabili d'ambiente.** Nome del prodotto, lingua delle risposte, scope,
   modelli, indirizzi, testi dell'interfaccia (`PRODUCT_*`, `EMPTY_*`). Sono
   tutte nella tabella qui sotto e in `.env.example`.
2. **Le istruzioni del master**: `MASTER_INSTRUCTIONS_FILE` punta a un file tuo,
   con i segnaposto `{product}` e `{language}`. Il default e' in
   `demo-master-agent/src/master_agent/agents/master.py`.
3. **Le skill**: `MASTER_SKILLS_DIRS` aggiunge le tue cartelle a quelle
   incluse. Formato Agent Skills: una cartella, un `SKILL.md`, frontmatter
   YAML. `comparison` e' un esempio.
4. **I tool**: `MASTER_TOOL_FACTORIES` carica funzioni tue
   (`modulo:funzione`) che restituiscono i tool da aggiungere. `ui_table` e il
   piano di lavoro sono il vocabolario dell'interfaccia; il resto e' tuo.
5. **Quali tool chiedono un'approvazione** prima di partire:
   `MASTER_TOOLS_REQUIRING_APPROVAL`. La chat mostra cosa sta per succedere, e
   una persona dice si' o no.

**Quello che si tiene** e' l'ossatura: lo stream AG-UI e il reducer che lo
consuma, il client A2A col ciclo di vita del task, il servizio di memoria con
riassunti e ricerca semantica, i contratti fra i servizi, le sonde, le migrazioni.

**Quello che si butta**: `demo-knowledge-agent` e' un **esempio** di sottoagente
A2A -- corpus di tre documenti su tre linguaggi. Serve a mostrare come si scrive
un agente remoto che risponde con artefatti strutturati e sa fermarsi a chiedere
un chiarimento. Il tuo sottoagente prendera' il suo posto, o non ce ne sara'
nessuno: `MASTER_SUBAGENTS=` vuoto e il master non espone tool di sottoagente.

## Cosa non c'e', e perche'

**L'autenticazione.** Fuori perimetro per scelta, ma le giunture sono aperte
(il quadro completo e' in [SECURITY.md](../SECURITY.md)):

- `demo-master-agent/src/master_agent/server/scope.py` -- `scope_of_request`
  decide a quale scope appartiene una richiesta, una volta per richiesta, e
  tutti i tool lo rispettano. Oggi restituisce lo scope configurato, e legge
  l'intestazione **solo** se `MASTER_SCOPE_HEADER` la dichiara fidata: un
  valore che nessuno ha verificato e' una richiesta del client, non
  un'identita'. Il servizio dei processi fa lo stesso con
  `PROCESS_SCOPE_HEADER` e `PROCESS_IDENTITY_HEADER`;
- `create_app(scope_resolver=...)` accetta un resolver tuo, quindi aggiungere
  OIDC non richiede di modificare l'app;
- un proxy di identita' a cookie davanti funziona con `API_CREDENTIALS=include`
  nel frontend e `*_CORS_CREDENTIALS=true` nei servizi;
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
| `MASTER_MEMORY_SERVICE_URL` | la conversazione vive in RAM e muore col processo |
| `MASTER_POSTGRES_DSN` | i log operativi restano per replica, e con due repliche il tab LOG ne mostra meta'; le notifiche push non si deduplicano |
| `MEMORY_SUMMARY_MODEL` | i turni fuori finestra escono dal contesto senza riassunto, e non si imparano fatti duraturi |
| `MEMORY_EMBEDDING_MODEL` | `search_memories` non trova niente e lo dichiara |
| `KNOWLEDGE_SERVICE_TOKEN` | la card estesa non e' accessibile: il modello interroga il sottoagente senza sapere cosa contiene |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | nessuna traccia esportata, tutto il resto uguale |
| `PROCESS_POSTGRES_DSN` (irraggiungibile) | il servizio dei processi non parte: non c'e' una modalita' in memoria, sarebbe un motore che dice di essere durevole e non lo e' |
| `PROCESS_AGENTS` | i passi `agent` e `open_goal` falliscono col nome dell'agente cercato; i processi di soli `tool` e `decision` girano lo stesso |
| `PROCESS_URL` (frontend) | il tab **Instances** dice che non c'e' un servizio dei processi, invece di mostrare un errore |
| `MASTER_PROCESS_SERVICE_URL` (master) | l'agente non ha i tool per avviare o leggere un processo: non li vede proprio, quindi non prova a usarli |
| `OPENAI_API_KEY` sul process-service | fallisce solo `open_goal`: e' l'unico posto dove quel servizio parla con un modello |
| `ANALYSIS_SERVICE_TOKEN` | come per il knowledge agent: card estesa non accessibile |
| una sola replica del master, o l'affinita' | con piu' repliche e nessuna affinita' per conversazione, una risposta a un'approvazione che arriva all'altra replica fallisce chiusa: non esegue niente, e la chat chiede di ripetere ([perche'](deploy/README.md#approvazioni-e-repliche)) |
| la chat, per le approvazioni | un turno vocale non puo' mostrare la domanda: la annulla, senza eseguire niente, e lo dice |

E i default che valgono per un tenant solo: `MASTER_DEFAULT_SCOPE` e' una
costante, `MASTER_SCOPE_HEADER` e' vuoto, la ritenzione e' spenta.

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
| `MASTER_VISION_MODEL` | `qwen/qwen3.8-27b` | Model the analyze_video tool talks to, separate from the conversation's: it must accept images, and video natively when it can. |
| `MASTER_FAKE_CLIENT` | `False` | Deterministic answers without a model. For tests and offline work. |
| `MASTER_ALLOWED_ORIGINS` | `http://localhost:3000, http://127.0.0.1:3000, http://localhost:3001, http://127.0.0.1:3001` | Comma-separated origins allowed by CORS. |
| `MASTER_CORS_CREDENTIALS` | `False` | Accept the page's cookies (a single sign-on or load-balancer cookie in front). Needs named origins, and the frontend's API_CREDENTIALS=include. |
| `MASTER_PRODUCT_NAME` | `Agent Platform` | What the agent calls itself in its own instructions. |
| `MASTER_PRODUCT_LANGUAGE` | *(empty)* | Language the agent answers in. Empty: the language the user writes in. |
| `MASTER_INSTRUCTIONS_FILE` | *(empty)* | A file whose text replaces the built-in instructions of the agent. `{product}` and `{language}` are filled in. |
| `MASTER_SKILLS_DIRS` | *(empty)* | Comma-separated folders of extra skills (one SKILL.md per subfolder), added to the built-in ones. |
| `MASTER_TOOL_FACTORIES` | *(empty)* | Comma-separated `module:function` paths; each function returns a list of tools added to the agent. Extension without editing the platform. |
| `MASTER_DEFAULT_SCOPE` | `local-laboratory` | Authorization boundary used when nothing else says otherwise. |
| `MASTER_SCOPE_HEADER` | *(empty)* | Header carrying the scope, read **only** when this is set: naming it means something in front has verified it and strips it from clients. |
| `MASTER_JSON_LOGS` | `False` | Structured logs for a collector instead of the readable line. |
| `MASTER_LOGS_ENDPOINT` | `True` | Whether `GET /logs` (the LOG tab) answers. Off in deployments where logs go to a collector. |
| `MASTER_MEMORY_SERVICE_URL` | *(empty)* | Memory service. Without it the conversation lives in RAM and dies with the process. |
| `MASTER_POSTGRES_DSN` | *(empty)* | Shared logs, deduplicated notifications and uploads shared by every replica. Without it all three are per replica. |
| `MASTER_KNOWLEDGE_AGENT_URL` | *(empty)* | One subagent, the short way. Ignored when MASTER_SUBAGENTS is set. |
| `MASTER_KNOWLEDGE_SERVICE_TOKEN` | *(empty)* | Service token of that subagent, for its extended card. |
| `MASTER_SUBAGENTS` | *(empty)* | Subagents as JSON: [{"name":"x","url":"http://..."}]. Their tokens go in MASTER_SUBAGENT_TOKEN_<NAME>. |
| `MASTER_PUBLIC_URL` | *(empty)* | How a subagent reaches this agent back, for push notifications. |
| `MASTER_PUSH_SECRET` | *(empty)* | Signs push notification tokens. Empty: no webhooks are registered, and none are accepted. |
| `MASTER_PUSH_WINDOW_SECONDS` | `3600` | Validity window of a push token; the previous one counts. |
| `MASTER_PROCESS_SERVICE_URL` | *(empty)* | Where durable processes live. Empty: the agent cannot start one. |
| `MASTER_VOICE_SERVICE_URL` | *(empty)* | The voice service, for its POST /transcribe. Empty: the video-analysis tool is not shown. |
| `MASTER_SUBAGENT_WAIT_SECONDS` | `60.0` | How long a turn waits before letting the outcome arrive by notification. |
| `MASTER_UPLOAD_DIR` | *(empty)* | Folder for uploads when there is no Postgres. Empty: OS temp. |
| `MASTER_UPLOAD_MAX_BYTES` | `209715200` | Largest accepted upload for /uploads. Bigger is refused. |
| `MASTER_UPLOAD_TTL_SECONDS` | `3600.0` | How long an uploaded file stays fetchable before it is swept away. |
| `MASTER_MEDIA_HOSTS` | *(empty)* | Comma-separated hosts analyze_video may download from even when they resolve to a private address. Everything else must be public. |
| `MASTER_VISION_MAX_VIDEO_BYTES` | `20971520` | Largest video sent whole to the vision model; above it, sampled frames are described instead. |
| `MASTER_MCP_SERVERS` | *(empty)* | External MCP servers as JSON: [{"name":"x","url":"http://...","token":"","allowed_tools":[],"approval":"never"}]. Tools are connected lazily on first use. |
| `MASTER_TOOLS_REQUIRING_APPROVAL` | `start_process` | Comma-separated tools that stop for a person's approval before they run. |
| `MASTER_MAX_MODEL_CALLS` | `15` | Model round trips one run may make before it has to answer. |
| `MASTER_MAX_TOOL_CALLS` | `50` | Tool invocations one run may make before it has to answer. |

### knowledge agent

| Variable | Default | What it decides |
|---|---|---|
| `KNOWLEDGE_BASE_URL` | `http://localhost:8200/` | The url this agent declares in its own card. |
| `KNOWLEDGE_SERVICE_TOKEN` | *(empty)* | Token that unlocks the extended card. Empty: nobody gets it. |
| `KNOWLEDGE_JSON_LOGS` | `False` | Structured logs for a collector instead of the readable line. |
| `KNOWLEDGE_LANGUAGE` | *(empty)* | Language of the answers. Empty: the language of the request. |
| `KNOWLEDGE_MCP_SERVERS` | *(empty)* | External MCP servers as JSON: [{"name":"x","url":"http://...","allowed_tools":[],"approval":"never"}]. Empty: no MCP tools. |
| `KNOWLEDGE_PUSH_ALLOWED_URLS` | *(empty)* | Comma-separated URL prefixes push notifications may be sent to. Empty: webhooks are refused. |
| `KNOWLEDGE_PUSH_ENCRYPTION_KEY` | *(empty)* | Fernet key encrypting webhook tokens at rest in the durable store. Empty: stored as given. |
| `KNOWLEDGE_TASK_STORE_DSN` | *(empty)* | Postgres for tasks and webhooks, so they survive a restart. Empty: kept in memory and lost with the process. |
| `OPENAI_BASE_URL` | `https://openrouter.ai/api/v1` | Where the model lives. Any OpenAI-compatible endpoint. |
| `OPENAI_API_KEY` | *(empty)* | Credential for that endpoint. |
| `OPENAI_CHAT_COMPLETION_MODEL` | `anthropic/claude-sonnet-5` | The model this agent reads its corpus with. |
| `KNOWLEDGE_FAKE_CLIENT` | `False` | Fixed answers without a model, and without a credential: for tests and for running the platform offline. |

### analysis agent

| Variable | Default | What it decides |
|---|---|---|
| `ANALYSIS_BASE_URL` | `http://localhost:8400/` | The url this agent declares in its own card. |
| `ANALYSIS_SERVICE_TOKEN` | *(empty)* | Token that unlocks the extended card. Empty: nobody gets it. |
| `ANALYSIS_JSON_LOGS` | `False` | Structured logs for a collector instead of the readable line. |
| `ANALYSIS_LANGUAGE` | *(empty)* | Language of the answers. Empty: the language of the request. |
| `ANALYSIS_MCP_SERVERS` | *(empty)* | External MCP servers as JSON: [{"name":"x","url":"http://...","allowed_tools":[],"approval":"never"}]. Empty: no MCP tools. |
| `ANALYSIS_PUSH_ALLOWED_URLS` | *(empty)* | Comma-separated URL prefixes push notifications may be sent to. Empty: webhooks are refused. |
| `ANALYSIS_PUSH_ENCRYPTION_KEY` | *(empty)* | Fernet key encrypting webhook tokens at rest in the durable store. Empty: stored as given. |
| `ANALYSIS_TASK_STORE_DSN` | *(empty)* | Postgres for tasks and webhooks, so they survive a restart. Empty: kept in memory and lost with the process. |
| `OPENAI_BASE_URL` | `https://openrouter.ai/api/v1` | Where the model lives. Any OpenAI-compatible endpoint. |
| `OPENAI_API_KEY` | *(empty)* | Credential for that endpoint. |
| `OPENAI_CHAT_COMPLETION_MODEL` | `anthropic/claude-sonnet-5` | The model this agent reasons with. It computes with tools, not with it. |
| `ANALYSIS_FAKE_CLIENT` | `False` | Fixed answers without a model, and without a credential: for tests and for running the platform offline. |

### memory service

| Variable | Default | What it decides |
|---|---|---|
| `MEMORY_POSTGRES_DSN` | `postgresql://127.0.0.1:5432/memory` | Durable transcripts, summaries and facts. Required. |
| `MEMORY_PORT` | `8100` | Where the service listens when started locally. |
| `MEMORY_POOL_MIN_SIZE` | `1` | Connections kept open. |
| `MEMORY_POOL_MAX_SIZE` | `10` | Connections at most. |
| `MEMORY_RETENTION_DAYS` | `0` | Days of inactivity after which a thread is forgotten. 0 = never. |
| `MEMORY_JSON_LOGS` | `False` | Structured logs for a collector instead of the readable line. |
| `MEMORY_MAX_REQUEST_BYTES` | `25000000` | Largest request body accepted, streamed or declared. |
| `MEMORY_SUMMARY_MODEL` | *(empty)* | Model for summaries. Empty means no compaction and no durable facts. |
| `MEMORY_SUMMARY_BASE_URL` | `https://openrouter.ai/api/v1` | Endpoint of the model that summarizes and extracts facts. |
| `MEMORY_SUMMARY_API_KEY` | *(empty)* | Credential for that endpoint. |
| `MEMORY_SUMMARY_LANGUAGE` | *(empty)* | Language summaries are written in. Empty: the language of the conversation. |
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
| `PROCESS_DEFAULT_SCOPE` | `local-laboratory` | Authorization boundary unless a trusted header says otherwise. |
| `PROCESS_SCOPE_HEADER` | *(empty)* | Header carrying the scope, read **only** when this is set: naming it means a proxy in front has verified it and strips it from clients. |
| `PROCESS_IDENTITY_HEADER` | *(empty)* | Header carrying the verified identity of whoever approves or answers, set by an authenticating proxy. Empty: the name typed in the panel is recorded as unverified. |
| `PROCESS_JSON_LOGS` | `False` | Structured logs for a collector instead of the readable line. |
| `PROCESS_POOL_MIN_SIZE` | `1` | Connections kept open. |
| `PROCESS_POOL_MAX_SIZE` | `10` | Connections at most. |
| `PROCESS_PORT` | `8300` | Where the service listens when started locally. |
| `PROCESS_ALLOWED_ORIGINS` | `http://localhost:3000, http://127.0.0.1:3000, http://localhost:3001, http://127.0.0.1:3001` | Which pages may read this API from a browser. Comma-separated. |
| `PROCESS_CORS_CREDENTIALS` | `False` | Accept the page's cookies (a single sign-on or load-balancer cookie in front). Needs named origins, and the frontend's API_CREDENTIALS=include. |
| `PROCESS_PUBLIC_URL` | `http://localhost:8300` | How a remote agent reaches this service back, for notifications. |
| `PROCESS_PUSH_SECRET` | *(empty)* | Signs notification tokens. Required when agents are configured: without it no agent step can be woken up. |
| `PROCESS_AGENTS` | `{}` | Agents a step may delegate to, as JSON: {"knowledge": "http://..."}. |
| `PROCESS_EXECUTOR_ID` | *(empty)* | Identity of this replica for durable recovery: unique per replica, stable across its restarts (a StatefulSet pod name). Empty: DBOS's single-server default. |
| `PROCESS_APP_VERSION` | *(empty)* | Version tag of the workflow code. Empty: DBOS derives it from the code, and only recovers workflows started by the same version. |
| `PROCESS_MAX_REQUEST_BYTES` | `1000000` | Largest request body accepted, streamed or declared. |

### voice service

| Variable | Default | What it decides |
|---|---|---|
| `VOICE_PORT` | `8500` | Where the service listens when started locally. |
| `VOICE_JSON_LOGS` | `False` | Structured logs for a collector instead of the readable line. |
| `VOICE_MASTER_AGENT_URL` | `http://127.0.0.1:8000` | The master agent, whose AG-UI endpoint answers each turn. |
| `VOICE_ALLOWED_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://127.0.0.1:3001` | Comma-separated pages allowed to open the voice WebSocket. Browsers do not apply CORS to WebSockets: this is the check that does. |
| `VOICE_TRANSCRIBE_MAX_BYTES` | `100000000` | Largest audio file `/transcribe` accepts. |
| `VOICE_STT_MODEL` | `small` | faster-whisper model size (tiny, base, small, medium, large-v3), on CPU. |
| `VOICE_STT_LANGUAGE` | *(empty)* | ISO 639-1 code of the speech. Empty: detected per turn. |
| `VOICE_TTS_LANG_CODE` | `a` | Kokoro language code: a (US English), b (UK English), e (Spanish), f (French), h (Hindi), i (Italian), j (Japanese), p (Portuguese), z (Chinese). |
| `VOICE_TTS_VOICE` | `af_heart` | Kokoro voice, matching the language (af_heart, if_sara, ...). |
| `VOICE_APPROVAL_NOTICE` | `That needs a person's approval, which I can't take by voice. Ask for it in the chat to approve it there.` | Said, in the TTS language, when a spoken request needs a person's approval: voice cannot ask, so the action is cancelled. |
| `VOICE_ANALYSIS_WINDOW_S` | `0.32` | Audio judged by the VAD at a time, in seconds. |
| `VOICE_SILENCE_THRESHOLD_S` | `0.6` | Trailing silence that ends a turn, in seconds. |

### scraping MCP server

| Variable | Default | What it decides |
|---|---|---|
| `SCRAPING_PORT` | `8600` | Where the server listens. |
| `SCRAPING_JSON_LOGS` | `False` | Structured logs for a collector instead of the readable line. |
| `SCRAPING_SERVER_HOSTS` | `scraping-mcp:*,localhost:*,127.0.0.1:*` | Host headers this server answers to (`host:*` for any port): the MCP DNS-rebinding protection. |
| `SCRAPING_ALLOW_PRIVATE_TARGETS` | `False` | Let `fetch_url` reach private, loopback and link-local addresses. Off: the scraper cannot be pointed at the platform's own network. |
| `SCRAPING_TARGET_HOSTS` | *(empty)* | Comma-separated host names `fetch_url` may reach even when they resolve to a private address (an intranet wiki, say). |
| `SCRAPING_MAX_CHARS` | `50000` | Longest Markdown handed back to the model, in characters. |
| `SCRAPING_TIMEOUT_SECONDS` | `45.0` | How long one page may take to render before it is abandoned. |
| `SCRAPING_MAX_SESSIONS` | `32` | Concurrent MCP sessions at most. |

### frontend

Read at request time, so the same image serves any environment. The
`NEXT_PUBLIC_*` names still work, as the fallback baked in at build time.

| Variable | Default | What it decides |
|---|---|---|
| `AGUI_URL` | `http://127.0.0.1:8000/agui` | Where the agent answers. Uploads and logs are derived from it. |
| `PROCESS_URL` | *(empty)* | The process service. Empty: the Instances panel says there is none. |
| `VOICE_URL` | *(empty)* | The voice WebSocket. Empty: no microphone button. |
| `API_CREDENTIALS` | `same-origin` | Cookies to the services: `include` behind a cookie-based proxy (single sign-on, load-balancer affinity). Needs the services' `*_CORS_CREDENTIALS`. |
| `PRODUCT_NAME` | `Agent Platform` | Name in the header and in the tab. |
| `PRODUCT_TAGLINE` | `an agent at work` | Line under the name. |
| `PRODUCT_DESCRIPTION` | `Chat, work plan, events and logs of a running agent.` | Description of the page. |
| `PRODUCT_DISCLAIMER` | `The agent can be wrong. Follow the plan and inspect the events.` | Footer line: a caveat about quality. |
| `PRODUCT_AI_DISCLOSURE` | `You are interacting with an artificial intelligence system, not a human.` | The statement that the user is talking to an AI system (EU AI Act, art. 50). |
| `PRODUCT_LOCALE` | `en` | `lang` of the page. |
| `PRODUCT_MONOGRAM` | `a/` | The characters in the header's badge. |
| `PRODUCT_BADGES` | *(empty)* | Comma-separated labels in the header. Empty: none. |
| `EMPTY_EYEBROW` | `from the request to the result` | Small line above the empty conversation's headline. |
| `EMPTY_HEADLINE` | `An agent at work.` | Headline of the empty conversation. |
| `EMPTY_SUBHEAD` | `Every step, visible.` | Second line of the headline. |
| `EMPTY_BODY` | `Ask for a comparison: follow the reasoning, the tools and the final table. The plan shows where we are.` | Paragraph under the headline. |

<!-- env-table:end -->

## Cartelle

| Cartella | Ruolo |
|---|---|
| `demo-master-agent` | agente principale, endpoint AG-UI su SSE |
| `demo-knowledge-agent` | sottoagente di knowledge base, esposto via A2A |
| `demo-analysis-agent` | sottoagente che misura e confronta numeri, via A2A |
| `demo-memory-service` | memoria delle conversazioni: transcript, riassunti, fatti, ricordi |
| `demo-process-service` | processi durevoli: definizioni versionate, istanze |
| `demo-voice-service` | voce in tempo reale, con modelli locali |
| `demo-scraping-mcp` | server MCP che legge pagine web in un browser headless |
| `demo-frontend` | interfaccia Next.js |
| `packages/platform-core` | il codice che i servizi Python condividono |
| `demo-infra` | compose, contratti, manifest, documentazione (questa cartella) |

## I servizi in piedi

| Servizio | Porta sull'host | A cosa serve |
|---|---|---|
| `frontend` | 3000 | l'interfaccia |
| `master-agent` | 8000 | AG-UI su SSE, upload, `/logs`, `/transparency` |
| `process-service` | 8300 | processi durevoli, istanze su Postgres |
| `voice-service` | 8500 | il WebSocket del microfono |
| `knowledge-agent` | loopback 8200 | sottoagente A2A: legge un corpus |
| `analysis-agent` | loopback 8400 | sottoagente A2A: misura e confronta |
| `memory-service` | — | memoria conversazionale, interna |
| `scraping-mcp` | — | lo scraper, su una rete sua con il solo knowledge agent |
| `postgres` | loopback | **l'unico database**: un database e un ruolo per servizio |
| `postgres-init` | — | un job: crea ruoli e database a ogni `up`, poi esce |

Le porte stanno su `BIND_ADDRESS`, di default `127.0.0.1`: questa macchina e
basta. `0.0.0.0` espone la piattaforma alla rete, e la piattaforma non ha
autenticazione propria. La memoria e lo scraper **non pubblicano porte**. Lo
scraper apre pagine scelte da estranei: sta su una rete dove c'e' solo il
knowledge agent, e da li' non raggiunge ne' Postgres ne' gli altri servizi.

Ogni container gira come utente non-root, senza capability e con
`no-new-privileges`; tutti tranne lo scraper, il cui Chromium scrive profilo e
cache, con il filesystem in sola lettura.

**Due sottoagenti, non uno.** Con un sottoagente solo l'instradamento non
esiste: qualunque domanda va all'unico che c'e'. `knowledge` legge quello che
qualcuno ha scritto, `analysis` lavora sui numeri che riceve nella richiesta;
sono due domini davvero diversi, ed entrambi sono **esempi da sostituire**.

## Avvio con Docker

```bash
cp .env.example .env        # la API key, e un valore per ogni segreto
docker compose up --build   # http://localhost:3000
```

Senza una API key: `FAKE_MODEL=true` nel `.env`, e ogni agente risponde con un
testo fisso. `tools/smoke.sh` fa proprio questo, e controlla che il core
risponda come lo usa l'interfaccia.

## Avvio in sviluppo

Più rapido per iterare, niente rebuild di immagini:

```bash
cd ../demo-master-agent && uv run python -m master_agent    # :8000
cd ../demo-frontend && npm run dev                  # :3000
```

Senza LLM e senza rete:

```bash
cd ../demo-master-agent && MASTER_FAKE_CLIENT=true uv run python -m master_agent
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

La risposta finale e' resa come Markdown mentre arriva; il pulsante `stop`
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

Nella timeline compaiono due voci `knowledge`, e finché la prima è `running`
mentre l'altra è già `done` stai guardando il parallelismo mentre accade.
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

## Postgres

Un solo server, **un database e un ruolo per servizio**:

| Database | Ruolo | Di chi |
|---|---|---|
| `master` | `master_svc` | lo stato che il master condivide fra le repliche: log, notifiche gia' arrivate, upload |
| `memory` | `memory_svc` | trascritti, riassunti, fatti, ricordi (con pgvector) |
| `processes` | `process_svc` | le istanze durevoli, e le tabelle di DBOS |
| `agents` | `agents_svc` | task e webhook A2A dei due sottoagenti |

Ogni servizio si collega con il proprio ruolo, possiede il proprio database e
non puo' aprire quello degli altri. Il superutente del `.env` lo usa solo il job
`postgres-init`, che esegue `postgres/provision.sh` a **ogni** `up`: crea quello
che manca e applica le password del `.env`, quindi una password ruotata si
cambia nel `.env` e con un altro `up`. Chi vuole due server cambia un DSN: ogni
servizio ha il proprio e non sa niente degli altri.

```bash
docker compose up -d postgres postgres-init
docker compose exec postgres psql -U "$POSTGRES_USER" -l   # master, memory, processes, agents
```

I dati stanno nel volume `agent-platform_postgres-data`, e sopravvivono a
`docker compose down`; per azzerarli serve `down -v`. `POSTGRES_VOLUME` fa
usare un volume con un altro nome: portare i dati da una versione precedente,
che aveva uno schema diverso, e' spiegato nel [CHANGELOG](../CHANGELOG.md).

Dall'host la porta e' pubblicata **solo su loopback**
(`POSTGRES_HOST_PORT`, di default 5432: si cambia se la macchina ha gia' un
Postgres nativo). Dagli altri container l'indirizzo e' `postgres:5432`.

Ogni servizio applica le proprie **migrazioni all'avvio**: numerate,
idempotenti, registrate in `schema_migrations`, e serializzate da un lock di
Postgres, cosi' due repliche che partono insieme non le applicano due volte. Un
fork non deve trovare uno script ed eseguirlo a mano prima che il servizio
parta.

### C'era MongoDB, e non c'e' piu'

La memoria teneva i trascritti in Mongo, con i messaggi impacchettati in
**documenti bucket** — un pattern che esiste per far somigliare un database a
documenti a una tabella. In Postgres una riga per messaggio e' gia' la forma
economica, e l'append e' una sola istruzione invece di una ricerca del bucket
non pieno con ritentativo per lo scrittore perdente.

Il modello a documenti resta la risposta giusta per entita' autocontenute lette
tutte insieme, o per scritture che superano quello che regge un nodo. Un turno
di conversazione non e' nessuna delle due: e' append-only, uniforme, letto a
coda. E tre sistemi al 99,9% fanno 99,7% combinato — 26 ore di disservizio
l'anno invece di 8,7.

## C'era Redis, e non c'e' piu'

Redis teneva quattro cose: la coda calda della memoria, l'indice semantico, il
lock di compattazione, e lo stream dei log operativi condiviso fra le repliche.
Erano quattro scelte ragionevoli, e tre di esse in Postgres diventano piu'
semplici, non solo diverse.

| Cosa faceva | Adesso | Cosa cambia |
| --- | --- | --- |
| coda calda con TTL | una query indicizzata | sparisce la cache **e** il ragionamento sulle due copie |
| indice semantico | `pgvector` | l'indice sta accanto ai trascritti da cui si ricostruisce |
| lock di compattazione | `pg_try_advisory_lock` | niente lease da far scadere, niente Lua per rilasciare il proprio |
| log fra repliche | una tabella, potata | il cursore c'era gia' |

**Dove Redis restava la scelta migliore**: lo stream dei log. Redis Streams con
`MAXLEN` e' fatto esattamente per quello, e una tabella potata a ogni scrittura
e' un compromesso. Il punto e' che quel tab e' una **comodita' di laboratorio**: in un
deployment vero i log vanno a un collector (c'e' gia' OTLP), e tenere in piedi
un secondo datastore per una finestra di 500 righe non regge il conto.

**Quando tornerebbe la risposta giusta**: p99 sotto il millisecondo, fanout a
molti consumatori, o un volume di log che una tabella non regge. Sono condizioni
misurabili, e nessuna e' vera qui.

## I log operativi con piu' di una replica

Il tab LOG legge `GET /logs` con un cursore. Il collettore in RAM di ogni
processo resta, ma quando `MASTER_POSTGRES_DSN` e' configurato le righe finiscono
anche in una tabella condivisa, e l'endpoint legge da li': due repliche
raccontano una storia sola invece di meta' ciascuna. Il numero di sequenza
arriva dal database, non dal processo: due repliche che numerassero le proprie
righe si scontrerebbero alla prima lettura.

Il cursore e' **opaco**: il client lo rimanda com'e'. `dropped` dice quante
righe sono uscite dalla finestra fra due letture, cosi' un buco non si legge
come continuita'.

Senza database non si rompe niente: ogni replica mostra le proprie righe, e lo
dichiara nei log all'avvio.

## Sonde, log e tracce

`/health/live` dice se il processo e' bloccato e non chiede niente a nessuno:
una sonda di liveness che interrogasse i database trasformerebbe un guasto in
un loop di riavvii. `/health/ready` dice se il servizio puo' servire, ed e'
quella che il compose usa per `service_healthy`.

Cosa conta come dipendenza cambia per servizio: per il master il servizio di
memoria si' e i sottoagenti no -- uno giu' degrada un turno e il tool lo
dichiara, mentre senza memoria ogni thread ripartirebbe da zero in silenzio.
Per la memoria Postgres e' obbligatorio, ed e' l'unica dipendenza rimasta: non
c'e' piu' una cache da cui degradare.

Con `JSON_LOGS=true` le righe diventano oggetti con servizio, logger e thread
del turno. Con `OTEL_EXPORTER_OTLP_ENDPOINT` tracce e metriche attraversano
tutti i servizi, chiamate al modello comprese; senza, non si esporta niente e
non si rompe niente.

## Immagini e deploy

Le immagini di base sono pinnate per **digest**: un tag si sposta, un digest
no, e due build della stessa riga devono partire dalla stessa base. Nessun
processo gira come root, e tutti i servizi tranne lo scraper reggono
`readOnlyRootFilesystem` -- i servizi Python chiamano uvicorn dal venv, perche'
`uv run` vuole una cache scrivibile.

Il frontend legge la propria configurazione **a runtime**: la stessa immagine
serve staging e produzione cambiando `AGUI_URL` e `PRODUCT_NAME`, senza
ricostruire niente. Prima quelle variabili finivano nel bundle a build time.

I manifest Kubernetes stanno in `deploy/`, con due repliche di default sul
master agent, sulla memoria, sui processi e sul frontend: non e'
dimensionamento, e' la sonda che fa emergere subito una regressione dello
stato di processo. Postgres resta fuori, e il perche' e' scritto in
[`deploy/README.md`](deploy/README.md).

A ogni tag `vX.Y.Z` il workflow `release` pubblica le immagini su GitHub
Container Registry, con SBOM e provenance allegati.

## Contratti fra i servizi

`contracts/` tiene i campioni versionati di cio' che passa da un servizio all'altro:
l'artefatto A2A del sottoagente, lo stato condiviso AG-UI, le risposte dell'API
di memoria. Ogni servizio li carica dai propri test invece di tenerne una copia che
diverge, e quando un campione non torna il fallimento nomina il contratto, la
differenza e **chi lo consuma**.

Esiste per una rottura vera: rinominare l'artefatto da `scheda` a `briefing` ha
toccato tre servizi, ed e' passata liscia solo perche' erano aperti nella
stessa sessione. In un fork non lo sono.

Dettagli e regole di modifica in `contracts/README.md`.

## Test

Ogni servizio ha la sua suite, e nessun test chiama un modello a pagamento
(`OPENAI_API_KEY=` vuota). La memoria, i processi, il master e platform-core
hanno anche test contro un Postgres vero: una conversazione e un'istanza sono
righe che devono sopravvivere al processo che le ha scritte, e un finto in
memoria non proverebbe niente al riguardo. Senza il DSN quei test si saltano;
con il DSN ognuno si crea il proprio database `_test`, e non tocca quello di un
servizio in esecuzione. Come impostarli e' in
[CONTRIBUTING.md](../CONTRIBUTING.md).

Sopra le suite, `tools/smoke.sh` avvia il core con il modello finto e lo
interroga come fa l'interfaccia. La CI esegue tutto a ogni pull request, e in
piu': i manifest validati con kubeconform, gli script con shellcheck, le
tabelle d'ambiente confrontate con il codice, le immagini costruite e
scansionate con Trivy.

`node_modules` contiene binari specifici della piattaforma: se alterni Windows e
WSL sulla stessa cartella, rilancia `npm install` dopo ogni cambio.

## Cosa e' sperimentale, e cosa viene dopo

La voce funziona ma e' un work in progress: il modello che genera la risposta
parlata sta per cambiare, e le misure di latenza sono di una macchina sola.

I lavori futuri stanno in §11 della [spec](docs/specs/2026-09-07-agui-lab-design.md):
registry degli agenti con ricerca semantica, decadimento dei fatti,
reindicizzazione dei ricordi, un backoffice per le run passate.
