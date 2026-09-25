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

function patch(entries: Entry[], id: string, change: (entry: Entry) => Entry): Entry[] {
  return entries.map((e) => (e.id === id ? change(e) : e));
}

export function reduce(state: LabState, event: AGUIEvent): LabState {
  const next: LabState = { ...state, events: [...state.events, event] };

  switch (event.type) {
    case "RUN_STARTED":
      return { ...next, running: true, error: null };

    case "RUN_FINISHED":
      return { ...next, running: false };

    case "RUN_ERROR":
      return { ...next, running: false, error: String(event.message ?? "error") };

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
      return {
        ...next,
        entries: next.entries.filter(
          (e) => e.id !== event.messageId || e.kind !== "assistant" || e.text !== "",
        ),
      };

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

    case "SUBAGENT_STARTED":
      return {
        ...next,
        entries: [
          ...next.entries,
          {
            kind: "subagent",
            id: event.subagentRunId,
            name: event.name,
            description: event.description ?? "",
            status: "running",
          },
        ],
      };

    case "SUBAGENT_FINISHED":
      return {
        ...next,
        entries: patch(next.entries, event.subagentRunId, (e) =>
          e.kind === "subagent" && e.status === "running" ? { ...e, status: "done" } : e,
        ),
      };

    case "SUBAGENT_ERROR":
      return {
        ...next,
        entries: patch(next.entries, event.subagentRunId, (e) =>
          e.kind === "subagent" ? { ...e, status: "failed", error: event.message } : e,
        ),
      };

    case "STATE_SNAPSHOT":
      return { ...next, shared: event.snapshot };

    default:
      return next;
  }
}

export function withUserMessage(state: LabState, id: string, text: string): LabState {
  return {
    ...state,
    error: null,
    entries: [...state.entries, { kind: "user", id, text }],
  };
}

/**
 * Appends `textDelta` to the timeline as an assistant entry — creating one
 * under `id` on its first call for that id, growing it on the next ones.
 * Voice turns arrive as whole-sentence chunks rather than the token-by-token
 * deltas `TEXT_MESSAGE_CONTENT` patches, but the shape in the timeline is the
 * same either way: one growing assistant entry, not one bubble per chunk.
 */
export function withAssistantText(state: LabState, id: string, textDelta: string): LabState {
  if (!state.entries.some((e) => e.id === id)) {
    return { ...state, entries: [...state.entries, { kind: "assistant", id, text: textDelta }] };
  }
  return { ...state, entries: patch(state.entries, id, (e) => (e.kind === "assistant" ? { ...e, text: e.text + textDelta } : e)) };
}
