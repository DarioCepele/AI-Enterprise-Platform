"use client";

import { useEffect, useRef, useState } from "react";
import { fetchLogs, type LogEntry } from "@/lib/agui/logs";

const TONE: Record<string, string> = {
  ERROR: "text-red-600",
  WARNING: "text-amber-600",
  INFO: "text-[var(--muted)]",
};

export function LogPanel({ running }: { running: boolean }) {
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const cursor = useRef("");
  const wasRunning = useRef(false);
  const [dropped, setDropped] = useState(0);

  useEffect(() => {
    const shouldPoll = running || wasRunning.current;
    wasRunning.current = running;
    if (!shouldPoll) return;

    let cancelled = false;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function poll() {
      try {
        const page = await fetchLogs(cursor.current, controller.signal);
        if (cancelled) return;
        cursor.current = page.cursor;
        if (page.entries.length > 0) {
          setEntries((current) => [...current, ...page.entries]);
        }
        setDropped((current) => current + page.dropped);
        setError(null);
      } catch (err) {
        if (!cancelled) setError(String(err));
      } finally {
        if (!cancelled && running) timer = setTimeout(poll, 1000);
      }
    }

    void poll();
    return () => {
      cancelled = true;
      controller.abort();
      clearTimeout(timer);
    };
  }, [running]);

  return (
    <div className="min-h-0 flex-1 overflow-y-auto font-mono text-[11px]">
      {error && <p role="alert" className="py-1 text-red-600">log non raggiungibili: {error}</p>}
      {dropped > 0 && <p role="status" className="py-1 text-amber-600">{dropped} righe di log non più disponibili</p>}
      {entries.length === 0 && !error && (
        <p className="py-1 text-[var(--muted)]">nessun log</p>
      )}
      {entries.map((entry) => (
        <div key={entry.seq} className="flex gap-2 border-b border-[var(--border)] py-1">
          <span className="shrink-0 text-[var(--muted)]">{entry.ts.slice(11, 19)}</span>
          <span className={`shrink-0 ${TONE[entry.level] ?? ""}`}>[{entry.source}]</span>
          <span className="whitespace-pre-wrap break-all">{entry.message}</span>
        </div>
      ))}
    </div>
  );
}
