"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { runAgent } from "@/lib/agui/client";
import type { MessagePart } from "@/lib/agui/types";
import { initialState, reduce, withUserMessage, withAssistantText, type LabState } from "@/lib/agui/reducer";
import { VoiceSession, voiceConfigured } from "@/lib/voice/client";
import { Chat } from "./Chat";
import { Inspector } from "./Inspector";
import { PlanPanel } from "./PlanPanel";
import { LabHeader } from "./LabHeader";
import { product } from "@/lib/config";

export function Lab() {
  const [state, setState] = useState<LabState>(initialState);
  const [threadId] = useState(() => crypto.randomUUID());
  const inFlight = useRef(false);
  const abort = useRef<AbortController | null>(null);

  const stop = useCallback(() => abort.current?.abort(), []);

  const [voiceActive, setVoiceActive] = useState(false);
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const voiceSession = useRef<VoiceSession | null>(null);
  const voiceTurnAssistantId = useRef<string | null>(null);

  useEffect(() => () => voiceSession.current?.stop(), []);

  const stopVoice = useCallback(() => {
    voiceSession.current?.stop();
    voiceSession.current = null;
    setVoiceActive(false);
  }, []);

  const toggleVoice = useCallback(async () => {
    if (voiceActive) {
      stopVoice();
      return;
    }
    setVoiceError(null);
    const session = new VoiceSession({
      onUserTranscript: (text) => {
        voiceTurnAssistantId.current = null;
        setState((s) => withUserMessage(s, crypto.randomUUID(), text));
      },
      onAssistantTextChunk: (text) => {
        voiceTurnAssistantId.current ??= crypto.randomUUID();
        setState((s) => withAssistantText(s, voiceTurnAssistantId.current!, text));
      },
      onTurnCancelled: () => {
        voiceTurnAssistantId.current = null;
      },
      onError: setVoiceError,
      onClose: () => setVoiceActive(false),
    });
    voiceSession.current = session;
    try {
      await session.start();
      setVoiceActive(true);
    } catch (err) {
      voiceSession.current = null;
      setVoiceError(err instanceof Error ? err.message : String(err));
    }
  }, [voiceActive, stopVoice]);

  const send = useCallback(
    async (content: string | MessagePart[], displayText?: string) => {
      if (inFlight.current) return;
      inFlight.current = true;
      const controller = new AbortController();
      abort.current = controller;
      const text = displayText ?? (typeof content === "string" ? content : "");
      const userMessage = { id: crypto.randomUUID(), role: "user", content };
      setState((s) => ({ ...withUserMessage(s, userMessage.id, text), running: true }));

      try {
        await runAgent(
          {
            threadId,
            runId: crypto.randomUUID(),
            messages: [userMessage],
            state: {},
            tools: [],
            context: [],
            forwardedProps: {},
          },
          (event) => setState((s) => ({ ...reduce(s, event), running: true })),
          controller.signal,
        );
      } catch (err) {
        if (controller.signal.aborted) {
          setState((s) => ({ ...s, running: false }));
        } else {
          setState((s) => ({ ...s, running: false, error: String(err) }));
        }
      } finally {
        inFlight.current = false;
        abort.current = null;
        setState((s) => ({ ...s, running: false }));
      }
    },
    [threadId],
  );

  return (
    <div className="lab-shell flex flex-col">
      <LabHeader />
      <div className="lab-grid min-h-0 flex-1">
      <main className="flex min-h-0 min-w-0 flex-col border-r border-[var(--border)]" aria-label="Conversation">
        <Chat
          entries={state.entries}
          running={state.running}
          error={state.error}
          onSend={send}
          onStop={stop}
          voiceAvailable={voiceConfigured()}
          voiceActive={voiceActive}
          voiceError={voiceError}
          onToggleVoice={toggleVoice}
        />
      </main>
      <aside className="lab-aside flex min-h-0 min-w-0 flex-col" aria-label="Plan and agent activity">
        <PlanPanel shared={state.shared} />
        <Inspector events={state.events} running={state.running} />
      </aside>
      </div>
      <footer className="border-t border-[var(--border)] px-6 py-2 text-xs text-[var(--muted)]">
        {product.disclaimer}
      </footer>
    </div>
  );
}
