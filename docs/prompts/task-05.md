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

### Task 5: Immagine del master agent e repo `demo-infra`

Il primo dei due task di containerizzazione. Qui l'agente diventa un'immagine e
`demo-infra` acquisisce il `compose.yaml` che, per ora, alza un servizio solo.

**Files:**
- Create: `demo-master-agent/Dockerfile`
- Create: `demo-master-agent/.dockerignore`
- Create: `demo-infra/compose.yaml`
- Create: `demo-infra/.env.example`

**Interfaces:**
- Consumes: `demo.server.app.create_app` (Task 4)
- Produces: servizio compose `master-agent`, in ascolto su `8000`

- [ ] **Step 1: Scrivere `demo-master-agent/.dockerignore`**

```gitignore
.venv/
__pycache__/
.pytest_cache/
.git/
.env
tests/
```

- [ ] **Step 2: Scrivere `demo-master-agent/Dockerfile`**

```dockerfile
# uv fornisce l'immagine con il gestore gia' dentro: niente pip, niente wheel a mano.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# Prima i soli manifest: cosi' il layer delle dipendenze si invalida
# solo quando cambiano le dipendenze, non a ogni modifica del codice.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev

EXPOSE 8000

# host 0.0.0.0: dentro un container 127.0.0.1 non e' raggiungibile da fuori.
CMD ["uv", "run", "uvicorn", "demo.server.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 3: Costruire l'immagine e verificare che l'app risponda**

```bash
cd /mnt/c/project/demo/demo-master-agent
docker build -t demo-master-agent:dev .
docker run --rm -d --name ma-test -p 8000:8000 -e DEMO_FAKE_CLIENT=true demo-master-agent:dev
sleep 3
curl -s http://127.0.0.1:8000/health
docker rm -f ma-test
```

Expected: `{"status":"ok"}`

- [ ] **Step 4: Scrivere `demo-infra/.env.example`**

```bash
# Copiare in .env e riempire. compose lo legge automaticamente.
OPENAI_BASE_URL=https://openrouter.ai/api/v1
OPENAI_API_KEY=sk-or-v1-...
OPENAI_CHAT_COMPLETION_MODEL=anthropic/claude-sonnet-5

# true = nessuna chiamata LLM
DEMO_FAKE_CLIENT=false
```

- [ ] **Step 5: Scrivere `demo-infra/compose.yaml`**

```yaml
# I servizi si costruiscono dai repo fratelli: i tre repo devono stare
# nella stessa cartella padre perche' questi context relativi funzionino.
services:
  master-agent:
    build: ../demo-master-agent
    ports:
      - "8000:8000"
    environment:
      OPENAI_BASE_URL: ${OPENAI_BASE_URL}
      OPENAI_API_KEY: ${OPENAI_API_KEY}
      OPENAI_CHAT_COMPLETION_MODEL: ${OPENAI_CHAT_COMPLETION_MODEL}
      DEMO_FAKE_CLIENT: ${DEMO_FAKE_CLIENT:-false}
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]
      interval: 10s
      timeout: 3s
      retries: 5
```

- [ ] **Step 6: Alzare il servizio via compose e verificarlo**

```bash
cd /mnt/c/project/demo/demo-infra
cp .env.example .env
docker compose up -d --build
sleep 5
docker compose ps
curl -s http://127.0.0.1:8000/health
docker compose down
```

Expected: `master-agent` in stato `running (healthy)`, e `{"status":"ok"}` dal curl.

- [ ] **Step 7: Commit nei due repo**

```bash
cd /mnt/c/project/demo/demo-master-agent
git add Dockerfile .dockerignore
git commit -m "feat: immagine docker del master agent"

cd /mnt/c/project/demo/demo-infra
git add compose.yaml .env.example
git commit -m "feat: compose con il servizio master-agent"
```

---
