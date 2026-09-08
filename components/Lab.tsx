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
  const abort = useRef<AbortController | null>(null);

  // Interrompere e' una scelta dell'utente, non un errore: la run si chiude
  // con quello che ha gia' prodotto, senza riquadro rosso.
  const stop = useCallback(() => abort.current?.abort(), []);

  const send = useCallback(
    async (text: string) => {
      if (inFlight.current) return;
      inFlight.current = true;
      const controller = new AbortController();
      abort.current = controller;
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
          controller.signal,
        );
      } catch (err) {
        if (controller.signal.aborted) {
          setState((s) => ({ ...s, running: false }));
        } else {
          setState((s) => ({ ...s, running: false, error: String(err) }));
        }
      } finally {
        inFlight.current = false;
        abort.current = null;
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
          onStop={stop}
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
