"use client";

import type { Entry } from "@/lib/agui/entries";
import { EntryView } from "./entries";

interface Props {
  entries: Entry[];
  running: boolean;
  error: string | null;
  onSend: (text: string) => void;
}

export function Chat({ entries, running, error, onSend }: Props) {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-6 py-6">
        {entries.map((entry) => (
          <EntryView key={entry.id} entry={entry} />
        ))}
        {running && (
          <p role="status" className="font-mono text-[11px] uppercase tracking-wide text-[var(--muted)]">
            Sto lavorando…
          </p>
        )}
        {error && <p role="alert" className="text-xs text-red-600">errore: {error}</p>}
      </div>

      <form
        className="border-t border-[var(--border)] px-6 py-4"
        onSubmit={(e) => {
          e.preventDefault();
          const input = e.currentTarget.elements.namedItem("q") as HTMLInputElement;
          if (running || !input.value.trim()) return;
          onSend(input.value);
          input.value = "";
        }}
      >
        <div className="flex items-center gap-2 rounded-full border border-[var(--border)] px-4 py-2">
          <input
            name="q"
            aria-label="Messaggio"
            disabled={running}
            placeholder="Scrivi un messaggio…"
            className="min-w-0 flex-1 bg-transparent text-sm focus-visible:outline-2 focus-visible:outline-offset-2"
          />
          <button
            type="submit"
            disabled={running}
            className="rounded-full bg-[var(--foreground)] px-4 py-1.5 text-xs text-[var(--background)] disabled:opacity-40"
          >
            invia
          </button>
        </div>
      </form>
    </div>
  );
}
