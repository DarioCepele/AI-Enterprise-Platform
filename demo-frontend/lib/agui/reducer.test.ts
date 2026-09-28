import { describe, expect, it } from "vitest";
import { loadFixture } from "./fixtures/load";
import { parseArtifact, parseReasoningDelta } from "./entries";
import {
  awaitingApproval,
  initialState,
  reduce,
  resumeOf,
  withDecisions,
  withUserMessage,
} from "./reducer";
import type { AGUIEvent } from "./types";

function run(events: AGUIEvent[]) {
  return events.reduce(reduce, initialState);
}

describe("reduce", () => {
  it("accumulates text deltas into a single message", () => {
    const state = run([
      { type: "RUN_STARTED", threadId: "t", runId: "r" },
      { type: "TEXT_MESSAGE_START", messageId: "m1", role: "assistant" },
      { type: "TEXT_MESSAGE_CONTENT", messageId: "m1", delta: "ciao " },
      { type: "TEXT_MESSAGE_CONTENT", messageId: "m1", delta: "mondo" },
      { type: "TEXT_MESSAGE_END", messageId: "m1" },
    ]);

    expect(state.entries).toEqual([{ kind: "assistant", id: "m1", text: "ciao mondo" }]);
  });

  it("marks the run as running and then finished", () => {
    let state = run([{ type: "RUN_STARTED", threadId: "t", runId: "r" }]);
    expect(state.running).toBe(true);

    state = reduce(state, { type: "RUN_FINISHED", threadId: "t", runId: "r" });
    expect(state.running).toBe(false);
  });

  it("records every event in the inspector", () => {
    const state = run([
      { type: "RUN_STARTED", threadId: "t", runId: "r" },
      { type: "TEXT_MESSAGE_START", messageId: "m1", role: "assistant" },
    ]);

    expect(state.events.map((e) => e.type)).toEqual([
      "RUN_STARTED",
      "TEXT_MESSAGE_START",
    ]);
  });

  it("replaces the shared state on STATE_SNAPSHOT", () => {
    const state = run([
      { type: "STATE_SNAPSHOT", snapshot: { artifacts: [{ component: "ui-table" }] } },
    ]);

    expect(state.shared).toEqual({ artifacts: [{ component: "ui-table" }] });
  });

  it("collects tool results", () => {
    const state = run([
      { type: "TOOL_CALL_START", toolCallId: "c1", toolCallName: "ui_table" },
      { type: "TOOL_CALL_RESULT", toolCallId: "c1", content: '{"component":"ui-table"}' },
    ]);

    expect(state.entries).toEqual([
      { kind: "tool", id: "c1", name: "ui_table", args: "", done: false },
      {
        kind: "artifact", id: "c1:artifact",
        artifact: { component: "unknown", id: "art_?", raw: { component: "ui-table" } },
      },
    ]);
  });

  it("accumulates a tool's argument deltas", () => {
    const state = run([
      { type: "TOOL_CALL_START", toolCallId: "c1", toolCallName: "ui_table" },
      { type: "TOOL_CALL_ARGS", toolCallId: "c1", delta: '{"title":' },
      { type: "TOOL_CALL_ARGS", toolCallId: "c1", delta: '"Confronto"}' },
      { type: "TOOL_CALL_END", toolCallId: "c1" },
    ]);

    expect(state.entries[0]).toMatchObject({
      kind: "tool", args: '{"title":"Confronto"}', done: true,
    });
  });

  it("surfaces the error on RUN_ERROR and stops the run", () => {
    let state = run([{ type: "RUN_STARTED", threadId: "t", runId: "r" }]);
    state = reduce(state, { type: "RUN_ERROR", message: "boom" });

    expect(state.running).toBe(false);
    expect(state.error).toBe("boom");
  });

  it("drops the empty message wrapping a tool call", () => {
    const state = run([
      { type: "TEXT_MESSAGE_START", messageId: "m1", role: "assistant" },
      { type: "TOOL_CALL_START", toolCallId: "c1", toolCallName: "ui_table" },
      { type: "TOOL_CALL_END", toolCallId: "c1" },
      { type: "TEXT_MESSAGE_END", messageId: "m1" },
      { type: "TEXT_MESSAGE_START", messageId: "m2", role: "assistant" },
      { type: "TEXT_MESSAGE_CONTENT", messageId: "m2", delta: "Ecco il confronto." },
      { type: "TEXT_MESSAGE_END", messageId: "m2" },
    ]);

    expect(state.entries.filter((entry) => entry.kind === "assistant")).toEqual([
      { kind: "assistant", id: "m2", text: "Ecco il confronto." },
    ]);
  });

  it("ignores an unknown event without breaking", () => {
    const futuro = { type: "EVENTO_FUTURO", qualcosa: 1 } as unknown as AGUIEvent;
    const state = run([futuro]);

    expect(state.events).toHaveLength(1);
    expect(state.entries).toHaveLength(0);
  });

  it("appends the user without mutating state and clears the previous error", () => {
    const before = { ...initialState, error: "boom" };
    const state = withUserMessage(before, "u1", "ciao");
    expect(state.entries).toEqual([{ kind: "user", id: "u1", text: "ciao" }]);
    expect(state.error).toBeNull();
    expect(before).toEqual({ ...initialState, error: "boom" });
    expect(state.events).toEqual([]);
  });

  it("drops reasoning without text even when it carries only signatures", () => {
    const state = run([
      { type: "REASONING_MESSAGE_START", messageId: "r1", role: "reasoning" },
      {
        type: "REASONING_ENCRYPTED_VALUE", entityId: "r1", subtype: "message",
        encryptedValue: '[{"type":"reasoning.signature","value":"abc"}]',
      },
      { type: "REASONING_MESSAGE_END", messageId: "r1" },
    ]);
    expect(state.entries).toEqual([]);
    expect(state.events).toHaveLength(3);
  });

  it("adds no artifacts for plan results", () => {
    const state = run([
      { type: "TOOL_CALL_START", toolCallId: "c1", toolCallName: "todo_set_status" },
      { type: "TOOL_CALL_END", toolCallId: "c1" },
      { type: "TOOL_CALL_RESULT", toolCallId: "c1", content: '{"component":"plan"}' },
    ]);
    expect(state.entries).toEqual([
      { kind: "tool", id: "c1", name: "todo_set_status", args: "", done: true },
    ]);
  });
});

