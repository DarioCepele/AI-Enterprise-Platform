import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { groupEvents } from "@/lib/agui/groups";
import { loadFixture } from "@/lib/agui/fixtures/load";
import { fetchLogs } from "@/lib/agui/logs";
import type { AGUIEvent } from "@/lib/agui/types";
import { Inspector } from "./Inspector";

const EVENTS = loadFixture("stream-qwen");
vi.mock("@/lib/agui/logs", () => ({ fetchLogs: vi.fn() }));
const fetchMock = vi.mocked(fetchLogs);
beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockResolvedValue({ entries: [], cursor: "", dropped: 0 });
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("Inspector", () => {
  it("counts every event, including the ones it cannot read", () => {
    render(<Inspector events={EVENTS} running={false} />);

    expect(screen.getByText(String(EVENTS.length))).toBeInTheDocument();
  });

  it("the reasoning filter shows only reasoning", () => {
    render(<Inspector events={EVENTS} running={false} />);
    fireEvent.click(screen.getByRole("button", { name: "reasoning" }));

    const rows = screen.getAllByRole("group");
    const reasoning = EVENTS.filter((e) => e.type.startsWith("REASONING"));
    expect(rows).toHaveLength(groupEvents(reasoning).length);
    expect(screen.getByRole("button", { name: "reasoning" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText(String(EVENTS.length))).toBeInTheDocument();
    for (const row of rows) {
      expect(row.textContent).toMatch(/^REASONING_/);
    }
  });

  it("consecutive deltas become one row with a count", () => {
    render(<Inspector events={EVENTS} running={false} />);
    const deltas = EVENTS.filter((e) => e.type === "REASONING_ENCRYPTED_VALUE").length;
    const badges = screen
      .getAllByRole("group")
      .filter((row) => row.textContent?.startsWith("REASONING_ENCRYPTED_VALUE"))
      .map((row) => Number(row.textContent!.match(/×(\d+)/)![1]));

    expect(screen.getAllByRole("group").length).toBeLessThan(EVENTS.length);
    expect(badges.reduce((a, b) => a + b, 0)).toBe(deltas);
  });

  it("the payload appears only when the row is opened", () => {
    const events: AGUIEvent[] = [{ type: "RUN_STARTED", threadId: "t", runId: "r" }];
    const { container } = render(<Inspector events={events} running={false} />);

    expect(container.querySelector("pre")).toBeNull();
    fireEvent.click(screen.getByText("RUN_STARTED"));
    expect(container.querySelector("pre")?.textContent).toBe(JSON.stringify(events[0], null, 2));
  });

  it("an opened group shows the payload array, truncated and declared", () => {
    const delta = (i: number): AGUIEvent => ({
      type: "REASONING_ENCRYPTED_VALUE",
      subtype: "reasoning",
      entityId: `e${i}`,
      encryptedValue: "[]",
    });
    const events = Array.from({ length: 60 }, (_, i) => delta(i));
    const { container } = render(<Inspector events={events} running={false} />);
    fireEvent.click(screen.getByText("REASONING_ENCRYPTED_VALUE"));

    const payload = JSON.parse(container.querySelector("pre")!.textContent!);
    expect(Array.isArray(payload)).toBe(true);
    expect(payload).toHaveLength(50);
    expect(screen.getByText(/first 50 of 60 events/)).toBeInTheDocument();
  });

  it("the tool filter shows only TOOL_CALL_*", () => {
    render(<Inspector events={EVENTS} running={false} />);
    fireEvent.click(screen.getByRole("button", { name: "tool" }));

    for (const row of screen.getAllByRole("group")) {
      expect(row.textContent).toMatch(/^TOOL_CALL_/);
    }
  });

  it("the all filter includes reasoning", () => {
    render(<Inspector events={EVENTS} running={false} />);

    expect(screen.getAllByRole("group").length).toBe(groupEvents(EVENTS).length);
    expect(screen.getAllByRole("group").some((r) => r.textContent?.startsWith("REASONING_"))).toBe(true);
  });

  it("filters text and state and keeps unknown events and payloads under the all filter", () => {
    const events: AGUIEvent[] = [
      { type: "TEXT_MESSAGE_CONTENT", messageId: "m", delta: "ciao" },
      { type: "RUN_STARTED", threadId: "t", runId: "r" },
      { type: "STATE_SNAPSHOT", snapshot: { plan: { status: "idle" } } },
      { type: "EVENTO_FUTURO", value: 42 } as unknown as AGUIEvent,
    ];
    render(<Inspector events={events} running={false} />);
    fireEvent.click(screen.getByRole("button", { name: "text" }));
    expect(screen.getAllByRole("group")).toHaveLength(1);
    expect(screen.getByRole("group")).toHaveTextContent(/^TEXT_MESSAGE_CONTENT/);
    fireEvent.click(screen.getByRole("button", { name: "state" }));
    expect(screen.getAllByRole("group")).toHaveLength(2);
    for (const row of screen.getAllByRole("group")) {
      expect(row.textContent).toMatch(/^(RUN_|STATE_)/);
    }
    fireEvent.click(screen.getByRole("button", { name: "all" }));
    const rows = screen.getAllByRole("group");
    expect(rows).toHaveLength(events.length);
    for (const [i, row] of rows.entries()) {
      fireEvent.click(row.querySelector("summary")!);
      expect(row.querySelector("pre")?.textContent).toBe(JSON.stringify(events[i], null, 2));
    }
  });

  it("keeps the filter as events arrive and handles an empty stream", () => {
    const { rerender } = render(<Inspector events={[]} running={false} />);
    expect(screen.getByText("0")).toBeInTheDocument();
    expect(screen.queryAllByRole("group")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "reasoning" }));
    rerender(<Inspector events={[
      { type: "REASONING_START", messageId: "r" },
      { type: "RUN_STARTED", threadId: "t", runId: "run" },
    ]} running={false} />);
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getAllByRole("group")).toHaveLength(1);
    expect(screen.getByRole("group")).toHaveTextContent(/^REASONING_START/);
  });

  it("keeps filter and logs while switching between the two views", () => {
    render(<Inspector events={[{ type: "REASONING_START", messageId: "r" }]} running={false} />);
    const eventButton = screen.getByRole("button", { name: "Events 1" });
    const logButton = screen.getByRole("button", { name: "Log" });
    expect(eventButton).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "reasoning" }));
    fireEvent.click(logButton);
    expect(logButton).toHaveAttribute("aria-pressed", "true");
    expect(eventButton).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText("no logs yet")).toBeVisible();
    expect(screen.queryByRole("button", { name: "reasoning" })).not.toBeInTheDocument();
    fireEvent.click(eventButton);
    expect(screen.getByRole("button", { name: "reasoning" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("no logs yet")).not.toBeVisible();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("collects logs while hidden and keeps cursor and tail after the run", async () => {
    vi.useFakeTimers();
    const page = (seq: number) => ({
      cursor: String(seq), dropped: 0,
      entries: [{ seq, ts: "2026-09-08T10:00:00Z", level: "INFO", source: "master_agent", message: `riga ${seq}` }],
    });
    fetchMock.mockResolvedValueOnce(page(1)).mockResolvedValueOnce(page(2)).mockResolvedValueOnce(page(3));
    const { rerender } = render(<Inspector events={[]} running />);
    await act(async () => {});
    expect(fetchMock.mock.calls[0][0]).toBe("");
    expect(screen.getByText("riga 1")).not.toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Log" }));
    expect(screen.getByText("riga 1")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Events 0" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(fetchMock.mock.calls[1][0]).toBe("1");
    rerender(<Inspector events={[]} running={false} />);
    await act(async () => {});
    expect(fetchMock.mock.calls[2][0]).toBe("2");
    fireEvent.click(screen.getByRole("button", { name: "Log" }));
    for (const seq of [1, 2, 3]) expect(screen.getByText(`riga ${seq}`)).toBeVisible();
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});
