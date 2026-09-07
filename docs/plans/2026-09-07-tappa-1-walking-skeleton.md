# Tappa 1 — Walking Skeleton — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Un giro end-to-end minimo — l'utente scrive un prompt, l'agente risponde in streaming e chiama un tool, e una UI a tre pannelli mostra chat, stato condiviso ed event inspector alimentati dallo stesso stream SSE.

**Architecture:** Backend FastAPI che espone un agente MAF via `agent-framework-ag-ui` su `POST /agui` (SSE, protocollo AG-UI). Frontend Next.js con client SSE e reducer scritti a mano: un solo stream alimenta tre viste. Nessun sottoagente, nessun piano di lavoro, nessuna skill — arrivano nelle tappe 2 e 3.

**Tech Stack:** Python 3.12, MAF 1.17.0, `agent-framework-ag-ui` 1.2.2, FastAPI, uvicorn, pytest, uv. Next.js (App Router) + TypeScript + Tailwind, vitest.

**Spec:** `docs/specs/2026-09-07-agui-lab-design.md`

## Global Constraints

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

## File Structure

```
demo/
  backend/
    pyproject.toml                     dipendenze e config pytest
    .env.example                       profili OpenRouter / LM Studio
    src/demo/
      __init__.py
      config.py                        lettura env -> Settings
      chat_clients/
        __init__.py
        fake.py                        fake client: testo, e variante che chiama tool
      tools/
        __init__.py
        ui_tools.py                    tool ui_table
      agents/
        __init__.py
        master.py                      build_master_agent()
      server/
        __init__.py
        app.py                         create_app(): FastAPI + endpoint AG-UI
      __main__.py                      uvicorn entrypoint
    tests/
      conftest.py
      test_fake_client.py
      test_ui_tools.py
      test_agui_stream.py              test di integrazione sugli eventi
  frontend/
    package.json, tsconfig.json, next.config.ts, tailwind.config.ts
    app/layout.tsx, app/page.tsx
    lib/agui/
      types.ts                         tipi degli eventi AG-UI
      client.ts                        POST + parser SSE
      reducer.ts                       eventi -> stato UI
      reducer.test.ts
    components/
      Chat.tsx
      StatePanel.tsx
      Inspector.tsx
      Lab.tsx                          layout a tre pannelli, possiede lo stato
  docs/specs/, docs/plans/
```

Responsabilità: `client.ts` sa solo di rete e di parsing SSE; `reducer.ts` è puro e non sa nulla di rete; i componenti non contengono logica di stato. Questa separazione è ciò che rende testabile il reducer senza un browser.

---

### Task 1: Scaffold del backend

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/.env.example`
- Create: `backend/src/demo/__init__.py`
- Create: `backend/src/demo/config.py`
- Test: `backend/tests/test_config.py`

**Interfaces:**
- Consumes: niente (prima task)
- Produces: `demo.config.Settings` (dataclass con `base_url: str`, `api_key: str`, `model: str`, `use_fake_client: bool`), `demo.config.get_settings() -> Settings`

- [ ] **Step 1: Creare il progetto e installare le dipendenze**

```bash
# Il repo git non esiste ancora: le spec sono gia' in docs/, si parte da li'.
cd /mnt/c/project/demo
git init -b main

mkdir -p backend
cd /mnt/c/project/demo/backend
uv init --no-workspace --python 3.12 .
uv add "agent-framework-core==1.17.0" "agent-framework-ag-ui>=1.2.2" agent-framework-openai fastapi uvicorn python-dotenv
uv add --dev pytest pytest-asyncio httpx
```

- [ ] **Step 2: Scrivere `backend/pyproject.toml`**

Sostituire la sezione di config generata aggiungendo in coda:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
pythonpath = ["src"]
```

- [ ] **Step 3: Scrivere `backend/.env.example`**

```bash
# Profilo OpenRouter (default)
OPENAI_BASE_URL=https://openrouter.ai/api/v1
OPENAI_API_KEY=sk-or-v1-...
OPENAI_CHAT_COMPLETION_MODEL=anthropic/claude-sonnet-5

# Profilo LM Studio -- richiede un modello con tool-calling reale
# OPENAI_BASE_URL=http://localhost:1234/v1
# OPENAI_API_KEY=lm-studio
# OPENAI_CHAT_COMPLETION_MODEL=qwen3-14b

# true = nessuna chiamata LLM, usa il fake client (sviluppo offline)
DEMO_FAKE_CLIENT=false
```

- [ ] **Step 4: Scrivere il test**

`backend/tests/test_config.py`:

```python
from demo.config import get_settings


def test_settings_read_from_env(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:1234/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_CHAT_COMPLETION_MODEL", "m")
    monkeypatch.setenv("DEMO_FAKE_CLIENT", "true")

    s = get_settings()

    assert s.base_url == "http://localhost:1234/v1"
    assert s.model == "m"
    assert s.use_fake_client is True


def test_fake_client_defaults_to_false(monkeypatch):
    monkeypatch.delenv("DEMO_FAKE_CLIENT", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "k")

    assert get_settings().use_fake_client is False
```

- [ ] **Step 5: Eseguire il test e verificare che fallisca**

Run: `cd /mnt/c/project/demo/backend && uv run pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'demo.config'`

- [ ] **Step 6: Implementare `backend/src/demo/config.py`**

```python
"""Lettura della configurazione da variabili d'ambiente."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    base_url: str
    api_key: str
    model: str
    use_fake_client: bool


def get_settings() -> Settings:
    """Costruisce le Settings dall'ambiente. Nessuna cache: i test cambiano l'env."""
    return Settings(
        base_url=os.getenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1"),
        api_key=os.getenv("OPENAI_API_KEY", ""),
        model=os.getenv("OPENAI_CHAT_COMPLETION_MODEL", "anthropic/claude-sonnet-5"),
        use_fake_client=os.getenv("DEMO_FAKE_CLIENT", "false").lower() == "true",
    )
```

- [ ] **Step 7: Eseguire il test e verificare che passi**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (2 test)

- [ ] **Step 8: Commit**

```bash
cd /mnt/c/project/demo
git add backend/pyproject.toml backend/.env.example backend/src/demo/config.py backend/tests/test_config.py
git commit -m "feat: scaffold backend con lettura configurazione"
```

---

### Task 2: Fake chat client

Serve prima di tutto il resto: è ciò che rende i test deterministici e permette di sviluppare senza LLM.

**Files:**
- Create: `backend/src/demo/chat_clients/__init__.py`
- Create: `backend/src/demo/chat_clients/fake.py`
- Test: `backend/tests/test_fake_client.py`

**Interfaces:**
- Consumes: niente
- Produces: `demo.chat_clients.fake.FakeStreamingChatClient(chunks: list[str] | None = None, delay: float = 0.0)` e `demo.chat_clients.fake.ToolCallingFakeClient(tool_name: str, tool_args: dict[str, Any], final_text: str = "Fatto.")`

- [ ] **Step 1: Scrivere il test**

`backend/tests/test_fake_client.py`:

```python
import pytest
from agent_framework import ChatResponse

from demo.chat_clients.fake import FakeStreamingChatClient


@pytest.mark.asyncio
async def test_streaming_yields_one_update_per_chunk():
    client = FakeStreamingChatClient(chunks=["uno ", "due ", "tre"])

    stream = client._inner_get_response(messages=[], stream=True, options={})
    texts = []
    async for update in stream:
        texts.extend(c.text for c in update.contents if c.text)

    assert texts == ["uno ", "due ", "tre"]


@pytest.mark.asyncio
async def test_non_streaming_returns_joined_text():
    client = FakeStreamingChatClient(chunks=["uno ", "due"])

    response: ChatResponse = await client._inner_get_response(
        messages=[], stream=False, options={}
    )

    assert response.messages[0].text == "uno due"
```

- [ ] **Step 2: Eseguire il test e verificare che fallisca**

Run: `uv run pytest tests/test_fake_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'demo.chat_clients'`

- [ ] **Step 3: Implementare il fake client**

`backend/src/demo/chat_clients/__init__.py` — file vuoto.

`backend/src/demo/chat_clients/fake.py`:

