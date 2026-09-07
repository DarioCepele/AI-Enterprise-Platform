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

### Task 3: Tool `ui_table`

**Files:**
- Create: `demo-master-agent/src/demo/tools/__init__.py`
- Create: `demo-master-agent/src/demo/tools/ui_tools.py`
- Test: `demo-master-agent/tests/test_ui_tools.py`

**Interfaces:**
- Consumes: niente
- Produces: `demo.tools.ui_tools.ui_table` (`FunctionTool`) e `demo.tools.ui_tools.get_tools() -> list[FunctionTool]`

Il tool restituisce un `Content` costruito con `state_update(text, *, state, tool_result)`: `text` va al modello, `tool_result` alla UI, `state` nello stato condiviso.

- [ ] **Step 1: Scrivere il test**

`demo-master-agent/tests/test_ui_tools.py`:

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

`demo-master-agent/src/demo/tools/__init__.py` — file vuoto.

`demo-master-agent/src/demo/tools/ui_tools.py`:

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
cd /mnt/c/project/demo/demo-master-agent
git add src/demo/tools tests/test_ui_tools.py
git commit -m "feat: tool ui_table con payload per la UI"
```

---

---
