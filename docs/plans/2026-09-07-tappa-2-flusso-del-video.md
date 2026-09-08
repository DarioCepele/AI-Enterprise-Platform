# Tappa 2 — Flusso del video — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Portare il laboratorio dal walking skeleton al flusso della registrazione — un piano di lavoro visibile che avanza mentre l'agente lavora, skill caricate a runtime, log operativi veri, e una chat che e' una timeline di entry tipizzate invece di una lista di bolle.

**Architecture:** Il backend guadagna tre famiglie di tool (`todo_write`/`todo_set_status`, `load_skill`, e `ui_table` con `id`) e un canale di log applicativi che viaggia sullo stesso stream SSE come eventi `CUSTOM`. Il frontend smette di modellare la chat come `ChatMessage[]` e la modella come `Entry[]` — un'unione discriminata su `kind` — cosi' ragionamento, tool, sottoagenti e artefatti sono tutti cittadini della stessa timeline. Nessun canale nuovo verso il browser oltre allo stream AG-UI.

**Tech Stack:** Python 3.12, MAF 1.17.0, `agent-framework-ag-ui` 1.2.2, FastAPI, uvicorn, pytest, uv. Next.js 16 (App Router) + TypeScript + Tailwind 4, vitest.

**Spec:** `docs/specs/2026-09-07-agui-lab-design.md`

## Global Constraints

Tutti i vincoli della tappa 1 restano validi. Si ripetono qui perche' chi esegue un task legge questo file, non il precedente.

- Python 3.12. Gestione dipendenze con `uv`.
- `agent-framework-core==1.17.0`, `agent-framework-ag-ui>=1.2.2`, `agent-framework-openai`. **Non usare 1.5.x.**
- Import sotto namespace `agent_framework.*` (`agent_framework.openai`, `agent_framework.ag_ui`, `agent_framework.a2a`). I moduli top-level `agent_framework_openai` / `agent_framework_a2a` sono la vecchia forma: non usarli.
- Le classi di contenuto per-variante **non esistono in 1.17**. Usare `Content` con le factory: `Content.from_text(...)`, `Content.from_function_call(...)`, `Content.from_text_reasoning(...)`. Se vedi `TextContent` in un esempio online, quell'esempio e' per una versione precedente.
- `Message` **non accetta** `text=`: si costruisce con `Message(role=..., contents=[Content.from_text("x")])`. L'attributo `.text` esiste in lettura, non in scrittura.
- Un chat client che deve eseguire tool **deve** ereditare da `agent_framework._tools.FunctionInvocationLayer` oltre che da `BaseChatClient`, nell'ordine `class X(FunctionInvocationLayer, BaseChatClient)`.
- L'endpoint AG-UI si monta con `add_agent_framework_fastapi_endpoint`. **Non scrivere un mapper di eventi a mano.**
- `add_agent_framework_fastapi_endpoint(..., allow_origins=...)` accetta il parametro e **lo ignora**. Il CORS resta montato a mano con `CORSMiddleware`.
- `state_update(text, *, state, tool_result)` deposita i payload in `Content.additional_properties` sotto `__ag_ui_tool_result_state__` (dict) e `__ag_ui_tool_result_display__` (**stringa JSON**). Il `content` di `TOOL_CALL_RESULT` che arriva al frontend e' quindi una stringa JSON da parsare.
- Lo `state` passato a `state_update` e' fuso con semantica `dict.update`: le chiavi di primo livello vengono **sostituite**, non fuse in profondita'. In questa tappa e' un vincolo attivo: `plan` e `artifacts` sono due chiavi di primo livello diverse proprio per non sovrascriversi a vicenda. Un tool che scrive `plan` non deve mai passare anche `artifacts`, e viceversa.
- Ogni tool call e' avvolta da `TEXT_MESSAGE_START` / `TEXT_MESSAGE_END` senza `TEXT_MESSAGE_CONTENT` in mezzo. Il reducer deve scartare i messaggi rimasti vuoti alla chiusura.
- Il JSON di AG-UI e' **camelCase** (`threadId`, `runId`, `messageId`, `delta`).
- Nessuna chiamata LLM reale nei test.
- **Il ragionamento non arriva come testo.** Misurato sul filo con `qwen/qwen3.8-27b`: la sequenza e' `REASONING_START` -> `REASONING_MESSAGE_START` -> N x `REASONING_ENCRYPTED_VALUE` -> `REASONING_MESSAGE_END` -> `REASONING_END`. **`REASONING_MESSAGE_CONTENT` non viene mai emesso.** Il testo sta dentro `REASONING_ENCRYPTED_VALUE.encryptedValue`, che malgrado il nome non e' cifrato: e' una stringa JSON `[{"type": "reasoning.text", "text": "...", "format": "unknown", "index": 0}]`. L'identificativo del messaggio e' in **`entityId`**, non `messageId`.
- Un solo giro di Qwen produce ~408 `REASONING_ENCRYPTED_VALUE`. Vanno aggregati in una entry sola, altrimenti l'inspector e' inutilizzabile.
- Struttura **multi-repo**: `demo-master-agent`, `demo-frontend`, `demo-infra` sono repo git distinti e fratelli dentro `C:\project\demo`. Ogni task committa nel proprio repo.
- I container si costruiscono su Windows con Docker Desktop. Chi esegue un task docker riporta l'output reale.
- I sottoagenti A2A e `call_agent_*` sono **tappa 3**: fuori da questo piano.

---

## File Structure

```
demo-master-agent/
  src/demo/
    tools/ui_tools.py          MODIFICA  ui_table guadagna un id di artefatto
    tools/plan_tools.py        NUOVO     todo_write, todo_set_status
    tools/skill_tools.py       NUOVO     load_skill
    skills/                    NUOVO     cartella delle skill in formato SKILL.md
      comparison/SKILL.md      NUOVO     la skill usata dalla demo
    logging_bridge.py          NUOVO     handler di logging -> eventi CUSTOM
    agents/master.py           MODIFICA  istruzioni e registrazione dei nuovi tool
    server/app.py              MODIFICA  DEFAULT_STATE con plan, montaggio del bridge
  tests/
    test_plan_tools.py         NUOVO
    test_skill_tools.py        NUOVO
    test_logging_bridge.py     NUOVO
    test_ui_tools.py           MODIFICA  copre l'id

demo-frontend/
  lib/agui/types.ts            MODIFICA  eventi REASONING_*, CUSTOM
  lib/agui/entries.ts          NUOVO     l'unione Entry e i suoi costruttori
  lib/agui/reducer.ts          MODIFICA  produce entries invece di messages
  lib/agui/fixtures/
    stream-qwen.txt            NUOVO     stream SSE reale, catturato
  components/Lab.tsx           MODIFICA  header, footer, nuova composizione
  components/Chat.tsx          MODIFICA  timeline con dispatch su entry.kind
  components/entries/          NUOVO     un componente per variante di entry
  components/PlanPanel.tsx     NUOVO     il piano di lavoro
  components/Inspector.tsx     MODIFICA  filtro ragionamento, tab LOG
  components/LogPanel.tsx      NUOVO     il tab LOG
  components/artifacts/UiTable.tsx  NUOVO  renderer dell'artefatto tabella
```

Le entry hanno un componente ciascuna sotto `components/entries/` perche' il dispatch su `kind` cresce a ogni tappa: tenerle in un solo file lo farebbe gonfiare fino a diventare illeggibile entro la tappa 3.

---

## Nota di progetto: dove vive il piano

`state_update` sostituisce le chiavi di primo livello dello stato invece di fonderle in profondita'. Quindi `todo_set_status`, che cambia **un** passo, deve comunque riemettere l'oggetto `plan` **intero**. Per farlo deve poterlo rileggere, e i tool MAF non ricevono lo stato condiviso come argomento.

La soluzione di questo piano e' un `PlanStore`: un piccolo oggetto tenuto dall'agente, di cui i tool sono chiusure. Nessuna variabile globale, testabile in isolamento.

**Limite dichiarato:** un `PlanStore` per istanza di agente, e l'app costruisce un agente solo. Due schede del browser condividono quindi lo stesso piano. Per una demo di laboratorio va bene; se in tappa 3 servisse separarle, si passa a una mappa indicizzata per thread. Va scritto nel README, non scoperto dall'utente.

---

### Task 1: `ui_table` emette un id di artefatto

Lo stato condiviso elenca gli artefatti, il payload pieno viaggia nel `TOOL_CALL_RESULT`. Senza una chiave comune il frontend non sa accoppiarli. La spec §4.2 la chiama `id`; l'implementazione della tappa 1 l'ha omessa.

**Files:**
- Modify: `demo-master-agent/src/demo/tools/ui_tools.py`
- Test: `demo-master-agent/tests/test_ui_tools.py`

**Interfaces:**
- Consumes: niente (primo task)
- Produces: `ui_table(title, columns, rows) -> Content`, il cui `__ag_ui_tool_result_display__` contiene `{"component": "ui-table", "id": str, "title": str, "columns": list[str], "rows": list[list[str]]}` e il cui `__ag_ui_tool_result_state__` e' `{"artifacts": [{"id": str, "component": "ui-table", "title": str}]}`. L'id ha forma `art_N`, N progressivo da 1.

- [ ] **Step 1: Scrivere il test che fallisce**

In `demo-master-agent/tests/test_ui_tools.py`, aggiungere in fondo:

```python
def test_ui_table_assigns_matching_ids_to_result_and_state():
    content = ui_table.func(title="Confronto", columns=["A"], rows=[["1"]])

    payload = json.loads(content.additional_properties[DISPLAY_KEY])
    state = content.additional_properties[STATE_KEY]

    # La chiave che unisce l'elenco nello stato al payload pieno del tool result.
    assert payload["id"] == state["artifacts"][0]["id"]
    assert payload["id"].startswith("art_")


def test_ui_table_ids_are_unique_across_calls():
    first = ui_table.func(title="Uno", columns=["A"], rows=[["1"]])
    second = ui_table.func(title="Due", columns=["A"], rows=[["2"]])

    first_id = json.loads(first.additional_properties[DISPLAY_KEY])["id"]
    second_id = json.loads(second.additional_properties[DISPLAY_KEY])["id"]

    assert first_id != second_id
```

- [ ] **Step 2: Eseguire il test e verificare che fallisca**

Run: `cd demo-master-agent && uv run pytest tests/test_ui_tools.py -v`
Expected: FAIL con `KeyError: 'id'`.

- [ ] **Step 3: Implementare**

Sostituire integralmente `demo-master-agent/src/demo/tools/ui_tools.py`:

```python
"""Tool che producono artefatti renderizzati dal frontend."""
from __future__ import annotations

import itertools
from typing import Annotated

from agent_framework import Content, FunctionTool, tool
from agent_framework.ag_ui import state_update

# Chiavi riservate sotto cui state_update deposita i suoi payload in
# Content.additional_properties. Il display payload e' una stringa JSON,
# lo state resta un dict. L'emitter AG-UI le estrae e le rimuove.
STATE_KEY = "__ag_ui_tool_result_state__"
DISPLAY_KEY = "__ag_ui_tool_result_display__"

# Gli artefatti sono numerati per processo. Serve solo a dare al frontend una
# chiave con cui accoppiare l'elenco nello stato al payload nel tool result:
# non e' un identificativo stabile fra riavvii, e non deve diventarlo.
_artifact_ids = itertools.count(1)


@tool
def ui_table(
    title: Annotated[str, "Titolo della tabella"],
    columns: Annotated[list[str], "Intestazioni di colonna"],
    rows: Annotated[list[list[str]], "Righe, ognuna lunga quanto columns"],
) -> Content:
    """Mostra una tabella all'utente.

    Usa questo tool quando devi confrontare piu' elementi lungo dimensioni comuni.
    """
    artifact_id = f"art_{next(_artifact_ids)}"
    return state_update(
        text=f"Ho mostrato la tabella '{title}' con {len(rows)} righe.",
        tool_result={
            "component": "ui-table",
            "id": artifact_id,
            "title": title,
            "columns": columns,
            "rows": rows,
        },
        # Solo la chiave `artifacts`: state_update sostituisce le chiavi di
        # primo livello, quindi toccare anche `plan` qui lo cancellerebbe.
        state={
            "artifacts": [
                {"id": artifact_id, "component": "ui-table", "title": title}
            ]
        },
    )


def get_tools() -> list[FunctionTool]:
    """I tool nativi disponibili al master agent."""
    return [ui_table]
```

- [ ] **Step 4: Aggiornare il test della tappa 1 che ora contraddice l'id**

`test_ui_table_merges_into_shared_state` asserisce uno stato senza `id`. Sostituirlo con:

```python
def test_ui_table_merges_into_shared_state():
    content = ui_table.func(title="Confronto", columns=["A"], rows=[["1"]])

    state = content.additional_properties[STATE_KEY]
    assert state["artifacts"][0]["component"] == "ui-table"
    assert state["artifacts"][0]["title"] == "Confronto"
```

- [ ] **Step 5: Eseguire la suite intera**

Run: `cd demo-master-agent && uv run pytest -v`
Expected: PASS, nessuna regressione.

- [ ] **Step 6: Commit**

```bash
cd demo-master-agent
git add src/demo/tools/ui_tools.py tests/test_ui_tools.py
git commit -m "feat: id di artefatto su ui_table per accoppiare stato e tool result"
```

---

### Task 2: I tool del piano di lavoro

**Files:**
- Create: `demo-master-agent/src/demo/tools/plan_tools.py`
- Test: `demo-master-agent/tests/test_plan_tools.py`

**Interfaces:**
- Consumes: `STATE_KEY` da `demo.tools.ui_tools`
- Produces:
  - `class PlanStore` con `snapshot() -> dict`, `write(steps: list[dict]) -> dict`, `set_status(step_id: int, status: str, note: str | None) -> dict`. Tutti restituiscono l'oggetto `plan` completo.
  - `build_plan_tools(store: PlanStore) -> list[FunctionTool]`, che restituisce `[todo_write, todo_set_status]` legati a quello store.
  - Forma di `plan`: `{"status": str, "steps": [{"id": int, "title": str, "detail": str, "source": str, "status": str, "started_at": str | None, "ended_at": str | None, "note": str | None}]}`. `status` del piano in `idle | in_progress | completed | failed`; `status` di uno step in `pending | in_progress | completed | failed`.

- [ ] **Step 1: Scrivere il test che fallisce**

Creare `demo-master-agent/tests/test_plan_tools.py`:

