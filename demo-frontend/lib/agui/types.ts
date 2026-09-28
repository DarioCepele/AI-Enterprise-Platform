/**
 * A question the agent stopped on, per the AG-UI interrupts spec: the run
 * ends with `outcome.type === "interrupt"`, and the next run on the thread
 * must answer every open one in its `resume`. `toolCallId` names the tool
 * call waiting for a person's approval, when that is the reason.
 */
export interface Interrupt {
  id: string;
  reason: string;
  message?: string;
  toolCallId?: string;
  responseSchema?: unknown;
  expiresAt?: string;
  metadata?: Record<string, unknown>;
}

export type RunOutcome =
  | { type: "success" }
  | { type: "interrupt"; interrupts: Interrupt[] };

/** One answer to an open interrupt, sent in the next run's `resume`. */
export interface ResumeEntry {
  interruptId: string;
  status: "resolved" | "cancelled";
  payload?: unknown;
}

export type AGUIEvent =
  | { type: "RUN_STARTED"; threadId: string; runId: string }
  | { type: "RUN_FINISHED"; threadId: string; runId: string; outcome?: RunOutcome }
  | { type: "RUN_ERROR"; message: string; code?: string }
  | { type: "TEXT_MESSAGE_START"; messageId: string; role: string }
  | { type: "TEXT_MESSAGE_CONTENT"; messageId: string; delta: string }
  | { type: "TEXT_MESSAGE_END"; messageId: string }
  | { type: "TOOL_CALL_START"; toolCallId: string; toolCallName: string }
  | { type: "TOOL_CALL_ARGS"; toolCallId: string; delta: string }
  | { type: "TOOL_CALL_END"; toolCallId: string }
  | { type: "TOOL_CALL_RESULT"; toolCallId: string; content: unknown }
  | { type: "STATE_SNAPSHOT"; snapshot: Record<string, unknown> }
  | { type: "STATE_DELTA"; delta: unknown[] }
  | { type: "MESSAGES_SNAPSHOT"; messages: unknown[] }
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
  | {
      type: "SUBAGENT_STARTED";
      subagentRunId: string;
      name: string;
      description?: string;
      parentToolCallId?: string;
    }
  | { type: "SUBAGENT_FINISHED"; subagentRunId: string; result?: unknown }
  | { type: "SUBAGENT_ERROR"; subagentRunId: string; message: string; code?: string }
  | { type: "CUSTOM"; name: string; value: unknown };

/** A single piece of user-authored text, per the AG-UI multimodal input standard. */
export interface TextInputPart {
  type: "text";
  text: string;
}

/**
 * A video attachment referenced by URL, per the AG-UI multimodal input
 * standard. `mimeType` is required in practice, not just in the type: the
 * server tolerates a missing one when handing content to the model (it
 * falls back to `video/*`), but the separate path that builds the AG-UI
 * messages snapshot does not - a part with no `mimeType` fails Pydantic
 * validation there and takes the whole run down. Always send the file's own
 * `File.type`.
 */
export interface VideoInputPart {
  type: "video";
  source: { type: "url"; value: string; mimeType: string };
}

export type MessagePart = TextInputPart | VideoInputPart;

export interface RunInput {
  threadId: string;
  runId: string;
  /** Plain text for an ordinary message, or a list of parts once an attachment is involved. */
  messages: { id: string; role: string; content: string | MessagePart[] }[];
  state: Record<string, unknown>;
  tools: unknown[];
  context: unknown[];
  forwardedProps: Record<string, unknown>;
  /** Answers to the interrupts the previous run ended on: all of them, at once. */
  resume?: ResumeEntry[];
}