```python
"""Chat client finto: emette chunk deterministici, senza rete.

Serve ai test e allo sviluppo offline. Isola il resto del sistema dall'LLM.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Mapping, Sequence
from typing import Any

from agent_framework import (
    BaseChatClient,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    Message,
    ResponseStream,
)

DEFAULT_CHUNKS = ["Sto ", "elaborando ", "la ", "risposta."]


class FakeStreamingChatClient(BaseChatClient):
    """Emette `chunks` uno alla volta, con `delay` secondi di distanza."""

    def __init__(self, chunks: list[str] | None = None, delay: float = 0.0) -> None:
        super().__init__()
        self._chunks = chunks if chunks is not None else list(DEFAULT_CHUNKS)
        self._delay = delay

    def _inner_get_response(
        self,
        *,
        messages: Sequence[Message],
        stream: bool,
        options: Mapping[str, Any],
        **kwargs: Any,
    ) -> Awaitable[ChatResponse] | ResponseStream[ChatResponseUpdate, ChatResponse]:
        if not stream:

            async def _once() -> ChatResponse:
                return ChatResponse(
                    messages=[Message(role="assistant", text="".join(self._chunks))]
                )

            return _once()

        async def _stream():
            for chunk in self._chunks:
                if self._delay:
                    await asyncio.sleep(self._delay)
                yield ChatResponseUpdate(
                    contents=[Content.from_text(chunk)], role="assistant"
                )

        return ResponseStream(_stream(), finalizer=ChatResponse.from_updates)


class ToolCallingFakeClient(FunctionInvocationLayer, BaseChatClient):
    """Primo giro: chiama `tool_name` con `tool_args`. Giri successivi: testo.

    Eredita da FunctionInvocationLayer, senza il quale Agent non esegue i tool.
    Serve a testare offline la catena TOOL_CALL_* -> STATE_SNAPSHOT.
    """

    def __init__(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        final_text: str = "Fatto.",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._tool_name = tool_name
        self._tool_args = tool_args
        self._final_text = final_text
        self._turn = 0

    def _inner_get_response(
        self,
        *,
        messages: Sequence[Message],
        stream: bool,
        options: Mapping[str, Any],
        **kwargs: Any,
    ) -> Awaitable[ChatResponse] | ResponseStream[ChatResponseUpdate, ChatResponse]:
        self._turn += 1
        if self._turn == 1:
            contents = [
                Content.from_function_call(
                    call_id="call_1", name=self._tool_name, arguments=self._tool_args
                )
            ]
        else:
            contents = [Content.from_text(self._final_text)]

        if not stream:

            async def _once() -> ChatResponse:
                return ChatResponse(
                    messages=[Message(role="assistant", contents=contents)]
                )

            return _once()

        async def _stream():
            for content in contents:
                yield ChatResponseUpdate(contents=[content], role="assistant")

        return ResponseStream(_stream(), finalizer=ChatResponse.from_updates)
```

Aggiungere in cima al file l'import del mixin, accanto agli altri:

```python
from agent_framework._tools import FunctionInvocationLayer
```

- [ ] **Step 4: Eseguire il test e verificare che passi**

Run: `uv run pytest tests/test_fake_client.py -v`
Expected: PASS (2 test)

- [ ] **Step 5: Commit**

```bash
git add backend/src/demo/chat_clients backend/tests/test_fake_client.py
git commit -m "feat: fake chat client per test deterministici"
```

---

### Task 3: Tool `ui_table`

**Files:**
- Create: `backend/src/demo/tools/__init__.py`
- Create: `backend/src/demo/tools/ui_tools.py`
- Test: `backend/tests/test_ui_tools.py`

**Interfaces:**
- Consumes: niente
- Produces: `demo.tools.ui_tools.ui_table` (`FunctionTool`) e `demo.tools.ui_tools.get_tools() -> list[FunctionTool]`

Il tool restituisce un `Content` costruito con `state_update(text, *, state, tool_result)`: `text` va al modello, `tool_result` alla UI, `state` nello stato condiviso.

- [ ] **Step 1: Scrivere il test**

`backend/tests/test_ui_tools.py`:

```python
import json

from demo.tools.ui_tools import DISPLAY_KEY, STATE_KEY, get_tools, ui_table


def test_get_tools_exposes_ui_table():
    names = [t.name for t in get_tools()]
    assert "ui_table" in names


def test_ui_table_builds_a_ui_payload():
    # FunctionTool espone la funzione sottostante come .func
    content = ui_table.func(
        title="Confronto",
        columns=["Tema", "A", "B"],
        rows=[["Copertura", "vuoto", "pieno"]],
    )

    # state_update serializza tool_result in una stringa JSON.
    payload = json.loads(content.additional_properties[DISPLAY_KEY])
    assert payload["component"] == "ui-table"
    assert payload["title"] == "Confronto"
    assert payload["columns"] == ["Tema", "A", "B"]
    assert payload["rows"] == [["Copertura", "vuoto", "pieno"]]


def test_ui_table_merges_into_shared_state():
    content = ui_table.func(title="Confronto", columns=["A"], rows=[["1"]])

    # Lo state invece resta un dict.
    assert content.additional_properties[STATE_KEY] == {
        "artifacts": [{"component": "ui-table", "title": "Confronto"}]
    }


def test_ui_table_text_is_for_the_model_not_the_ui():
    content = ui_table.func(title="Confronto", columns=["A"], rows=[["1"]])

    assert "Confronto" in content.text
    assert "rows" not in content.text
```

- [ ] **Step 2: Eseguire il test e verificare che fallisca**

Run: `uv run pytest tests/test_ui_tools.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'demo.tools'`

- [ ] **Step 3: Implementare il tool**

`backend/src/demo/tools/__init__.py` — file vuoto.

`backend/src/demo/tools/ui_tools.py`:

```python
"""Tool che producono artefatti renderizzati dal frontend."""
from __future__ import annotations

from typing import Annotated

from agent_framework import Content, FunctionTool, tool
from agent_framework.ag_ui import state_update

# Chiavi riservate sotto cui state_update deposita i suoi payload in
# Content.additional_properties. Il display payload e' una stringa JSON,
# lo state resta un dict. L'emitter AG-UI le estrae e le rimuove.
STATE_KEY = "__ag_ui_tool_result_state__"
DISPLAY_KEY = "__ag_ui_tool_result_display__"


@tool
def ui_table(
    title: Annotated[str, "Titolo della tabella"],
    columns: Annotated[list[str], "Intestazioni di colonna"],
    rows: Annotated[list[list[str]], "Righe, ognuna lunga quanto columns"],
) -> Content:
    """Mostra una tabella all'utente.

    Usa questo tool quando devi confrontare piu' elementi lungo dimensioni comuni.
    """
    return state_update(
        text=f"Ho mostrato la tabella '{title}' con {len(rows)} righe.",
        tool_result={
            "component": "ui-table",
            "title": title,
            "columns": columns,
            "rows": rows,
        },
        state={"artifacts": [{"component": "ui-table", "title": title}]},
    )


def get_tools() -> list[FunctionTool]:
    """I tool nativi disponibili al master agent."""
    return [ui_table]
```

- [ ] **Step 4: Eseguire il test e verificare che passi**

Run: `uv run pytest tests/test_ui_tools.py -v`
Expected: PASS (4 test)

- [ ] **Step 5: Commit**

```bash
git add backend/src/demo/tools backend/tests/test_ui_tools.py
git commit -m "feat: tool ui_table con payload per la UI"
```

---

### Task 4: Master agent e app FastAPI

**Files:**
- Create: `backend/src/demo/agents/__init__.py`
- Create: `backend/src/demo/agents/master.py`
- Create: `backend/src/demo/server/__init__.py`
- Create: `backend/src/demo/server/app.py`
- Create: `backend/src/demo/__main__.py`
- Test: `backend/tests/test_agui_stream.py`
- Test: `backend/tests/conftest.py`

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

- [ ] **Step 1: Scrivere `backend/tests/conftest.py`**

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

`backend/tests/test_agui_stream.py`:

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

`backend/src/demo/agents/__init__.py` — file vuoto.

`backend/src/demo/agents/master.py`:

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

`backend/src/demo/server/__init__.py` — file vuoto.

`backend/src/demo/server/app.py`:

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

`backend/src/demo/__main__.py`:

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
cd /mnt/c/project/demo/backend
DEMO_FAKE_CLIENT=true uv run python -m demo &
curl -sN -X POST http://127.0.0.1:8000/agui \
  -H 'Content-Type: application/json' -H 'Accept: text/event-stream' \
  -d '{"threadId":"t1","runId":"r1","state":{},"messages":[{"id":"m1","role":"user","content":"ciao"}],"tools":[],"context":[],"forwardedProps":{}}'
```

Expected: una sequenza che inizia con `data: {"type":"RUN_STARTED",...}` e termina con `data: {"type":"RUN_FINISHED",...}`, con `TEXT_MESSAGE_CONTENT` intermedi. Fermare il server dopo la verifica.

- [ ] **Step 8: Commit**

```bash
git add backend/src/demo/agents backend/src/demo/server backend/src/demo/__main__.py backend/tests/
git commit -m "feat: endpoint AG-UI SSE con master agent"
```

---

### Task 5: Tipi e client SSE del frontend

**Files:**
- Create: `frontend/` (progetto Next.js)
- Create: `frontend/lib/agui/types.ts`
- Create: `frontend/lib/agui/client.ts`

**Interfaces:**
- Consumes: l'endpoint `POST /agui` della Task 4
- Produces: `AGUIEvent` (union type), `runAgent(input: RunInput, onEvent: (e: AGUIEvent) => void): Promise<void>`

- [ ] **Step 1: Creare il progetto Next.js**

```bash
cd /mnt/c/project/demo
npx create-next-app@latest frontend --typescript --tailwind --app --eslint --no-src-dir --import-alias "@/*" --use-npm
cd frontend && npm i -D vitest
```

- [ ] **Step 2: Aggiungere lo script di test in `frontend/package.json`**

Dentro `"scripts"`, aggiungere:

```json
"test": "vitest run"
```

- [ ] **Step 3: Scrivere i tipi degli eventi**

`frontend/lib/agui/types.ts`:

```typescript
// Eventi AG-UI, in camelCase come arrivano sul filo.
// Solo il sottoinsieme prodotto dalla tappa 1; le tappe 2 e 3 ne aggiungono altri.