```python
import pytest

from demo.tools.plan_tools import PlanStore, build_plan_tools
from demo.tools.ui_tools import STATE_KEY

STEPS = [
    {
        "id": 1,
        "title": "Carica la skill",
        "detail": "Skill di confronto.",
        "source": "skill:comparison#1",
    },
    {
        "id": 2,
        "title": "Produci la tabella",
        "detail": "Confronto tabellare.",
        "source": "ui_table",
    },
]


def test_write_creates_pending_steps_and_starts_the_plan():
    store = PlanStore()
    plan = store.write(STEPS)

    assert plan["status"] == "in_progress"
    assert [s["status"] for s in plan["steps"]] == ["pending", "pending"]
    assert plan["steps"][0]["title"] == "Carica la skill"
    assert plan["steps"][0]["started_at"] is None


def test_set_status_touches_only_the_named_step():
    store = PlanStore()
    store.write(STEPS)
    plan = store.set_status(1, "in_progress", None)

    assert plan["steps"][0]["status"] == "in_progress"
    assert plan["steps"][0]["started_at"] is not None
    assert plan["steps"][1]["status"] == "pending"


def test_plan_completes_when_every_step_completes():
    store = PlanStore()
    store.write(STEPS)
    store.set_status(1, "completed", None)
    plan = store.set_status(2, "completed", None)

    assert plan["status"] == "completed"
    assert plan["steps"][1]["ended_at"] is not None


def test_a_failed_step_fails_the_plan():
    store = PlanStore()
    store.write(STEPS)
    store.set_status(1, "completed", None)
    plan = store.set_status(2, "failed", "il tool non ha risposto")

    assert plan["status"] == "failed"
    assert plan["steps"][1]["note"] == "il tool non ha risposto"


def test_unknown_step_is_rejected_loudly():
    store = PlanStore()
    store.write(STEPS)

    with pytest.raises(ValueError, match="passo 99"):
        store.set_status(99, "completed", None)


def test_unknown_status_is_rejected_loudly():
    store = PlanStore()
    store.write(STEPS)

    with pytest.raises(ValueError, match="quasi"):
        store.set_status(1, "quasi", None)


def test_snapshot_is_a_copy_not_a_live_reference():
    store = PlanStore()
    store.write(STEPS)
    taken = store.snapshot()
    store.set_status(1, "completed", None)

    # Chi ha preso lo snapshot lo serializza dopo: non deve vederlo cambiare.
    assert taken["steps"][0]["status"] == "pending"


def test_tools_write_the_plan_into_shared_state():
    store = PlanStore()
    todo_write, _ = build_plan_tools(store)

    content = todo_write.func(steps=STEPS)
    state = content.additional_properties[STATE_KEY]

    assert "plan" in state
    # Mai `artifacts` insieme a `plan`: state_update sostituisce le chiavi di
    # primo livello, e passarle insieme cancellerebbe gli artefatti gia' emessi.
    assert "artifacts" not in state
    assert state["plan"]["steps"][0]["id"] == 1


def test_set_status_tool_reemits_the_whole_plan():
    store = PlanStore()
    todo_write, todo_set_status = build_plan_tools(store)
    todo_write.func(steps=STEPS)

    content = todo_set_status.func(step_id=1, status="completed", note=None)
    plan = content.additional_properties[STATE_KEY]["plan"]

    # Riemesso intero, non solo il passo cambiato.
    assert len(plan["steps"]) == 2
    assert plan["steps"][0]["status"] == "completed"


def test_plan_tool_text_is_short_and_for_the_model():
    store = PlanStore()
    todo_write, _ = build_plan_tools(store)

    content = todo_write.func(steps=STEPS)

    assert "2" in content.text
    assert "started_at" not in content.text
```

- [ ] **Step 2: Eseguire il test e verificare che fallisca**

Run: `cd demo-master-agent && uv run pytest tests/test_plan_tools.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'demo.tools.plan_tools'`.

- [ ] **Step 3: Implementare**

Creare `demo-master-agent/src/demo/tools/plan_tools.py`:

```python
"""Il piano di lavoro come stato condiviso.

I tool non emettono testo per l'utente: mutano `state.plan`, e il pannello
"Piano di lavoro" e' una funzione pura di quell'oggetto.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any

from agent_framework import Content, FunctionTool, tool
from agent_framework.ag_ui import state_update

STEP_STATUSES = ("pending", "in_progress", "completed", "failed")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PlanStore:
    """Tiene il piano corrente.

    Serve perche' `state_update` sostituisce le chiavi di primo livello dello
    stato invece di fonderle: per cambiare un passo bisogna riemettere il piano
    intero, quindi bisogna poterlo rileggere. I tool MAF non ricevono lo stato
    condiviso, quindi lo teniamo qui.

    Un solo piano per istanza: la demo costruisce un agente solo, quindi due
    schede del browser condividono lo stesso piano. Limite accettato, scritto
    nel README.
    """

    def __init__(self) -> None:
        self._plan: dict[str, Any] = {"status": "idle", "steps": []}

    def snapshot(self) -> dict[str, Any]:
        """Copia del piano. Copia e non riferimento: chi la riceve la serializza dopo."""
        return {
            "status": self._plan["status"],
            "steps": [dict(step) for step in self._plan["steps"]],
        }

    def write(self, steps: list[dict[str, Any]]) -> dict[str, Any]:
        """Sostituisce il piano. Ogni passo parte da `pending`."""
        self._plan = {
            "status": "in_progress",
            "steps": [
                {
                    "id": int(step["id"]),
                    "title": str(step["title"]),
                    "detail": str(step.get("detail", "")),
                    "source": str(step.get("source", "")),
                    "status": "pending",
                    "started_at": None,
                    "ended_at": None,
                    "note": None,
                }
                for step in steps
            ],
        }
        return self.snapshot()

    def set_status(
        self, step_id: int, status: str, note: str | None
    ) -> dict[str, Any]:
        """Cambia lo stato di un passo e ricalcola quello del piano."""
        if status not in STEP_STATUSES:
            raise ValueError(
                f"stato '{status}' sconosciuto: attesi {', '.join(STEP_STATUSES)}"
            )

        step = next((s for s in self._plan["steps"] if s["id"] == step_id), None)
        if step is None:
            known = ", ".join(str(s["id"]) for s in self._plan["steps"]) or "nessuno"
            raise ValueError(f"passo {step_id} non esiste: passi noti {known}")

        step["status"] = status
        step["note"] = note
        if status == "in_progress" and step["started_at"] is None:
            step["started_at"] = _now()
        if status in ("completed", "failed"):
            step["ended_at"] = _now()

        statuses = [s["status"] for s in self._plan["steps"]]
        if "failed" in statuses:
            self._plan["status"] = "failed"
        elif all(s == "completed" for s in statuses):
            self._plan["status"] = "completed"
        else:
            self._plan["status"] = "in_progress"

        return self.snapshot()


def build_plan_tools(store: PlanStore) -> list[FunctionTool]:
    """I tool del piano, legati a `store`.

    Sono chiusure e non funzioni di modulo perche' lo stato del piano non deve
    essere globale: i test ne costruiscono uno per caso.
    """

    @tool
    def todo_write(
        steps: Annotated[
            list[dict],
            "I passi del piano. Ogni passo: id (intero, da 1), title, detail, source.",
        ],
    ) -> Content:
        """Scrive il piano di lavoro, sostituendo quello precedente.

        Usalo una volta sola all'inizio, quando la richiesta dell'utente
        richiede piu' passi. Non usarlo per richieste da un passo solo.
        """
        plan = store.write(steps)
        return state_update(
            text=f"Piano scritto: {len(plan['steps'])} passi.",
            tool_result={"component": "plan", "steps": len(plan["steps"])},
            state={"plan": plan},
        )

    @tool
    def todo_set_status(
        step_id: Annotated[int, "L'id del passo da aggiornare"],
        status: Annotated[str, "Uno fra: pending, in_progress, completed, failed"],
        note: Annotated[
            str | None, "Motivo, obbligatorio quando status e' failed"
        ] = None,
    ) -> Content:
        """Aggiorna lo stato di un passo del piano.

        Marca un passo `in_progress` prima di lavorarci e `completed` appena
        finito, cosi' l'utente vede il piano avanzare mentre lavori.
        """
        plan = store.set_status(step_id, status, note)
        return state_update(
            text=f"Passo {step_id}: {status}.",
            tool_result={"component": "plan", "step_id": step_id, "status": status},
            state={"plan": plan},
        )

    return [todo_write, todo_set_status]
```

- [ ] **Step 4: Eseguire i test**

Run: `cd demo-master-agent && uv run pytest tests/test_plan_tools.py -v`
Expected: PASS, 10 test.

- [ ] **Step 5: Commit**

```bash
cd demo-master-agent
git add src/demo/tools/plan_tools.py tests/test_plan_tools.py
git commit -m "feat: todo_write e todo_set_status come stato condiviso"
```

---

### Task 3: Le skill in formato `SKILL.md` e il tool `load_skill`

La spec §2.4 impone il formato **Agent Skills**: una cartella per skill, con dentro un `SKILL.md` che ha frontmatter YAML e corpo markdown. Nessun registry proprietario.

**Files:**
- Create: `demo-master-agent/src/demo/tools/skill_tools.py`
- Create: `demo-master-agent/src/demo/skills/comparison/SKILL.md`
- Test: `demo-master-agent/tests/test_skill_tools.py`
- Modify: `demo-master-agent/pyproject.toml` (le skill sono dati, vanno incluse nel wheel)

**Interfaces:**
- Consumes: niente. `load_skill` restituisce testo al modello con `Content.from_text`, non un artefatto UI: non passa da `state_update` e non usa `STATE_KEY` / `DISPLAY_KEY`.
- Produces:
  - `SKILLS_DIR: Path` — la cartella `src/demo/skills`
  - `parse_skill(text: str) -> dict` con chiavi `name`, `description`, `body`
  - `list_skills(root: Path = SKILLS_DIR) -> list[dict]` — `[{"name", "description", "body"}]`. Include il corpo perche' `build_skill_tools` riusa lo stesso dict per servirlo a `load_skill`, senza una seconda funzione di lettura.
  - `build_skill_tools(root: Path = SKILLS_DIR) -> list[FunctionTool]` che restituisce `[load_skill]`
  - `load_skill(name: str) -> Content`: il corpo della skill finisce in `content.text`, cioe' **al modello**; non e' un artefatto UI.

Il frontmatter si parsa a mano con una regex invece di aggiungere una dipendenza YAML: due campi scalari non giustificano `pyyaml`. Se un domani le skill avessero frontmatter annidato, quella e' la riga da cambiare.

- [ ] **Step 1: Scrivere il test che fallisce**

Creare `demo-master-agent/tests/test_skill_tools.py`:

```python
import pytest

from demo.tools.skill_tools import (
    SKILLS_DIR,
    build_skill_tools,
    list_skills,
    parse_skill,
)

SKILL_TEXT = """---
name: comparison
description: Confronta piu' elementi lungo dimensioni comuni.
---

# Confronto

Individua le dimensioni, poi chiama ui_table.
"""


def test_parse_skill_splits_frontmatter_from_body():
    parsed = parse_skill(SKILL_TEXT)

    assert parsed["name"] == "comparison"
    assert parsed["description"].startswith("Confronta")
    assert "chiama ui_table" in parsed["body"]
    assert "---" not in parsed["body"]


def test_parse_skill_rejects_a_file_without_frontmatter():
    with pytest.raises(ValueError, match="frontmatter"):
        parse_skill("# Solo markdown\n")


def test_parse_skill_rejects_frontmatter_without_a_name():
    text = "---\ndescription: senza nome\n---\n\ncorpo\n"

    with pytest.raises(ValueError, match="name"):
        parse_skill(text)


def test_the_repo_ships_the_comparison_skill():
    names = [s["name"] for s in list_skills()]

    assert "comparison" in names


def test_every_shipped_skill_parses():
    # Una skill malformata deve rompere i test, non la demo davanti a qualcuno.
    for skill in list_skills():
        assert skill["name"]
        assert skill["description"]


def test_load_skill_returns_the_body_to_the_model(tmp_path):
    skill_dir = tmp_path / "esempio"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(SKILL_TEXT, encoding="utf-8")
    (load_skill,) = build_skill_tools(root=tmp_path)

    content = load_skill.func(name="comparison")

    assert "chiama ui_table" in content.text


def test_load_skill_names_the_alternatives_when_it_fails(tmp_path):
    skill_dir = tmp_path / "esempio"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(SKILL_TEXT, encoding="utf-8")
    (load_skill,) = build_skill_tools(root=tmp_path)

    content = load_skill.func(name="inesistente")

    # Un errore che elenca le alternative: il modello puo' correggersi da solo.
    assert "inesistente" in content.text
    assert "comparison" in content.text


def test_skills_dir_exists_in_the_package():
    assert SKILLS_DIR.is_dir()
```

- [ ] **Step 2: Eseguire il test e verificare che fallisca**

Run: `cd demo-master-agent && uv run pytest tests/test_skill_tools.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'demo.tools.skill_tools'`.

- [ ] **Step 3: Scrivere la skill**

Creare `demo-master-agent/src/demo/skills/comparison/SKILL.md`:

```markdown
---
name: comparison
description: Confronta piu' elementi lungo dimensioni comuni e rende il risultato in tabella.
---

# Confronto strutturato

Quando l'utente chiede di confrontare due o piu' cose:

1. Individua le **dimensioni** del confronto. Se l'utente le ha nominate, usa
   quelle e non aggiungerne. Se non le ha nominate, scegline tre o quattro che
   distinguano davvero gli elementi.
2. Chiama `ui_table` con una colonna per la dimensione e una colonna per
   ciascun elemento confrontato.
3. Dopo la tabella scrivi due o tre righe che dicano **cosa cambia davvero**,
   non che ripetano le celle.

Non descrivere il confronto a parole prima di aver chiamato `ui_table`:
l'utente vede la tabella comparire, e ripeterla nel testo la rende rumore.
```

- [ ] **Step 4: Implementare il tool**

Creare `demo-master-agent/src/demo/tools/skill_tools.py`:

```python
"""Skill in formato Agent Skills: una cartella, un SKILL.md, frontmatter YAML.

Il formato e' quello aperto adottato dall'ecosistema, non un registry nostro:
una skill scritta qui si porta altrove senza riscriverla.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any

from agent_framework import Content, FunctionTool, tool

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"

# Frontmatter delimitato da --- in apertura e chiusura, corpo markdown dopo.
_FRONTMATTER = re.compile(r"\A---\s*\n(?P<meta>.*?)\n---\s*\n(?P<body>.*)\Z", re.S)
# Solo campi scalari `chiave: valore`. Due campi non giustificano pyyaml;
# se il frontmatter diventasse annidato, e' questa riga da sostituire.
_FIELD = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_-]*):\s*(?P<value>.*)$")


def parse_skill(text: str) -> dict[str, str]:
    """Divide un SKILL.md in metadati e corpo. Solleva se la forma non torna."""
    match = _FRONTMATTER.match(text)
    if match is None:
        raise ValueError(
            "SKILL.md senza frontmatter: serve un blocco --- in cima al file"
        )

    meta: dict[str, str] = {}
    for line in match.group("meta").splitlines():
        if not line.strip():
            continue
        field = _FIELD.match(line.strip())
        if field is None:
            raise ValueError(f"riga di frontmatter non interpretabile: {line!r}")
        meta[field.group("key")] = field.group("value").strip()

    if "name" not in meta:
        raise ValueError("frontmatter senza campo name")
    if "description" not in meta:
        raise ValueError(f"skill '{meta['name']}' senza campo description")

    return {
        "name": meta["name"],
        "description": meta["description"],
        "body": match.group("body").strip(),
    }


def list_skills(root: Path = SKILLS_DIR) -> list[dict[str, Any]]:
    """Le skill disponibili, ordinate per nome. Una skill rotta solleva subito."""
    found = []
    for skill_file in sorted(root.glob("*/SKILL.md")):
        parsed = parse_skill(skill_file.read_text(encoding="utf-8"))
        found.append(parsed)
    return found


def build_skill_tools(root: Path = SKILLS_DIR) -> list[FunctionTool]:
    """Il tool load_skill, legato a una cartella di skill.

    `root` e' un parametro perche' i test caricano da una tmp_path invece che
    dalle skill vere del repo.
    """
    catalogue = list_skills(root)
    listing = "\n".join(f"- {s['name']}: {s['description']}" for s in catalogue)

    @tool
    def load_skill(
        name: Annotated[str, "Il nome della skill, come compare nel catalogo"],
    ) -> Content:
        """Carica le istruzioni operative di una skill.

        Chiamalo quando la richiesta ricade in un dominio coperto dal catalogo,
        prima di iniziare a lavorare. Le skill disponibili sono:
        """
        wanted = next((s for s in catalogue if s["name"] == name), None)
        if wanted is None:
            known = ", ".join(s["name"] for s in catalogue) or "nessuna"
            # Errore come testo, non eccezione: il modello legge, si corregge,
            # e la run continua invece di morire su un nome sbagliato.
            return Content.from_text(
                f"La skill '{name}' non esiste. Skill disponibili: {known}."
            )
        return Content.from_text(wanted["body"])

    # La docstring del tool e' cio' che il modello legge: il catalogo va dentro.
    load_skill.description = f"{load_skill.description}\n{listing}"
    return [load_skill]
```

