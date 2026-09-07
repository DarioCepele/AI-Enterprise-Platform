import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { loadFixture } from "@/lib/agui/fixtures/load";
import type { AGUIEvent } from "@/lib/agui/types";
import { Inspector } from "./Inspector";

const EVENTS = loadFixture("stream-qwen");

describe("Inspector", () => {
  it("conta tutti gli eventi, anche quelli che non sa interpretare", () => {
    render(<Inspector events={EVENTS} />);

    expect(screen.getByText(String(EVENTS.length))).toBeInTheDocument();
  });

  it("il filtro ragionamento mostra solo il ragionamento", () => {
    render(<Inspector events={EVENTS} />);
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
    render(<Inspector events={EVENTS} />);
    fireEvent.click(screen.getByRole("button", { name: "tool" }));

    for (const row of screen.getAllByRole("group")) {
      expect(row.textContent).toMatch(/^TOOL_CALL_/);
    }
  });

  it("il filtro tutti include il ragionamento", () => {
    // Non si nasconde nulla dal flusso grezzo: e' il punto dell'inspector.
    render(<Inspector events={EVENTS} />);

    expect(screen.getAllByRole("group").length).toBe(EVENTS.length);
  });

  it("filtra testo e stato e conserva eventi sconosciuti e payload nel filtro tutti", () => {
    const events: AGUIEvent[] = [
      { type: "TEXT_MESSAGE_CONTENT", messageId: "m", delta: "ciao" },
      { type: "RUN_STARTED", threadId: "t", runId: "r" },
      { type: "STATE_SNAPSHOT", snapshot: { plan: { status: "idle" } } },
      { type: "EVENTO_FUTURO", value: 42 } as unknown as AGUIEvent,
    ];
    render(<Inspector events={events} />);
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
    const { rerender } = render(<Inspector events={[]} />);
    expect(screen.getByText("0")).toBeInTheDocument();
    expect(screen.queryAllByRole("group")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "ragionamento" }));
    rerender(<Inspector events={[
      { type: "REASONING_START", messageId: "r" },
      { type: "RUN_STARTED", threadId: "t", runId: "run" },
    ]} />);
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getAllByRole("group")).toHaveLength(1);
    expect(screen.getByRole("group")).toHaveTextContent(/^REASONING_START/);
  });
});
