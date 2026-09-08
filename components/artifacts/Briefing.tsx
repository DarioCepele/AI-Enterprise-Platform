import type { Artifact } from "@/lib/agui/entries";

export function Briefing({ artifact }: { artifact: Artifact }) {
  if (artifact.component !== "briefing") return null;

  return (
    <figure className="briefing my-2 rounded-lg border border-[var(--border)] bg-[var(--surface)] p-3">
      <figcaption className="mb-2 flex flex-wrap items-baseline gap-2">
        <span className="font-mono text-[11px] uppercase tracking-wide text-[var(--accent)]">
          {artifact.agent}
        </span>
        {artifact.question && (
          <span className="text-xs text-[var(--muted)]">{artifact.question}</span>
        )}
      </figcaption>
      <p className="whitespace-pre-wrap text-sm leading-relaxed">{artifact.summary}</p>
      {artifact.documents.length > 0 && (
        <p className="mt-2 font-mono text-[11px] text-[var(--muted)]">
          fonti: {artifact.documents.join(", ")}
        </p>
      )}
    </figure>
  );
}