export type AGUIEvent =
  | { type: "RUN_STARTED"; threadId: string; runId: string }
  | { type: "RUN_FINISHED"; threadId: string; runId: string }
  | { type: "RUN_ERROR"; message: string }
  | { type: "TEXT_MESSAGE_START"; messageId: string; role: string }
  | { type: "TEXT_MESSAGE_CONTENT"; messageId: string; delta: string }
  | { type: "TEXT_MESSAGE_END"; messageId: string }
  | { type: "TOOL_CALL_START"; toolCallId: string; toolCallName: string }
  | { type: "TOOL_CALL_ARGS"; toolCallId: string; delta: string }
  | { type: "TOOL_CALL_END"; toolCallId: string }
  | { type: "TOOL_CALL_RESULT"; toolCallId: string; content: unknown }
  | { type: "STATE_SNAPSHOT"; snapshot: Record<string, unknown> }
  | { type: "STATE_DELTA"; delta: unknown[] }
  | { type: "MESSAGES_SNAPSHOT"; messages: unknown[] }
  | { type: string; [key: string]: unknown }; // fallback esplicito sull'ignoto

export interface RunInput {
  threadId: string;
  runId: string;
  messages: { id: string; role: string; content: string }[];
  state: Record<string, unknown>;
  tools: unknown[];
  context: unknown[];
  forwardedProps: Record<string, unknown>;
}
```

- [ ] **Step 4: Scrivere il client SSE**

`frontend/lib/agui/client.ts`:

```typescript
import type { AGUIEvent, RunInput } from "./types";

const ENDPOINT = process.env.NEXT_PUBLIC_AGUI_URL ?? "http://127.0.0.1:8000/agui";

/**
 * Esegue una run e invoca onEvent per ogni evento SSE ricevuto.
 * Il parsing e' manuale perche' EventSource non supporta POST.
 */
