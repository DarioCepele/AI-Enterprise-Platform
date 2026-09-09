"use client";

interface PlanStep {
  id: number;
  title: string;
  detail: string;
  source: string;
  status: string;
  note: string | null;
}

interface Plan {
  status: string;
  steps: PlanStep[];
}

const MARKER: Record<string, string> = {
  pending: "○",
  in_progress: "◉",
  completed: "●",
  failed: "✕",
};

const STATUS_LABEL: Record<string, string> = {
  pending: "In attesa",
  in_progress: "In corso",
  completed: "Completato",
  failed: "Fallito",
};

const TONE: Record<string, string> = {
  pending: "text-[var(--muted)]",
  in_progress: "text-amber-600",
  completed: "text-emerald-600",
  failed: "text-red-600",
};

function isPlanStep(value: unknown): value is PlanStep {
  if (typeof value !== "object" || value === null) return false;
  const step = value as Record<string, unknown>;
  return (
    typeof step.id === "number" &&
    Number.isFinite(step.id) &&
    typeof step.title === "string" &&
    typeof step.detail === "string" &&
    typeof step.source === "string" &&
    typeof step.status === "string" &&
    (step.note === null || typeof step.note === "string")
  );
}

function readPlan(shared: Record<string, unknown>): Plan | null {
  const plan = shared.plan;
  if (typeof plan !== "object" || plan === null) return null;
  const steps = (plan as { steps?: unknown }).steps;
  if (!Array.isArray(steps) || steps.length === 0) return null;
  if (!steps.every(isPlanStep)) return null;
  if (typeof (plan as { status?: unknown }).status !== "string") return null;
  return plan as Plan;
}

export function PlanPanel({ shared }: { shared: Record<string, unknown> }) {
  const plan = readPlan(shared);

  if (plan === null) {
    return (
      <section className="border-b border-[var(--border)] px-4 py-3">
        <h2 className="font-mono text-[11px] uppercase tracking-wide text-[var(--muted)]">
          Piano di lavoro
        </h2>
        <p className="pt-1 text-xs text-[var(--muted)]">no plan in progress</p>
      </section>
    );
  }

  const done = plan.steps.filter((s) => s.status === "completed").length;

  return (
    <section className="border-b border-[var(--border)] px-4 py-3">
      <header className="flex items-baseline justify-between">
        <h2 className="font-mono text-[11px] uppercase tracking-wide text-[var(--muted)]">
          Piano di lavoro
        </h2>
        <span className="font-mono text-xs">
          {done}/{plan.steps.length}
        </span>
      </header>

      <ol className="space-y-3 pt-3">
        {plan.steps.map((step) => (
          <li key={step.id} className="flex gap-2">
            <span
              role="img"
              aria-label={STATUS_LABEL[step.status] ?? "Stato sconosciuto"}
              className={`pt-0.5 text-xs ${TONE[step.status] ?? ""}`}
            >
              {MARKER[step.status] ?? "○"}
            </span>
            <div className="min-w-0">
              <p className="text-sm">{step.title}</p>
              {step.detail && (
                <p className="text-xs text-[var(--muted)]">{step.detail}</p>
              )}
              {step.source && (
                <code className="mt-1 inline-block rounded bg-[var(--surface)] px-1.5 py-0.5 font-mono text-[11px] text-[var(--muted)]">
                  {step.source}
                </code>
              )}
              {step.note && <p className="pt-1 text-xs text-red-600">{step.note}</p>}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
