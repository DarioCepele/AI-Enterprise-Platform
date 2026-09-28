"use client";

import { useCallback, useEffect, useState } from "react";
import {
  answerStep,
  decideStep,
  fetchInstance,
  fetchInstances,
  processesConfigured,
} from "@/lib/processes/client";
import {
  INSTANCE_LABEL,
  STEP_LABEL,
  isOver,
  needsSomebody,
  waitingStep,
  type Instance,
  type InstanceStep,
} from "@/lib/processes/types";

const MARKER: Record<string, string> = {
  pending: "○",
  running: "◉",
  waiting: "◔",
  waiting_human: "?",
  waiting_approval: "!",
  completed: "●",
  failed: "✕",
  rejected: "✕",
  escalated: "↗",
  compensated: "↩",
  compensation_failed: "✕",
};

const TONE: Record<string, string> = {
  pending: "text-[var(--muted)]",
  running: "text-amber-600",
  waiting: "text-amber-600",
  waiting_human: "text-sky-600",
  waiting_approval: "text-sky-600",
  completed: "text-emerald-600",
  failed: "text-red-600",
  rejected: "text-red-600",
  escalated: "text-amber-600",
  compensated: "text-violet-600",
  compensation_failed: "text-red-600",
};

const FILTERS = [
  { key: "", label: "all" },
  { key: "waiting_approval", label: "to decide" },
  { key: "waiting_human", label: "to answer" },
  { key: "completed", label: "finished" },
] as const;

function when(value: string | null): string {
  if (!value) return "";
  return new Date(value).toLocaleTimeString();
}

/** What a step is waiting for, said in the words of whoever has to act. */
function WaitingOn({
  instance,
  step,
  onDone,
}: {
  instance: Instance;
  step: InstanceStep;
  onDone: () => void;
}) {
  const [text, setText] = useState("");
  const [by, setBy] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const act = useCallback(
    async (run: () => Promise<void>) => {
      setBusy(true);
      setError(null);
      try {
        await run();
        setText("");
        onDone();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(false);
      }
    },
    [onDone],
  );

  if (step.status === "waiting_human") {
    return (
      <div className="mt-2 rounded border border-[var(--border)] p-2">
        <p className="text-xs">{step.question ?? "The agent asked for a clarification."}</p>
        <div className="mt-2 flex gap-2">
          <input
            aria-label="Answer"
            value={text}
            disabled={busy}
            onChange={(event) => setText(event.target.value)}
            placeholder="answer the agent"
            className="min-w-0 flex-1 rounded border border-[var(--border)] bg-[var(--background)] px-2 py-1 text-xs"
          />
          <button
            type="button"
            disabled={busy || text.trim() === ""}
            onClick={() => act(() => answerStep(instance.id, step.step_id, text.trim()))}
            className="rounded bg-[var(--surface)] px-2 py-1 text-xs disabled:opacity-50"
          >
            Answer
          </button>
        </div>
        {error && <p role="alert" className="pt-1 text-xs text-red-600">{error}</p>}
      </div>
    );
  }

  return (
    <div className="mt-2 rounded border border-[var(--border)] p-2">
      <p className="text-xs">{step.question ?? "Waiting for a decision."}</p>
      <div className="mt-2 flex gap-2">
        <input
          aria-label="Who decides"
          value={by}
          disabled={busy}
          onChange={(event) => setBy(event.target.value)}
          placeholder="who decides"
          className="min-w-0 flex-1 rounded border border-[var(--border)] bg-[var(--background)] px-2 py-1 text-xs"
        />
        <button
          type="button"
          disabled={busy || by.trim() === ""}
          onClick={() =>
            act(() => decideStep(instance.id, step.step_id, by.trim(), "approved"))
          }
          className="rounded bg-[var(--surface)] px-2 py-1 text-xs disabled:opacity-50"
        >
          Approve
        </button>
        <button
          type="button"
          disabled={busy || by.trim() === ""}
          onClick={() =>
            act(() => decideStep(instance.id, step.step_id, by.trim(), "rejected"))
          }
          className="rounded bg-[var(--surface)] px-2 py-1 text-xs disabled:opacity-50"
        >
          Reject
        </button>
      </div>
      {error && <p role="alert" className="pt-1 text-xs text-red-600">{error}</p>}
    </div>
  );
}