- [ ] **Step 5: Includere le skill nel pacchetto**

Le skill sono dati, non codice: senza questo, `uv run pytest` le trova sul filesystem ma l'immagine docker no.

In `demo-master-agent/pyproject.toml`, sotto `[tool.hatch.build.targets.wheel]`, aggiungere:

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/demo"]

[tool.hatch.build.targets.wheel.force-include]
"src/demo/skills" = "demo/skills"
```

- [ ] **Step 6: Eseguire i test**

Run: `cd demo-master-agent && uv run pytest tests/test_skill_tools.py -v`
Expected: PASS, 8 test.

- [ ] **Step 7: Commit**

```bash
cd demo-master-agent
git add src/demo/tools/skill_tools.py src/demo/skills tests/test_skill_tools.py pyproject.toml
git commit -m "feat: skill in formato SKILL.md e tool load_skill"
```

---


### Task 4: Il collettore dei log applicativi

Il tab LOG del laboratorio di riferimento mostra log veri del server. Gli eventi `CUSTOM` di AG-UI **non sono disponibili al codice applicativo** — verificato in `agent_framework_ag_ui`: `CustomEvent` viene costruito solo dal framework, per `usage`, `oauth_consent_request`, `function_approval_request` e `PredictState`, e nessuna delle 24 factory di `Content` ne produce uno arbitrario. Costruirli a mano viola il vincolo globale. I log viaggiano quindi su un endpoint HTTP proprio (Task 5); questo task costruisce solo il collettore, che del trasporto non sa nulla.

**Due paletti, entrambi coperti da un test:**

1. **Solo i logger `demo.*`.** I logger di libreria (`httpx`, `openai`, `uvicorn`) scrivono URL e header di richiesta: inoltrarli al browser significa pubblicare la chiave API a chiunque apra la pagina.
2. **Buffer circolare.** Il server e' longevo e il client legge a cursore: il buffer tiene le ultime `MAX_LOG_EVENTS` righe e dice al client quante ne ha perse, invece di crescere senza fine o di mentire.

**Files:**
- Create: `demo-master-agent/src/demo/logging_bridge.py`
- Test: `demo-master-agent/tests/test_logging_bridge.py`

**Interfaces:**
- Consumes: niente
- Produces:
  - `MAX_LOG_EVENTS: int = 500`
  - `class LogCollector` con `attach() -> None`, `detach() -> None`, `since(cursor: int) -> dict`
  - `since(cursor)` restituisce `{"entries": list[dict], "cursor": int, "dropped": int}`. `entries` sono le righe con numero di sequenza `> cursor`; `cursor` e' il nuovo cursore da rimandare alla chiamata successiva; `dropped` e' quante righe sono uscite dal buffer prima che il client le leggesse.
  - Ogni voce: `{"seq": int, "ts": str, "level": str, "source": str, "message": str}` — `ts` ISO 8601 UTC, `level` in maiuscolo, `source` il nome del logger senza il prefisso `demo.`.
  - `LogCollector` e' anche context manager (`attach` in entrata, `detach` in uscita).

- [ ] **Step 1: Scrivere il test che fallisce**

Creare `demo-master-agent/tests/test_logging_bridge.py`:

```python
import logging

from demo.logging_bridge import MAX_LOG_EVENTS, LogCollector


def test_collects_application_logs():
    with LogCollector() as collector:
        logging.getLogger("demo.tools").info("piano scritto")

    page = collector.since(0)

    assert len(page["entries"]) == 1
    assert page["entries"][0]["level"] == "INFO"
    assert page["entries"][0]["source"] == "tools"
    assert page["entries"][0]["message"] == "piano scritto"
    assert page["entries"][0]["ts"].endswith("+00:00")
    assert page["dropped"] == 0


def test_library_logs_never_reach_the_stream():
    # httpx e openai loggano URL con la chiave API dentro: se questo test
    # sparisce, la chiave finisce nel browser di chi apre la pagina.
    with LogCollector() as collector:
        logging.getLogger("httpx").info("POST https://api.example/v1?key=segreto")
        logging.getLogger("openai").warning("retry")
        logging.getLogger("uvicorn.access").info("GET /agui")

    assert collector.since(0)["entries"] == []


def test_the_cursor_advances_and_does_not_repeat_entries():
    with LogCollector() as collector:
        logging.getLogger("demo.a").info("uno")
        first = collector.since(0)
        logging.getLogger("demo.a").info("due")
        second = collector.since(first["cursor"])

    assert [e["message"] for e in first["entries"]] == ["uno"]
    assert [e["message"] for e in second["entries"]] == ["due"]
    assert second["cursor"] > first["cursor"]


def test_reading_twice_from_the_same_cursor_is_idempotent():
    # Il client puo' ritentare dopo un errore di rete: non deve perdere righe.
    with LogCollector() as collector:
        logging.getLogger("demo.a").info("uno")

    assert collector.since(0)["entries"] == collector.since(0)["entries"]


def test_the_buffer_is_capped_and_reports_what_it_dropped():
    with LogCollector() as collector:
        for i in range(MAX_LOG_EVENTS + 50):
            logging.getLogger("demo.rumore").info("riga %d", i)

    page = collector.since(0)

    assert len(page["entries"]) == MAX_LOG_EVENTS
    assert page["dropped"] == 50
    # Le righe tenute sono le ultime, non le prime.
    assert page["entries"][-1]["message"] == f"riga {MAX_LOG_EVENTS + 49}"


def test_detach_stops_collecting():
    collector = LogCollector()
    collector.attach()
    collector.detach()
    logging.getLogger("demo.a").info("dopo il distacco")

    assert collector.since(0)["entries"] == []


def test_attaching_twice_does_not_double_every_line():
    collector = LogCollector()
    collector.attach()
    collector.attach()
    logging.getLogger("demo.a").info("una volta sola")
    collector.detach()

    assert len(collector.since(0)["entries"]) == 1


def test_exceptions_arrive_as_text_not_as_objects():
    with LogCollector() as collector:
        try:
            raise RuntimeError("il tool e' esploso")
        except RuntimeError:
            logging.getLogger("demo.tools").exception("chiamata fallita")

    entry = collector.since(0)["entries"][0]

    assert entry["level"] == "ERROR"
    assert "il tool e' esploso" in entry["message"]
    assert "Traceback" in entry["message"]
```

- [ ] **Step 2: Eseguire il test e verificare che fallisca**

Run: `cd demo-master-agent && uv run pytest tests/test_logging_bridge.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'demo.logging_bridge'`.

- [ ] **Step 3: Implementare**

Creare `demo-master-agent/src/demo/logging_bridge.py`:

```python
"""Raccolta dei log applicativi per il tab LOG del frontend.

Non tocca AG-UI: gli eventi CUSTOM del protocollo sono riservati al framework
(usage, oauth_consent_request, function_approval_request, PredictState) e non
esiste una factory di Content che ne produca uno arbitrario. I log viaggiano
quindi su un endpoint HTTP proprio, che legge questo collettore a cursore.
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any

# Solo i logger dell'applicazione. I logger di libreria (httpx, openai)
# scrivono URL e header di richiesta: inoltrarli al browser significa
# pubblicare la chiave API. Il filtro e' una misura di sicurezza, non estetica.
APP_LOGGER = "demo"

# Il server e' longevo. Il buffer tiene le ultime righe e dichiara quante ne
# ha perse, invece di crescere finche' la memoria finisce.
MAX_LOG_EVENTS = 500


def _timestamp(record: logging.LogRecord) -> str:
    return datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(
        timespec="milliseconds"
    )


class _CollectingHandler(logging.Handler):
    def __init__(self, collector: LogCollector) -> None:
        super().__init__(level=logging.INFO)
        self._collector = collector

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if record.exc_info:
            # formatException produce il traceback senza il messaggio davanti.
            message = f"{message}\n{self.formatter.formatException(record.exc_info)}"

        self._collector.append(
            {
                "ts": _timestamp(record),
                "level": record.levelname,
                "source": record.name.removeprefix(f"{APP_LOGGER}."),
                "message": message,
            }
        )


class LogCollector:
    """Buffer circolare dei log di `demo.*`, letto a cursore.

    Il cursore e' il numero di sequenza dell'ultima riga vista. Rileggere dallo
    stesso cursore restituisce le stesse righe: un client che ritenta dopo un
    errore di rete non perde nulla.
    """

    def __init__(self) -> None:
        self._entries: deque[dict[str, Any]] = deque(maxlen=MAX_LOG_EVENTS)
        self._handler: _CollectingHandler | None = None
        self._next_seq = 1
        # uvicorn serve le richieste su piu' thread: append e since si incrociano.
        self._lock = threading.Lock()

    def append(self, entry: dict[str, Any]) -> None:
        with self._lock:
            entry["seq"] = self._next_seq
            self._next_seq += 1
            self._entries.append(entry)

    def attach(self) -> None:
        """Aggancia il collettore al logger `demo`. Chiamarlo due volte non duplica."""
        if self._handler is not None:
            return
        self._handler = _CollectingHandler(self)
        self._handler.setFormatter(logging.Formatter())
        logger = logging.getLogger(APP_LOGGER)
        logger.addHandler(self._handler)
        # Senza questo, il livello ereditato dal root (WARNING) scarta gli INFO.
        logger.setLevel(logging.INFO)

    def detach(self) -> None:
        if self._handler is None:
            return
        logging.getLogger(APP_LOGGER).removeHandler(self._handler)
        self._handler = None

    def since(self, cursor: int) -> dict[str, Any]:
        """Le righe con seq > cursor, il nuovo cursore, e quante se ne sono perse."""
        with self._lock:
            entries = [dict(e) for e in self._entries if e["seq"] > cursor]
            oldest_kept = self._entries[0]["seq"] if self._entries else self._next_seq
            # Quante righe sono uscite dal buffer prima che il client le leggesse.
            dropped = max(0, oldest_kept - cursor - 1)
            newest = entries[-1]["seq"] if entries else cursor
            return {"entries": entries, "cursor": newest, "dropped": dropped}

    def __enter__(self) -> LogCollector:
        self.attach()
        return self

    def __exit__(self, *exc: object) -> None:
        self.detach()
```

- [ ] **Step 4: Eseguire i test**

Run: `cd demo-master-agent && uv run pytest tests/test_logging_bridge.py -v`
Expected: PASS, 8 test.

- [ ] **Step 5: Commit**

```bash
cd demo-master-agent
git add src/demo/logging_bridge.py tests/test_logging_bridge.py
git commit -m "feat: buffer circolare dei log applicativi, letto a cursore"
```

---

### Task 5: L'agente monta i nuovi tool e l'app espone `/logs`

Qui i pezzi dei task precedenti diventano un agente che sa pianificare, e il collettore diventa un endpoint.

Attenzione al CORS: la tappa 1 ha montato `allow_methods=["POST"]`. Aggiungere un `GET` senza aggiornarlo lo fa fallire **solo nel browser**, dove nessun test lo vede.

**Files:**
- Modify: `demo-master-agent/src/demo/agents/master.py`
- Modify: `demo-master-agent/src/demo/server/app.py`
- Test: `demo-master-agent/tests/test_logs_endpoint.py`
- Test: `demo-master-agent/tests/test_agui_stream.py` (un caso nuovo)
- Modify: `demo-master-agent/tests/conftest.py`

**Interfaces:**
- Consumes: `PlanStore`, `build_plan_tools` (Task 2); `build_skill_tools` (Task 3); `LogCollector` (Task 4); `get_tools` (Task 1)
- Produces:
  - `build_master_agent(chat_client=None, plan_store=None) -> Agent`
  - `create_app(agent=None, collector=None) -> FastAPI` con `GET /logs?cursor=<int>` che risponde `{"entries": [...], "cursor": int, "dropped": int}`
  - `DEFAULT_STATE = {"artifacts": [], "plan": {"status": "idle", "steps": []}}`

- [ ] **Step 1: Scrivere i test che falliscono**

Creare `demo-master-agent/tests/test_logs_endpoint.py`:

```python
import logging

from fastapi.testclient import TestClient

from demo.logging_bridge import LogCollector
from demo.server.app import create_app


def test_logs_endpoint_returns_collected_lines():
    collector = LogCollector()
    app = create_app(collector=collector)

    with TestClient(app) as client:
        logging.getLogger("demo.tools").info("piano scritto")
        body = client.get("/logs").json()

    assert [e["message"] for e in body["entries"]] == ["piano scritto"]
    assert body["cursor"] > 0
    assert body["dropped"] == 0


def test_logs_endpoint_honours_the_cursor():
    collector = LogCollector()
    app = create_app(collector=collector)

    with TestClient(app) as client:
        logging.getLogger("demo.tools").info("uno")
        first = client.get("/logs").json()
        logging.getLogger("demo.tools").info("due")
        second = client.get("/logs", params={"cursor": first["cursor"]}).json()

    assert [e["message"] for e in second["entries"]] == ["due"]


def test_logs_endpoint_never_leaks_library_logs():
    collector = LogCollector()
    app = create_app(collector=collector)

    with TestClient(app) as client:
        logging.getLogger("httpx").info("POST https://api.example/v1?key=segreto")
        body = client.get("/logs").json()

    assert body["entries"] == []


def test_cors_allows_the_browser_to_read_logs():
    # Il preflight di una GET cross-origin fallisce se allow_methods resta
    # solo POST, e fallisce solo nel browser: nessun altro test lo vede.
    app = create_app(collector=LogCollector())

    with TestClient(app) as client:
        response = client.options(
            "/logs",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_default_state_carries_an_empty_plan():
    from demo.server.app import DEFAULT_STATE

    # Senza questo, il pannello del piano non ha una forma da rendere prima
    # che il primo todo_write arrivi, e deve indovinarla.
    assert DEFAULT_STATE["plan"] == {"status": "idle", "steps": []}
    assert DEFAULT_STATE["artifacts"] == []
```

Aggiungere in `demo-master-agent/tests/conftest.py`:

```python
from demo.tools.plan_tools import PlanStore


@pytest.fixture
def plan_app():
    """App con un client che al primo giro scrive un piano."""
    agent = build_master_agent(
        chat_client=ToolCallingFakeClient(
            tool_name="todo_write",
            tool_args={
                "steps": [
                    {
                        "id": 1,
                        "title": "Primo passo",
                        "detail": "dettaglio",
                        "source": "ui_table",
                    }
                ]
            },
            final_text="Piano pronto.",
        ),
        plan_store=PlanStore(),
    )
    return create_app(agent=agent)
```

E in `demo-master-agent/tests/test_agui_stream.py`, seguendo lo stile dei casi gia' presenti:

L'helper esistente e' `async def collect_events(app) -> list[dict]` e prende **solo** l'app: il prompt e' fisso, nella costante `REQUEST` in cima al file. I test sono `async` e marcati `@pytest.mark.asyncio`.

```python
@pytest.mark.asyncio
async def test_plan_tool_reaches_the_shared_state(plan_app):
    events = await collect_events(plan_app)

    snapshots = [e for e in events if e["type"] == "STATE_SNAPSHOT"]
    assert snapshots, "nessuno STATE_SNAPSHOT: il tool del piano non ha girato"

    plan = snapshots[-1]["snapshot"]["plan"]
    assert plan["status"] == "in_progress"
    assert plan["steps"][0]["title"] == "Primo passo"
    # `artifacts` sopravvive accanto a `plan`: sono due chiavi di primo livello
    # diverse proprio perche' state_update sostituisce, non fonde.
    assert "artifacts" in snapshots[-1]["snapshot"]
```

- [ ] **Step 2: Eseguire i test e verificare che falliscano**

Run: `cd demo-master-agent && uv run pytest tests/test_logs_endpoint.py tests/test_agui_stream.py -v`
Expected: FAIL — `create_app() got an unexpected keyword argument 'collector'` e `404` su `/logs`.

- [ ] **Step 3: Montare i tool sull'agente**

Sostituire `demo-master-agent/src/demo/agents/master.py`:

```python
"""Costruzione del master agent."""
from __future__ import annotations

from agent_framework import Agent, BaseChatClient
from agent_framework.openai import OpenAIChatCompletionClient

from ..chat_clients.fake import FakeStreamingChatClient
from ..config import get_settings
from ..tools.plan_tools import PlanStore, build_plan_tools
from ..tools.skill_tools import build_skill_tools
from ..tools.ui_tools import get_tools

INSTRUCTIONS = """Sei l'agente di un laboratorio dimostrativo.
Rispondi in italiano, in modo conciso.

