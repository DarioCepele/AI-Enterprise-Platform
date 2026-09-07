Esegui gli step in ordine, uno alla volta. Fermati a fine task.
Non anticipare task successivi. Non inventare API: se una firma non e' nel
testo, verificala nel package installato prima di usarla.
Alla fine incolla l'output reale dei comandi di test, non un riassunto.

## Vincoli di progetto (validi per ogni task)

- Python 3.12. Gestione dipendenze con `uv`.
- `agent-framework-core==1.17.0`, `agent-framework-ag-ui>=1.2.2`, `agent-framework-openai`. **Non usare 1.5.x.**
- Import sotto namespace `agent_framework.*` (`agent_framework.openai`, `agent_framework.ag_ui`). I moduli top-level `agent_framework_openai` / `agent_framework_a2a` sono la vecchia forma: non usarli.
- Le classi di contenuto per-variante **non esistono in 1.17**. Usare `Content` con le factory: `Content.from_text(...)`, `Content.from_function_call(...)`, `Content.from_text_reasoning(...)`. Se vedi `TextContent` in un esempio online, quell'esempio è per una versione precedente.
- Nessuna chiamata LLM reale nei test: si usa il fake chat client della Task 2.
- Il JSON di AG-UI è **camelCase** (`threadId`, `runId`, `messageId`, `delta`), anche se Python usa snake_case.
- `state_update(text, *, state, tool_result)` non espone i suoi payload come attributi di `Content`: li deposita in `Content.additional_properties` sotto `__ag_ui_tool_result_state__` (dict) e `__ag_ui_tool_result_display__` (**stringa JSON**, non dict). Di conseguenza il `content` di `TOOL_CALL_RESULT` che arriva al frontend è una stringa JSON da parsare.
- Dopo ogni `TOOL_CALL_RESULT` prodotto da `state_update`, l'endpoint emette uno `STATE_SNAPSHOT` deterministico. Senza chiamate a tool non arriva alcun evento di stato: con il fake client base il pannello stato resta legittimamente vuoto.
- **Ogni tool call è avvolta da una coppia `TEXT_MESSAGE_START` / `TEXT_MESSAGE_END` senza `TEXT_MESSAGE_CONTENT` in mezzo.** Verificato sul filo. Il reducer deve scartare i messaggi rimasti vuoti alla chiusura, altrimenti la chat mostra una bolla vuota per ogni chiamata a tool.
- Lo `state` passato a `state_update` è fuso con semantica `dict.update`: le chiavi di primo livello vengono **sostituite**, non fuse in profondità. Due tool che scrivono la stessa chiave si sovrascrivono a vicenda. Rilevante dalla tappa 2 in poi.
- `add_agent_framework_fastapi_endpoint(..., allow_origins=[...])` **accetta il parametro e lo ignora**: in 1.2.2 la sua docstring dice *"allow_origins: CORS origins (not yet implemented)"*. Il CORS va aggiunto a mano con `CORSMiddleware`, altrimenti il browser blocca il frontend senza che nessun test lato server se ne accorga.
- Il progetto ha un `[build-system]` hatchling con `packages = ["src/demo"]`: senza, `pythonpath` in pytest basta ai test ma `python -m demo` e l'immagine docker non trovano il pacchetto.
- `Message` **non accetta** `text=` in 1.17: `Message(role=..., text="x")` solleva `TypeError: unexpected keyword argument 'text'`. Si costruisce con `Message(role=..., contents=[Content.from_text("x")])`. L'attributo `.text` esiste in lettura, non in scrittura.
- Un chat client che deve eseguire tool **deve** ereditare da `agent_framework._tools.FunctionInvocationLayer` oltre che da `BaseChatClient`, nell'ordine `class X(FunctionInvocationLayer, BaseChatClient)`. Senza, `Agent` logga *"The provided chat client does not support function invoking"* e i tool non vengono mai eseguiti.
- Nessuna autenticazione in questa tappa. Niente MSAL, niente OBO.
- Struttura **multi-repo**: `demo-master-agent`, `demo-frontend`, `demo-infra` sono repo git distinti e fratelli dentro `C:\project\demo` (in WSL: `/mnt/c/project/demo`). Ogni task committa nel proprio repo. Non esiste un repo che li contiene tutti.
- Ogni repo deployabile ha il suo `Dockerfile`; `demo-infra/compose.yaml` li costruisce da percorsi fratelli. In sviluppo si gira nativi, i container servono alla verifica d'insieme.

---

### Task 9: Immagine del frontend, compose completo, README

**Files:**
- Create: `demo-frontend/Dockerfile`
- Create: `demo-frontend/.dockerignore`
- Modify: `demo-infra/compose.yaml`
- Create: `demo-infra/README.md`

**Interfaces:**
- Consumes: tutto quanto sopra
- Produces: `docker compose up` che alza l'intera demo

**Attenzione a due cose, entrambe fonte di errori silenziosi:**

1. Le variabili `NEXT_PUBLIC_*` sono **incorporate al momento della build**, non lette a runtime. Vanno passate come `ARG`, non come `environment`.
2. La fetch verso l'agente parte dal **browser**, non dal container del frontend. L'URL deve quindi essere `http://localhost:8000/agui` — il nome di servizio compose `master-agent` non è risolvibile dal browser.

