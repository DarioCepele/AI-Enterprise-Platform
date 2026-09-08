const SEGNI = {
  "in corso": "◐",
  concluso: "●",
  errore: "✕",
} as const;

interface Props {
  name: string;
  description: string;
  stato: "in corso" | "concluso" | "errore";
  errore?: string;
}

export function SubagentEntry({ name, description, stato, errore }: Props) {
  return (
    <div
      data-stato={stato}
      className="subagent inline-flex max-w-full items-start gap-2 rounded-md border border-[var(--border)] bg-[var(--surface-accent)] px-2 py-1 text-xs"
    >
      <span aria-hidden className="font-mono">
        {SEGNI[stato]}
      </span>
      <span className="min-w-0">
        <span className="font-mono">{name}</span>
        <span className="text-[var(--muted)]"> · {stato}</span>
        {description && <span className="block text-[var(--muted)]">{description}</span>}
        {errore && <span className="block text-red-600">{errore}</span>}
      </span>
    </div>
  );
}