Quando la richiesta si risolve in un passo solo, rispondi e basta.

Quando richiede piu' passi:
1. Chiama `todo_write` per scrivere il piano, prima di iniziare.
2. Se un passo ricade in un dominio coperto da una skill, chiama `load_skill`
   e segui le istruzioni che ricevi.
3. Marca ogni passo `in_progress` prima di lavorarci e `completed` appena
   finito, con `todo_set_status`. L'utente vede il piano avanzare: un piano
   aggiornato solo alla fine non serve a nessuno.
4. Se un passo fallisce, marcalo `failed` con una nota e prosegui con gli altri.

Quando devi confrontare piu' elementi lungo dimensioni comuni, usa il tool
`ui_table` invece di descrivere il confronto a parole."""


def _default_chat_client() -> BaseChatClient:
    settings = get_settings()
    if settings.use_fake_client:
        return FakeStreamingChatClient()
    return OpenAIChatCompletionClient(
        model=settings.model,
        api_key=settings.api_key,
        base_url=settings.base_url,
    )


def build_master_agent(
    chat_client: BaseChatClient | None = None,
    plan_store: PlanStore | None = None,
) -> Agent:
    """Il master agent. `chat_client` e `plan_store` vanno passati nei test."""
    store = plan_store if plan_store is not None else PlanStore()
    return Agent(
        name="master",
        instructions=INSTRUCTIONS,
        client=chat_client or _default_chat_client(),
        tools=[*get_tools(), *build_plan_tools(store), *build_skill_tools()],
    )
```

- [ ] **Step 4: Esporre l'endpoint dei log**

Sostituire `demo-master-agent/src/demo/server/app.py`:

```python
"""App FastAPI: espone il master agent via AG-UI su SSE, piu' i log operativi."""
from __future__ import annotations

from agent_framework import Agent
from agent_framework.ag_ui import add_agent_framework_fastapi_endpoint
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..agents.master import build_master_agent
from ..config import get_settings
from ..logging_bridge import LogCollector

# La forma iniziale dello stato condiviso. `plan` c'e' gia' vuoto perche' il
# pannello del frontend possa renderlo prima del primo todo_write, invece di
# doverne indovinare la forma.
DEFAULT_STATE = {"artifacts": [], "plan": {"status": "idle", "steps": []}}


def create_app(
    agent: Agent | None = None,
    collector: LogCollector | None = None,
) -> FastAPI:
    """Costruisce l'app. `agent` e `collector` vanno passati nei test."""
    app = FastAPI(title="Laboratorio AG-UI")
    log_collector = collector if collector is not None else LogCollector()
    log_collector.attach()

    # Le origini non sono hardcoded: il dev server di Next slitta di porta se la
    # 3000 e' occupata, e un'origine sbagliata fallisce solo nel browser.
    allowed_origins = list(get_settings().allowed_origins)
    # In agent-framework-ag-ui 1.2.2 allow_origins non e' ancora implementato.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        # GET serve a /logs: senza, il preflight fallisce solo nel browser.
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/logs")
    async def logs(cursor: int = 0) -> dict[str, object]:
        """I log applicativi dopo `cursor`.

        Canale separato dallo stream AG-UI: gli eventi CUSTOM del protocollo
        sono riservati al framework e non sono emettibili dal codice applicativo.
        """
        return log_collector.since(cursor)

    add_agent_framework_fastapi_endpoint(
        app,
        agent or build_master_agent(),
        "/agui",
        allow_origins=allowed_origins,
        default_state=DEFAULT_STATE,
    )
    return app
```

- [ ] **Step 5: Eseguire la suite intera**

Run: `cd demo-master-agent && uv run pytest -v`
Expected: PASS. I test della tappa 1 non devono regredire.

Se `test_agui_stream.py` fallisce perche' il fake client ora vede piu' tool, controllare che `ToolCallingFakeClient` chiami il tool giusto: con tre famiglie di tool montate, il nome nel fixture deve combaciare esattamente.

- [ ] **Step 6: Verificare a mano che il piano giri davvero**

Con i container su e una chiave valida in `.env`:

```bash
curl -s -X POST http://localhost:8000/agui \
  -H 'Content-Type: application/json' \
  -d '{"threadId":"t1","runId":"r1","messages":[{"id":"m1","role":"user","content":"Confronta Python e Go su tipizzazione, concorrenza ed errori. Fai prima un piano."}],"state":{},"tools":[],"context":[],"forwardedProps":{}}' \
  | grep -o '"type":"[A-Z_]*"' | sort | uniq -c
```

Expected: compaiono `TOOL_CALL_START` per `todo_write` e piu' `STATE_SNAPSHOT`. Riportare l'output reale, non riassumerlo.

```bash
curl -s http://localhost:8000/logs | head -c 400
```

Expected: JSON con `entries`, `cursor`, `dropped`. Nessuna riga con dentro una chiave che inizi per `sk-`.

- [ ] **Step 7: Commit**

```bash
cd demo-master-agent
git add src/demo/agents/master.py src/demo/server/app.py tests/
git commit -m "feat: master agent con piano e skill, endpoint /logs"
```

---


### Task 5b: I tool emettono log

Aggiunto **durante l'esecuzione del piano**, non nella stesura. I task 4 e 5 hanno costruito il collettore e l'endpoint, ma nessun modulo applicativo chiama `logging`: `GET /logs` restituisce `entries: []` a ogni run, e il tab LOG del Task 11 sarebbe vuoto per costruzione. Verificato dopo il Task 5: nessuna occorrenza di `getLogger` in `src/demo/` fuori da `logging_bridge.py`.

Le righe di log devono raccontare **cosa ha fatto l'agente**, non ripetere quello che l'inspector mostra già. L'inspector porta gli eventi del protocollo; il log porta il punto di vista del server: quale tool è partito, con che esito, quanto ci ha messo, cosa è andato storto.

**Files:**
- Modify: `demo-master-agent/src/demo/tools/plan_tools.py`
- Modify: `demo-master-agent/src/demo/tools/skill_tools.py`
- Modify: `demo-master-agent/src/demo/tools/ui_tools.py`
- Test: `demo-master-agent/tests/test_tool_logging.py`

**Interfaces:**
- Consumes: `LogCollector` da `demo.logging_bridge` (Task 4); i tool dei Task 1, 2, 3
- Produces: nessuna interfaccia nuova. Ogni modulo di tool ottiene un `logger = logging.getLogger(__name__)`, che essendo i moduli sotto il pacchetto `demo` produce nomi `demo.tools.plan_tools` e simili — quindi il filtro del collettore li cattura e la loro `source` diventa `tools.plan_tools`.

- [ ] **Step 1: Scrivere il test che fallisce**

Creare `demo-master-agent/tests/test_tool_logging.py`:

```python
from demo.logging_bridge import LogCollector
from demo.tools.plan_tools import PlanStore, build_plan_tools
from demo.tools.skill_tools import build_skill_tools
from demo.tools.ui_tools import ui_table

STEPS = [
    {"id": 1, "title": "Primo", "detail": "d", "source": "ui_table"},
    {"id": 2, "title": "Secondo", "detail": "d", "source": "ui_table"},
]


def test_writing_a_plan_is_logged():
    store = PlanStore()
    todo_write, _ = build_plan_tools(store)

    with LogCollector() as collector:
        todo_write.func(steps=STEPS)

    entries = collector.since(0)["entries"]
    assert len(entries) == 1
    assert entries[0]["source"] == "tools.plan_tools"
    assert "2" in entries[0]["message"]


def test_every_step_transition_is_logged():
    store = PlanStore()
    todo_write, todo_set_status = build_plan_tools(store)
    todo_write.func(steps=STEPS)

    with LogCollector() as collector:
        todo_set_status.func(step_id=1, status="in_progress", note=None)
        todo_set_status.func(step_id=1, status="completed", note=None)

    messages = [e["message"] for e in collector.since(0)["entries"]]
    assert len(messages) == 2
    assert "in_progress" in messages[0]
    assert "completed" in messages[1]


def test_a_failed_step_is_logged_as_an_error_with_its_reason():
    store = PlanStore()
    todo_write, todo_set_status = build_plan_tools(store)
    todo_write.func(steps=STEPS)

    with LogCollector() as collector:
        todo_set_status.func(step_id=1, status="failed", note="il tool non risponde")

    entry = collector.since(0)["entries"][0]
    # Un passo fallito e' la riga che qualcuno cerchera' nel tab LOG: deve
    # distinguersi per livello, e portare il motivo con se'.
    assert entry["level"] == "ERROR"
    assert "il tool non risponde" in entry["message"]


def test_loading_a_skill_is_logged_with_its_name():
    (load_skill,) = build_skill_tools()

    with LogCollector() as collector:
        load_skill.func(name="comparison")

    entry = collector.since(0)["entries"][0]
    assert entry["source"] == "tools.skill_tools"
    assert "comparison" in entry["message"]


def test_asking_for_an_unknown_skill_is_logged_as_a_warning():
    (load_skill,) = build_skill_tools()

    with LogCollector() as collector:
        load_skill.func(name="inesistente")

    entry = collector.since(0)["entries"][0]
    assert entry["level"] == "WARNING"
    assert "inesistente" in entry["message"]


def test_producing_a_table_is_logged_with_its_shape():
    with LogCollector() as collector:
        ui_table.func(title="Confronto", columns=["A", "B"], rows=[["1", "2"]])

    entry = collector.since(0)["entries"][0]
    assert entry["source"] == "tools.ui_tools"
    # La forma della tabella e' cio' che serve per capire un artefatto sbagliato.
    assert "Confronto" in entry["message"]
    assert "1" in entry["message"]


def test_tool_logs_never_carry_the_whole_payload():
    # Il log e' una riga da leggere, non un dump: il payload completo e' gia'
    # nell'inspector. Una riga lunga rende il tab LOG inutilizzabile.
    with LogCollector() as collector:
        ui_table.func(
            title="Confronto",
            columns=["A", "B"],
            rows=[["testo molto lungo " * 20, "altro testo lungo " * 20]],
        )

    entry = collector.since(0)["entries"][0]
    assert len(entry["message"]) < 200
```

- [ ] **Step 2: Eseguire il test e verificare che fallisca**

Run: `cd demo-master-agent && uv run pytest tests/test_tool_logging.py -v`
Expected: FAIL, `assert len(entries) == 1` con `entries` vuoto — nessun modulo logga ancora.

- [ ] **Step 3: Implementare**

In ciascuno dei tre moduli di tool, aggiungere in cima:

```python
import logging

logger = logging.getLogger(__name__)
```

Poi una riga di log per ogni esito osservabile:

- `plan_tools.py`, dentro `todo_write`: dopo aver scritto il piano, un `logger.info` che dice quanti passi ha il piano.
- `plan_tools.py`, dentro `todo_set_status`: un `logger.error` con il motivo quando lo stato è `failed`, un `logger.info` col nuovo stato negli altri casi. Il messaggio deve contenere la stringa dello stato (`in_progress`, `completed`, `failed`), perché è su quella che i test filtrano.
- `skill_tools.py`, dentro `load_skill`: un `logger.info` col nome quando la skill esiste, un `logger.warning` col nome richiesto quando non esiste.
- `ui_tools.py`, dentro `ui_table`: un `logger.info` con titolo, numero di colonne e numero di righe. **Mai le celle**: il test `test_tool_logs_never_carry_the_whole_payload` lo impedisce, ed è il punto — il payload completo sta già nell'inspector.

Nessun log dentro `PlanStore`: è la struttura dati, e loggare lì produrrebbe righe doppie quando i tool la chiamano.

- [ ] **Step 4: Eseguire i test**

Run: `cd demo-master-agent && uv run pytest tests/test_tool_logging.py -v`
Expected: PASS, 7 test.

Run: `cd demo-master-agent && uv run pytest -q`
Expected: nessuna regressione.

- [ ] **Step 5: Verificare che i log arrivino davvero all'endpoint**

Con i container su e una chiave valida in `demo-infra/.env`:

```bash
cd demo-infra
docker compose up -d --build
curl -s -X POST http://localhost:8000/agui \
  -H 'Content-Type: application/json' \
  -d '{"threadId":"t1","runId":"r1","messages":[{"id":"m1","role":"user","content":"Confronta Python e Go su tipizzazione e concorrenza. Fai prima un piano."}],"state":{},"tools":[],"context":[],"forwardedProps":{}}' \
  > /dev/null
curl -s "http://localhost:8000/logs?cursor=0"
```

Expected: `entries` **non** vuoto, con righe da `tools.plan_tools`, `tools.skill_tools` e `tools.ui_tools`. Riportare l'output reale.

E il controllo che nessun segreto sia passato:

```bash
curl -s "http://localhost:8000/logs?cursor=0" | grep -c "sk-"
```

Expected: `0`.

- [ ] **Step 6: Commit**

```bash
cd demo-master-agent
git add src/demo/tools tests/test_tool_logging.py
git commit -m "feat: i tool emettono log operativi per il tab LOG"
```

---
### Task 6: La fixture di stream reale e i tipi degli eventi di ragionamento

I test del reducer girano su uno stream vero catturato dal backend, non su eventi scritti a mano: e' l'unico modo perche' una sorpresa del protocollo rompa un test invece della demo.

Lo stream di riferimento e' gia' stato catturato con `qwen/qwen3.8-27b` e contiene 974 righe: `RUN_STARTED`, 408 `REASONING_ENCRYPTED_VALUE`, un giro completo di `TOOL_CALL_*` su `ui_table`, `STATE_SNAPSHOT`, 50 `TEXT_MESSAGE_CONTENT`, `MESSAGES_SNAPSHOT`, `RUN_FINISHED`.

**Files:**
- Create: `demo-frontend/lib/agui/fixtures/stream-qwen.txt`
- Create: `demo-frontend/lib/agui/fixtures/load.ts`
- Modify: `demo-frontend/lib/agui/types.ts`
- Test: `demo-frontend/lib/agui/fixtures/load.test.ts`

**Interfaces:**
- Consumes: niente
- Produces:
  - `loadFixture(name: string): AGUIEvent[]` da `lib/agui/fixtures/load.ts` — legge un file `.txt` in formato SSE e restituisce gli eventi in ordine.
  - `AGUIEvent` esteso con: `REASONING_START`, `REASONING_MESSAGE_START`, `REASONING_ENCRYPTED_VALUE`, `REASONING_MESSAGE_END`, `REASONING_END`, `CUSTOM`.

- [ ] **Step 1: Portare la fixture nel repo**

Lo stream catturato e' versionato accanto a questo piano, in `demo-infra/docs/fixtures/stream-qwen.txt`. E' gia' stato controllato: non contiene chiavi.

```bash
cd demo-frontend
mkdir -p lib/agui/fixtures
cp ../demo-infra/docs/fixtures/stream-qwen.txt lib/agui/fixtures/stream-qwen.txt
grep -c . lib/agui/fixtures/stream-qwen.txt
```

Expected: `487`.

Per ricatturarne uno con un modello diverso — non serve per eseguire il piano — con i container su e una chiave valida:

```bash
curl -s -X POST http://localhost:8000/agui \
  -H 'Content-Type: application/json' \
  -d '{"threadId":"fix1","runId":"fix1","messages":[{"id":"m1","role":"user","content":"Confronta Python e Go su: tipizzazione, concorrenza, gestione errori. Usa una tabella."}],"state":{},"tools":[],"context":[],"forwardedProps":{}}' \
  -o lib/agui/fixtures/stream-qwen.txt
