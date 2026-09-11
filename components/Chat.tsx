"use client";

import { useEffect, useRef, useState } from "react";
import type { Entry } from "@/lib/agui/entries";
import type { MessagePart } from "@/lib/agui/types";
import { uploadVideo } from "@/lib/agui/client";
import { emptyState } from "@/lib/config";
import { EntryView } from "./entries";

interface Props {
  entries: Entry[];
  running: boolean;
  error: string | null;
  /**
   * Plain text for an ordinary message. Once a video is attached, `content`
   * carries the parts (text + video) and `displayText` is what the timeline
   * shows for the user's own bubble.
   */
  onSend: (content: string | MessagePart[], displayText?: string) => void;
  onStop?: () => void;
}

const STICKY_PX = 80;

export function Chat({ entries, running, error, onSend, onStop }: Props) {
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const fileInput = useRef<HTMLInputElement>(null);
  const [video, setVideo] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const busy = running || uploading;

  useEffect(() => {
    const el = scroller.current;
    if (!el || !stick.current) return;
    el.scrollTop = el.scrollHeight;
  });

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div
        ref={scroller}
        onScroll={(e) => {
          const el = e.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight <= STICKY_PX;
        }}
        className="min-h-0 flex-1 space-y-4 overflow-y-auto px-6 py-6"
      >
        {entries.length === 0 && (
          <div className="mx-auto max-w-lg py-12">
            <p className="mb-3 font-mono text-[10px] uppercase tracking-widest text-[var(--accent)]">{emptyState.eyebrow}</p>
            <h2 className="text-3xl font-medium leading-tight tracking-tight">{emptyState.headline}<br />{emptyState.subhead}</h2>
            <p className="mt-4 max-w-sm text-sm leading-relaxed text-[var(--muted)]">{emptyState.body}</p>
          </div>
        )}
        <div>
          {entries.map((entry) => (
            <div key={entry.id} className="timeline-entry" data-kind={entry.kind}>
              <EntryView entry={entry} />
            </div>
          ))}
        </div>
        {running && (
          <div className="flex items-center gap-3">
            <p role="status" className="font-mono text-[11px] uppercase tracking-wide text-[var(--muted)]">
              Working…
            </p>
            {onStop && (
              <button
                type="button"
                onClick={onStop}
                className="rounded-full border border-[var(--border)] px-2 py-0.5 font-mono text-[11px] uppercase tracking-wide text-[var(--muted)]"
              >
                stop
              </button>
            )}
          </div>
        )}
        {error && <p role="alert" className="text-xs text-red-600">error: {error}</p>}
      </div>

      <form
        className="border-t border-[var(--border)] px-6 py-4"
        onSubmit={(e) => {
          e.preventDefault();
          const input = e.currentTarget.elements.namedItem("q") as HTMLInputElement;
          const text = input.value.trim();
          if (busy) return;
          if (!text && !video) return;
          stick.current = true;

          if (!video) {
            onSend(text);
            input.value = "";
            return;
          }

          const file = video;
          setUploading(true);
          setUploadError(null);
          uploadVideo(file)
            .then((url) => {
              const parts: MessagePart[] = [];
              if (text) parts.push({ type: "text", text });
              parts.push({ type: "video", source: { type: "url", value: url } });
              onSend(parts, text || `video: ${file.name}`);
              input.value = "";
              setVideo(null);
              if (fileInput.current) fileInput.current.value = "";
            })
            .catch((err) => {
              setUploadError(err instanceof Error ? err.message : String(err));
            })
            .finally(() => setUploading(false));
        }}
      >
        <div className="flex items-center gap-2 rounded-full border border-[var(--border)] px-4 py-2">
          <input
            ref={fileInput}
            type="file"
            accept="video/*"
            aria-label="Attach a video"
            className="hidden"
            onChange={(e) => {
              setUploadError(null);
              setVideo(e.currentTarget.files?.[0] ?? null);
            }}
          />
          <button
            type="button"
            onClick={() => fileInput.current?.click()}
            disabled={busy}
            title={video ? video.name : "Attach a video"}
            className="shrink-0 rounded-full border border-[var(--border)] px-2 py-1 font-mono text-[11px] disabled:opacity-40"
          >
            {video ? "🎬" : "+ video"}
          </button>
          <input
            name="q"
            aria-label="Message"
            disabled={busy}
            placeholder="Write a message…"
            className="min-w-0 flex-1 bg-transparent text-sm focus-visible:outline-2 focus-visible:outline-offset-2"
          />
          <button
            type="submit"
            disabled={busy}
            className="rounded-full bg-[var(--foreground)] px-4 py-1.5 text-xs text-[var(--background)] disabled:opacity-40"
          >
            {uploading ? "uploading…" : "send"}
          </button>
        </div>
        {video && (
          <p className="mt-1 px-1 font-mono text-[10px] text-[var(--muted)]">
            video: {video.name}{" "}
            <button
              type="button"
              onClick={() => {
                setVideo(null);
                if (fileInput.current) fileInput.current.value = "";
              }}
              className="underline"
            >
              remove
            </button>
          </p>
        )}
        {uploadError && (
          <p role="alert" className="mt-1 px-1 text-xs text-red-600">
            upload error: {uploadError}
          </p>
        )}
      </form>
    </div>
  );
}
