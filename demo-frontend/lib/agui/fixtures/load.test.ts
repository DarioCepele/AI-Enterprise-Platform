import { describe, expect, it } from "vitest";
import { loadFixture } from "./load";

describe("loadFixture", () => {
  it("reads the real stream in order", () => {
    const events = loadFixture("stream-qwen");

    expect(events).toHaveLength(486);
    expect(events[0].type).toBe("RUN_STARTED");
    expect(events.at(-1)?.type).toBe("RUN_FINISHED");
  });

  it("contains the reasoning sequence we expect", () => {
    const events = loadFixture("stream-qwen");
    const types = events.map((event) => event.type);

    expect(types).toContain("REASONING_MESSAGE_START");
    expect(types.filter((type) => type === "REASONING_ENCRYPTED_VALUE")).toHaveLength(408);
    expect(types).not.toContain("REASONING_MESSAGE_CONTENT");
    const starts = events.filter((event) => event.type === "REASONING_MESSAGE_START");
    expect(starts).toHaveLength(2);
    const messageIds = starts.map((event) => event.messageId);
    for (const event of events) {
      if (event.type === "REASONING_ENCRYPTED_VALUE") {
        expect(messageIds).toContain(event.entityId);
        expect(typeof event.encryptedValue).toBe("string");
        expect(Array.isArray(JSON.parse(event.encryptedValue))).toBe(true);
      }
    }
  });

  it("contains a full tool round with its result", () => {
    const types = loadFixture("stream-qwen").map((event) => event.type);

    expect(types).toContain("TOOL_CALL_START");
    expect(types).toContain("TOOL_CALL_RESULT");
    expect(types).toContain("STATE_SNAPSHOT");
    expect(types.indexOf("TOOL_CALL_START")).toBeLessThan(types.indexOf("TOOL_CALL_RESULT"));
    expect(types.indexOf("TOOL_CALL_RESULT")).toBeLessThan(types.indexOf("STATE_SNAPSHOT"));
  });
});
