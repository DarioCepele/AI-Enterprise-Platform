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

    case "TEXT_MESSAGE_END":
      // Ogni tool call e' avvolta da START/END senza CONTENT in mezzo:
      // senza questo filtro la chat mostrerebbe una bolla vuota per ogni tool.
      return {
        ...next,
        messages: next.messages.filter(
          (m) => m.id !== event.messageId || m.content.length > 0,
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

    case "STATE_SNAPSHOT":
      return { ...next, shared: event.snapshot };

    // MESSAGES_SNAPSHOT ed eventi ancora sconosciuti: registrati
    // nell'inspector, nessun altro effetto.
    default:
      return next;
  }
}
