import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadFixture } from "@/lib/agui/fixtures/load";
import { fetchLogs } from "@/lib/agui/logs";
import type { AGUIEvent } from "@/lib/agui/types";
import { Inspector } from "./Inspector";

const EVENTS = loadFixture("stream-qwen");
vi.mock("@/lib/agui/logs", () => ({ fetchLogs: vi.fn() }));
const fetchMock = vi.mocked(fetchLogs);
beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockResolvedValue({ entries: [], cursor: 0, dropped: 0 });
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("Inspector", () => {
  it("conta tutti gli eventi, anche quelli che non sa interpretare", () => {
    render(<Inspector events={EVENTS} running={false} />);

    expect(screen.getByText(String(EVENTS.length))).toBeInTheDocument();
  });

  it("il filtro ragionamento mostra solo il ragionamento", () => {
    render(<Inspector events={EVENTS} running={false} />);
    fireEvent.click(screen.getByRole("button", { name: "ragionamento" }));

    const rows = screen.getAllByRole("group");
    expect(rows).toHaveLength(EVENTS.filter((e) => e.type.startsWith("REASONING")).length);
    expect(screen.getByRole("button", { name: "ragionamento" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText(String(EVENTS.length))).toBeInTheDocument();
    for (const row of rows) {
      expect(row.textContent).toMatch(/^REASONING_/);
    }
    // La fixture completa rende oltre 400 details: le query di accessibilita'
    // in jsdom possono superare il timeout standard su Windows.
  }, 15_000);

  it("il filtro tool mostra solo i TOOL_CALL_*", () => {
    render(<Inspector events={EVENTS} running={false} />);
    fireEvent.click(screen.getByRole("button", { name: "tool" }));

    for (const row of screen.getAllByRole("group")) {
      expect(row.textContent).toMatch(/^TOOL_CALL_/);
    }
  });

  it("il filtro tutti include il ragionamento", () => {
    // Non si nasconde nulla dal flusso grezzo: e' il punto dell'inspector.
    render(<Inspector events={EVENTS} running={false} />);

    expect(screen.getAllByRole("group").length).toBe(EVENTS.length);
  });

  it("filtra testo e stato e conserva eventi sconosciuti e payload nel filtro tutti", () => {
    const events: AGUIEvent[] = [
      { type: "TEXT_MESSAGE_CONTENT", messageId: "m", delta: "ciao" },
      { type: "RUN_STARTED", threadId: "t", runId: "r" },
      { type: "STATE_SNAPSHOT", snapshot: { plan: { status: "idle" } } },
      { type: "EVENTO_FUTURO", value: 42 } as unknown as AGUIEvent,
    ];
    render(<Inspector events={events} running={false} />);
    fireEvent.click(screen.getByRole("button", { name: "testo" }));
    expect(screen.getAllByRole("group")).toHaveLength(1);
    expect(screen.getByRole("group")).toHaveTextContent(/^TEXT_MESSAGE_CONTENT/);
    fireEvent.click(screen.getByRole("button", { name: "stato" }));
    expect(screen.getAllByRole("group")).toHaveLength(2);
    for (const row of screen.getAllByRole("group")) {
      expect(row.textContent).toMatch(/^(RUN_|STATE_)/);
    }
    fireEvent.click(screen.getByRole("button", { name: "tutti" }));
    expect(screen.getAllByRole("group")).toHaveLength(events.length);
    expect(screen.getAllByRole("group").map((row) => row.querySelector("pre")?.textContent))
      .toEqual(events.map((event) => JSON.stringify(event, null, 2)));
  });

  it("mantiene il filtro quando arrivano eventi e gestisce lo stream vuoto", () => {
    const { rerender } = render(<Inspector events={[]} running={false} />);
    expect(screen.getByText("0")).toBeInTheDocument();
    expect(screen.queryAllByRole("group")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "ragionamento" }));
    rerender(<Inspector events={[
      { type: "REASONING_START", messageId: "r" },
      { type: "RUN_STARTED", threadId: "t", runId: "run" },
    ]} running={false} />);
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getAllByRole("group")).toHaveLength(1);
    expect(screen.getByRole("group")).toHaveTextContent(/^REASONING_START/);
  });

  it("conserva filtro e log passando tra le due viste", () => {
    render(<Inspector events={[{ type: "REASONING_START", messageId: "r" }]} running={false} />);
    const eventButton = screen.getByRole("button", { name: "Event inspector 1" });
    const logButton = screen.getByRole("button", { name: "Log" });
    expect(eventButton).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "ragionamento" }));
    fireEvent.click(logButton);
    expect(logButton).toHaveAttribute("aria-pressed", "true");
    expect(eventButton).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText("nessun log")).toBeVisible();
    expect(screen.queryByRole("button", { name: "ragionamento" })).not.toBeInTheDocument();
    fireEvent.click(eventButton);
    expect(screen.getByRole("button", { name: "ragionamento" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("nessun log")).not.toBeVisible();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("raccoglie i log a vista nascosta e conserva cursore e coda dopo la run", async () => {
    vi.useFakeTimers();
    const page = (seq: number) => ({
      cursor: seq, dropped: 0,
      entries: [{ seq, ts: "2026-09-08T10:00:00Z", level: "INFO", source: "demo", message: `riga ${seq}` }],
    });
    fetchMock.mockResolvedValueOnce(page(1)).mockResolvedValueOnce(page(2)).mockResolvedValueOnce(page(3));
    const { rerender } = render(<Inspector events={[]} running />);
    await act(async () => {});
    expect(fetchMock.mock.calls[0][0]).toBe(0);
    expect(screen.getByText("riga 1")).not.toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Log" }));
    expect(screen.getByText("riga 1")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Event inspector 0" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(fetchMock.mock.calls[1][0]).toBe(1);
    rerender(<Inspector events={[]} running={false} />);
    await act(async () => {});
    expect(fetchMock.mock.calls[2][0]).toBe(2);
    fireEvent.click(screen.getByRole("button", { name: "Log" }));
    for (const seq of [1, 2, 3]) expect(screen.getByText(`riga ${seq}`)).toBeVisible();
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});
