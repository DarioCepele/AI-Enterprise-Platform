"use client";

import { useEffect, useRef } from "react";
import type { Entry } from "@/lib/agui/entries";
import { emptyState } from "@/lib/config";
import { EntryView } from "./entries";

interface Props {
  entries: Entry[];
  running: boolean;
  error: string | null;
  onSend: (text: string) => void;
  onStop?: () => void;
}

const STICKY_PX = 80;

export function Chat({ entries, running, error, onSend, onStop }: Props) {
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  useEffect(() => {
    const el = scroller.current;
    if (!el || !stick.current) return;
    el.scrollTop = el.scrollHeight;
  });

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div
        ref={scroller}
        onScroll={(e) => {
          const el = e.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight <= STICKY_PX;
        }}
        className="min-h-0 flex-1 space-y-4 overflow-y-auto px-6 py-6"
      >
        {entries.length === 0 && (
          <div className="mx-auto max-w-lg py-12">
            <p className="mb-3 font-mono text-[10px] uppercase tracking-widest text-[var(--accent)]">{emptyState.eyebrow}</p>
            <h2 className="text-3xl font-medium leading-tight tracking-tight">{emptyState.headline}<br />{emptyState.subhead}</h2>
            <p className="mt-4 max-w-sm text-sm leading-relaxed text-[var(--muted)]">{emptyState.body}</p>
          </div>
        )}
        <div>
          {entries.map((entry) => (
            <div key={entry.id} className="timeline-entry" data-kind={entry.kind}>
              <EntryView entry={entry} />
            </div>
          ))}
        </div>
        {running && (
          <div className="flex items-center gap-3">
            <p role="status" className="font-mono text-[11px] uppercase tracking-wide text-[var(--muted)]">
              Working…
            </p>
            {onStop && (
              <button
                type="button"
                onClick={onStop}
                className="rounded-full border border-[var(--border)] px-2 py-0.5 font-mono text-[11px] uppercase tracking-wide text-[var(--muted)]"
              >
                stop
              </button>
            )}
          </div>
        )}
        {error && <p role="alert" className="text-xs text-red-600">error: {error}</p>}
      </div>

      <form
        className="border-t border-[var(--border)] px-6 py-4"
        onSubmit={(e) => {
          e.preventDefault();
          const input = e.currentTarget.elements.namedItem("q") as HTMLInputElement;
          if (running || !input.value.trim()) return;
          stick.current = true;
          onSend(input.value);
          input.value = "";
        }}
      >
        <div className="flex items-center gap-2 rounded-full border border-[var(--border)] px-4 py-2">
          <input
            name="q"
            aria-label="Message"
            disabled={running}
            placeholder="Write a message…"
            className="min-w-0 flex-1 bg-transparent text-sm focus-visible:outline-2 focus-visible:outline-offset-2"
          />
          <button
            type="submit"
            disabled={running}
            className="rounded-full bg-[var(--foreground)] px-4 py-1.5 text-xs text-[var(--background)] disabled:opacity-40"
          >
            send
          </button>
        </div>
      </form>
    </div>
  );
}
