import {
  parseArtifact,
  parseReasoningDelta,
  type ApprovalEntryData,
  type ApprovalRequest,
  type Entry,
} from "./entries";
import type { AGUIEvent, Interrupt, ResumeEntry } from "./types";

export type { Entry } from "./entries";

/**
 * Microsoft Agent Framework also announces each approval as a call to this
 * tool, for clients that predate AG-UI interrupts. The interrupt carries the
 * same question, so the call is left out of the timeline (the inspector
 * still shows it).
 */
const LEGACY_APPROVAL_TOOL = "confirm_changes";

/** What the person reads when their answer could not be applied. */
const APPROVAL_FAILURES: Record<string, string> = {
  APPROVAL_RESUME_NOT_FOUND:
    "This approval is no longer open: it expired, the agent restarted, or the " +
    "answer reached another replica of the agent. Nothing was run. Ask again.",
  APPROVAL_RESUME_INVALID: "This answer conflicts with one already given. Nothing new was run.",
};

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

    case "RUN_FINISHED": {
      if (event.outcome?.type !== "interrupt" || event.outcome.interrupts.length === 0) {
        return { ...next, running: false };
      }
      const approval: ApprovalEntryData = {
        kind: "approval",
        id: `approval:${event.outcome.interrupts[0].id}`,
        requests: event.outcome.interrupts.map((interrupt) => requestOf(next.entries, interrupt)),
        status: "pending",
        decisions: {},
      };
      return { ...next, running: false, entries: [...next.entries, approval] };
    }

    case "RUN_ERROR": {
      const message = String(event.message ?? "error");
      const sent = [...next.entries]
        .reverse()
        .find((e): e is ApprovalEntryData => e.kind === "approval" && e.status === "sent");
      if (!sent || !event.code?.startsWith("APPROVAL_")) {
        return { ...next, running: false, error: message };
      }
      const error = APPROVAL_FAILURES[event.code] ?? `The answer was not applied: ${message}`;
      return {
        ...next,
        running: false,
        entries: patch(next.entries, sent.id, (e) =>
          e.kind === "approval" ? { ...e, status: "failed", error } : e,
        ),
      };
    }

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
      if (event.toolCallName === LEGACY_APPROVAL_TOOL) return next;
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

function requestOf(entries: Entry[], interrupt: Interrupt): ApprovalRequest {
  const call = entries.find((e) => e.kind === "tool" && e.id === interrupt.toolCallId);
  return {
    interruptId: interrupt.id,
    tool: call?.kind === "tool" ? call.name : "an action",
    args: call?.kind === "tool" ? call.args : "",
    question: interrupt.message ?? "Approve this action?",
  };
}

/** True while the agent waits for a person: no other input may go out. */
export function awaitingApproval(state: LabState): boolean {
  return state.entries.some((e) => e.kind === "approval" && e.status === "pending");
}

/** Records the answers given on one approval entry, as they are sent. */
export function withDecisions(
  state: LabState,
  entryId: string,
  decisions: Record<string, boolean>,
): LabState {
  return {
    ...state,
    error: null,
    entries: patch(state.entries, entryId, (e) =>
      e.kind === "approval" ? { ...e, status: "sent", decisions } : e,
    ),
  };
}

/** The `resume` of the next run: every open question answered, yes or no. */
export function resumeOf(decisions: Record<string, boolean>): ResumeEntry[] {
  return Object.entries(decisions).map(([interruptId, approved]) => ({
    interruptId,
    status: "resolved",
    payload: { approved },
  }));
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