```

- [ ] **Step 2: Scrivere il test che fallisce**

Creare `demo-frontend/lib/agui/fixtures/load.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { loadFixture } from "./load";

describe("loadFixture", () => {
  it("legge lo stream reale in ordine", () => {
    const events = loadFixture("stream-qwen");

    expect(events[0].type).toBe("RUN_STARTED");
    expect(events.at(-1)?.type).toBe("RUN_FINISHED");
  });

  it("contiene la sequenza di ragionamento che ci aspettiamo", () => {
    const types = loadFixture("stream-qwen").map((e) => e.type);

    // Misurato sul filo: il testo del ragionamento arriva solo dentro
    // REASONING_ENCRYPTED_VALUE. REASONING_MESSAGE_CONTENT non arriva mai.
    expect(types).toContain("REASONING_MESSAGE_START");
    expect(types).toContain("REASONING_ENCRYPTED_VALUE");
    expect(types).not.toContain("REASONING_MESSAGE_CONTENT");
  });

  it("contiene un giro completo di tool con il suo risultato", () => {
    const types = loadFixture("stream-qwen").map((e) => e.type);

    expect(types).toContain("TOOL_CALL_START");
    expect(types).toContain("TOOL_CALL_RESULT");
    expect(types).toContain("STATE_SNAPSHOT");
  });
});
```

- [ ] **Step 3: Eseguire il test e verificare che fallisca**

Run: `cd demo-frontend && npx vitest run lib/agui/fixtures/load.test.ts`
Expected: FAIL, `Cannot find module './load'`.

- [ ] **Step 4: Implementare il caricatore**

Creare `demo-frontend/lib/agui/fixtures/load.ts`:

```ts
import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { AGUIEvent } from "../types";

/**
 * Legge uno stream SSE registrato e restituisce i suoi eventi.
 *
 * Solo per i test: usa `node:fs`, quindi non deve mai finire in un componente.
 * Il parsing e' volutamente ingenuo — le fixture hanno un evento per riga
 * `data: `, mentre il parser vero (lib/agui/client.ts) gestisce anche i casi
 * di frammentazione della rete.
 */
export function loadFixture(name: string): AGUIEvent[] {
  const path = join(__dirname, `${name}.txt`);
  return readFileSync(path, "utf-8")
    .split("\n")
    .filter((line) => line.startsWith("data: "))
    .map((line) => JSON.parse(line.slice("data: ".length)) as AGUIEvent);
}
```

- [ ] **Step 5: Estendere i tipi degli eventi**

In `demo-frontend/lib/agui/types.ts`, aggiungere all'unione `AGUIEvent`, prima del commento sul catch-all:

```ts
  // Ragionamento. Forma misurata sul filo con qwen/qwen3.8-27b:
  //   REASONING_START -> REASONING_MESSAGE_START -> N x REASONING_ENCRYPTED_VALUE
  //   -> REASONING_MESSAGE_END -> REASONING_END
  // REASONING_MESSAGE_CONTENT non viene mai emesso: il testo sta dentro
  // encryptedValue, che malgrado il nome non e' cifrato ed e' una stringa JSON.
  // L'identificativo del messaggio e' in `entityId`, non `messageId`.
  | { type: "REASONING_START"; messageId: string }
  | { type: "REASONING_MESSAGE_START"; messageId: string; role: string }
  | {
      type: "REASONING_ENCRYPTED_VALUE";
      subtype: string;
      entityId: string;
      encryptedValue: string;
    }
  | { type: "REASONING_MESSAGE_END"; messageId: string }
  | { type: "REASONING_END"; messageId: string }
  // CUSTOM lo emette il framework, non il nostro codice: `usage` a fine turno,
  // e in altri contesti approvazioni e consensi OAuth.
  | { type: "CUSTOM"; name: string; value: unknown }
```

- [ ] **Step 6: Eseguire i test**

Run: `cd demo-frontend && npx vitest run lib/agui/fixtures/load.test.ts && npx tsc --noEmit`
Expected: PASS, 3 test. `tsc` pulito.

- [ ] **Step 7: Commit**

```bash
cd demo-frontend
git add lib/agui/fixtures lib/agui/types.ts
git commit -m "test: fixture di stream reale e tipi degli eventi di ragionamento"
```

---

### Task 7: Il reducer produce entry tipizzate

E' il cambio che rende possibile tutto il resto: la chat smette di essere una lista di messaggi e diventa una timeline di entry, dove ragionamento, tool e artefatti sono cittadini di prima classe accanto al testo.

**Files:**
- Create: `demo-frontend/lib/agui/entries.ts`
- Modify: `demo-frontend/lib/agui/reducer.ts`
- Test: `demo-frontend/lib/agui/reducer.test.ts`

**Interfaces:**
- Consumes: `AGUIEvent` (Task 6), `loadFixture` (Task 6)
- Produces, da `lib/agui/entries.ts`:

```ts
export type Artifact =
  | { component: "ui-table"; id: string; title: string; columns: string[]; rows: string[][] }
  | { component: "unknown"; id: string; raw: unknown };

export type Entry =
  | { kind: "user"; id: string; text: string }
  | { kind: "assistant"; id: string; text: string }
  | { kind: "reasoning"; id: string; text: string; done: boolean }
  | { kind: "tool"; id: string; name: string; args: string; done: boolean }
  | { kind: "artifact"; id: string; artifact: Artifact };

export function parseReasoningDelta(encryptedValue: string): string;
export function parseArtifact(content: unknown): Artifact | null;
```

- Produces, da `lib/agui/reducer.ts`: `LabState` con `entries: Entry[]` al posto di `messages` e `toolCalls`; `shared` ed `events` restano.

`parseArtifact` restituisce `null` quando il tool result non e' un artefatto UI (i tool del piano restituiscono `{"component": "plan", ...}`, che non va reso in chat).

- [ ] **Step 1: Scrivere il test che fallisce**

Sostituire il contenuto di `demo-frontend/lib/agui/reducer.test.ts` mantenendo i casi della tappa 1 che restano validi, e aggiungere:

```ts
import { describe, expect, it } from "vitest";
import { loadFixture } from "./fixtures/load";
import { parseArtifact, parseReasoningDelta } from "./entries";
import { initialState, reduce } from "./reducer";
import type { AGUIEvent } from "./types";

function run(events: AGUIEvent[]) {
  return events.reduce(reduce, initialState);
}

describe("parseReasoningDelta", () => {
  it("estrae il testo dal payload che si chiama encryptedValue ma non lo e'", () => {
    const raw =
      '[{"type": "reasoning.text", "text": " user", "format": "unknown", "index": 0}]';

    expect(parseReasoningDelta(raw)).toBe(" user");
  });

  it("ignora i frammenti che non sono testo di ragionamento", () => {
    const raw = '[{"type": "reasoning.signature", "value": "abc"}]';

    expect(parseReasoningDelta(raw)).toBe("");
  });

  it("rifiuta un payload malformato invece di degradare in silenzio", () => {
    expect(() => parseReasoningDelta("non e' json")).toThrow(/ragionamento/);
  });
});

describe("parseArtifact", () => {
  it("riconosce una ui-table dentro la stringa JSON del tool result", () => {
    const content = JSON.stringify({
      component: "ui-table",
      id: "art_1",
      title: "Confronto",
      columns: ["A"],
      rows: [["1"]],
    });

    expect(parseArtifact(content)).toEqual({
      component: "ui-table",
      id: "art_1",
      title: "Confronto",
      columns: ["A"],
      rows: [["1"]],
    });
  });

  it("non tratta il risultato dei tool del piano come un artefatto", () => {
    // I tool del piano scrivono lo stato: il pannello del piano li rende,
    // la chat no. Senza questo, ogni todo_set_status sporca la timeline.
    expect(parseArtifact(JSON.stringify({ component: "plan", step_id: 1 }))).toBeNull();
  });

  it("degrada su una variante sconosciuta invece di sparire", () => {
    const content = JSON.stringify({ component: "ui-chart", id: "art_9" });

    expect(parseArtifact(content)).toEqual({
      component: "unknown",
      id: "art_9",
      raw: { component: "ui-chart", id: "art_9" },
    });
  });

  it("ignora un tool result che non e' JSON", () => {
    expect(parseArtifact("Ho mostrato la tabella.")).toBeNull();
  });
});

describe("reduce sullo stream reale", () => {
  const events = loadFixture("stream-qwen");
  const state = run(events);

  it("aggrega 408 delta di ragionamento in una entry per messaggio", () => {
    const reasoning = state.entries.filter((e) => e.kind === "reasoning");

    expect(reasoning.length).toBeLessThanOrEqual(2);
    expect(reasoning[0].text.length).toBeGreaterThan(50);
    expect(reasoning[0].done).toBe(true);
  });

  it("non lascia bolle vuote", () => {
    const empty = state.entries.filter(
      (e) => (e.kind === "assistant" || e.kind === "reasoning") && e.text === "",
    );

    expect(empty).toEqual([]);
  });

  it("produce una entry tool e la sua entry artefatto", () => {
    const tools = state.entries.filter((e) => e.kind === "tool");
    const artifacts = state.entries.filter((e) => e.kind === "artifact");

    expect(tools).toHaveLength(1);
    expect(tools[0]).toMatchObject({ name: "ui_table", done: true });
    expect(artifacts).toHaveLength(1);
  });

  it("tiene ogni evento nell'inspector, riconosciuto o no", () => {
    expect(state.events).toHaveLength(events.length);
  });

  it("chiude la run", () => {
    expect(state.running).toBe(false);
    expect(state.error).toBeNull();
  });

  it("l'ordine della timeline e' quello di arrivo", () => {
    const kinds = state.entries.map((e) => e.kind);

    // Il ragionamento precede il tool, il tool precede l'artefatto.
    expect(kinds.indexOf("reasoning")).toBeLessThan(kinds.indexOf("tool"));
    expect(kinds.indexOf("tool")).toBeLessThan(kinds.indexOf("artifact"));
  });
});
```

- [ ] **Step 2: Eseguire il test e verificare che fallisca**

Run: `cd demo-frontend && npx vitest run lib/agui/reducer.test.ts`
Expected: FAIL, `Cannot find module './entries'`.

- [ ] **Step 3: Implementare le entry**

Creare `demo-frontend/lib/agui/entries.ts`:

```ts
/** Le voci della timeline della chat. Un'unione discriminata su `kind`. */

export type Artifact =
  | {
      component: "ui-table";
      id: string;
      title: string;
      columns: string[];
      rows: string[][];
    }
  | { component: "unknown"; id: string; raw: unknown };

export type Entry =
  | { kind: "user"; id: string; text: string }
  | { kind: "assistant"; id: string; text: string }
  | { kind: "reasoning"; id: string; text: string; done: boolean }
  | { kind: "tool"; id: string; name: string; args: string; done: boolean }
  | { kind: "artifact"; id: string; artifact: Artifact };

/**
 * Estrae il testo da REASONING_ENCRYPTED_VALUE.
 *
 * Malgrado il nome, `encryptedValue` non e' cifrato: e' una stringa JSON che
 * contiene una lista di frammenti, di cui interessano quelli `reasoning.text`.
 */
export function parseReasoningDelta(encryptedValue: string): string {
  let fragments: unknown;
  try {
    fragments = JSON.parse(encryptedValue);
  } catch {
    throw new Error(
      `payload di ragionamento non interpretabile: ${encryptedValue.slice(0, 80)}`,
    );
  }
  if (!Array.isArray(fragments)) {
    throw new Error("payload di ragionamento: attesa una lista di frammenti");
  }
  return fragments
    .filter(
      (f): f is { type: string; text: string } =>
        typeof f === "object" &&
        f !== null &&
        (f as { type?: unknown }).type === "reasoning.text" &&
        typeof (f as { text?: unknown }).text === "string",
    )
    .map((f) => f.text)
    .join("");
}

/**
 * Riconosce un artefatto UI dentro il `content` di un TOOL_CALL_RESULT.
 *
 * Restituisce null quando il tool result non e' un artefatto: i tool del piano
 * restituiscono `{"component": "plan"}`, che il pannello del piano rende e la
 * chat no. Restituisce null anche quando il content e' testo semplice.
 */
export function parseArtifact(content: unknown): Artifact | null {
  if (typeof content !== "string") return null;

  let payload: unknown;
  try {
    payload = JSON.parse(content);
  } catch {
    // Testo per il modello, non un artefatto. Non e' un errore.
    return null;
  }
  if (typeof payload !== "object" || payload === null) return null;

  const shape = payload as Record<string, unknown>;
  if (typeof shape.component !== "string") return null;
  if (shape.component === "plan") return null;

  const id = typeof shape.id === "string" ? shape.id : "art_?";

  if (
    shape.component === "ui-table" &&
    typeof shape.title === "string" &&
    isStringArray(shape.columns) &&
    Array.isArray(shape.rows) &&
    shape.rows.every(isStringArray)
  ) {
    return {
      component: "ui-table",
      id,
      title: shape.title,
      columns: shape.columns,
      rows: shape.rows,
    };
  }

  // Variante non ancora implementata: si rende un fallback esplicito invece
  // di far sparire in silenzio qualcosa che il backend ha prodotto.
  return { component: "unknown", id, raw: payload };
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}
```

- [ ] **Step 4: Riscrivere il reducer**

Sostituire `demo-frontend/lib/agui/reducer.ts`:

```ts
import { parseArtifact, parseReasoningDelta, type Entry } from "./entries";
import type { AGUIEvent } from "./types";

export type { Entry } from "./entries";

export interface LabState {
  running: boolean;
  error: string | null;
  entries: Entry[];
  shared: Record<string, unknown>;
  events: AGUIEvent[];
}

export const initialState: LabState = {
  running: false,
  error: null,
  entries: [],
  shared: {},
  events: [],
};

/** Rimpiazza la entry con quell'id, lasciando invariate le altre. */
function patch(entries: Entry[], id: string, change: (entry: Entry) => Entry): Entry[] {
  return entries.map((e) => (e.id === id ? change(e) : e));
}

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
        entries: [...next.entries, { kind: "assistant", id: event.messageId, text: "" }],
      };

    case "TEXT_MESSAGE_CONTENT":
      return {
        ...next,
        entries: patch(next.entries, event.messageId, (e) =>
          e.kind === "assistant" ? { ...e, text: e.text + event.delta } : e,
        ),
      };

    case "TEXT_MESSAGE_END":
      // Ogni tool call e' avvolta da START/END senza CONTENT in mezzo:
      // senza questo filtro la chat mostra una bolla vuota per ogni tool.
      return {
        ...next,
        entries: next.entries.filter(
          (e) => e.id !== event.messageId || e.kind !== "assistant" || e.text !== "",
        ),
      };

    // Il testo del ragionamento arriva solo dentro REASONING_ENCRYPTED_VALUE,
    // un evento per token: REASONING_MESSAGE_CONTENT non viene mai emesso.
    // I delta si fondono in una entry sola, altrimenti un giro di Qwen ne
    // produce 400 e la timeline diventa illeggibile.
    case "REASONING_MESSAGE_START":
      return {
        ...next,
        entries: [
          ...next.entries,
          { kind: "reasoning", id: event.messageId, text: "", done: false },
        ],
      };

    case "REASONING_ENCRYPTED_VALUE":
      return {
        ...next,
        entries: patch(next.entries, event.entityId, (e) =>
          e.kind === "reasoning"
            ? { ...e, text: e.text + parseReasoningDelta(event.encryptedValue) }
            : e,
        ),
      };

    case "REASONING_MESSAGE_END":
      return {
        ...next,
        entries: next.entries
          .filter((e) => e.id !== event.messageId || e.kind !== "reasoning" || e.text !== "")
          .map((e) =>
            e.id === event.messageId && e.kind === "reasoning" ? { ...e, done: true } : e,
          ),
      };

    case "TOOL_CALL_START":
      return {
        ...next,
        entries: [
          ...next.entries,
          {
            kind: "tool",
            id: event.toolCallId,
            name: event.toolCallName,
            args: "",
            done: false,
          },
        ],
      };

    case "TOOL_CALL_ARGS":
      return {
        ...next,
        entries: patch(next.entries, event.toolCallId, (e) =>
          e.kind === "tool" ? { ...e, args: e.args + event.delta } : e,
        ),
      };

    case "TOOL_CALL_END":
      return {
        ...next,
        entries: patch(next.entries, event.toolCallId, (e) =>
          e.kind === "tool" ? { ...e, done: true } : e,
        ),
      };

    case "TOOL_CALL_RESULT": {
      const artifact = parseArtifact(event.content);
      if (artifact === null) return next;
      return {
        ...next,
        entries: [
          ...next.entries,
          { kind: "artifact", id: `${event.toolCallId}:artifact`, artifact },
        ],
      };
    }

    case "STATE_SNAPSHOT":
      return { ...next, shared: event.snapshot };

    // REASONING_START, REASONING_END, CUSTOM, MESSAGES_SNAPSHOT ed eventi
    // ancora sconosciuti: registrati nell'inspector, nessun altro effetto.
    default:
      return next;
  }
}

