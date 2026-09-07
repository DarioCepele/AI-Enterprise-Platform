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
- `Message` **non accetta** `text=` in 1.17: `Message(role=..., text="x")` solleva `TypeError: unexpected keyword argument 'text'`. Si costruisce con `Message(role=..., contents=[Content.from_text("x")])`. L'attributo `.text` esiste in lettura, non in scrittura.
- Un chat client che deve eseguire tool **deve** ereditare da `agent_framework._tools.FunctionInvocationLayer` oltre che da `BaseChatClient`, nell'ordine `class X(FunctionInvocationLayer, BaseChatClient)`. Senza, `Agent` logga *"The provided chat client does not support function invoking"* e i tool non vengono mai eseguiti.
- Nessuna autenticazione in questa tappa. Niente MSAL, niente OBO.
- Struttura **multi-repo**: `demo-master-agent`, `demo-frontend`, `demo-infra` sono repo git distinti e fratelli dentro `C:\project\demo` (in WSL: `/mnt/c/project/demo`). Ogni task committa nel proprio repo. Non esiste un repo che li contiene tutti.
- Ogni repo deployabile ha il suo `Dockerfile`; `demo-infra/compose.yaml` li costruisce da percorsi fratelli. In sviluppo si gira nativi, i container servono alla verifica d'insieme.

---

### Task 8: UI a tre pannelli

**Files:**
- Create: `demo-frontend/components/Chat.tsx`
- Create: `demo-frontend/components/StatePanel.tsx`
- Create: `demo-frontend/components/Inspector.tsx`
- Create: `demo-frontend/components/Lab.tsx`
- Modify: `demo-frontend/app/page.tsx`

**Interfaces:**
- Consumes: `runAgent` (Task 6), `reduce` / `initialState` / `LabState` (Task 7)
- Produces: la pagina completa

- [ ] **Step 1: Scrivere `demo-frontend/components/Chat.tsx`**

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

- [ ] **Step 2: Scrivere `demo-frontend/components/StatePanel.tsx`**

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

- [ ] **Step 3: Scrivere `demo-frontend/components/Inspector.tsx`**

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

- [ ] **Step 4: Scrivere `demo-frontend/components/Lab.tsx`**

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

- [ ] **Step 5: Sostituire `demo-frontend/app/page.tsx`**

```tsx
import { Lab } from "@/components/Lab";

export default function Page() {
  return <Lab />;
}
```

- [ ] **Step 6: Verificare che compili**

Run: `cd /mnt/c/project/demo/demo-frontend && npx tsc --noEmit && npm run build`
Expected: nessun errore

- [ ] **Step 7: Verifica manuale end-to-end**

Due terminali:

```bash
# terminale 1
cd /mnt/c/project/demo/demo-master-agent && DEMO_FAKE_CLIENT=true uv run python -m demo

# terminale 2
cd /mnt/c/project/demo/demo-frontend && npm run dev
```

Aprire `http://localhost:3000`, scrivere "ciao", premere invia.

Expected:
- il testo della risposta compare **progressivamente**, non tutto insieme;
- l'inspector elenca `RUN_STARTED`, `TEXT_MESSAGE_START`, più `TEXT_MESSAGE_CONTENT`, `TEXT_MESSAGE_END`, `MESSAGES_SNAPSHOT`, `RUN_FINISHED`;
- i filtri `testo` / `tool` / `stato` riducono la lista;
- nessun errore CORS in console;
- il pannello "Stato condiviso" resta vuoto — **è corretto**: il fake client non chiama tool, quindi nessuno `STATE_SNAPSHOT` viene emesso. Si popola nella Task 9 con un LLM vero.

- [ ] **Step 8: Commit**

```bash
cd /mnt/c/project/demo/demo-frontend
git add components app/page.tsx
git commit -m "feat: UI a tre pannelli alimentata da un solo stream"
```

---

---
