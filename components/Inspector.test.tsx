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
    const reasoning = EVENTS.filter((e) => e.type.startsWith("REASONING"));
    expect(rows).toHaveLength(groupEvents(reasoning).length);
    expect(screen.getByRole("button", { name: "ragionamento" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText(String(EVENTS.length))).toBeInTheDocument();
    for (const row of rows) {
      expect(row.textContent).toMatch(/^REASONING_/);
    }
  });

  it("i delta consecutivi diventano una riga sola col conteggio", () => {
    // 408 righe identiche rendevano l'inspector illeggibile: ora una riga per
    // gruppo, con il numero di eventi accorpati.
    render(<Inspector events={EVENTS} running={false} />);
    const deltas = EVENTS.filter((e) => e.type === "REASONING_ENCRYPTED_VALUE").length;
    const badges = screen
      .getAllByRole("group")
      .filter((row) => row.textContent?.startsWith("REASONING_ENCRYPTED_VALUE"))
      .map((row) => Number(row.textContent!.match(/×(\d+)/)![1]));

    expect(screen.getAllByRole("group").length).toBeLessThan(EVENTS.length);
    expect(badges.reduce((a, b) => a + b, 0)).toBe(deltas);
  });

  it("il payload compare solo quando la riga viene aperta", () => {
    const events: AGUIEvent[] = [{ type: "RUN_STARTED", threadId: "t", runId: "r" }];
    const { container } = render(<Inspector events={events} running={false} />);

    expect(container.querySelector("pre")).toBeNull();
    fireEvent.click(screen.getByText("RUN_STARTED"));
    expect(container.querySelector("pre")?.textContent).toBe(JSON.stringify(events[0], null, 2));
  });

  it("un gruppo aperto mostra l'array dei payload, troncato e dichiarato", () => {
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
    expect(screen.getByText(/primi 50 di 60 eventi/)).toBeInTheDocument();
  });

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

    expect(screen.getAllByRole("group").length).toBe(groupEvents(EVENTS).length);
    expect(screen.getAllByRole("group").some((r) => r.textContent?.startsWith("REASONING_"))).toBe(true);
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
    const rows = screen.getAllByRole("group");
    expect(rows).toHaveLength(events.length);
    for (const [i, row] of rows.entries()) {
      fireEvent.click(row.querySelector("summary")!);
      expect(row.querySelector("pre")?.textContent).toBe(JSON.stringify(events[i], null, 2));
    }
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
