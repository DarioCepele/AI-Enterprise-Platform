"use client";

import { useState } from "react";
import type { AGUIEvent } from "@/lib/agui/types";
import { LogPanel } from "./LogPanel";

const FILTERS = {
  tutti: () => true,
  ragionamento: (e: AGUIEvent) => e.type.startsWith("REASONING"),
  tool: (e: AGUIEvent) => e.type.startsWith("TOOL_CALL"),
  stato: (e: AGUIEvent) => e.type.startsWith("STATE") || e.type.startsWith("RUN"),
  testo: (e: AGUIEvent) => e.type.startsWith("TEXT_MESSAGE"),
} as const;

export function Inspector({ events, running }: { events: AGUIEvent[]; running: boolean }) {
  const [tab, setTab] = useState<"eventi" | "log">("eventi");
  const [filter, setFilter] = useState<keyof typeof FILTERS>("tutti");
  const shown = events.filter(FILTERS[filter]);

  return (
    <section aria-label="Inspector" className="inspector flex min-h-0 flex-1 flex-col p-4">
      <nav aria-label="Vista inspector" className="inspector-tabs mb-3 flex gap-4">
        <button
          type="button"
          onClick={() => setTab("eventi")}
          aria-pressed={tab === "eventi"}
          className="font-mono text-[11px] uppercase tracking-wide"
        >
          Event inspector <span className="tabular-nums">{events.length}</span>
        </button>
        <button
          type="button"
          onClick={() => setTab("log")}
          aria-pressed={tab === "log"}
          className="font-mono text-[11px] uppercase tracking-wide"
        >
          Log
        </button>
      </nav>

      <div hidden={tab !== "eventi"} className={tab === "eventi" ? "flex min-h-0 flex-1 flex-col" : undefined}>
        <div className="mb-2 flex flex-wrap gap-1">
          {(Object.keys(FILTERS) as (keyof typeof FILTERS)[]).map((name) => (
            <button
              key={name}
              type="button"
              onClick={() => setFilter(name)}
              aria-pressed={filter === name}
              className={`rounded-full px-2 py-0.5 text-xs ${
                filter === name
                  ? "bg-[var(--foreground)] text-[var(--background)]"
                  : "bg-[var(--surface)] text-[var(--muted)]"
              }`}
            >
              {name}
            </button>
          ))}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto font-mono text-xs">
          {shown.map((e, i) => (
            <details key={i} role="group" className="border-b border-[var(--border)] py-1">
              <summary className="cursor-pointer text-[var(--foreground)]">{e.type}</summary>
              <pre className="overflow-x-auto pt-1 text-[var(--muted)]">
                {JSON.stringify(e, null, 2)}
              </pre>
            </details>
          ))}
        </div>
      </div>
      {/* Il polling segue la run anche quando si guardano gli eventi. */}
      <div hidden={tab !== "log"} className={tab === "log" ? "flex min-h-0 flex-1 flex-col" : undefined}>
        <LogPanel running={running} />
      </div>
    </section>
  );
}
