import type { Artifact } from "@/lib/agui/entries";

export function Scheda({ artifact }: { artifact: Artifact }) {
  if (artifact.component !== "scheda") return null;

  return (
    <figure className="scheda my-2 rounded-lg border border-[var(--border)] bg-[var(--surface)] p-3">
      <figcaption className="mb-2 flex flex-wrap items-baseline gap-2">
        <span className="font-mono text-[11px] uppercase tracking-wide text-[var(--accent)]">
          {artifact.agente}
        </span>
        {artifact.domanda && (
          <span className="text-xs text-[var(--muted)]">{artifact.domanda}</span>
        )}
      </figcaption>
      <p className="whitespace-pre-wrap text-sm leading-relaxed">{artifact.estratto}</p>
      {artifact.documenti.length > 0 && (
        <p className="mt-2 font-mono text-[11px] text-[var(--muted)]">
          fonti: {artifact.documenti.join(", ")}
        </p>
      )}
    </figure>
  );
}
