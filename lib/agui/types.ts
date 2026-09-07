// Eventi AG-UI, in camelCase come arrivano sul filo.
// Sottoinsieme usato dal laboratorio nelle tappe 1 e 2.

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
  // Ragionamento. Forma misurata sul filo con qwen/qwen3.8-27b:
  // REASONING_START -> REASONING_MESSAGE_START -> N x REASONING_ENCRYPTED_VALUE
  // -> REASONING_MESSAGE_END -> REASONING_END.
  // In questo stream il testo non arriva come REASONING_MESSAGE_CONTENT:
  // encryptedValue contiene una stringa JSON con i frammenti di testo.
  // Solo REASONING_ENCRYPTED_VALUE usa entityId; i delimitatori usano messageId.
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
  // CUSTOM lo emette il framework: usage, approvazioni e consensi OAuth.
  | { type: "CUSTOM"; name: string; value: unknown };

// Nessun membro catch-all nell'unione: combaciando con ogni `type` distruggerebbe
// il narrowing, e dentro ogni `case` del reducer i campi tornerebbero `unknown`.
// Gli eventi non ancora modellati (le tappe 2 e 3 ne aggiungono) arrivano comunque
// a runtime: li raccoglie il ramo `default` del reducer, che li registra
// nell'inspector senza interpretarli.

export interface RunInput {
  threadId: string;
  runId: string;
  messages: { id: string; role: string; content: string }[];
  state: Record<string, unknown>;
  tools: unknown[];
  context: unknown[];
  forwardedProps: Record<string, unknown>;
}
