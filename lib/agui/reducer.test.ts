import { describe, expect, it } from "vitest";
import { initialState, reduce } from "./reducer";
import type { AGUIEvent } from "./types";

function run(events: AGUIEvent[]) {
  return events.reduce(reduce, initialState);
}

describe("reduce", () => {
  it("accumula i delta di testo in un solo messaggio", () => {
    const state = run([
      { type: "RUN_STARTED", threadId: "t", runId: "r" },
      { type: "TEXT_MESSAGE_START", messageId: "m1", role: "assistant" },
      { type: "TEXT_MESSAGE_CONTENT", messageId: "m1", delta: "ciao " },
      { type: "TEXT_MESSAGE_CONTENT", messageId: "m1", delta: "mondo" },
      { type: "TEXT_MESSAGE_END", messageId: "m1" },
    ]);

    expect(state.messages).toHaveLength(1);
    expect(state.messages[0].content).toBe("ciao mondo");
  });

  it("segna la run come in corso e poi conclusa", () => {
    let state = run([{ type: "RUN_STARTED", threadId: "t", runId: "r" }]);
    expect(state.running).toBe(true);

    state = reduce(state, { type: "RUN_FINISHED", threadId: "t", runId: "r" });
    expect(state.running).toBe(false);
  });

  it("registra ogni evento nell'inspector", () => {
    const state = run([
      { type: "RUN_STARTED", threadId: "t", runId: "r" },
      { type: "TEXT_MESSAGE_START", messageId: "m1", role: "assistant" },
    ]);

    expect(state.events.map((e) => e.type)).toEqual([
      "RUN_STARTED",
      "TEXT_MESSAGE_START",
    ]);
  });

  it("sostituisce lo stato condiviso su STATE_SNAPSHOT", () => {
    const state = run([
      { type: "STATE_SNAPSHOT", snapshot: { artifacts: [{ component: "ui-table" }] } },
    ]);

    expect(state.shared).toEqual({ artifacts: [{ component: "ui-table" }] });
  });

  it("raccoglie i risultati dei tool", () => {
    // content arriva come stringa JSON: state_update serializza il payload.
    const state = run([
      { type: "TOOL_CALL_START", toolCallId: "c1", toolCallName: "ui_table" },
      { type: "TOOL_CALL_RESULT", toolCallId: "c1", content: '{"component":"ui-table"}' },
    ]);

    expect(state.toolCalls).toHaveLength(1);
    expect(state.toolCalls[0].name).toBe("ui_table");
    expect(state.toolCalls[0].result).toBe('{"component":"ui-table"}');
  });

  it("accumula i delta degli argomenti di un tool", () => {
    const state = run([
      { type: "TOOL_CALL_START", toolCallId: "c1", toolCallName: "ui_table" },
      { type: "TOOL_CALL_ARGS", toolCallId: "c1", delta: '{"title":' },
      { type: "TOOL_CALL_ARGS", toolCallId: "c1", delta: '"Confronto"}' },
      { type: "TOOL_CALL_END", toolCallId: "c1" },
    ]);

    expect(state.toolCalls[0].args).toBe('{"title":"Confronto"}');
  });

  it("espone l'errore su RUN_ERROR e ferma la run", () => {
    let state = run([{ type: "RUN_STARTED", threadId: "t", runId: "r" }]);
    state = reduce(state, { type: "RUN_ERROR", message: "boom" });

    expect(state.running).toBe(false);
    expect(state.error).toBe("boom");
  });

  it("scarta il messaggio vuoto che avvolge una tool call", () => {
    // Sul filo ogni tool call e' racchiusa fra START ed END senza CONTENT.
    const state = run([
      { type: "TEXT_MESSAGE_START", messageId: "m1", role: "assistant" },
      { type: "TOOL_CALL_START", toolCallId: "c1", toolCallName: "ui_table" },
      { type: "TOOL_CALL_END", toolCallId: "c1" },
      { type: "TEXT_MESSAGE_END", messageId: "m1" },
      { type: "TEXT_MESSAGE_START", messageId: "m2", role: "assistant" },
      { type: "TEXT_MESSAGE_CONTENT", messageId: "m2", delta: "Ecco il confronto." },
      { type: "TEXT_MESSAGE_END", messageId: "m2" },
    ]);

    expect(state.messages).toHaveLength(1);
    expect(state.messages[0].content).toBe("Ecco il confronto.");
  });

  it("ignora un evento sconosciuto senza rompersi", () => {
    // Un evento che i tipi non modellano ancora: arriva a runtime, non a compile time.
    const futuro = { type: "EVENTO_FUTURO", qualcosa: 1 } as unknown as AGUIEvent;
    const state = run([futuro]);

    expect(state.events).toHaveLength(1);
    expect(state.messages).toHaveLength(0);
  });
});
