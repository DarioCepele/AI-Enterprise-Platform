import { product } from "@/lib/config";

export function LabHeader() {
  return (
    <header className="flex flex-wrap items-center justify-between gap-4 border-b border-[var(--border)] px-6 py-4">
      <div className="flex items-center gap-3">
        <span aria-hidden="true" className="flex h-9 w-9 items-center justify-center rounded-lg bg-[var(--signal)] font-mono text-sm text-[var(--background)]">{product.monogram}</span>
        <div>
          <h1 className="text-base font-semibold tracking-tight">{product.name}</h1>
          <p className="text-xs text-[var(--muted)]">{product.tagline}</p>
        </div>
      </div>
      <div aria-label="Technologies" className="flex flex-wrap gap-1.5">
        {product.badges.map((badge) => (
          <span key={badge} className="rounded-md border border-[var(--border)] px-2 py-1 font-mono text-[10px] text-[var(--muted)]">{badge}</span>
        ))}
      </div>
    </header>
  );
}
