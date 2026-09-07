import { describe, expect, it } from "vitest";
import { loadFixture } from "./fixtures/load";
import { parseArtifact, parseReasoningDelta } from "./entries";
import { initialState, reduce, withUserMessage } from "./reducer";
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

    expect(state.entries).toEqual([{ kind: "assistant", id: "m1", text: "ciao mondo" }]);
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

    expect(state.entries).toEqual([
      { kind: "tool", id: "c1", name: "ui_table", args: "", done: false },
      {
        kind: "artifact", id: "c1:artifact",
        artifact: { component: "unknown", id: "art_?", raw: { component: "ui-table" } },
      },
    ]);
  });

  it("accumula i delta degli argomenti di un tool", () => {
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

    expect(state.entries.filter((entry) => entry.kind === "assistant")).toEqual([
      { kind: "assistant", id: "m2", text: "Ecco il confronto." },
    ]);
  });

  it("ignora un evento sconosciuto senza rompersi", () => {
    // Un evento che i tipi non modellano ancora: arriva a runtime, non a compile time.
    const futuro = { type: "EVENTO_FUTURO", qualcosa: 1 } as unknown as AGUIEvent;
    const state = run([futuro]);

    expect(state.events).toHaveLength(1);
    expect(state.entries).toHaveLength(0);
  });

  it("aggiunge l'utente senza mutare lo stato e azzera l'errore precedente", () => {
    const before = { ...initialState, error: "boom" };
    const state = withUserMessage(before, "u1", "ciao");
    expect(state.entries).toEqual([{ kind: "user", id: "u1", text: "ciao" }]);
    expect(state.error).toBeNull();
    expect(before).toEqual({ ...initialState, error: "boom" });
    expect(state.events).toEqual([]);
  });

  it("scarta ragionamento senza testo anche se contiene solo firme", () => {
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

  it("non aggiunge artefatti per i risultati del piano", () => {
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
  it("estrae e concatena i frammenti di testo del ragionamento", () => {
    expect(parseReasoningDelta(JSON.stringify([
      { type: "reasoning.text", text: " user", format: "unknown", index: 0 },
      { type: "reasoning.signature", value: "abc" },
      { type: "reasoning.text", text: " request" },
    ]))).toBe(" user request");
  });

  it("ignora i frammenti che non sono testo di ragionamento", () => {
    expect(parseReasoningDelta('[null,42,{"type":"reasoning.signature","value":"abc"},{"type":"reasoning.text","text":42}]')).toBe("");
  });

  it.each(["non e' json", "null", "{}"])("rifiuta un payload malformato: %s", (raw) => {
    expect(() => parseReasoningDelta(raw)).toThrow(/ragionamento/);
  });
});

describe("parseArtifact", () => {
  it("riconosce una ui-table dentro la stringa JSON del tool result", () => {
    const table = { component: "ui-table", id: "art_1", title: "Confronto", columns: ["A"], rows: [["1"]] };
    expect(parseArtifact(JSON.stringify(table))).toEqual(table);
  });

  it("non tratta il risultato dei tool del piano come un artefatto", () => {
    expect(parseArtifact(JSON.stringify({ component: "plan", step_id: 1 }))).toBeNull();
  });

  it("degrada su una variante sconosciuta invece di sparire", () => {
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

describe("reduce sullo stream reale", () => {
  const events = loadFixture("stream-qwen");

  it("aggrega 408 delta di ragionamento in due entry complete", () => {
    const state = run(events);
    const reasoning = state.entries.filter((e) => e.kind === "reasoning");
    expect(reasoning).toHaveLength(2);
    for (const entry of reasoning) {
      expect(entry.text.length).toBeGreaterThan(50);
      expect(entry.done).toBe(true);
    }
  });

  it("non lascia bolle vuote", () => {
    const empty = run(events).entries.filter(
      (e) => (e.kind === "assistant" || e.kind === "reasoning") && e.text === "",
    );
    expect(empty).toEqual([]);
  });

  it("produce una entry tool e la sua entry artefatto", () => {
    const state = run(events);
    const tools = state.entries.filter((e) => e.kind === "tool");
    const artifacts = state.entries.filter((e) => e.kind === "artifact");
    expect(tools).toHaveLength(1);
    expect(tools[0]).toMatchObject({ name: "ui_table", done: true });
    expect(artifacts).toHaveLength(1);
    expect(artifacts[0].id).toBe(`${tools[0].id}:artifact`);
    expect(artifacts[0].artifact.component).toBe("ui-table");
  });

  it("tiene ogni evento nell'inspector, riconosciuto o no", () => {
    expect(run(events).events).toEqual(events);
  });

  it("chiude la run", () => {
    const state = run(events);
    expect(state.running).toBe(false);
    expect(state.error).toBeNull();
  });

  it("mantiene l'ordine di arrivo della timeline", () => {
    const kinds = run(events).entries.map((e) => e.kind);
    expect(kinds.indexOf("reasoning")).toBeLessThan(kinds.indexOf("tool"));
    expect(kinds.indexOf("tool")).toBeLessThan(kinds.indexOf("artifact"));
  });
});