describe("parseReasoningDelta", () => {
  it("extracts and joins the reasoning text fragments", () => {
    expect(parseReasoningDelta(JSON.stringify([
      { type: "reasoning.text", text: " user", format: "unknown", index: 0 },
      { type: "reasoning.signature", value: "abc" },
      { type: "reasoning.text", text: " request" },
    ]))).toBe(" user request");
  });

  it("ignores fragments that are not reasoning text", () => {
    expect(parseReasoningDelta('[null,42,{"type":"reasoning.signature","value":"abc"},{"type":"reasoning.text","text":42}]')).toBe("");
  });

  it.each(["not json", "null", "{}"])("rejects a malformed payload: %s", (raw) => {
    expect(() => parseReasoningDelta(raw)).toThrow(/reasoning/);
  });
});

describe("parseArtifact", () => {
  it("recognizes a ui-table inside the tool result's JSON string", () => {
    const table = { component: "ui-table", id: "art_1", title: "Confronto", columns: ["A"], rows: [["1"]] };
    expect(parseArtifact(JSON.stringify(table))).toEqual(table);
  });

  it("does not treat plan tool results as an artifact", () => {
    expect(parseArtifact(JSON.stringify({ component: "plan", step_id: 1 }))).toBeNull();
  });

  it("degrades on an unknown variant instead of vanishing", () => {
    const payload = { component: "ui-chart", id: "art_9" };
    expect(parseArtifact(JSON.stringify(payload))).toEqual({ component: "unknown", id: "art_9", raw: payload });
  });

  it.each([
    { columns: [42], rows: [["1"]] },
    { columns: ["A"], rows: [null] },
    { columns: ["A"], rows: [[{ value: "1" }]] },
  ])("conserva una tabella malformata nel fallback: %j", (cells) => {
    const payload = { component: "ui-table", id: "art_bad", title: "Confronto", ...cells };
    expect(parseArtifact(JSON.stringify(payload))).toEqual({
      component: "unknown", id: "art_bad", raw: payload,
    });
  });

  it.each(["Ho mostrato la tabella.", "null", "42", "{}", null, { component: "ui-table" }])(
    "ignora un risultato che non contiene un artefatto serializzato: %j", (content) => {
      expect(parseArtifact(content)).toBeNull();
    },
  );
});

