import { useState } from "react";
import type { ApprovalEntryData } from "@/lib/agui/entries";
import { formatArgs } from "./ToolEntry";

interface Props {
  entry: ApprovalEntryData;
  /** Absent where answers cannot be sent (a read-only view): no buttons then. */
  onResolve?: (entryId: string, decisions: Record<string, boolean>) => void;
}

const BUTTON =
  "rounded-full px-3 py-1 text-xs focus-visible:outline-2 focus-visible:outline-offset-2 disabled:opacity-40";
const APPROVE = `${BUTTON} bg-[var(--foreground)] text-[var(--background)] hover:opacity-85`;
const REJECT = `${BUTTON} border border-[var(--border)] hover:border-[var(--wire)] hover:text-[var(--wire)]`;

/**
 * An action the agent will not take without a person's yes, shown with
 * exactly what would run. One action: the button sends the answer. Several
 * (one run can stop on more than one): each gets an answer, and they go out
 * together, since the protocol takes every open question in one resume.
 */
export function ApprovalEntry({ entry, onResolve }: Props) {
  const [draft, setDraft] = useState<Record<string, boolean>>({});
  const pending = entry.status === "pending" && onResolve !== undefined;
  const single = entry.requests.length === 1;
  const complete = entry.requests.every((r) => r.interruptId in draft);

  function answer(interruptId: string, approved: boolean) {
    if (single) onResolve?.(entry.id, { [interruptId]: approved });
    else setDraft((current) => ({ ...current, [interruptId]: approved }));
  }

  return (
    <section
      aria-label="Approval needed"
      data-status={entry.status}
      className="my-2 max-w-full rounded-md border border-[var(--signal)] bg-[var(--surface)] px-3 py-2 text-sm"
    >
      <p className="mb-1 text-xs font-medium text-[var(--signal)]">Approval needed</p>
      <ul className="space-y-3">
        {entry.requests.map((request) => {
          const shown = formatArgs(request.args);
          const decided = pending ? draft[request.interruptId] : entry.decisions[request.interruptId];
          return (
            <li key={request.interruptId}>
              <p>{request.question}</p>
              <p className="mt-1 font-mono text-xs">{request.tool}</p>
              {shown !== "" && (
                <pre className="mt-1 max-h-48 overflow-auto rounded bg-[var(--surface-accent)] px-2 py-1 font-mono text-xs">
                  {shown}
                </pre>
              )}
              {pending ? (
                <div className="mt-2 flex gap-2">
                  <button
                    type="button"
                    aria-pressed={single ? undefined : decided === true}
                    onClick={() => answer(request.interruptId, true)}
                    className={APPROVE}
                  >
                    Approve
                  </button>
                  <button
                    type="button"
                    aria-pressed={single ? undefined : decided === false}
                    onClick={() => answer(request.interruptId, false)}
                    className={REJECT}
                  >
                    Reject
                  </button>
                </div>
              ) : (
                decided !== undefined && (
                  <p className="mt-1 text-xs text-[var(--muted)]">{decided ? "Approved" : "Rejected"}</p>
                )
              )}
            </li>
          );
        })}
      </ul>
      {pending && !single && (
        <button
          type="button"
          disabled={!complete}
          onClick={() => onResolve?.(entry.id, draft)}
          className={`${APPROVE} mt-3`}
        >
          Send answers
        </button>
      )}
      {entry.error && (
        <p role="alert" className="mt-2 text-xs text-red-600">
          {entry.error}
        </p>
      )}
    </section>
  );
}
