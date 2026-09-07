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
- Un chat client che deve eseguire tool **deve** ereditare da `agent_framework._tools.FunctionInvocationLayer` oltre che da `BaseChatClient`, nell'ordine `class X(FunctionInvocationLayer, BaseChatClient)`. Senza, `Agent` logga *"The provided chat client does not support function invoking"* e i tool non vengono mai eseguiti.
- Nessuna autenticazione in questa tappa. Niente MSAL, niente OBO.
- Directory di lavoro: `C:\project\demo` (in WSL: `/mnt/c/project/demo`).

---

### Task 8: README e verifica con LLM reale

**Files:**
- Create: `demo/README.md`
- Create: `demo/.gitignore`

**Interfaces:**
- Consumes: tutto quanto sopra
- Produces: istruzioni di avvio

- [ ] **Step 1: Scrivere `demo/.gitignore`**

```gitignore
# Python
__pycache__/
*.py[cod]
.venv/
.pytest_cache/
backend/.env

# Node
node_modules/
.next/
frontend/.env.local

# OS
.DS_Store
```

- [ ] **Step 2: Scrivere `demo/README.md`**

````markdown
# Laboratorio AG-UI

Demo locale di un'interfaccia agentica: chat in streaming, stato condiviso
ed event inspector, tutti alimentati da un solo stream SSE in protocollo AG-UI.

Design: `docs/specs/2026-09-07-agui-lab-design.md`

## Requisiti

- Python 3.12 e [uv](https://docs.astral.sh/uv/)
- Node 20+
- Una API key OpenRouter, oppure LM Studio in ascolto su `localhost:1234`

## Avvio

```bash
# backend
cd backend
cp .env.example .env        # inserire la propria API key
uv run python -m demo       # http://127.0.0.1:8000

# frontend, in un altro terminale
cd frontend
npm install
npm run dev                 # http://localhost:3000
```

Per lavorare senza LLM e senza rete: `DEMO_FAKE_CLIENT=true uv run python -m demo`

## Test

```bash
cd backend && uv run pytest -v
cd frontend && npm test
```

## Stato

Tappa 1 (walking skeleton) completata. Mancano il piano di lavoro, le skill,
la tabella comparativa (tappa 2) e i sottoagenti A2A (tappa 3).
````

- [ ] **Step 3: Eseguire l'intera suite di test**

```bash
cd /mnt/c/project/demo/backend && uv run pytest -v
cd /mnt/c/project/demo/frontend && npm test
```

Expected: tutti verdi. Riportare il conteggio effettivo.

- [ ] **Step 4: Verifica con un LLM reale e il tool**

Configurare `.env` con una API key vera, poi:

```bash
cd /mnt/c/project/demo/backend && uv run python -m demo
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

- [ ] **Step 5: Commit**

```bash
cd /mnt/c/project/demo
git add README.md .gitignore
git commit -m "docs: README e istruzioni di avvio"
```

---
