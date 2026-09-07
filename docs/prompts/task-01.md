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

### Task 1: Repo `demo-master-agent` e configurazione — ✅ GIÀ COMPLETATO

> Eseguito e committato prima della migrazione a multi-repo. La sua storia vive in
> `demo-master-agent` (`62c800c` scaffold, `530bdf6` migrazione). Resta qui per
> riferimento: **non rieseguirlo**, si riparte dalla Task 2.

**Files:**
- Create: `demo-master-agent/pyproject.toml`
- Create: `demo-master-agent/.env.example`
- Create: `demo-master-agent/.gitignore`
- Create: `demo-master-agent/src/demo/__init__.py`
- Create: `demo-master-agent/src/demo/config.py`
- Test: `demo-master-agent/tests/test_config.py`

**Interfaces:**
- Consumes: niente (prima task)
- Produces: `demo.config.Settings` (dataclass con `base_url: str`, `api_key: str`, `model: str`, `use_fake_client: bool`), `demo.config.get_settings() -> Settings`

**Stato finale a valle della migrazione**, per chi arriva adesso:

```
demo-master-agent/          <- repo git proprio, branch main
  .gitignore                __pycache__/, .venv/, .pytest_cache/, .env
  .env.example
  pyproject.toml            name = "demo-master-agent"
  uv.lock
  src/demo/config.py
  tests/test_config.py
```

`pyproject.toml` contiene:

```toml
[project]
name = "demo-master-agent"
version = "0.1.0"
description = "Master agent del laboratorio AG-UI: AG-UI su SSE + tool nativi"
requires-python = ">=3.12"
dependencies = [
    "agent-framework-ag-ui>=1.2.2",
    "agent-framework-core==1.17.0",
    "agent-framework-openai>=1.14.2",
    "fastapi>=0.139.2",
    "python-dotenv>=1.2.3",
    "uvicorn>=0.52.4",
]

[dependency-groups]
dev = [
    "httpx>=0.28.1",
    "pytest>=9.1.1",
    "pytest-asyncio>=1.4.0",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
pythonpath = ["src"]
```

- [x] **Verifica dello stato** (l'unico passo da rifare se hai dubbi)

Run: `cd /mnt/c/project/demo/demo-master-agent && uv run pytest -v`
Expected: PASS (2 test)

---
