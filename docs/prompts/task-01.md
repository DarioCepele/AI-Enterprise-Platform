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
