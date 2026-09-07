/** Una riga compatta: il nome del tool. Gli argomenti stanno nell'inspector. */
export function ToolEntry({ name, done }: { name: string; done: boolean }) {
  return (
    <div className="inline-flex items-center gap-2 rounded-md border border-[var(--border)] bg-[var(--surface)] px-2 py-1 font-mono text-xs">
      <span className="text-[var(--muted)]">{">_"}</span>
      <span>{name}</span>
      {!done && <span className="text-[var(--muted)]">…</span>}
    </div>
  );
}