- [ ] **Step 1: Scrivere `demo-frontend/.dockerignore`**

```gitignore
node_modules/
.next/
.git/
.env.local
```

- [ ] **Step 2: Abilitare l'output standalone in `demo-frontend/next.config.ts`**

```typescript
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Produce un bundle autosufficiente: immagine finale senza node_modules.
  output: "standalone",
};

export default nextConfig;
```

- [ ] **Step 3: Scrivere `demo-frontend/Dockerfile`**

```dockerfile
FROM node:22-alpine AS builder
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# NEXT_PUBLIC_* viene incorporato qui, in build: a runtime sarebbe troppo tardi.
ARG NEXT_PUBLIC_AGUI_URL=http://localhost:8000/agui
ENV NEXT_PUBLIC_AGUI_URL=$NEXT_PUBLIC_AGUI_URL
RUN npm run build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production

COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

EXPOSE 3000
CMD ["node", "server.js"]
```

- [ ] **Step 4: Aggiungere il servizio frontend a `demo-infra/compose.yaml`**

Sotto `master-agent`, allo stesso livello di indentazione:

```yaml
  frontend:
    build:
      context: ../demo-frontend
      args:
        # URL usato dal browser, non dalla rete interna di compose.
        NEXT_PUBLIC_AGUI_URL: http://localhost:8000/agui
    ports:
      - "3000:3000"
    depends_on:
      master-agent:
        condition: service_healthy
```

- [ ] **Step 5: Alzare tutto e verificare**

```bash
cd /mnt/c/project/demo/demo-infra
docker compose up -d --build
sleep 10
docker compose ps
```

Expected: `master-agent` e `frontend` entrambi `running`, il primo `healthy`.

Aprire `http://localhost:3000`, scrivere "ciao".

Expected: la risposta compare progressivamente, l'inspector si popola, nessun errore CORS in console.

```bash
docker compose down
```

- [ ] **Step 6: Eseguire l'intera suite di test**

```bash
cd /mnt/c/project/demo/demo-master-agent && uv run pytest -v
cd /mnt/c/project/demo/demo-frontend && npm test
```

Expected: tutti verdi. Riportare il conteggio effettivo.

- [ ] **Step 7: Verifica con un LLM reale e il tool**

Configurare `demo-infra/.env` con una API key vera, poi in sviluppo nativo:

```bash
cd /mnt/c/project/demo/demo-master-agent && uv run python -m demo
cd /mnt/c/project/demo/demo-frontend && npm run dev
```

Su `http://localhost:3000` scrivere: *"Confronta in tabella i vantaggi di SSE e WebSocket"*.

Expected:
- l'agente chiama `ui_table`;
- l'inspector mostra `TOOL_CALL_START`, `TOOL_CALL_ARGS`, `TOOL_CALL_END`, `TOOL_CALL_RESULT`;
- il pannello stato mostra `artifacts` popolato;
- **nessuna bolla vuota** in chat prima della risposta.

Sequenza di eventi attesa, verificata sul filo con un client di prova:

```
RUN_STARTED
TEXT_MESSAGE_START            <- messaggio vuoto che avvolge la tool call
TOOL_CALL_START  name=ui_table
TOOL_CALL_ARGS   delta='{"title": ..., "columns": [...], "rows": [...]}'
TOOL_CALL_END
TOOL_CALL_RESULT content='{"component": "ui-table", ...}'   <- stringa JSON
STATE_SNAPSHOT   snapshot={"artifacts": [{"component": "ui-table", ...}]}
TEXT_MESSAGE_END
TEXT_MESSAGE_START
TEXT_MESSAGE_CONTENT delta='...'
TEXT_MESSAGE_END
MESSAGES_SNAPSHOT
RUN_FINISHED
```

Se il modello non chiama il tool: è un limite del modello, non un bug. Annotarlo nel README e riprovare con un modello più capace. Su LM Studio è l'esito atteso con modelli piccoli.

- [ ] **Step 8: Scrivere `demo-infra/README.md`**

````markdown
# Laboratorio AG-UI

Demo locale di un'interfaccia agentica: chat in streaming, stato condiviso ed
event inspector, tutti alimentati da un solo stream SSE in protocollo AG-UI.

Design: `docs/specs/2026-09-07-agui-lab-design.md`

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

Senza LLM e senza rete: `DEMO_FAKE_CLIENT=true uv run python -m demo`

## Test

```bash
cd ../demo-master-agent && uv run pytest -v
cd ../demo-frontend && npm test
```

## Stato

Tappa 1 (walking skeleton) completata. Mancano il piano di lavoro, le skill e la
tabella comparativa (tappa 2), e i sottoagenti A2A (tappa 3).
````

- [ ] **Step 9: Commit nei due repo**

```bash
cd /mnt/c/project/demo/demo-frontend
git add Dockerfile .dockerignore next.config.ts
git commit -m "feat: immagine docker del frontend"

cd /mnt/c/project/demo/demo-infra
git add compose.yaml README.md
git commit -m "feat: compose completo e README"
```

---
