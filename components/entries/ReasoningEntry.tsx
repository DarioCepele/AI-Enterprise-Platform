/** Il ragionamento e' contesto, non risposta: arriva collassato. */
export function ReasoningEntry({ text, done }: { text: string; done: boolean }) {
  return (
    <details className="rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2">
      <summary className="cursor-pointer font-mono text-[11px] uppercase tracking-wide text-[var(--muted)]">
        Ragionamento {done ? "" : "…"}
      </summary>
      <p className="whitespace-pre-wrap pt-2 text-xs text-[var(--muted)]">{text}</p>
    </details>
  );
}
