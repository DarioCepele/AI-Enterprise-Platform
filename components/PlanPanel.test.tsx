import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PlanPanel } from "./PlanPanel";

const PLAN = {
  status: "in_progress",
  steps: [
    {
      id: 1,
      title: "Carica la skill",
      detail: "Skill di confronto.",
      source: "skill:comparison#1",
      status: "completed",
      started_at: "2026-09-07T09:00:00+00:00",
      ended_at: "2026-09-07T09:00:02+00:00",
      note: null,
    },
    {
      id: 2,
      title: "Produci la tabella",
      detail: "Confronto tabellare.",
      source: "ui_table",
      status: "in_progress",
      started_at: "2026-09-07T09:00:02+00:00",
      ended_at: null,
      note: null,
    },
  ],
};

describe("PlanPanel", () => {
  it("segue lo snapshot successivo senza mantenere stato locale", () => {
    const { rerender } = render(<PlanPanel shared={{ plan: PLAN }} />);
    expect(screen.getByText("1/2")).toBeInTheDocument();
    rerender(<PlanPanel shared={{ plan: {
      ...PLAN, status: "completed",
      steps: PLAN.steps.map((step) => ({ ...step, status: "completed" })),
    } }} />);
    expect(screen.getByText("2/2")).toBeInTheDocument();
    expect(screen.queryByText("1/2")).not.toBeInTheDocument();
  });

  it("rende accessibili tutti e quattro gli stati dei passi", () => {
    render(<PlanPanel shared={{ plan: {
      status: "in_progress",
      steps: ["pending", "in_progress", "completed", "failed"].map((status, id) => ({
        ...PLAN.steps[0], id, status,
      })),
    } }} />);
    for (const name of ["In attesa", "In corso", "Completato", "Fallito"]) {
      expect(screen.getByRole("img", { name })).toBeInTheDocument();
    }
    expect(screen.getByText("1/4")).toBeInTheDocument();
  });

  it.each([null, { status: "in_progress", steps: [null] }, {
    status: "in_progress", steps: [{ ...PLAN.steps[0], title: { invalid: true } }],
  }])("gestisce uno snapshot del piano malformato: %j", (plan) => {
    render(<PlanPanel shared={{ plan }} />);
    expect(screen.getByText(/nessun piano/i)).toBeInTheDocument();
  });

  it("conta i passi completati sul totale", () => {
    render(<PlanPanel shared={{ plan: PLAN }} />);

    expect(screen.getByText("1/2")).toBeInTheDocument();
  });

  it("mostra titolo, dettaglio e origine di ogni passo", () => {
    render(<PlanPanel shared={{ plan: PLAN }} />);

    expect(screen.getByText("Carica la skill")).toBeInTheDocument();
    expect(screen.getByText("Skill di confronto.")).toBeInTheDocument();
    expect(screen.getByText("skill:comparison#1")).toBeInTheDocument();
  });

  it("dice che non c'e' un piano quando lo stato e' idle", () => {
    render(<PlanPanel shared={{ plan: { status: "idle", steps: [] } }} />);

    expect(screen.getByText(/nessun piano/i)).toBeInTheDocument();
  });

  it("regge uno stato condiviso senza piano", () => {
    render(<PlanPanel shared={{}} />);

    expect(screen.getByText(/nessun piano/i)).toBeInTheDocument();
  });

  it("mostra la nota di un passo fallito", () => {
    const failed = {
      status: "failed",
      steps: [{ ...PLAN.steps[0], status: "failed", note: "il tool non ha risposto" }],
    };
    render(<PlanPanel shared={{ plan: failed }} />);

    expect(screen.getByText("il tool non ha risposto")).toBeInTheDocument();
  });
});
