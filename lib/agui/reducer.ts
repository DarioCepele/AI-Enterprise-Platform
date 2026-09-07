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