describe("reduce over the real stream", () => {
  const events = loadFixture("stream-qwen");

  it("aggregates 408 reasoning deltas into two complete entries", () => {
    const state = run(events);
    const reasoning = state.entries.filter((e) => e.kind === "reasoning");
    expect(reasoning).toHaveLength(2);
    for (const entry of reasoning) {
      expect(entry.text.length).toBeGreaterThan(50);
      expect(entry.done).toBe(true);
    }
  });

  it("leaves no empty bubbles", () => {
    const empty = run(events).entries.filter(
      (e) => (e.kind === "assistant" || e.kind === "reasoning") && e.text === "",
    );
    expect(empty).toEqual([]);
  });

  it("produces a tool entry and its artifact entry", () => {
    const state = run(events);
    const tools = state.entries.filter((e) => e.kind === "tool");
    const artifacts = state.entries.filter((e) => e.kind === "artifact");
    expect(tools).toHaveLength(1);
    expect(tools[0]).toMatchObject({ name: "ui_table", done: true });
    expect(artifacts).toHaveLength(1);
    expect(artifacts[0].id).toBe(`${tools[0].id}:artifact`);
    expect(artifacts[0].artifact.component).toBe("ui-table");
  });

  it("keeps every event in the inspector, recognized or not", () => {
    expect(run(events).events).toEqual(events);
  });

  it("closes the run", () => {
    const state = run(events);
    expect(state.running).toBe(false);
    expect(state.error).toBeNull();
  });

  it("keeps the timeline's arrival order", () => {
    const kinds = run(events).entries.map((e) => e.kind);
    expect(kinds.indexOf("reasoning")).toBeLessThan(kinds.indexOf("tool"));
    expect(kinds.indexOf("tool")).toBeLessThan(kinds.indexOf("artifact"));
  });
});

describe("subagents", () => {
  const avvio = (id: string, name = "knowledge", description = "domanda"): AGUIEvent => ({
    type: "SUBAGENT_STARTED",
    subagentRunId: id,
    name,
    description,
  });

  it("a started subagent appears in the timeline as running", () => {
    const state = reduce(initialState, avvio("s1"));

    expect(state.entries).toEqual([
      { kind: "subagent", id: "s1", name: "knowledge", description: "domanda", status: "running" },
    ]);
  });

  it("finishing updates that subagent and not the others", () => {
    let state = reduce(initialState, avvio("s1", "knowledge", "prima"));
    state = reduce(state, avvio("s2", "knowledge", "seconda"));
    state = reduce(state, { type: "SUBAGENT_FINISHED", subagentRunId: "s2" });

    expect(state.entries.map((e) => e.kind === "subagent" && e.status)).toEqual([
      "running",
      "done",
    ]);
  });

  it("two starts with no finish in between stay two parallel entries", () => {
    let state = reduce(initialState, avvio("s1"));
    state = reduce(state, avvio("s2"));

    expect(state.entries).toHaveLength(2);
    expect(state.entries.every((e) => e.kind === "subagent" && e.status === "running")).toBe(true);
  });

  it("a subagent error stays visible with its message", () => {
    let state = reduce(initialState, avvio("s1"));
    state = reduce(state, {
      type: "SUBAGENT_ERROR",
      subagentRunId: "s1",
      message: "knowledge agent down",
      code: "ConnectionError",
    });

    const entry = state.entries[0];
    expect(entry.kind === "subagent" && entry.status).toBe("failed");
    expect(entry.kind === "subagent" && entry.error).toBe("knowledge agent down");
  });

  it("a finish without a start invents no entry", () => {
    const state = reduce(initialState, { type: "SUBAGENT_FINISHED", subagentRunId: "mai-visto" });

    expect(state.entries).toEqual([]);
    expect(state.events).toHaveLength(1);
  });
});

describe("the subagent's briefing", () => {
  const briefing = {
    component: "briefing",
    id: "kb_123",
    agent: "knowledge",
    question: "Come tipizza Go?",
    documents: ["go"],
    summary: "Statica, verificata dal compilatore.",
  };

  it("a briefing becomes a timeline artifact, not text", () => {
    const state = reduce(initialState, {
      type: "TOOL_CALL_RESULT",
      toolCallId: "c1",
      content: JSON.stringify(briefing),
    });

    const entry = state.entries[0];
    expect(entry.kind).toBe("artifact");
    expect(entry.kind === "artifact" && entry.artifact.component).toBe("briefing");
  });

  it("keeps sources and question, which are why it is structured", () => {
    const state = reduce(initialState, {
      type: "TOOL_CALL_RESULT",
      toolCallId: "c1",
      content: JSON.stringify(briefing),
    });

    const entry = state.entries[0];
    if (entry.kind !== "artifact" || entry.artifact.component !== "briefing") throw new Error("no");
    expect(entry.artifact.documents).toEqual(["go"]);
    expect(entry.artifact.question).toBe("Come tipizza Go?");
  });

  it("a briefing without a summary degrades to the fallback instead of pretending", () => {
    const state = reduce(initialState, {
      type: "TOOL_CALL_RESULT",
      toolCallId: "c1",
      content: JSON.stringify({ component: "briefing", id: "kb_1", documents: ["go"] }),
    });

    const entry = state.entries[0];
    expect(entry.kind === "artifact" && entry.artifact.component).toBe("unknown");
  });
});

