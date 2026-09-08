const BADGES = ["AG-UI", "MAF 1.17", "Next.js"];

export function LabHeader() {
  return (
    <header className="flex flex-wrap items-center justify-between gap-4 border-b border-[var(--border)] px-6 py-4">
      <div className="flex items-center gap-3">
        <span aria-hidden="true" className="flex h-9 w-9 items-center justify-center rounded-lg bg-[var(--foreground)] font-mono text-sm text-[var(--background)]">a/</span>
        <div>
          <h1 className="text-base font-semibold tracking-tight">Laboratorio AG-UI</h1>
          <p className="font-mono text-[10px] uppercase tracking-widest text-[var(--muted)]">Studio 02 / agente in azione</p>
        </div>
      </div>
      <div aria-label="Tecnologie del laboratorio" className="flex flex-wrap gap-1.5">
        {BADGES.map((badge) => (
          <span key={badge} className="rounded-md border border-[var(--border)] px-2 py-1 font-mono text-[10px] text-[var(--muted)]">{badge}</span>
        ))}
      </div>
    </header>
  );
}
