"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { VoiceSession, voiceConfigured } from "@/lib/voice/client";

interface Line {
  id: string;
  speaker: "user" | "assistant";
  text: string;
}

/**
 * A live voice turn with the agent: microphone in, transcript and speech
 * back out, over `/ws/voice` on `demo-voice-service` — a separate path from
 * the AG-UI chat in `Chat.tsx`/`Lab.tsx`. Renders nothing when this
 * deployment has no voice service configured, same principle as the process
 * panel.
 */
export function VoiceChat() {
  const [active, setActive] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lines, setLines] = useState<Line[]>([]);
  const [configured] = useState(voiceConfigured);
  const session = useRef<VoiceSession | null>(null);

  const stop = useCallback(() => {
    session.current?.stop();
    session.current = null;
    setActive(false);
  }, []);

  // A closed tab should not leave a microphone open.
  useEffect(() => () => session.current?.stop(), []);

  const start = useCallback(async () => {
    setError(null);
    setLines([]);
    const s = new VoiceSession({
      onUserTranscript: (text) =>
        setLines((prev) => [...prev, { id: crypto.randomUUID(), speaker: "user", text }]),
      onAssistantTextChunk: (text) =>
        setLines((prev) => [...prev, { id: crypto.randomUUID(), speaker: "assistant", text }]),
      onError: (message) => setError(message),
      onClose: () => setActive(false),
    });
    session.current = s;
    try {
      await s.start();
      setActive(true);
    } catch (err) {
      session.current = null;
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  if (!configured) return null;

  return (
    <div className="border-b border-[var(--border)] p-3" aria-label="Voice chat">
      <button
        type="button"
        onClick={() => (active ? stop() : start())}
        aria-pressed={active}
        className={`flex items-center gap-2 rounded-full border px-3 py-1 text-xs ${
          active
            ? "border-[var(--signal)] text-[var(--signal)]"
            : "border-[var(--border)] text-[var(--foreground)] hover:border-[var(--wire)] hover:text-[var(--wire)]"
        }`}
      >
        {active && <span aria-hidden="true" className="h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--signal)]" />}
        {active ? "stop" : "parla con l'agente"}
      </button>
      {error && (
        <p role="alert" className="mt-1 text-xs text-red-600">
          {error}
        </p>
      )}
      {lines.length > 0 && (
        <ul className="mt-2 space-y-1 text-xs">
          {lines.map((line) => (
            <li key={line.id}>
              <span className="text-[var(--muted)]">{line.speaker === "user" ? "tu" : "agente"}:</span>{" "}
              {line.text}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
