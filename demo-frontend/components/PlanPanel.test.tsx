import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PlanPanel } from "./PlanPanel";

const PLAN = {
  status: "in_progress",
  steps: [
    {
      id: 1,
      title: "Load the skill",
      detail: "Skill di confronto.",
      source: "skill:comparison#1",
      status: "completed",
      started_at: "2026-09-07T09:00:00+00:00",
      ended_at: "2026-09-07T09:00:02+00:00",
      note: null,
    },
    {
      id: 2,
      title: "Produce the table",
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
  it("follows the next snapshot without keeping local state", () => {
    const { rerender } = render(<PlanPanel shared={{ plan: PLAN }} />);
    expect(screen.getByText("1/2")).toBeInTheDocument();
    rerender(<PlanPanel shared={{ plan: {
      ...PLAN, status: "completed",
      steps: PLAN.steps.map((step) => ({ ...step, status: "completed" })),
    } }} />);
    expect(screen.getByText("2/2")).toBeInTheDocument();
    expect(screen.queryByText("1/2")).not.toBeInTheDocument();
  });

  it("renders all four step statuses accessibly", () => {
    render(<PlanPanel shared={{ plan: {
      status: "in_progress",
      steps: ["pending", "in_progress", "completed", "failed"].map((status, id) => ({
        ...PLAN.steps[0], id, status,
      })),
    } }} />);
    for (const name of ["Pending", "In progress", "Completed", "Failed"]) {
      expect(screen.getByRole("img", { name })).toBeInTheDocument();
    }
    expect(screen.getByText("1/4")).toBeInTheDocument();
  });

  it.each([null, { status: "in_progress", steps: [null] }, {
    status: "in_progress", steps: [{ ...PLAN.steps[0], title: { invalid: true } }],
  }])("handles a malformed plan snapshot: %j", (plan) => {
    render(<PlanPanel shared={{ plan }} />);
    expect(screen.getByText(/no plan/i)).toBeInTheDocument();
  });

  it("counts completed steps out of the total", () => {
    render(<PlanPanel shared={{ plan: PLAN }} />);

    expect(screen.getByText("1/2")).toBeInTheDocument();
  });

  it("shows title, detail and source of every step", () => {
    render(<PlanPanel shared={{ plan: PLAN }} />);

    expect(screen.getByText("Load the skill")).toBeInTheDocument();
    expect(screen.getByText("Skill di confronto.")).toBeInTheDocument();
    expect(screen.getByText("skill:comparison#1")).toBeInTheDocument();
  });

  it("says there is no plan when the state is idle", () => {
    render(<PlanPanel shared={{ plan: { status: "idle", steps: [] } }} />);

    expect(screen.getByText(/no plan/i)).toBeInTheDocument();
  });

  it("copes with a shared state that has no plan", () => {
    render(<PlanPanel shared={{}} />);

    expect(screen.getByText(/no plan/i)).toBeInTheDocument();
  });

  it("shows the note of a failed step", () => {
    const failed = {
      status: "failed",
      steps: [{ ...PLAN.steps[0], status: "failed", note: "il tool non ha risposto" }],
    };
    render(<PlanPanel shared={{ plan: failed }} />);

    expect(screen.getByText("il tool non ha risposto")).toBeInTheDocument();
  });
});
