/**
 * Una riga compatta: il nome del tool, con gli argomenti a richiesta.
 *
 * Gli argomenti arrivano come delta e restano una stringa: si mostrano
 * indentati quando sono JSON valido, grezzi quando il modello li tronca.
 */
export function ToolEntry({ name, args, done }: { name: string; args: string; done: boolean }) {
  const shown = format(args);

  return (
    <details className="inline-block max-w-full rounded-md border border-[var(--border)] bg-[var(--surface)] px-2 py-1 font-mono text-xs">
      <summary className="flex cursor-pointer items-center gap-2 whitespace-nowrap">
        <span className="text-[var(--muted)]">{">_"}</span>
        <span>{name}</span>
        {!done && <span className="text-[var(--muted)]">…</span>}
      </summary>
      <pre className="mt-1 overflow-x-auto text-[var(--muted)]">
        {shown === "" ? "nessun argomento" : shown}
      </pre>
    </details>
  );
}

function format(args: string): string {
  const trimmed = args.trim();
  if (trimmed === "") return "";
  try {
    return JSON.stringify(JSON.parse(trimmed), null, 2);
  } catch {
    return trimmed;
  }
}
