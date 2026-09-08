import { describe, expect, it } from "vitest";
import { groupEvents } from "./groups";
import type { AGUIEvent } from "./types";

const delta = (id: string): AGUIEvent => ({
  type: "REASONING_ENCRYPTED_VALUE",
  subtype: "reasoning",
  entityId: id,
  encryptedValue: "[]",
});

describe("groupEvents", () => {
  it("does not group an empty list", () => {
    expect(groupEvents([])).toEqual([]);
  });

  it("merges consecutive events of the same type into one group", () => {
    const events: AGUIEvent[] = [delta("a"), delta("a"), delta("a")];
    const groups = groupEvents(events);
    expect(groups).toHaveLength(1);
    expect(groups[0].type).toBe("REASONING_ENCRYPTED_VALUE");
    expect(groups[0].events).toHaveLength(3);
  });

  it("closes the group when the type changes and opens another after it", () => {
    const events: AGUIEvent[] = [
      { type: "REASONING_MESSAGE_START", messageId: "m1", role: "assistant" },
      delta("m1"),
      delta("m1"),
      { type: "REASONING_MESSAGE_END", messageId: "m1" },
      delta("m2"),
    ];
    const groups = groupEvents(events);
    expect(groups.map((g) => [g.type, g.events.length])).toEqual([
      ["REASONING_MESSAGE_START", 1],
      ["REASONING_ENCRYPTED_VALUE", 2],
      ["REASONING_MESSAGE_END", 1],
      ["REASONING_ENCRYPTED_VALUE", 1],
    ]);
  });

  it("the group index is the first event's, not the group's", () => {
    const events: AGUIEvent[] = [
      { type: "RUN_STARTED", threadId: "t", runId: "r" },
      delta("a"),
      delta("a"),
      { type: "RUN_FINISHED", threadId: "t", runId: "r" },
    ];
    expect(groupEvents(events).map((g) => g.index)).toEqual([0, 1, 3]);
  });

  it("keeps every event, losing none", () => {
    const events: AGUIEvent[] = [delta("a"), delta("b"), delta("c")];
    const groups = groupEvents(events);
    expect(groups.flatMap((g) => g.events)).toEqual(events);
  });
});
