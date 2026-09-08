"use client";

import { memo, useState } from "react";
import { groupEvents, type EventGroup } from "@/lib/agui/groups";
import type { AGUIEvent } from "@/lib/agui/types";
import { LogPanel } from "./LogPanel";

const FILTERS = {
  all: { label: "tutti", match: () => true },
  reasoning: { label: "ragionamento", match: (e: AGUIEvent) => e.type.startsWith("REASONING") },
  tools: { label: "tool", match: (e: AGUIEvent) => e.type.startsWith("TOOL_CALL") },
  subagents: { label: "sottoagenti", match: (e: AGUIEvent) => e.type.startsWith("SUBAGENT") },
  state: {
    label: "stato",
    match: (e: AGUIEvent) => e.type.startsWith("STATE") || e.type.startsWith("RUN"),
  },
  text: { label: "testo", match: (e: AGUIEvent) => e.type.startsWith("TEXT_MESSAGE") },
} as const;

const MAX_PAYLOAD = 50;

const EventRow = memo(function EventRow({ group }: { group: EventGroup }) {
  const [open, setOpen] = useState(false);
  const count = group.events.length;
  const shown = group.events.slice(0, MAX_PAYLOAD);
  const payload = count === 1 ? group.events[0] : shown;

  return (
    <details role="group" open={open} className="border-b border-[var(--border)] py-1">
      <summary
        onClick={(e) => {
          e.preventDefault();
          setOpen((was) => !was);
        }}
        className="flex cursor-pointer items-center gap-2 text-[var(--foreground)]"
      >
        <span>{group.type}</span>
        {count > 1 && (
          <span className="rounded-full bg-[var(--surface)] px-1.5 tabular-nums text-[var(--muted)]">
            {`×${count}`}
          </span>
        )}
      </summary>
      {open && (
        <>
          <pre className="overflow-x-auto pt-1 text-[var(--muted)]">
            {JSON.stringify(payload, null, 2)}
          </pre>
          {count > shown.length && (
            <p className="pt-1 text-[var(--muted)]">
              …primi {shown.length} di {count} eventi del gruppo
            </p>
          )}
        </>
      )}
    </details>
  );
});

export function Inspector({ events, running }: { events: AGUIEvent[]; running: boolean }) {
  const [tab, setTab] = useState<"events" | "log">("events");
  const [filter, setFilter] = useState<keyof typeof FILTERS>("all");
  const groups = groupEvents(events.filter(FILTERS[filter].match));

  return (
    <section aria-label="Inspector" className="inspector flex min-h-0 flex-1 flex-col p-4">
      <nav aria-label="Vista inspector" className="inspector-tabs mb-3 flex gap-4">
        <button
          type="button"
          onClick={() => setTab("events")}
          aria-pressed={tab === "events"}
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

      <div hidden={tab !== "events"} className={tab === "events" ? "flex min-h-0 flex-1 flex-col" : undefined}>
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
              {FILTERS[name].label}
            </button>
          ))}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto font-mono text-xs">
          {groups.map((group) => (
            <EventRow key={`${group.index}:${group.type}`} group={group} />
          ))}
        </div>
      </div>
      <div hidden={tab !== "log"} className={tab === "log" ? "flex min-h-0 flex-1 flex-col" : undefined}>
        <LogPanel running={running} />
      </div>
    </section>
  );
}
