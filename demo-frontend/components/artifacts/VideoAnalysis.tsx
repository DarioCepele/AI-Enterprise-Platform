import type { Artifact } from "@/lib/agui/entries";

export function VideoAnalysis({ artifact }: { artifact: Artifact }) {
  if (artifact.component !== "video-analysis") return null;

  return (
    <figure className="video-analysis my-2 rounded-lg border border-[var(--border)] bg-[var(--surface)] p-3">
      <figcaption className="mb-2 flex flex-wrap items-baseline gap-2">
        <span className="font-mono text-[11px] text-[var(--wire)]">video analysis</span>
        <a
          href={artifact.video_url}
          target="_blank"
          rel="noreferrer"
          className="truncate text-xs text-[var(--muted)] hover:text-[var(--wire)] hover:underline"
        >
          {artifact.video_url}
        </a>
      </figcaption>
      {artifact.transcript ? (
        <p className="whitespace-pre-wrap text-sm leading-relaxed">{artifact.transcript}</p>
      ) : (
        <p className="text-sm italic text-[var(--muted)]">no speech understood</p>
      )}
      {artifact.description ? (
        <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-[var(--muted)]">
          {artifact.description}
        </p>
      ) : (
        <p className="mt-2 text-sm italic text-[var(--muted)]">no frame description produced</p>
      )}
    </figure>
  );
}
