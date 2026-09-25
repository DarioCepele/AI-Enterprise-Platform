"use client";

import { useEffect, useRef, useState } from "react";
import type { Entry } from "@/lib/agui/entries";
import type { MessagePart } from "@/lib/agui/types";
import { uploadVideo } from "@/lib/agui/client";
import { emptyState, product } from "@/lib/config";
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
  /** Hidden entirely when this deployment has no voice service, same as before. */
  voiceAvailable: boolean;
  voiceActive: boolean;
  voiceError: string | null;
  onToggleVoice: () => void;
}

const STICKY_PX = 80;

/** A frame with a status dot — filled once a file is attached, matching the
 * "signal" dot used elsewhere for something armed/live rather than a stock
 * camcorder glyph. */
function VideoIcon({ attached }: { attached: boolean }) {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
      <rect x="1" y="2.5" width="9" height="9" rx="2" stroke="currentColor" strokeWidth="1.3" />
      <path d="M10 6L13 4V10L10 8" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
      <circle cx="5.5" cy="7" r="1.4" fill={attached ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.1" />
    </svg>
  );
}

/** A spinning arc — shown in place of VideoIcon while the file is actually
 * uploading, so "attached, waiting to send" and "uploading right now" don't
 * look the same. */
function SpinnerIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      fill="none"
      aria-hidden="true"
      className="animate-spin"
      data-testid="video-upload-spinner"
    >
      <circle cx="7" cy="7" r="5.5" stroke="currentColor" strokeWidth="1.3" strokeOpacity="0.25" />
      <path d="M12.5 7a5.5 5.5 0 0 0-5.5-5.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </svg>
  );
}

/** A capsule mic — filled while armed, matching VideoIcon's "attached" fill. */
function MicIcon({ active }: { active: boolean }) {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
      <rect x="5" y="1" width="4" height="7" rx="2" stroke="currentColor" strokeWidth="1.3" fill={active ? "currentColor" : "none"} />
      <path d="M3 6.5C3 9 4.8 10.5 7 10.5C9.2 10.5 11 9 11 6.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
      <path d="M7 10.5V13" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </svg>
  );
}

export function Chat({
  entries,
  running,
  error,
  onSend,
  onStop,
  voiceAvailable,
  voiceActive,
  voiceError,
  onToggleVoice,
}: Props) {
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const fileInput = useRef<HTMLInputElement>(null);
  const [video, setVideo] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const busy = running || uploading || voiceActive;
  // The mic itself must stay clickable while voice is active — that's how it
  // stops. Only a text-side run in flight blocks starting a voice turn.
  const micDisabled = running || uploading;

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
          <div className="mx-auto flex min-h-[60vh] max-w-lg flex-col justify-center">
            <p
              role="note"
              className="mb-4 rounded-lg border border-[var(--border)] px-3 py-2 text-sm text-[var(--foreground)]"
            >
              {product.aiDisclosure}
            </p>
            <p className="mb-3 text-sm text-[var(--muted)]">{emptyState.eyebrow}</p>
            <h2 className="text-3xl font-medium leading-tight tracking-tight text-balance">{emptyState.headline}<br />{emptyState.subhead}</h2>
            <p className="mt-4 max-w-sm text-sm leading-relaxed text-[var(--muted)]">{emptyState.body}</p>
          </div>
        )}
        <div className="mx-auto max-w-[70ch]">
          {entries.map((entry) => (
            <div key={entry.id} className="timeline-entry" data-kind={entry.kind}>
              <EntryView entry={entry} />
            </div>
          ))}
        </div>
        {running && (
          <div className="flex items-center gap-2">
            <span aria-hidden="true" className="h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--signal)]" />
            <p role="status" className="text-sm text-[var(--signal)]">
              Working…
            </p>
            {onStop && (
              <button
                type="button"
                onClick={onStop}
                className="rounded-full border border-[var(--border)] px-2 py-0.5 text-xs text-[var(--muted)] hover:border-[var(--wire)] hover:text-[var(--wire)]"
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
              parts.push({
                type: "video",
                source: { type: "url", value: url, mimeType: file.type || "video/mp4" },
              });
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
            className="flex shrink-0 items-center gap-1.5 rounded-full border border-[var(--border)] px-2 py-1 text-xs hover:border-[var(--wire)] hover:text-[var(--wire)] disabled:opacity-40 disabled:hover:border-[var(--border)] disabled:hover:text-inherit"
          >
            {uploading ? <SpinnerIcon /> : <VideoIcon attached={video !== null} />}
            {uploading
              ? "uploading…"
              : video
                ? video.name.length > 16 ? `${video.name.slice(0, 13)}…` : video.name
                : "video"}
          </button>
          {voiceAvailable && (
            <button
              type="button"
              onClick={onToggleVoice}
              disabled={micDisabled}
              aria-pressed={voiceActive}
              title={voiceActive ? "Stop talking" : "Talk to the agent"}
              className={`flex shrink-0 items-center gap-1.5 rounded-full border px-2 py-1 text-xs disabled:opacity-40 ${
                voiceActive
                  ? "border-[var(--signal)] text-[var(--signal)]"
                  : "border-[var(--border)] hover:border-[var(--wire)] hover:text-[var(--wire)]"
              }`}
            >
              {voiceActive && <span aria-hidden="true" className="h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--signal)]" />}
              <MicIcon active={voiceActive} />
            </button>
          )}
          <input
            name="q"
            aria-label="Message"
            disabled={busy}
            placeholder={voiceActive ? "Listening…" : "Write a message…"}
            className="min-w-0 flex-1 bg-transparent text-sm focus-visible:outline-2 focus-visible:outline-offset-2"
          />
          <button
            type="submit"
            disabled={busy}
            className="rounded-full bg-[var(--foreground)] px-4 py-1.5 text-xs text-[var(--background)] hover:opacity-85 disabled:opacity-40"
          >
            {uploading ? "uploading…" : "send"}
          </button>
        </div>
        {video && (
          <button
            type="button"
            onClick={() => {
              setVideo(null);
              if (fileInput.current) fileInput.current.value = "";
            }}
            className="mt-1 px-1 text-xs text-[var(--muted)] hover:text-[var(--wire)] hover:underline"
          >
            remove attachment
          </button>
        )}
        {uploadError && (
          <p role="alert" className="mt-1 px-1 text-xs text-red-600">
            upload error: {uploadError}
          </p>
        )}
        {voiceError && (
          <p role="alert" className="mt-1 px-1 text-xs text-red-600">
            voice error: {voiceError}
          </p>
        )}
      </form>
    </div>
  );
}
