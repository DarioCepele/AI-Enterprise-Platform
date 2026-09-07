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

### Task 7: Reducer

Il pezzo con più logica, e l'unico interamente testabile senza browser né rete.

**Files:**
- Create: `demo-frontend/lib/agui/reducer.ts`
- Test: `demo-frontend/lib/agui/reducer.test.ts`

**Interfaces:**
- Consumes: `AGUIEvent` dalla Task 6
- Produces: `LabState`, `initialState: LabState`, `reduce(state: LabState, event: AGUIEvent): LabState`

- [ ] **Step 1: Scrivere il test**

`demo-frontend/lib/agui/reducer.test.ts`:

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

Run: `cd /mnt/c/project/demo/demo-frontend && npm test`
Expected: FAIL — `Failed to resolve import "./reducer"`

- [ ] **Step 3: Implementare il reducer**

`demo-frontend/lib/agui/reducer.ts`:

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
cd /mnt/c/project/demo/demo-frontend
git add lib/agui/reducer.ts lib/agui/reducer.test.ts
git commit -m "feat: reducer AG-UI puro con test"
```

---

---
