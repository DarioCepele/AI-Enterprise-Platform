const SIGNS = {
  running: "◐",
  done: "●",
  failed: "✕",
} as const;

const LABELS = {
  running: "in corso",
  done: "concluso",
  failed: "errore",
} as const;

interface Props {
  name: string;
  description: string;
  status: "running" | "done" | "failed";
  error?: string;
}

export function SubagentEntry({ name, description, status, error }: Props) {
  return (
    <div
      data-status={status}
      className="subagent inline-flex max-w-full items-start gap-2 rounded-md border border-[var(--border)] bg-[var(--surface-accent)] px-2 py-1 text-xs"
    >
      <span aria-hidden className="font-mono">
        {SIGNS[status]}
      </span>
      <span className="min-w-0">
        <span className="font-mono">{name}</span>
        <span className="text-[var(--muted)]"> · {LABELS[status]}</span>
        {description && <span className="block text-[var(--muted)]">{description}</span>}
        {error && <span className="block text-red-600">{error}</span>}
      </span>
    </div>
  );
}
