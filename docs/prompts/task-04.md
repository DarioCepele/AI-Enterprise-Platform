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
- Struttura **multi-repo**: `demo-master-agent`, `demo-frontend`, `demo-infra` sono repo git distinti e fratelli dentro `C:\project\demo` (in WSL: `/mnt/c/project/demo`). Ogni task committa nel proprio repo. Non esiste un repo che li contiene tutti.
- Ogni repo deployabile ha il suo `Dockerfile`; `demo-infra/compose.yaml` li costruisce da percorsi fratelli. In sviluppo si gira nativi, i container servono alla verifica d'insieme.

---

### Task 4: Master agent e app FastAPI

**Files:**
- Create: `demo-master-agent/src/demo/agents/__init__.py`
- Create: `demo-master-agent/src/demo/agents/master.py`
- Create: `demo-master-agent/src/demo/server/__init__.py`
- Create: `demo-master-agent/src/demo/server/app.py`
- Create: `demo-master-agent/src/demo/__main__.py`
- Test: `demo-master-agent/tests/test_agui_stream.py`
- Test: `demo-master-agent/tests/conftest.py`

**Interfaces:**
- Consumes: `demo.config.get_settings`, `demo.chat_clients.fake.FakeStreamingChatClient` e `ToolCallingFakeClient`, `demo.tools.ui_tools.get_tools`
- Produces: `demo.agents.master.build_master_agent(chat_client=None) -> Agent`, `demo.server.app.create_app(agent=None) -> FastAPI`

L'endpoint AG-UI si monta con una sola chiamata. Firma reale verificata:

```
add_agent_framework_fastapi_endpoint(app, agent, path='/', state_schema=None,
    predict_state_config=None, allow_origins=None, default_state=None, tags=None,
    dependencies=None, snapshot_store=None, snapshot_scope_resolver=None,
    checkpoint_storage=None, keepalive_seconds=15, a2ui_config=None) -> None
```

- [ ] **Step 1: Scrivere `demo-master-agent/tests/conftest.py`**

```python
import pytest

from demo.agents.master import build_master_agent
from demo.chat_clients.fake import FakeStreamingChatClient, ToolCallingFakeClient
from demo.server.app import create_app


@pytest.fixture
def app():
    """App con un client che emette solo testo."""
    agent = build_master_agent(
        chat_client=FakeStreamingChatClient(chunks=["ciao ", "mondo"])
    )
    return create_app(agent=agent)


@pytest.fixture
def tool_app():
    """App con un client che al primo giro chiama ui_table."""
    agent = build_master_agent(
        chat_client=ToolCallingFakeClient(
            tool_name="ui_table",
            tool_args={
                "title": "Confronto",
                "columns": ["Tema", "A", "B"],
                "rows": [["Copertura", "vuoto", "pieno"]],
            },
            final_text="Ecco il confronto.",
        )
    )
    return create_app(agent=agent)
```

- [ ] **Step 2: Scrivere il test di integrazione**

`demo-master-agent/tests/test_agui_stream.py`:

```python
"""Verifica la sequenza di eventi AG-UI prodotta da una run."""
import json

import httpx
import pytest

REQUEST = {
    "threadId": "t1",
    "runId": "r1",
    "state": {},
    "messages": [{"id": "m1", "role": "user", "content": "ciao"}],
    "tools": [],
    "context": [],
    "forwardedProps": {},
}


async def collect_events(app) -> list[dict]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream(
            "POST", "/agui", json=REQUEST, headers={"Accept": "text/event-stream"}
        ) as response:
            assert response.status_code == 200
            events = []
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[len("data: "):]))
            return events


@pytest.mark.asyncio
async def test_run_starts_and_finishes(app):
    events = await collect_events(app)
    types = [e["type"] for e in events]

    assert types[0] == "RUN_STARTED"
    assert types[-1] in {"RUN_FINISHED", "RUN_ERROR"}
    assert types.count("RUN_STARTED") == 1


@pytest.mark.asyncio
async def test_text_is_streamed_in_deltas(app):
    events = await collect_events(app)

    deltas = [e["delta"] for e in events if e["type"] == "TEXT_MESSAGE_CONTENT"]
    assert deltas == ["ciao ", "mondo"]


@pytest.mark.asyncio
async def test_every_text_message_start_has_an_end(app):
    events = await collect_events(app)

    starts = [e["messageId"] for e in events if e["type"] == "TEXT_MESSAGE_START"]
    ends = [e["messageId"] for e in events if e["type"] == "TEXT_MESSAGE_END"]
    assert sorted(starts) == sorted(ends)


@pytest.mark.asyncio
async def test_run_id_is_echoed_back(app):
    events = await collect_events(app)

    started = next(e for e in events if e["type"] == "RUN_STARTED")
    assert started["runId"] == "r1"
    assert started["threadId"] == "t1"


@pytest.mark.asyncio
async def test_tool_call_emits_result_then_state_snapshot(tool_app):
    """La catena completa di una tool call, senza LLM."""
    events = await collect_events(tool_app)
    types = [e["type"] for e in events]

    assert "TOOL_CALL_START" in types
    assert types.index("TOOL_CALL_RESULT") < types.index("STATE_SNAPSHOT")

    result = next(e for e in events if e["type"] == "TOOL_CALL_RESULT")
    # Il payload arriva serializzato, non come oggetto.
    assert isinstance(result["content"], str)
    assert json.loads(result["content"])["component"] == "ui-table"

    snapshot = next(e for e in events if e["type"] == "STATE_SNAPSHOT")
    assert snapshot["snapshot"]["artifacts"][0]["component"] == "ui-table"


@pytest.mark.asyncio
async def test_tool_call_produces_no_empty_message(tool_app):
    """La coppia START/END che avvolge la tool call non deve diventare un messaggio."""
    events = await collect_events(tool_app)

    snapshot = next(e for e in events if e["type"] == "MESSAGES_SNAPSHOT")
    assistant = [m for m in snapshot["messages"] if m.get("role") == "assistant"]
    assert all(m.get("content") for m in assistant), (
        "un messaggio assistant vuoto significa che la chat mostrerebbe una bolla vuota"
    )
```