function Detail({ instance, onChanged }: { instance: Instance; onChanged: () => void }) {
  return (
    // Indented, with a rule on the left: without them the list of steps reads
    // as more instances instead of the steps of this one.
    <div className="mt-2 border-l-2 border-[var(--border)] pl-3">
      {instance.note && <p className="pb-2 text-xs text-[var(--muted)]">{instance.note}</p>}
      <ol className="space-y-2">
        {instance.steps.map((step) => (
          <li key={step.step_id} className="flex gap-2">
            <span
              role="img"
              aria-label={STEP_LABEL[step.status] ?? step.status}
              className={`pt-0.5 text-xs ${TONE[step.status] ?? ""}`}
            >
              {MARKER[step.status] ?? "○"}
            </span>
            <div className="min-w-0 flex-1">
              <p className="flex items-baseline gap-2 text-sm">
                <span className="font-mono text-xs">{step.step_id}</span>
                {step.owner && (
                  <span className="text-[11px] text-[var(--muted)]">{step.owner}</span>
                )}
                {step.ended_at && (
                  <span className="ml-auto font-mono text-[10px] text-[var(--muted)]">
                    {when(step.ended_at)}
                  </span>
                )}
              </p>
              {step.note && <p className="text-xs text-[var(--muted)]">{step.note}</p>}
              {needsSomebody(step.status) && (
                <WaitingOn instance={instance} step={step} onDone={onChanged} />
              )}
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}

export function InstancePanel({ running }: { running: boolean }) {
  const [instances, setInstances] = useState<Instance[]>([]);
  const [filter, setFilter] = useState<string>("");
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fetched, setFetched] = useState<Instance | null>(null);
  const [reload, setReload] = useState(0);
  // Whether a process service exists at all is configuration, read once: it
  // does not change while somebody is looking at the page.
  const [configured] = useState(processesConfigured);
  // Derived, not stored: the detail belongs to the instance that is open, and
  // clearing it in an effect would be a render for nothing.
  const detail = open && fetched?.id === open ? fetched : null;

  useEffect(() => {
    if (!configured) return;
    let cancelled = false;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function poll() {
      try {
        const found = await fetchInstances(filter, controller.signal);
        if (cancelled) return;
        setInstances(found);
        setError(null);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      } finally {
        // An instance moves on its own -- an agent answers, somebody approves --
        // so the list keeps looking even when nothing is running here.
        if (!cancelled) timer = setTimeout(poll, running ? 1500 : 5000);
      }
    }

    void poll();
    return () => {
      cancelled = true;
      controller.abort();
      clearTimeout(timer);
    };
  }, [configured, filter, running, reload]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    const controller = new AbortController();
    fetchInstance(open, controller.signal)
      .then((found) => !cancelled && setFetched(found))
      .catch((err) => !cancelled && setError(String(err)));
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [open, instances, reload]);

  const changed = useCallback(() => setReload((n) => n + 1), []);

  if (!configured) {
    return (
      <p className="py-2 text-xs text-[var(--muted)]">
        No process service configured.
      </p>
    );
  }

  const waiting = instances.filter((instance) => needsSomebody(instance.status));

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <nav aria-label="Instance filter" className="flex gap-3 pb-2">
        {FILTERS.map((option) => (
          <button
            key={option.key}
            type="button"
            onClick={() => setFilter(option.key)}
            aria-pressed={filter === option.key}
            className="font-mono text-[11px] text-[var(--muted)] aria-pressed:text-[var(--foreground)]"
          >
            {option.label}
          </button>
        ))}
      </nav>

      {error && (
        <p role="alert" className="py-1 text-xs text-red-600">
          processes unreachable: {error}
        </p>
      )}

      {waiting.length > 0 && filter === "" && (
        <p role="status" className="pb-2 text-xs text-sky-600">
          {waiting.length} waiting for someone
        </p>
      )}

      {instances.length === 0 && !error && (
        <p className="py-1 text-xs text-[var(--muted)]">no instances</p>
      )}

      <ul className="space-y-1">
        {instances.map((instance) => {
          const isOpen = open === instance.id;
          const stopped = waitingStep(instance);
          return (
            <li key={instance.id} className="border-b border-[var(--border)] py-1">
              <button
                type="button"
                onClick={() => setOpen(isOpen ? null : instance.id)}
                aria-expanded={isOpen}
                className="flex w-full items-baseline gap-2 text-left"
              >
                <span
                  role="img"
                  aria-label={INSTANCE_LABEL[instance.status] ?? instance.status}
                  className={`text-xs ${TONE[instance.status] ?? ""}`}
                >
                  {MARKER[instance.status] ?? "○"}
                </span>
                <span className="min-w-0 flex-1 truncate text-sm">{instance.process_id}</span>
                <span className="font-mono text-[10px] text-[var(--muted)]">
                  {instance.id.slice(0, 8)}
                </span>
              </button>
              <p className="flex gap-2 pl-6 text-[11px] text-[var(--muted)]">
                <span>{INSTANCE_LABEL[instance.status] ?? instance.status}</span>
                {stopped && <span>· {stopped.step_id}</span>}
                {isOver(instance.status) && instance.updated_at && (
                  <span className="ml-auto font-mono">{when(instance.updated_at)}</span>
                )}
              </p>
              {isOpen && detail !== null && <Detail instance={detail} onChanged={changed} />}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