/** Aggiunge il messaggio dell'utente: il server non lo rimanda indietro. */
export function withUserMessage(state: LabState, id: string, text: string): LabState {
  return {
    ...state,
    error: null,
    entries: [...state.entries, { kind: "user", id, text }],
  };
}
```

- [ ] **Step 5: Eseguire i test**

Run: `cd demo-frontend && npx vitest run && npx tsc --noEmit`
Expected: test PASS. I test della tappa 1 che parlavano di `state.messages` vanno riscritti in termini di `state.entries`.

`tsc` fallisce temporaneamente gia' dal Task 7: `Chat.tsx` importa ancora `ChatMessage` e `Lab.tsx` usa `messages` in due punti. Il Task 8 migra Chat e il Task 12 migra Lab; non aggiungere compatibilita' temporanea nel reducer. Verificare che non compaiano errori diversi da questi riferimenti. L'intervallo con la migrazione incompleta parte dal Task 7 e termina con il Task 12.

- [ ] **Step 6: Commit**

```bash
cd demo-frontend
git add lib/agui/entries.ts lib/agui/reducer.ts lib/agui/reducer.test.ts
git commit -m "feat: il reducer produce entry tipizzate e aggrega il ragionamento"
```

---

### Task 8: La chat diventa una timeline

Un componente per variante di entry, sotto `components/entries/`. Il dispatch cresce a ogni tappa: tenerlo in un file solo lo farebbe diventare illeggibile entro la tappa 3.

**Files:**
- Create: `demo-frontend/components/entries/UserEntry.tsx`
- Create: `demo-frontend/components/entries/AssistantEntry.tsx`
- Create: `demo-frontend/components/entries/ReasoningEntry.tsx`
- Create: `demo-frontend/components/entries/ToolEntry.tsx`
- Create: `demo-frontend/components/entries/index.tsx`
- Modify: `demo-frontend/components/Chat.tsx`
- Test: `demo-frontend/components/entries/entries.test.tsx`

**Interfaces:**
- Consumes: `Entry`, `Artifact` (Task 7)
- Produces: `EntryView({ entry }: { entry: Entry })` da `components/entries/index.tsx` — il dispatch su `entry.kind`. `Chat` riceve `entries: Entry[]`, `running: boolean`, `error: string | null`, `onSend: (text: string) => void`.

Il test dei componenti richiede due dipendenze di sviluppo che il progetto non ha ancora.

- [ ] **Step 1: Aggiungere le dipendenze di test dei componenti**

```bash
cd demo-frontend
npm install -D @testing-library/react @testing-library/jest-dom jsdom
```

In `demo-frontend/vitest.config.ts` (crearlo se non esiste):

```ts
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
  },
  resolve: {
    alias: { "@": fileURLToPath(new URL("./", import.meta.url)) },
  },
});
```

Se `@vitejs/plugin-react` non e' installato: `npm install -D @vitejs/plugin-react`.

Run: `cd demo-frontend && npx vitest run`
Expected: i test esistenti del reducer continuano a passare sotto la nuova config.

- [ ] **Step 2: Scrivere il test che fallisce**

Creare `demo-frontend/components/entries/entries.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Entry } from "@/lib/agui/entries";
import { EntryView } from "./index";

describe("EntryView", () => {
  it("rende il messaggio dell'utente", () => {
    const entry: Entry = { kind: "user", id: "1", text: "ciao" };
    render(<EntryView entry={entry} />);

    expect(screen.getByText("ciao")).toBeDefined();
  });

  it("rende il ragionamento chiuso, non aperto", () => {
    const entry: Entry = { kind: "reasoning", id: "2", text: "penso", done: true };
    const { container } = render(<EntryView entry={entry} />);

    // Il ragionamento e' contesto, non risposta: arriva collassato.
    const details = container.querySelector("details");
    expect(details?.open).toBe(false);
    expect(screen.getByText("Ragionamento")).toBeDefined();
  });

  it("mostra il nome del tool, non i suoi argomenti grezzi", () => {
    const entry: Entry = {
      kind: "tool",
      id: "3",
      name: "load_skill",
      args: '{"name":"comparison"}',
      done: true,
    };
    render(<EntryView entry={entry} />);

    expect(screen.getByText("load_skill")).toBeDefined();
    expect(screen.queryByText(/"name":"comparison"/)).toBeNull();
  });

  it("rende una ui-table come tabella vera", () => {
    const entry: Entry = {
      kind: "artifact",
      id: "4",
      artifact: {
        component: "ui-table",
        id: "art_1",
        title: "Confronto",
        columns: ["Tema", "A"],
        rows: [["Tipi", "statici"]],
      },
    };
    render(<EntryView entry={entry} />);

    expect(screen.getByRole("table")).toBeDefined();
    expect(screen.getByText("Confronto")).toBeDefined();
    expect(screen.getByText("statici")).toBeDefined();
  });

  it("degrada visibilmente su un artefatto sconosciuto", () => {
    const entry: Entry = {
      kind: "artifact",
      id: "5",
      artifact: { component: "unknown", id: "art_9", raw: { component: "ui-chart" } },
    };
    render(<EntryView entry={entry} />);

    // Meglio un riquadro che dice "non so renderlo" di un artefatto sparito.
    expect(screen.getByText(/non so rendere/i)).toBeDefined();
  });
});
```

- [ ] **Step 3: Eseguire il test e verificare che fallisca**

Run: `cd demo-frontend && npx vitest run components/entries/entries.test.tsx`
Expected: FAIL, `Cannot find module './index'`.

- [ ] **Step 4: Implementare i componenti**

`demo-frontend/components/entries/UserEntry.tsx`:

```tsx
export function UserEntry({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[80%] whitespace-pre-wrap rounded-2xl bg-[var(--surface-accent)] px-4 py-3 text-sm">
        {text}
      </div>
    </div>
  );
}
```

`demo-frontend/components/entries/AssistantEntry.tsx`:

```tsx
export function AssistantEntry({ text }: { text: string }) {
  return <div className="whitespace-pre-wrap px-1 text-sm leading-relaxed">{text}</div>;
}
```

`demo-frontend/components/entries/ReasoningEntry.tsx`:

```tsx
/** Il ragionamento e' contesto, non risposta: arriva collassato. */
export function ReasoningEntry({ text, done }: { text: string; done: boolean }) {
  return (
    <details className="rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2">
      <summary className="cursor-pointer font-mono text-[11px] uppercase tracking-wide text-[var(--muted)]">
        Ragionamento {done ? "" : "…"}
      </summary>
      <p className="whitespace-pre-wrap pt-2 text-xs text-[var(--muted)]">{text}</p>
    </details>
  );
}
```

`demo-frontend/components/entries/ToolEntry.tsx`:

```tsx
/** Una riga compatta: il nome del tool. Gli argomenti stanno nell'inspector. */
export function ToolEntry({ name, done }: { name: string; done: boolean }) {
  return (
    <div className="inline-flex items-center gap-2 rounded-md border border-[var(--border)] bg-[var(--surface)] px-2 py-1 font-mono text-xs">
      <span className="text-[var(--muted)]">{">_"}</span>
      <span>{name}</span>
      {!done && <span className="text-[var(--muted)]">…</span>}
    </div>
  );
}
```

`demo-frontend/components/entries/index.tsx`:

```tsx
import type { Entry } from "@/lib/agui/entries";
import { UiTable } from "../artifacts/UiTable";
import { AssistantEntry } from "./AssistantEntry";
import { ReasoningEntry } from "./ReasoningEntry";
import { ToolEntry } from "./ToolEntry";
import { UserEntry } from "./UserEntry";

/**
 * Dispatch su `entry.kind`.
 *
 * Lo switch e' esaustivo: aggiungere una variante a `Entry` senza aggiungerla
 * qui e' un errore di compilazione, non una entry che sparisce a runtime.
 */
export function EntryView({ entry }: { entry: Entry }) {
  switch (entry.kind) {
    case "user":
      return <UserEntry text={entry.text} />;
    case "assistant":
      return <AssistantEntry text={entry.text} />;
    case "reasoning":
      return <ReasoningEntry text={entry.text} done={entry.done} />;
    case "tool":
      return <ToolEntry name={entry.name} done={entry.done} />;
    case "artifact":
      return <UiTable artifact={entry.artifact} />;
  }
  const unreachable: never = entry;
  throw new Error(`Variante di entry non supportata: ${JSON.stringify(unreachable)}`);
}
```

`demo-frontend/components/artifacts/UiTable.tsx` (creato qui perche' `index.tsx` lo importa; il Task 10 non lo tocca piu'):

```tsx
import type { Artifact } from "@/lib/agui/entries";