export async function runAgent(
  input: RunInput,
  onEvent: (event: AGUIEvent) => void,
): Promise<void> {
  const response = await fetch(ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(input),
  });

  if (!response.ok || !response.body) {
    throw new Error(`AG-UI ha risposto ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    // Gli eventi SSE sono separati da una riga vuota.
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() ?? "";

    for (const chunk of chunks) {
      for (const line of chunk.split("\n")) {
        if (!line.startsWith("data: ")) continue;
        onEvent(JSON.parse(line.slice(6)) as AGUIEvent);
      }
    }
  }
}
```

- [ ] **Step 5: Verificare che il progetto compili**

Run: `cd /mnt/c/project/demo/frontend && npx tsc --noEmit`
Expected: nessun errore

- [ ] **Step 6: Commit**

```bash
cd /mnt/c/project/demo
git add frontend/
git commit -m "feat: scaffold frontend Next.js con client AG-UI SSE"
```

---

### Task 6: Reducer

Il pezzo con più logica, e l'unico interamente testabile senza browser né rete.

**Files:**
- Create: `frontend/lib/agui/reducer.ts`
- Test: `frontend/lib/agui/reducer.test.ts`

**Interfaces:**
- Consumes: `AGUIEvent` dalla Task 5
- Produces: `LabState`, `initialState: LabState`, `reduce(state: LabState, event: AGUIEvent): LabState`

- [ ] **Step 1: Scrivere il test**

`frontend/lib/agui/reducer.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { initialState, reduce } from "./reducer";
import type { AGUIEvent } from "./types";

function run(events: AGUIEvent[]) {
  return events.reduce(reduce, initialState);
}

describe("reduce", () => {
  it("accumula i delta di testo in un solo messaggio", () => {
    const state = run([
      { type: "RUN_STARTED", threadId: "t", runId: "r" },
      { type: "TEXT_MESSAGE_START", messageId: "m1", role: "assistant" },
      { type: "TEXT_MESSAGE_CONTENT", messageId: "m1", delta: "ciao " },
      { type: "TEXT_MESSAGE_CONTENT", messageId: "m1", delta: "mondo" },
      { type: "TEXT_MESSAGE_END", messageId: "m1" },
    ]);

    expect(state.messages).toHaveLength(1);
    expect(state.messages[0].content).toBe("ciao mondo");
  });

  it("segna la run come in corso e poi conclusa", () => {
    let state = run([{ type: "RUN_STARTED", threadId: "t", runId: "r" }]);
    expect(state.running).toBe(true);

    state = reduce(state, { type: "RUN_FINISHED", threadId: "t", runId: "r" });
    expect(state.running).toBe(false);
  });

  it("registra ogni evento nell'inspector", () => {
    const state = run([
      { type: "RUN_STARTED", threadId: "t", runId: "r" },
      { type: "TEXT_MESSAGE_START", messageId: "m1", role: "assistant" },
    ]);

    expect(state.events.map((e) => e.type)).toEqual([
      "RUN_STARTED",
      "TEXT_MESSAGE_START",
    ]);
  });

  it("sostituisce lo stato condiviso su STATE_SNAPSHOT", () => {
    const state = run([
      { type: "STATE_SNAPSHOT", snapshot: { artifacts: [{ component: "ui-table" }] } },
    ]);

    expect(state.shared).toEqual({ artifacts: [{ component: "ui-table" }] });
  });

  it("raccoglie i risultati dei tool", () => {
    // content arriva come stringa JSON: state_update serializza il payload.
    const state = run([
      { type: "TOOL_CALL_START", toolCallId: "c1", toolCallName: "ui_table" },
      { type: "TOOL_CALL_RESULT", toolCallId: "c1", content: '{"component":"ui-table"}' },
    ]);

    expect(state.toolCalls).toHaveLength(1);
    expect(state.toolCalls[0].name).toBe("ui_table");
    expect(state.toolCalls[0].result).toBe('{"component":"ui-table"}');
  });

  it("espone l'errore su RUN_ERROR e ferma la run", () => {
    let state = run([{ type: "RUN_STARTED", threadId: "t", runId: "r" }]);
    state = reduce(state, { type: "RUN_ERROR", message: "boom" });

    expect(state.running).toBe(false);
    expect(state.error).toBe("boom");
  });

  it("scarta il messaggio vuoto che avvolge una tool call", () => {
    // Sul filo ogni tool call e' racchiusa fra START ed END senza CONTENT.
    const state = run([
      { type: "TEXT_MESSAGE_START", messageId: "m1", role: "assistant" },
      { type: "TOOL_CALL_START", toolCallId: "c1", toolCallName: "ui_table" },
      { type: "TOOL_CALL_END", toolCallId: "c1" },
      { type: "TEXT_MESSAGE_END", messageId: "m1" },
      { type: "TEXT_MESSAGE_START", messageId: "m2", role: "assistant" },
      { type: "TEXT_MESSAGE_CONTENT", messageId: "m2", delta: "Ecco il confronto." },
      { type: "TEXT_MESSAGE_END", messageId: "m2" },
    ]);

    expect(state.messages).toHaveLength(1);
    expect(state.messages[0].content).toBe("Ecco il confronto.");
  });

  it("ignora un evento sconosciuto senza rompersi", () => {
    const state = run([{ type: "EVENTO_FUTURO", qualcosa: 1 } as AGUIEvent]);

    expect(state.events).toHaveLength(1);
    expect(state.messages).toHaveLength(0);
  });
});
```

- [ ] **Step 2: Eseguire il test e verificare che fallisca**

Run: `cd /mnt/c/project/demo/frontend && npm test`
Expected: FAIL — `Failed to resolve import "./reducer"`

- [ ] **Step 3: Implementare il reducer**

`frontend/lib/agui/reducer.ts`:

```typescript
import type { AGUIEvent } from "./types";

export interface ChatMessage {
  id: string;
  role: string;
  content: string;
}

export interface ToolCall {
  id: string;
  name: string;
  args: string;
  result?: unknown;
}

export interface LabState {
  running: boolean;
  error: string | null;
  messages: ChatMessage[];
  toolCalls: ToolCall[];
  shared: Record<string, unknown>;
  events: AGUIEvent[];
}

export const initialState: LabState = {
  running: false,
  error: null,
  messages: [],
  toolCalls: [],
  shared: {},
  events: [],
};

/** Funzione pura: un evento entra, un nuovo stato esce. Nessuna rete, nessun effetto. */
export function reduce(state: LabState, event: AGUIEvent): LabState {
  // Ogni evento finisce nell'inspector, riconosciuto o no.
  const next: LabState = { ...state, events: [...state.events, event] };

  switch (event.type) {
    case "RUN_STARTED":
      return { ...next, running: true, error: null };

    case "RUN_FINISHED":
      return { ...next, running: false };

    case "RUN_ERROR":
      return { ...next, running: false, error: String(event.message ?? "errore") };

    case "TEXT_MESSAGE_START":
      return {
        ...next,
        messages: [
          ...next.messages,
          { id: event.messageId, role: event.role ?? "assistant", content: "" },
        ],
      };

    case "TEXT_MESSAGE_CONTENT":
      return {
        ...next,
        messages: next.messages.map((m) =>
          m.id === event.messageId ? { ...m, content: m.content + event.delta } : m,
        ),
      };

    case "TOOL_CALL_START":
      return {
        ...next,
        toolCalls: [
          ...next.toolCalls,
          { id: event.toolCallId, name: event.toolCallName, args: "" },
        ],
      };

    case "TOOL_CALL_ARGS":
      return {
        ...next,
        toolCalls: next.toolCalls.map((c) =>
          c.id === event.toolCallId ? { ...c, args: c.args + event.delta } : c,
        ),
      };

    case "TOOL_CALL_RESULT":
      return {
        ...next,
        toolCalls: next.toolCalls.map((c) =>
          c.id === event.toolCallId ? { ...c, result: event.content } : c,
        ),
      };

    case "TEXT_MESSAGE_END":
      // Ogni tool call e' avvolta da START/END senza CONTENT in mezzo:
      // senza questo filtro la chat mostrerebbe una bolla vuota per ogni tool.
      return {
        ...next,
        messages: next.messages.filter(
          (m) => m.id !== event.messageId || m.content.length > 0,
        ),
      };

    case "STATE_SNAPSHOT":
      return { ...next, shared: event.snapshot };

    // MESSAGES_SNAPSHOT ed eventi ancora sconosciuti: registrati
    // nell'inspector, nessun altro effetto.
    default:
      return next;
  }
}
```

- [ ] **Step 4: Eseguire il test e verificare che passi**

Run: `npm test`
Expected: PASS (8 test)

- [ ] **Step 5: Commit**

```bash
cd /mnt/c/project/demo
git add frontend/lib/agui/reducer.ts frontend/lib/agui/reducer.test.ts
git commit -m "feat: reducer AG-UI puro con test"
```

---

### Task 7: UI a tre pannelli

**Files:**
- Create: `frontend/components/Chat.tsx`
- Create: `frontend/components/StatePanel.tsx`
- Create: `frontend/components/Inspector.tsx`
- Create: `frontend/components/Lab.tsx`
- Modify: `frontend/app/page.tsx`

**Interfaces:**
- Consumes: `runAgent` (Task 5), `reduce` / `initialState` / `LabState` (Task 6)
- Produces: la pagina completa

- [ ] **Step 1: Scrivere `frontend/components/Chat.tsx`**

```tsx
"use client";

import type { ChatMessage } from "@/lib/agui/reducer";

interface Props {
  messages: ChatMessage[];
  running: boolean;
  error: string | null;
  onSend: (text: string) => void;
}

export function Chat({ messages, running, error, onSend }: Props) {
  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 space-y-3 overflow-y-auto p-4">
        {messages.map((m) => (
          <div
            key={m.id}
            className={m.role === "user" ? "text-right" : "text-left"}
          >
            <span className="inline-block max-w-[80%] whitespace-pre-wrap rounded-lg bg-gray-100 px-3 py-2 text-sm">
              {m.content}
            </span>
          </div>
        ))}
        {running && <p className="text-xs text-gray-500">sto lavorando…</p>}
        {error && <p className="text-xs text-red-600">errore: {error}</p>}
      </div>

      <form
        className="flex gap-2 border-t p-3"
        onSubmit={(e) => {
          e.preventDefault();
          const input = e.currentTarget.elements.namedItem("q") as HTMLInputElement;
          if (!input.value.trim()) return;
          onSend(input.value);
          input.value = "";
        }}
      >
        <input
          name="q"
          disabled={running}
          placeholder="Scrivi un messaggio…"
          className="flex-1 rounded-full border px-4 py-2 text-sm"
        />
        <button
          type="submit"
          disabled={running}
          className="rounded-full bg-black px-4 py-2 text-sm text-white disabled:opacity-40"
        >
          invia
        </button>
      </form>
    </div>
  );
}
```

- [ ] **Step 2: Scrivere `frontend/components/StatePanel.tsx`**

```tsx
"use client";

interface Props {
  shared: Record<string, unknown>;
}

/**
 * Mostra lo stato condiviso grezzo. In tappa 2 questo pannello diventa
 * il "Piano di lavoro" e legge shared.plan.
 */
export function StatePanel({ shared }: Props) {
  const empty = Object.keys(shared).length === 0;

  return (
    <div className="border-b p-4">
      <h2 className="mb-2 text-xs font-mono uppercase tracking-wide text-gray-500">
        Stato condiviso
      </h2>
      {empty ? (
        <p className="text-xs text-gray-400">nessuno stato</p>
      ) : (
        <pre className="overflow-x-auto text-xs">
          {JSON.stringify(shared, null, 2)}
        </pre>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Scrivere `frontend/components/Inspector.tsx`**

```tsx
"use client";

import { useState } from "react";
import type { AGUIEvent } from "@/lib/agui/types";

const FILTERS = {
  tutti: () => true,
  testo: (e: AGUIEvent) => e.type.startsWith("TEXT_MESSAGE"),
  tool: (e: AGUIEvent) => e.type.startsWith("TOOL_CALL"),
  stato: (e: AGUIEvent) => e.type.startsWith("STATE") || e.type.startsWith("RUN"),
} as const;

export function Inspector({ events }: { events: AGUIEvent[] }) {
  const [filter, setFilter] = useState<keyof typeof FILTERS>("tutti");
  const shown = events.filter(FILTERS[filter]);

  return (
    <div className="flex min-h-0 flex-1 flex-col p-4">
      <div className="mb-2 flex items-center gap-2">
        <h2 className="text-xs font-mono uppercase tracking-wide text-gray-500">
          Event inspector {events.length}
        </h2>
      </div>

      <div className="mb-2 flex gap-1">
        {(Object.keys(FILTERS) as (keyof typeof FILTERS)[]).map((name) => (
          <button
            key={name}
            onClick={() => setFilter(name)}
            className={`rounded-full px-2 py-0.5 text-xs ${
              filter === name ? "bg-black text-white" : "bg-gray-100"
            }`}
          >
            {name}
          </button>
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto font-mono text-xs">
        {shown.map((e, i) => (
          <details key={i} className="border-b py-1">
            <summary className="cursor-pointer text-amber-700">{e.type}</summary>
            <pre className="overflow-x-auto pt-1 text-gray-600">
              {JSON.stringify(e, null, 2)}
            </pre>
          </details>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Scrivere `frontend/components/Lab.tsx`**

```tsx
"use client";

import { useCallback, useState } from "react";
import { runAgent } from "@/lib/agui/client";
import { initialState, reduce, type LabState } from "@/lib/agui/reducer";
import { Chat } from "./Chat";
import { Inspector } from "./Inspector";
import { StatePanel } from "./StatePanel";

export function Lab() {
  const [state, setState] = useState<LabState>(initialState);
  const [threadId] = useState(() => crypto.randomUUID());

  const send = useCallback(
    async (text: string) => {
      const userMessage = { id: crypto.randomUUID(), role: "user", content: text };
      // Il messaggio utente lo aggiunge il client: il server non lo rimanda indietro.
      setState((s) => ({ ...s, messages: [...s.messages, userMessage], error: null }));

      try {
        await runAgent(
          {
            threadId,
            runId: crypto.randomUUID(),
            messages: [userMessage],
            state: {},
            tools: [],
            context: [],
            forwardedProps: {},
          },
          (event) => setState((s) => reduce(s, event)),
        );
      } catch (err) {
        setState((s) => ({ ...s, running: false, error: String(err) }));
      }
    },
    [threadId],
  );

  return (
    <div className="grid h-screen grid-cols-[1fr_420px]">
      <main className="min-w-0 border-r">
        <Chat
          messages={state.messages}
          running={state.running}
          error={state.error}
          onSend={send}
        />
      </main>
      <aside className="flex min-h-0 flex-col">
        <StatePanel shared={state.shared} />
        <Inspector events={state.events} />
      </aside>
    </div>
  );
}
```

- [ ] **Step 5: Sostituire `frontend/app/page.tsx`**

```tsx
import { Lab } from "@/components/Lab";

export default function Page() {
  return <Lab />;
}
```

- [ ] **Step 6: Verificare che compili**

Run: `cd /mnt/c/project/demo/frontend && npx tsc --noEmit && npm run build`
Expected: nessun errore

- [ ] **Step 7: Verifica manuale end-to-end**

Due terminali:

```bash
# terminale 1
cd /mnt/c/project/demo/backend && DEMO_FAKE_CLIENT=true uv run python -m demo

# terminale 2
cd /mnt/c/project/demo/frontend && npm run dev
```

Aprire `http://localhost:3000`, scrivere "ciao", premere invia.

Expected:
- il testo della risposta compare **progressivamente**, non tutto insieme;
- l'inspector elenca `RUN_STARTED`, `TEXT_MESSAGE_START`, più `TEXT_MESSAGE_CONTENT`, `TEXT_MESSAGE_END`, `MESSAGES_SNAPSHOT`, `RUN_FINISHED`;
- i filtri `testo` / `tool` / `stato` riducono la lista;
- nessun errore CORS in console;
- il pannello "Stato condiviso" resta vuoto — **è corretto**: il fake client non chiama tool, quindi nessuno `STATE_SNAPSHOT` viene emesso. Si popola nella Task 8 con un LLM vero.

- [ ] **Step 8: Commit**

```bash
cd /mnt/c/project/demo
git add frontend/components frontend/app/page.tsx
git commit -m "feat: UI a tre pannelli alimentata da un solo stream"
```

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

## Definizione di completo

La tappa 1 è finita quando:

1. `uv run pytest -v` passa nel backend, `npm test` passa nel frontend.
2. Con `DEMO_FAKE_CLIENT=true` la UI mostra testo che arriva progressivamente.
3. Con un LLM reale, una richiesta di confronto produce eventi `TOOL_CALL_*` nell'inspector.
4. I tre pannelli sono alimentati da un solo stream: nessuna seconda chiamata di rete oltre a `POST /agui`.

Il punto 4 è il vero obiettivo della tappa. Se per popolare un pannello serve una seconda API, l'architettura è sbagliata e la tappa 2 ci costruirebbe sopra.
