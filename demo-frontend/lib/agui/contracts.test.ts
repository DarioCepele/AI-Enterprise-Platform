import { describe, expect, it, vi } from "vitest";
import { contractSample, loadContract } from "@/lib/contracts";
import { parseArtifact } from "./entries";
import { reduce, initialState } from "./reducer";
import { fetchLogs, type LogPage } from "./logs";
import type { AGUIEvent } from "./types";

describe("contracts with the master agent", () => {
  it("turns the briefing tool result into the artifact the timeline renders", () => {
    const payload = contractSample("agui/tool-result-briefing");

    const artifact = parseArtifact(JSON.stringify(payload));

    expect(artifact).toEqual({ ...payload, component: "briefing" });
  });

  it("turns the ui-table tool result into the artifact the timeline renders", () => {
    const payload = contractSample("agui/tool-result-ui-table");

    expect(parseArtifact(JSON.stringify(payload))).toEqual(payload);
  });

  it("turns the video-analysis tool result into the artifact the timeline renders", () => {
    const payload = contractSample("agui/tool-result-video-analysis");

    expect(parseArtifact(JSON.stringify(payload))).toEqual(payload);
  });

  it("does not fall back to unknown on any declared tool result", () => {
    for (const name of [
      "agui/tool-result-briefing",
      "agui/tool-result-ui-table",
      "agui/tool-result-video-analysis",
    ]) {
      const artifact = parseArtifact(JSON.stringify(contractSample(name)));
      expect(artifact?.component, name).not.toBe("unknown");
    }
  });

  it("reads the shared state the agent publishes", () => {
    const state = contractSample<Record<string, unknown>>("agui/shared-state");
    const snapshot: AGUIEvent = { type: "STATE_SNAPSHOT", snapshot: state };

    const next = reduce(initialState, snapshot);

    expect(next.shared).toEqual(state);
  });

  it("renders every plan step field the contract declares", () => {
    const state = contractSample<{ plan: { steps: Record<string, unknown>[] } }>(
      "agui/shared-state",
    );
    const step = state.plan.steps[0];

    // The panel reads these by name; a rename upstream lands here first.
    expect(Object.keys(step).sort()).toEqual(
      ["detail", "ended_at", "id", "note", "source", "started_at", "status", "title"].sort(),
    );
  });

  it("names this repository as a consumer, so a break points here", () => {
    expect(loadContract("agui/tool-result-briefing").consumed_by).toContain("demo-frontend");
    expect(loadContract("agui/shared-state").consumed_by).toContain("demo-frontend");
  });
});

describe("the logs page", () => {
  it("is typed as the contract declares", () => {
    const page = contractSample<LogPage>("agui/logs-page");

    expect(typeof page.cursor).toBe("string");
    expect(typeof page.dropped).toBe("number");
    expect(Object.keys(page.entries[0]).sort()).toEqual(
      ["level", "message", "seq", "source", "ts"].sort(),
    );
  });

  it("sends the cursor back untouched, whatever is inside it", async () => {
    const page = contractSample<LogPage>("agui/logs-page");
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => page });
    vi.stubGlobal("fetch", fetchMock);

    await fetchLogs(page.cursor);

    expect(fetchMock.mock.calls[0][0]).toContain(encodeURIComponent(page.cursor));
  });
});