export function UiTable({ artifact }: { artifact: Artifact }) {
  if (artifact.component !== "ui-table") {
    // Il backend ha prodotto qualcosa che questa versione del frontend non
    // conosce. Dirlo e' meglio che farlo sparire.
    return (
      <div className="rounded-lg border border-dashed border-[var(--border)] p-3 text-xs text-[var(--muted)]">
        Non so rendere questo artefatto ({artifact.id}).
      </div>
    );
  }

  return (
    <figure className="overflow-x-auto rounded-xl border border-[var(--border)]">
      <figcaption className="border-b border-[var(--border)] px-4 py-3 text-sm font-medium">
        {artifact.title}
      </figcaption>
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr>
            {artifact.columns.map((column) => (
              <th
                key={column}
                className="border-b border-[var(--border)] px-4 py-2 text-left font-medium text-[var(--muted)]"
              >
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {artifact.rows.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {row.map((cell, cellIndex) => (
                <td
                  key={cellIndex}
                  className="border-b border-[var(--border)] px-4 py-2 align-top"
                >
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}
```

- [ ] **Step 5: Riscrivere `Chat.tsx`**

```tsx
"use client";

import type { Entry } from "@/lib/agui/entries";
import { EntryView } from "./entries";

interface Props {
  entries: Entry[];
  running: boolean;
  error: string | null;
  onSend: (text: string) => void;
}

export function Chat({ entries, running, error, onSend }: Props) {
  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 space-y-4 overflow-y-auto px-6 py-6">
        {entries.map((entry) => (
          <EntryView key={entry.id} entry={entry} />
        ))}
        {running && (
          <p className="font-mono text-[11px] uppercase tracking-wide text-[var(--muted)]">
            Sto lavorando…
          </p>
        )}
        {error && <p className="text-xs text-red-600">errore: {error}</p>}
      </div>

      <form
        className="border-t border-[var(--border)] px-6 py-4"
        onSubmit={(e) => {
          e.preventDefault();
          const input = e.currentTarget.elements.namedItem("q") as HTMLInputElement;
          if (!input.value.trim()) return;
          onSend(input.value);
          input.value = "";
        }}
      >
        <div className="flex items-center gap-2 rounded-full border border-[var(--border)] px-4 py-2">
          <input
            name="q"
            disabled={running}
            placeholder="Scrivi un messaggio…"
            className="flex-1 bg-transparent text-sm outline-none"
          />
          <button
            type="submit"
            disabled={running}
            className="rounded-full bg-[var(--foreground)] px-4 py-1.5 text-xs text-[var(--background)] disabled:opacity-40"
          >
            invia
          </button>
        </div>
      </form>
    </div>
  );
}
```

- [ ] **Step 6: Eseguire i test**

Run: `cd demo-frontend && npx vitest run && npx tsc --noEmit`
Expected: PASS, 5 test nuovi. `tsc` fallira' su `Lab.tsx`, che passa ancora `messages`: lo sistema il Task 12.

- [ ] **Step 7: Commit**

```bash
cd demo-frontend
git add components/entries components/artifacts components/Chat.tsx vitest.config.ts package.json package-lock.json
git commit -m "feat: la chat e' una timeline di entry tipizzate"
```

---

### Task 9: Il pannello del piano di lavoro

Funzione pura di `shared.plan`. Nessuna logica di stato: se il pannello deve calcolare qualcosa oltre al conteggio, il contratto e' sbagliato.

**Files:**
- Create: `demo-frontend/components/PlanPanel.tsx`
- Test: `demo-frontend/components/PlanPanel.test.tsx`
- Delete: `demo-frontend/components/StatePanel.tsx` (il piano lo sostituisce)

**Interfaces:**
- Consumes: `shared: Record<string, unknown>` da `LabState`
- Produces: `PlanPanel({ shared }: { shared: Record<string, unknown> })`

- [ ] **Step 1: Scrivere il test che fallisce**

Creare `demo-frontend/components/PlanPanel.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PlanPanel } from "./PlanPanel";

const PLAN = {
  status: "in_progress",
  steps: [
    {
      id: 1,
      title: "Carica la skill",
      detail: "Skill di confronto.",
      source: "skill:comparison#1",
      status: "completed",
      started_at: "2026-09-07T09:00:00+00:00",
      ended_at: "2026-09-07T09:00:02+00:00",
      note: null,
    },
    {
      id: 2,
      title: "Produci la tabella",
      detail: "Confronto tabellare.",
      source: "ui_table",
      status: "in_progress",
      started_at: "2026-09-07T09:00:02+00:00",
      ended_at: null,
      note: null,
    },
  ],
};

describe("PlanPanel", () => {
  it("conta i passi completati sul totale", () => {
    render(<PlanPanel shared={{ plan: PLAN }} />);

    expect(screen.getByText("1/2")).toBeDefined();
  });

  it("mostra titolo, dettaglio e origine di ogni passo", () => {
    render(<PlanPanel shared={{ plan: PLAN }} />);

    expect(screen.getByText("Carica la skill")).toBeDefined();
    expect(screen.getByText("Skill di confronto.")).toBeDefined();
    expect(screen.getByText("skill:comparison#1")).toBeDefined();
  });

  it("dice che non c'e' un piano quando lo stato e' idle", () => {
    render(<PlanPanel shared={{ plan: { status: "idle", steps: [] } }} />);

    expect(screen.getByText(/nessun piano/i)).toBeDefined();
  });

  it("regge uno stato condiviso senza piano", () => {
    // Prima del primo STATE_SNAPSHOT lo stato del client e' vuoto.
    render(<PlanPanel shared={{}} />);

    expect(screen.getByText(/nessun piano/i)).toBeDefined();
  });

  it("mostra la nota di un passo fallito", () => {
    const failed = {
      status: "failed",
      steps: [{ ...PLAN.steps[0], status: "failed", note: "il tool non ha risposto" }],
    };
    render(<PlanPanel shared={{ plan: failed }} />);

    expect(screen.getByText("il tool non ha risposto")).toBeDefined();
  });
});
```

- [ ] **Step 2: Eseguire il test e verificare che fallisca**

Run: `cd demo-frontend && npx vitest run components/PlanPanel.test.tsx`
Expected: FAIL, `Cannot find module './PlanPanel'`.

- [ ] **Step 3: Implementare**

Creare `demo-frontend/components/PlanPanel.tsx`:

```tsx
"use client";

interface PlanStep {
  id: number;
  title: string;
  detail: string;
  source: string;
  status: string;
  note: string | null;
}

interface Plan {
  status: string;
  steps: PlanStep[];
}

const MARKER: Record<string, string> = {
  pending: "○",
  in_progress: "◉",
  completed: "●",
  failed: "✕",
};

const TONE: Record<string, string> = {
  pending: "text-[var(--muted)]",
  in_progress: "text-amber-600",
  completed: "text-emerald-600",
  failed: "text-red-600",
};

function readPlan(shared: Record<string, unknown>): Plan | null {
  const plan = shared.plan;
  if (typeof plan !== "object" || plan === null) return null;
  const steps = (plan as { steps?: unknown }).steps;
  if (!Array.isArray(steps) || steps.length === 0) return null;
  return plan as Plan;
}

/** Funzione pura di `shared.plan`. Nessuna logica di stato qui dentro. */
export function PlanPanel({ shared }: { shared: Record<string, unknown> }) {
  const plan = readPlan(shared);

  if (plan === null) {
    return (
      <section className="border-b border-[var(--border)] px-4 py-3">
        <h2 className="font-mono text-[11px] uppercase tracking-wide text-[var(--muted)]">
          Piano di lavoro
        </h2>
        <p className="pt-1 text-xs text-[var(--muted)]">nessun piano in corso</p>
      </section>
    );
  }

  const done = plan.steps.filter((s) => s.status === "completed").length;

  return (
    <section className="border-b border-[var(--border)] px-4 py-3">
      <header className="flex items-baseline justify-between">
        <h2 className="font-mono text-[11px] uppercase tracking-wide text-[var(--muted)]">
          Piano di lavoro
        </h2>
        <span className="font-mono text-xs">
          {done}/{plan.steps.length}
        </span>
      </header>

      <ol className="space-y-3 pt-3">
        {plan.steps.map((step) => (
          <li key={step.id} className="flex gap-2">
            <span className={`pt-0.5 text-xs ${TONE[step.status] ?? ""}`}>
              {MARKER[step.status] ?? "○"}
            </span>
            <div className="min-w-0">
              <p className="text-sm">{step.title}</p>
              {step.detail && (
                <p className="text-xs text-[var(--muted)]">{step.detail}</p>
              )}
              {step.source && (
                <code className="mt-1 inline-block rounded bg-[var(--surface)] px-1.5 py-0.5 font-mono text-[11px] text-[var(--muted)]">
                  {step.source}
                </code>
              )}
              {step.note && <p className="pt-1 text-xs text-red-600">{step.note}</p>}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
```

- [ ] **Step 4: Eseguire i test e rimuovere `StatePanel`**

Run: `cd demo-frontend && npx vitest run components/PlanPanel.test.tsx`
Expected: PASS, 5 test.

```bash
cd demo-frontend
git rm components/StatePanel.tsx
```

`Lab.tsx` lo importa ancora: `tsc` fallira' fino al Task 12. E' atteso.

- [ ] **Step 5: Commit**

```bash
cd demo-frontend
git add components/PlanPanel.tsx components/PlanPanel.test.tsx
git commit -m "feat: pannello del piano di lavoro come funzione pura dello stato"
```

---

### Task 10: L'inspector guadagna il filtro del ragionamento

**Files:**
- Modify: `demo-frontend/components/Inspector.tsx`
- Test: `demo-frontend/components/Inspector.test.tsx`

**Interfaces:**
- Consumes: `AGUIEvent` (Task 6), `loadFixture` (Task 6)
- Produces: `Inspector({ events }: { events: AGUIEvent[] })` con i filtri `tutti | ragionamento | tool | stato | testo`

- [ ] **Step 1: Scrivere il test che fallisce**

Creare `demo-frontend/components/Inspector.test.tsx`:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { loadFixture } from "@/lib/agui/fixtures/load";
import { Inspector } from "./Inspector";

const EVENTS = loadFixture("stream-qwen");

describe("Inspector", () => {
  it("conta tutti gli eventi, anche quelli che non sa interpretare", () => {
    render(<Inspector events={EVENTS} />);

    expect(screen.getByText(String(EVENTS.length))).toBeDefined();
  });

  it("il filtro ragionamento mostra solo il ragionamento", () => {
    render(<Inspector events={EVENTS} />);
    fireEvent.click(screen.getByRole("button", { name: "ragionamento" }));

    const rows = screen.getAllByRole("group");
    expect(rows.length).toBeGreaterThan(0);
    for (const row of rows) {
      expect(row.textContent).toMatch(/^REASONING_/);
    }
  });

  it("il filtro tool mostra solo i TOOL_CALL_*", () => {
    render(<Inspector events={EVENTS} />);
    fireEvent.click(screen.getByRole("button", { name: "tool" }));

    for (const row of screen.getAllByRole("group")) {
      expect(row.textContent).toMatch(/^TOOL_CALL_/);
    }
  });

  it("il filtro tutti include il ragionamento", () => {
    // Non si nasconde nulla dal flusso grezzo: e' il punto dell'inspector.
    render(<Inspector events={EVENTS} />);

    expect(screen.getAllByRole("group").length).toBe(EVENTS.length);
  });
});
```

- [ ] **Step 2: Eseguire il test e verificare che fallisca**

Run: `cd demo-frontend && npx vitest run components/Inspector.test.tsx`
Expected: FAIL — il filtro `ragionamento` non esiste, `getByRole("button", { name: "ragionamento" })` non trova nulla.

- [ ] **Step 3: Implementare**

In `demo-frontend/components/Inspector.tsx`, sostituire la costante `FILTERS`:

```ts
const FILTERS = {
  tutti: () => true,
  ragionamento: (e: AGUIEvent) => e.type.startsWith("REASONING"),
  tool: (e: AGUIEvent) => e.type.startsWith("TOOL_CALL"),
  stato: (e: AGUIEvent) => e.type.startsWith("STATE") || e.type.startsWith("RUN"),
  testo: (e: AGUIEvent) => e.type.startsWith("TEXT_MESSAGE"),
} as const;
```

E dare a ogni riga il ruolo che il test cerca, sostituendo il `<details>` con:

```tsx
        {shown.map((e, i) => (
          <details key={i} role="group" className="border-b border-[var(--border)] py-1">
            <summary className="cursor-pointer text-amber-700">{e.type}</summary>
            <pre className="overflow-x-auto pt-1 text-[var(--muted)]">
              {JSON.stringify(e, null, 2)}
            </pre>
          </details>
        ))}
```

- [ ] **Step 4: Eseguire i test**

Run: `cd demo-frontend && npx vitest run components/Inspector.test.tsx`
Expected: PASS, 4 test.

- [ ] **Step 5: Commit**

```bash
cd demo-frontend
git add components/Inspector.tsx components/Inspector.test.tsx
git commit -m "feat: filtro ragionamento nell'inspector"
```

---

### Task 11: Il tab LOG

Legge `GET /logs` del Task 5, a cursore, mentre la run va avanti. Il polling parte quando una run e' in corso e si ferma quando finisce: un pannello che interroga il server a vuoto per sempre e' un difetto, non una funzione.

**Files:**
- Create: `demo-frontend/lib/agui/logs.ts`
- Create: `demo-frontend/components/LogPanel.tsx`
- Test: `demo-frontend/lib/agui/logs.test.ts`
- Test: `demo-frontend/components/LogPanel.test.tsx` (polling, cursore, coda finale, cleanup ed errori)

**Interfaces:**
- Consumes: niente
- Produces:
  - `fetchLogs(cursor: number, signal?: AbortSignal): Promise<{entries: LogEntry[]; cursor: number; dropped: number}>` da `lib/agui/logs.ts`
  - `LogEntry = { seq: number; ts: string; level: string; source: string; message: string }`
  - `LOGS_URL: string` — derivato da `NEXT_PUBLIC_AGUI_URL` sostituendo `/agui` con `/logs`, cosi' non serve una seconda variabile d'ambiente e le due non possono divergere.
  - `LogPanel({ running }: { running: boolean })`

- [ ] **Step 1: Scrivere il test che fallisce**

Creare `demo-frontend/lib/agui/logs.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from "vitest";
import { LOGS_URL, fetchLogs } from "./logs";

afterEach(() => vi.unstubAllGlobals());

describe("fetchLogs", () => {
  it("deriva l'URL dei log da quello di AG-UI", () => {
    // Una seconda variabile d'ambiente potrebbe divergere dalla prima:
    // meglio derivarla, cosi' non c'e' niente da tenere allineato.
    expect(LOGS_URL.endsWith("/logs")).toBe(true);
    expect(LOGS_URL).not.toContain("/agui");
  });

  it("passa il cursore e restituisce la pagina", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ entries: [], cursor: 7, dropped: 0 }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const page = await fetchLogs(3);

    expect(fetchMock.mock.calls[0][0]).toContain("cursor=3");
    expect(page.cursor).toBe(7);
  });

  it("solleva quando il server risponde male", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 503 }));

    await expect(fetchLogs(0)).rejects.toThrow(/503/);
  });
});
```

- [ ] **Step 2: Eseguire il test e verificare che fallisca**

Run: `cd demo-frontend && npx vitest run lib/agui/logs.test.ts`
Expected: FAIL, `Cannot find module './logs'`.

- [ ] **Step 3: Implementare il client dei log**

Creare `demo-frontend/lib/agui/logs.ts`:

```ts
export interface LogEntry {
  seq: number;
  ts: string;
  level: string;
  source: string;
  message: string;
}

export interface LogPage {
  entries: LogEntry[];
  cursor: number;
  dropped: number;
}

const AGUI_URL = process.env.NEXT_PUBLIC_AGUI_URL ?? "http://127.0.0.1:8000/agui";

/**
 * I log stanno su un endpoint proprio, non sullo stream AG-UI: gli eventi
 * CUSTOM del protocollo sono riservati al framework e il codice applicativo
 * non puo' emetterne. L'URL si deriva da quello di AG-UI invece di essere una
 * seconda variabile d'ambiente, che potrebbe divergere.
 */
export const LOGS_URL = AGUI_URL.replace(/\/agui$/, "/logs");

export async function fetchLogs(cursor: number, signal?: AbortSignal): Promise<LogPage> {
  const response = await fetch(`${LOGS_URL}?cursor=${cursor}`, { signal, cache: "no-store" });
  if (!response.ok) {
    throw new Error(`/logs ha risposto ${response.status}`);
  }
  return (await response.json()) as LogPage;
}
```

- [ ] **Step 4: Implementare il pannello**

Creare `demo-frontend/components/LogPanel.tsx`:

```tsx
"use client";

import { useEffect, useRef, useState } from "react";
import { fetchLogs, type LogEntry } from "@/lib/agui/logs";

const TONE: Record<string, string> = {
  ERROR: "text-red-600",
  WARNING: "text-amber-600",
  INFO: "text-[var(--muted)]",
};

/**
 * I log operativi del server.
 *
 * Interroga /logs mentre una run e' in corso, piu' una volta subito dopo per
 * raccogliere la coda. A riposo non chiama nulla: un pannello che interroga il
 * server per sempre e' un difetto, non una funzione.
 */
export function LogPanel({ running }: { running: boolean }) {
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const cursor = useRef(0);
  const wasRunning = useRef(false);
  const [dropped, setDropped] = useState(0);

  useEffect(() => {
    const shouldPoll = running || wasRunning.current;
    wasRunning.current = running;
    if (!shouldPoll) return;

    let cancelled = false;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function poll() {
      try {
        const page = await fetchLogs(cursor.current, controller.signal);
        if (cancelled) return;
        cursor.current = page.cursor;
        if (page.entries.length > 0) {
          setEntries((current) => [...current, ...page.entries]);
        }
        setDropped((current) => current + page.dropped);
        setError(null);
      } catch (err) {
        if (!cancelled) setError(String(err));
      } finally {
        if (!cancelled && running) timer = setTimeout(poll, 1000);
      }
    }

    void poll();
    return () => {
      cancelled = true;
      controller.abort();
      clearTimeout(timer);
    };
  }, [running]);

  return (
    <div className="min-h-0 flex-1 overflow-y-auto font-mono text-[11px]">
      {error && <p role="alert" className="py-1 text-red-600">log non raggiungibili: {error}</p>}
      {dropped > 0 && <p role="status" className="py-1 text-amber-600">{dropped} righe di log non più disponibili</p>}
      {entries.length === 0 && !error && (
        <p className="py-1 text-[var(--muted)]">nessun log</p>
      )}
      {entries.map((entry) => (
        <div key={entry.seq} className="flex gap-2 border-b border-[var(--border)] py-1">
          <span className="shrink-0 text-[var(--muted)]">{entry.ts.slice(11, 19)}</span>
          <span className={`shrink-0 ${TONE[entry.level] ?? ""}`}>[{entry.source}]</span>
          <span className="whitespace-pre-wrap break-all">{entry.message}</span>
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 5: Eseguire i test**

Run: `cd demo-frontend && npx vitest run lib/agui/logs.test.ts components/LogPanel.test.tsx && npx tsc --noEmit`
Expected: test PASS. TypeScript segnala soltanto i quattro errori noti in Lab fino al Task 12. Verificare con timer controllati: nessun polling iniziale a riposo, richieste seriali, una lettura finale e cleanup anche dopo la run.

- [ ] **Step 6: Commit**

```bash
cd demo-frontend
git add lib/agui/logs.ts lib/agui/logs.test.ts components/LogPanel.tsx components/LogPanel.test.tsx
git commit -m "feat: tab LOG alimentato dall'endpoint /logs"
```

---

### Task 12: La composizione del laboratorio e l'estetica

Ultimo task del frontend, ed e' l'unico in cui l'aspetto conta. Fino a qui la struttura era la posta in gioco; adesso e' ferma e si puo' vestire.

**REQUIRED SUB-SKILL: invocare `frontend-design` prima di scrivere il CSS di questo task.** Il laboratorio di riferimento e' il punto di partenza, non un bersaglio da copiare pixel per pixel.

Quello che si e' visto nella registrazione, come riferimento e non come specifica:

- **header**: nome del laboratorio a sinistra con sottotitolo in maiuscoletto monospace, badge delle tecnologie (`AG-UI`, `CopilotKit`, `A2UI`, `shadcn/ui`), identita' utente, azioni `aggiorna` / `esci`, interruttore del tema
- **due colonne**: chat a sinistra, colonna destra fissa intorno ai 420px
- **colonna destra in due sezioni**: piano di lavoro in alto, inspector sotto, con i tab `EVENT INSPECTOR` / `LOG` e il contatore accanto al primo
- **footer**: una riga che dichiara la natura dell'esercizio
- **timeline**: marcatori a pallino sul filo verticale a sinistra delle entry

**Files:**
- Modify: `demo-frontend/components/Lab.tsx`
- Modify: `demo-frontend/components/Inspector.tsx` (i tab)
- Modify: `demo-frontend/app/globals.css` (i token di colore)
- Modify: `demo-frontend/app/layout.tsx` (titolo e lingua)
- Create: `demo-frontend/components/LabHeader.tsx`
- Modify: `demo-frontend/components/Chat.tsx` (timeline e stato vuoto)
- Test: `demo-frontend/components/Lab.test.tsx`, `demo-frontend/components/Inspector.test.tsx`

**Interfaces:**
- Consumes: `Chat` (Task 8), `PlanPanel` (Task 9), `Inspector` (Task 10), `LogPanel` (Task 11), `withUserMessage` (Task 7)
- Produces: la pagina intera. Nessuna interfaccia per i task successivi.

- [ ] **Step 1: Definire i token di colore**

I componenti dei task 8-11 usano gia' `var(--border)`, `var(--surface)`, `var(--surface-accent)` e `var(--muted)`: senza queste definizioni rendono trasparenti.

In `demo-frontend/app/globals.css`, dentro `:root` e dentro il blocco `@media (prefers-color-scheme: dark)`:

```css
:root {
  --background: #ffffff;
  --foreground: #171717;
  --surface: #f6f6f7;
  --surface-accent: #eef1f6;
  --border: #e4e4e7;
  --muted: #71717a;
}

@media (prefers-color-scheme: dark) {
  :root {
    --background: #0a0a0a;
    --foreground: #ededed;
    --surface: #17171a;
    --surface-accent: #1d2027;
    --border: #27272a;
    --muted: #a1a1aa;
  }
}
```

Sostituire anche `font-family: Arial, Helvetica, sans-serif;` con `font-family: var(--font-sans);`: il progetto carica Geist in `layout.tsx` e poi non lo usa.

- [ ] **Step 2: Correggere i metadati della pagina**

In `demo-frontend/app/layout.tsx`:

```tsx
export const metadata: Metadata = {
  title: "Laboratorio AG-UI",
  description: "Chat, stato condiviso ed event inspector su un solo stream SSE.",
};
```

E `lang="it"` al posto di `lang="en"`: la pagina e' in italiano, e lo screen reader legge di conseguenza.

- [ ] **Step 3: Scrivere l'header**

Creare `demo-frontend/components/LabHeader.tsx`:

```tsx
const BADGES = ["AG-UI", "MAF 1.17", "Next.js"];

export function LabHeader() {
  return (
    <header className="flex items-center gap-4 border-b border-[var(--border)] px-6 py-3">
      <div>
        <p className="text-sm font-semibold leading-tight">Laboratorio AG-UI</p>
        <p className="font-mono text-[10px] uppercase tracking-widest text-[var(--muted)]">
          demo di studio
        </p>
      </div>
      <div className="flex gap-1.5">
        {BADGES.map((badge) => (
          <span
            key={badge}
            className="rounded-md border border-[var(--border)] px-2 py-0.5 font-mono text-[10px] text-[var(--muted)]"
          >
            {badge}
          </span>
        ))}
      </div>
    </header>
  );
}
```

A2A e' tappa 3: non dichiararlo fra le tecnologie attive. I badge dichiarano cosa c'e' davvero sotto: `CopilotKit` e `shadcn/ui` sono nel laboratorio di riferimento ma non qui, e scriverli sarebbe una bugia sul contenuto.

- [ ] **Step 4: Mettere i tab nell'inspector**

In `demo-frontend/components/Inspector.tsx`, aggiungere la prop `running` e i due tab, tenendo i filtri gia' scritti nel Task 10:

```tsx
"use client";

import { useState } from "react";
import type { AGUIEvent } from "@/lib/agui/types";
import { LogPanel } from "./LogPanel";

const FILTERS = {
  tutti: () => true,
  ragionamento: (e: AGUIEvent) => e.type.startsWith("REASONING"),
  tool: (e: AGUIEvent) => e.type.startsWith("TOOL_CALL"),
  stato: (e: AGUIEvent) => e.type.startsWith("STATE") || e.type.startsWith("RUN"),
  testo: (e: AGUIEvent) => e.type.startsWith("TEXT_MESSAGE"),
} as const;

export function Inspector({ events, running }: { events: AGUIEvent[]; running: boolean }) {
  const [tab, setTab] = useState<"eventi" | "log">("eventi");
  const [filter, setFilter] = useState<keyof typeof FILTERS>("tutti");
  const shown = events.filter(FILTERS[filter]);

  return (
    <section aria-label="Inspector" className="inspector flex min-h-0 flex-1 flex-col p-4">
      <nav aria-label="Vista inspector" className="inspector-tabs mb-3 flex gap-4">
        <button
          type="button"
          onClick={() => setTab("eventi")}
          aria-pressed={tab === "eventi"}
          className="font-mono text-[11px] uppercase tracking-wide"
        >
          Event inspector <span className="tabular-nums">{events.length}</span>
        </button>
        <button
          type="button"
          onClick={() => setTab("log")}
          aria-pressed={tab === "log"}
          className="font-mono text-[11px] uppercase tracking-wide"
        >
          Log
        </button>
      </nav>

      <div hidden={tab !== "eventi"} className={tab === "eventi" ? "flex min-h-0 flex-1 flex-col" : undefined}>
        <div className="mb-2 flex flex-wrap gap-1">
          {(Object.keys(FILTERS) as (keyof typeof FILTERS)[]).map((name) => (
            <button
              key={name}
              type="button"
              onClick={() => setFilter(name)}
              aria-pressed={filter === name}
              className={`rounded-full px-2 py-0.5 text-xs ${
                filter === name
                  ? "bg-[var(--foreground)] text-[var(--background)]"
                  : "bg-[var(--surface)] text-[var(--muted)]"
              }`}
            >
              {name}
            </button>
          ))}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto font-mono text-xs">
          {shown.map((e, i) => (
            <details key={i} role="group" className="border-b border-[var(--border)] py-1">
              <summary className="cursor-pointer text-[var(--foreground)]">{e.type}</summary>
              <pre className="overflow-x-auto pt-1 text-[var(--muted)]">
                {JSON.stringify(e, null, 2)}
              </pre>
            </details>
          ))}
        </div>
      </div>
      {/* Il polling segue la run anche quando si guardano gli eventi. */}
      <div hidden={tab !== "log"} className={tab === "log" ? "flex min-h-0 flex-1 flex-col" : undefined}>
        <LogPanel running={running} />
      </div>
    </section>
  );
}
```

Aggiornare tutti i render e rerender dei test Inspector con `running`. Tenere LogPanel montato nella vista nascosta: smontarlo al cambio tab perderebbe cursore e storico. Verificare polling nella vista eventi, cambio tab e coda finale.

- [ ] **Step 5: Ricomporre `Lab.tsx`**

```tsx
"use client";

import { useCallback, useRef, useState } from "react";
import { runAgent } from "@/lib/agui/client";
import { initialState, reduce, withUserMessage, type LabState } from "@/lib/agui/reducer";
import { Chat } from "./Chat";
import { Inspector } from "./Inspector";
import { PlanPanel } from "./PlanPanel";
import { LabHeader } from "./LabHeader";

export function Lab() {
  const [state, setState] = useState<LabState>(initialState);
  const [threadId] = useState(() => crypto.randomUUID());
  const inFlight = useRef(false);

  const send = useCallback(
    async (text: string) => {
      if (inFlight.current) return;
      inFlight.current = true;
      const userMessage = { id: crypto.randomUUID(), role: "user", content: text };
      // Il messaggio utente lo aggiunge il client: il server non lo rimanda indietro.
      setState((s) => ({ ...withUserMessage(s, userMessage.id, text), running: true }));

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
          // Il trasporto puo' restare aperto dopo l'evento terminale.
          // Il modulo si sblocca soltanto quando runAgent termina.
          (event) => setState((s) => ({ ...reduce(s, event), running: true })),
        );
      } catch (err) {
        setState((s) => ({ ...s, running: false, error: String(err) }));
      } finally {
        inFlight.current = false;
        setState((s) => ({ ...s, running: false }));
      }
    },
    [threadId],
  );

  return (
    <div className="lab-shell flex flex-col">
      <LabHeader />
      <div className="lab-grid min-h-0 flex-1">
      <main className="flex min-h-0 min-w-0 flex-col border-r border-[var(--border)]" aria-label="Conversazione">
        <Chat
          entries={state.entries}
          running={state.running}
          error={state.error}
          onSend={send}
        />
      </main>
      <aside className="lab-aside flex min-h-0 min-w-0 flex-col" aria-label="Piano e attività dell'agente">
        <PlanPanel shared={state.shared} />
        <Inspector events={state.events} running={state.running} />
      </aside>
      </div>
      <footer className="border-t border-[var(--border)] px-6 py-2 font-mono text-[10px] text-[var(--muted)]">
        Esercizio di laboratorio · L&apos;agente può sbagliare. Segui il piano e ispeziona gli eventi.
      </footer>
    </div>
  );
}
```

- [ ] **Step 6: Invocare `frontend-design` e rifinire**

Ora che la struttura compila e i test passano, invocare la skill `frontend-design` e applicarne le indicazioni a: scala tipografica, ritmo verticale, marcatori della timeline, densita' dell'inspector, stati vuoti. Non toccare la forma dei dati: se una scelta estetica richiede un campo nuovo dal backend, e' fuori da questo task.

- [ ] **Step 7: Verificare**

Run: `cd demo-frontend && npx vitest run && npx tsc --noEmit && npm run build`
Expected: tutti i test passano, `tsc` pulito, build ok. `tsc` deve essere pulito **adesso**: i fallimenti attesi dei task 8 e 9 si chiudono qui.

- [ ] **Step 8: Commit**

```bash
cd demo-frontend
git add components app
git commit -m "feat: composizione e estetica del laboratorio"
```

---

### Task 13: Verifica d'insieme e documentazione

Un piano che finisce con "i test passano" non ha finito: la tappa 1 e' stata dichiarata completa con 38 test verdi e nessuno aveva mai aperto la pagina in un browser.

**Files:**
- Modify: `demo-infra/docs/specs/2026-09-07-agui-lab-design.md`
- Modify: `demo-infra/README.md`
- Modify: `demo-master-agent/README.md`

- [ ] **Step 1: Ricostruire e alzare tutto**

```bash
cd demo-infra
docker compose build
docker compose up -d
docker compose ps
```

Expected: entrambi i servizi su, `master-agent` healthy. Riportare l'output reale.

- [ ] **Step 2: Provare il flusso completo nel browser**

Aprire `http://localhost:3000` e mandare:

> Confronta Python e Go su tipizzazione, concorrenza e gestione degli errori. Fai prima un piano di lavoro.

Verificare, uno per uno:

- il pannello **Piano di lavoro** si popola e il contatore avanza (`0/3`, `1/3`, …) **mentre** la run va avanti, non alla fine
- la chat mostra il ragionamento collassato, il chip del tool e la tabella renderizzata
- nessuna bolla vuota
- il filtro `ragionamento` dell'inspector restringe, e `tutti` continua a contenerlo
- il tab **LOG** mostra righe con orario e sorgente
- la console del browser non ha errori, in particolare nessun errore CORS sulla `GET /logs`

Se il modello non chiama `todo_write`, il difetto e' nelle istruzioni del Task 5, non nel frontend: si corregge li'.

- [ ] **Step 3: Verificare che i log non perdano segreti**

```bash
curl -s "http://localhost:8000/logs?cursor=0" | grep -c "sk-"
```

Expected: `0`. Se non e' zero, il filtro del Task 4 non tiene e **va sistemato prima di committare**.

- [ ] **Step 4: Aggiornare la spec**

In `demo-infra/docs/specs/2026-09-07-agui-lab-design.md`:

1. In §4, aggiungere una sezione **4.4 Log operativi** che dica: gli eventi `CUSTOM` di AG-UI sono riservati al framework e non emettibili dal codice applicativo, quindi i log viaggiano su `GET /logs?cursor=<int>`, lette a cursore da un buffer circolare di 500 righe, filtrate ai soli logger `demo.*` perche' quelli di libreria contengono la chiave API. Dichiarare che questo e' il secondo canale del sistema e che il principio "tre viste di un solo stream" vale per chat, piano e inspector, non per il tab log.
2. In §5, aggiungere il vincolo misurato sul ragionamento: `REASONING_MESSAGE_CONTENT` non viene emesso, il testo sta in `REASONING_ENCRYPTED_VALUE.encryptedValue` come stringa JSON non cifrata, l'id e' in `entityId`.
3. In §6, portare la tappa 2 a **fatto** e la tappa 3 a *in corso* o *da fare* secondo lo stato reale.
4. **Correggere il limite dichiarato sul profilo LM Studio.** La spec dice che con Gemma il tool `ui_table` non verra' chiamato. Misurato: Gemma 4 12B lo chiama, con `TOOL_CALL_START/ARGS/END/RESULT` e `STATE_SNAPSHOT` completi. La frase va riscritta o tolta.

- [ ] **Step 5: Aggiornare i README**

In `demo-master-agent/README.md`, documentare: i tre gruppi di tool, l'endpoint `/logs`, il formato `SKILL.md` e come aggiungere una skill, e il limite dichiarato del `PlanStore` — un piano per processo, quindi due schede del browser lo condividono.

In `demo-infra/README.md`, aggiungere che serve un modello con tool-calling reale perche' il piano funzioni, e i due profili gia' verificati (`qwen/qwen3.8-27b` su OpenRouter, `google/gemma-4-12b` su LM Studio).

- [ ] **Step 6: Commit**

```bash
cd demo-infra
git add docs README.md
git commit -m "docs: contratto dei log, vincolo sul ragionamento, tappa 2 conclusa"

cd ../demo-master-agent
git add README.md
git commit -m "docs: tool del piano, skill e endpoint dei log"
```

---

## Self-review del piano

Fatta sulla spec `2026-09-07-agui-lab-design.md`, sezione per sezione.

**Copertura.** §4.1 piano di lavoro: Task 2 (contratto) e Task 9 (pannello). §4.2 artefatti con `id`: Task 1, e resa in Task 8. §2.4 skill in formato Agent Skills: Task 3. Tappa 2 come definita in §6 — "piano di lavoro, `SKILL.md` + `load_skill`, `ui_table`, filtri inspector, tab log" — coperta dai task 1-13. §4.3 sottoagenti: **fuori**, e' tappa 3, dichiarato.

**Scostamenti dalla spec, entrambi deliberati e da riportare nella spec al Task 13:**

1. La spec §8 chiede che il reducer *"rifiuti uno stream malformato invece di degradare in silenzio"*. Il Task 7 lo fa solo per il payload di ragionamento, dove la forma e' documentata e una sorpresa e' significativa. Un tool result che non e' JSON restituisce `null` e non solleva, perche' e' il caso normale: `text` e' testo per il modello.
2. Il tab log introduce un secondo canale HTTP. Non era previsto: la spec assumeva implicitamente che tutto passasse dallo stream. Il Task 13 lo scrive nero su bianco.

**Coerenza dei tipi.** `Entry` e `Artifact` sono definiti nel Task 7 e consumati con gli stessi nomi nei task 8 e 9. `LogEntry` e' definito nel Task 11 e combacia campo per campo con la voce prodotta dal `LogCollector` del Task 4 (`seq`, `ts`, `level`, `source`, `message`). La forma di `plan` e' identica fra Task 2 (produzione), Task 5 (`DEFAULT_STATE`) e Task 9 (consumo).

**Ordine.** I task 1-5 sono backend e vanno in sequenza. Il 6 dipende dal 5 solo per ricatturare la fixture, e nemmeno da quello se il file e' gia' nel repo. I task 8-11 dipendono tutti dal 7 ma **non fra loro**: si possono eseguire in parallelo. Il 12 li richiede tutti. Il 13 chiude.

**Due punti dove chi implementa va probabilmente in difficolta':**

- Il Task 5 richiede che il modello chiami `todo_write` di sua iniziativa. Se non lo fa, si aggiustano le istruzioni, non il codice — e conviene provare con `qwen/qwen3.8-27b`, che il tool-calling ce l'ha davvero.
- Il Task 8 introduce il rendering dei componenti nei test: `jsdom`, `@testing-library/react` e `vitest.config.ts` non esistono ancora nel repo. Se la configurazione resiste, va risolta prima di scrivere i componenti, non dopo.