- [ ] **Step 3: Eseguire i test e verificare che falliscano**

Run: `uv run pytest tests/test_agui_stream.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'demo.agents'`

- [ ] **Step 4: Implementare il master agent**

`demo-master-agent/src/demo/agents/__init__.py` — file vuoto.

`demo-master-agent/src/demo/agents/master.py`:

```python
"""Costruzione del master agent."""
from __future__ import annotations

from agent_framework import Agent, BaseChatClient
from agent_framework.openai import OpenAIChatCompletionClient

from ..chat_clients.fake import FakeStreamingChatClient
from ..config import get_settings
from ..tools.ui_tools import get_tools

INSTRUCTIONS = """Sei l'agente di un laboratorio dimostrativo.
Rispondi in italiano, in modo conciso.
Quando devi confrontare piu' elementi lungo dimensioni comuni, usa il tool ui_table
invece di descrivere il confronto a parole."""


def _default_chat_client() -> BaseChatClient:
    settings = get_settings()
    if settings.use_fake_client:
        return FakeStreamingChatClient()
    return OpenAIChatCompletionClient(
        model=settings.model,
        api_key=settings.api_key,
        base_url=settings.base_url,
    )


def build_master_agent(chat_client: BaseChatClient | None = None) -> Agent:
    """Il master agent. `chat_client` va passato esplicitamente nei test."""
    return Agent(
        name="master",
        instructions=INSTRUCTIONS,
        client=chat_client or _default_chat_client(),
        tools=get_tools(),
    )
```

- [ ] **Step 5: Implementare l'app**

`demo-master-agent/src/demo/server/__init__.py` — file vuoto.

`demo-master-agent/src/demo/server/app.py`:

```python
"""App FastAPI: espone il master agent via AG-UI su SSE."""
from __future__ import annotations

from agent_framework import Agent
from agent_framework.ag_ui import add_agent_framework_fastapi_endpoint
from fastapi import FastAPI

from ..agents.master import build_master_agent

# Origini del dev server Next.js.
ALLOWED_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]

# Stato condiviso iniziale. In tappa 2 `plan` viene popolato dai tool del piano.
DEFAULT_STATE = {"artifacts": []}


def create_app(agent: Agent | None = None) -> FastAPI:
    """Costruisce l'app. `agent` va passato nei test per iniettare il fake client."""
    app = FastAPI(title="Laboratorio AG-UI")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    add_agent_framework_fastapi_endpoint(
        app,
        agent or build_master_agent(),
        "/agui",
        allow_origins=ALLOWED_ORIGINS,
        default_state=DEFAULT_STATE,
    )
    return app
```

`demo-master-agent/src/demo/__main__.py`:

```python
"""Entrypoint: python -m demo"""
import uvicorn

from .server.app import create_app

if __name__ == "__main__":
    uvicorn.run(create_app(), host="127.0.0.1", port=8000)
```

- [ ] **Step 6: Eseguire i test e verificare che passino**

Run: `uv run pytest tests/ -v`
Expected: PASS (tutti — 6 nuovi in `test_agui_stream.py`)

- [ ] **Step 7: Verifica manuale contro il server reale**

```bash
cd /mnt/c/project/demo/demo-master-agent
DEMO_FAKE_CLIENT=true uv run python -m demo &
curl -sN -X POST http://127.0.0.1:8000/agui \
  -H 'Content-Type: application/json' -H 'Accept: text/event-stream' \
  -d '{"threadId":"t1","runId":"r1","state":{},"messages":[{"id":"m1","role":"user","content":"ciao"}],"tools":[],"context":[],"forwardedProps":{}}'
```

Expected: una sequenza che inizia con `data: {"type":"RUN_STARTED",...}` e termina con `data: {"type":"RUN_FINISHED",...}`, con `TEXT_MESSAGE_CONTENT` intermedi. Fermare il server dopo la verifica.

- [ ] **Step 8: Commit**

```bash
cd /mnt/c/project/demo/demo-master-agent
git add src/demo/agents src/demo/server src/demo/__main__.py tests/
git commit -m "feat: endpoint AG-UI SSE con master agent"
```

---

---