// The run as the master agent streams it when a tool needs a person's yes
// (recorded from Microsoft Agent Framework 1.17, trimmed): the tool call, the
// framework's own legacy `confirm_changes` announcement, and the interrupt.
const interruptedRun: AGUIEvent[] = [
  { type: "RUN_STARTED", threadId: "t", runId: "r1" },
  { type: "TOOL_CALL_START", toolCallId: "call_1", toolCallName: "start_process" },
  { type: "TOOL_CALL_ARGS", toolCallId: "call_1", delta: '{"process_id": "example-approval"}' },
  { type: "TOOL_CALL_END", toolCallId: "call_1" },
  { type: "CUSTOM", name: "function_approval_request", value: { id: "i-1" } },
  { type: "TOOL_CALL_START", toolCallId: "c-legacy", toolCallName: "confirm_changes" },
  { type: "TOOL_CALL_ARGS", toolCallId: "c-legacy", delta: '{"function_name": "start_process"}' },
  { type: "TOOL_CALL_END", toolCallId: "c-legacy" },
  {
    type: "RUN_FINISHED",
    threadId: "t",
    runId: "r1",
    outcome: {
      type: "interrupt",
      interrupts: [
        { id: "i-1", reason: "tool_call", message: "Approve running start_process?", toolCallId: "call_1" },
      ],
    },
  },
];

describe("approvals", () => {
  it("an interrupted run becomes a question naming the action and its arguments", () => {
    const state = run(interruptedRun);

    expect(state.running).toBe(false);
    expect(state.entries.at(-1)).toEqual({
      kind: "approval",
      id: "approval:i-1",
      requests: [
        {
          interruptId: "i-1",
          tool: "start_process",
          args: '{"process_id": "example-approval"}',
          question: "Approve running start_process?",
        },
      ],
      status: "pending",
      decisions: {},
    });
    expect(awaitingApproval(state)).toBe(true);
  });

  it("leaves the framework's legacy confirm_changes call out of the timeline", () => {
    const state = run(interruptedRun);

    expect(state.entries.filter((e) => e.kind === "tool").map((e) => e.id)).toEqual(["call_1"]);
    expect(state.events.some((e) => e.type === "TOOL_CALL_START" && e.toolCallName === "confirm_changes")).toBe(true);
  });

  it("a run that simply finishes asks nothing", () => {
    const state = run([
      { type: "RUN_STARTED", threadId: "t", runId: "r" },
      { type: "RUN_FINISHED", threadId: "t", runId: "r", outcome: { type: "success" } },
    ]);

    expect(state.entries).toEqual([]);
    expect(awaitingApproval(state)).toBe(false);
  });

  it("an interrupt with no matching call still asks, without inventing details", () => {
    const state = run([
      {
        type: "RUN_FINISHED",
        threadId: "t",
        runId: "r",
        outcome: { type: "interrupt", interrupts: [{ id: "i-9", reason: "input_required" }] },
      },
    ]);

    const entry = state.entries[0];
    expect(entry.kind === "approval" && entry.requests[0]).toEqual({
      interruptId: "i-9",
      tool: "an action",
      args: "",
      question: "Approve this action?",
    });
  });

  it("answering records the decisions and releases the conversation", () => {
    const state = withDecisions(run(interruptedRun), "approval:i-1", { "i-1": true });

    expect(state.entries.at(-1)).toMatchObject({ status: "sent", decisions: { "i-1": true } });
    expect(awaitingApproval(state)).toBe(false);
  });

  it("the resume answers every question, in the protocol's shape", () => {
    expect(resumeOf({ "i-1": true, "i-2": false })).toEqual([
      { interruptId: "i-1", status: "resolved", payload: { approved: true } },
      { interruptId: "i-2", status: "resolved", payload: { approved: false } },
    ]);
  });

  it("an answer the agent cannot apply fails on the card, in words", () => {
    let state = withDecisions(run(interruptedRun), "approval:i-1", { "i-1": true });
    state = reduce(state, {
      type: "RUN_ERROR",
      message: "No pending approval interrupt found for resume interruptId 'i-1'.",
      code: "APPROVAL_RESUME_NOT_FOUND",
    });

    const entry = state.entries.at(-1);
    expect(entry).toMatchObject({ kind: "approval", status: "failed" });
    expect(entry?.kind === "approval" && entry.error).toMatch(/Nothing was run/);
    expect(state.error).toBeNull();
    expect(state.running).toBe(false);
  });

  it("an unrelated error stays a run error, not an approval failure", () => {
    let state = withDecisions(run(interruptedRun), "approval:i-1", { "i-1": true });
    state = reduce(state, { type: "RUN_ERROR", message: "model unavailable" });

    expect(state.error).toBe("model unavailable");
    expect(state.entries.at(-1)).toMatchObject({ status: "sent" });
  });
});
