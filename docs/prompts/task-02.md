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
- I container si costruiscono e si verificano su Windows con Docker Desktop; da WSL il daemon puo' non essere raggiungibile. Chi esegue un task docker deve riportare l'output reale di `docker compose ps` e del `curl`.
- Ogni repo deployabile ha il suo `Dockerfile`; `demo-infra/compose.yaml` li costruisce da percorsi fratelli. In sviluppo si gira nativi, i container servono alla verifica d'insieme.

---

### Task 2: Fake chat client

Serve prima di tutto il resto: è ciò che rende i test deterministici e permette di sviluppare senza LLM.

**Files:**
- Create: `demo-master-agent/src/demo/chat_clients/__init__.py`
- Create: `demo-master-agent/src/demo/chat_clients/fake.py`
- Test: `demo-master-agent/tests/test_fake_client.py`

**Interfaces:**
- Consumes: niente
- Produces: `demo.chat_clients.fake.FakeStreamingChatClient(chunks: list[str] | None = None, delay: float = 0.0)` e `demo.chat_clients.fake.ToolCallingFakeClient(tool_name: str, tool_args: dict[str, Any], final_text: str = "Fatto.")`

- [ ] **Step 1: Scrivere il test**

`demo-master-agent/tests/test_fake_client.py`:

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

`demo-master-agent/src/demo/chat_clients/__init__.py` — file vuoto.

`demo-master-agent/src/demo/chat_clients/fake.py`:

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
                    messages=[
                        Message(
                            role="assistant",
                            contents=[Content.from_text("".join(self._chunks))],
                        )
                    ]
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
cd /mnt/c/project/demo/demo-master-agent
git add src/demo/chat_clients tests/test_fake_client.py
git commit -m "feat: fake chat client per test deterministici"
```

---

---
