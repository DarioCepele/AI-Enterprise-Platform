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
