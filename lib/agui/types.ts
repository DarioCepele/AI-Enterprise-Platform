export type AGUIEvent =
  | { type: "RUN_STARTED"; threadId: string; runId: string }
  | { type: "RUN_FINISHED"; threadId: string; runId: string }
  | { type: "RUN_ERROR"; message: string }
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

export interface RunInput {
  threadId: string;
  runId: string;
  messages: { id: string; role: string; content: string }[];
  state: Record<string, unknown>;
  tools: unknown[];
  context: unknown[];
  forwardedProps: Record<string, unknown>;
}
