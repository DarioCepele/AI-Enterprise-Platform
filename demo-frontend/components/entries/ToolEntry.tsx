export function ToolEntry({ name, args, done }: { name: string; args: string; done: boolean }) {
  const shown = formatArgs(args);

  return (
    <details className="inline-block max-w-full rounded-md border border-[var(--border)] bg-[var(--surface)] px-2 py-1 font-mono text-xs">
      <summary className="flex cursor-pointer items-center gap-2 whitespace-nowrap">
        <span className="text-[var(--muted)]">{">_"}</span>
        <span>{name}</span>
        {!done && <span className="text-[var(--muted)]">…</span>}
      </summary>
      <pre className="mt-1 overflow-x-auto text-[var(--muted)]">
        {shown === "" ? "no arguments" : shown}
      </pre>
    </details>
  );
}

/** A tool call's arguments, indented when they are JSON, as sent otherwise. */
export function formatArgs(args: string): string {
  const trimmed = args.trim();
  if (trimmed === "") return "";
  try {
    return JSON.stringify(JSON.parse(trimmed), null, 2);
  } catch {
    return trimmed;
  }
}
