import { afterEach, describe, expect, it, vi } from "vitest";
import { PlaybackScheduler, type PlaybackContext } from "./scheduler";
import { VoiceSession } from "./client";
import { forgetRuntimeConfig } from "@/lib/runtime-config";

function fakeContext() {
  const currentTime = 0;
  const context: PlaybackContext = {
    get currentTime() {
      return currentTime;
    },
    destination: {},
    createBuffer: vi.fn((_channels: number, length: number, sampleRate: number) => ({
      duration: length / sampleRate,
    })),
    createBufferSource: vi.fn(() => ({ buffer: null, onended: null, connect: vi.fn(), start: vi.fn(), stop: vi.fn() })),
  };
  return context;
}

// The session dispatches each incoming message to the right handler and, for
// binary frames, to the scheduler under the current turn.
describe("VoiceSession.handleSocketMessage", () => {
  it("routes transcripts, text chunks, audio bytes and cancellation", () => {
    const scheduler = new PlaybackScheduler(fakeContext());
    const enqueueSpy = vi.spyOn(scheduler, "enqueue");
    const cancelSpy = vi.spyOn(scheduler, "cancelTurn");
    const onUserTranscript = vi.fn();
    const onAssistantTextChunk = vi.fn();
    const onTurnCancelled = vi.fn();

    const session = new VoiceSession(
      { onUserTranscript, onAssistantTextChunk, onTurnCancelled },
      { scheduler },
    );

    session.handleSocketMessage(JSON.stringify({ type: "user_transcript", text: "ciao" }));
    expect(onUserTranscript).toHaveBeenCalledWith("ciao");

    session.handleSocketMessage(JSON.stringify({ type: "assistant_text_chunk", text: "risposta" }));
    expect(onAssistantTextChunk).toHaveBeenCalledWith("risposta");

    session.handleSocketMessage(
      JSON.stringify({ type: "assistant_audio_chunk", encoding: "pcm16", sample_rate: 24000 }),
    );
    const pcm = new Int16Array([0, 16000, -16000]);
    session.handleSocketMessage(pcm.buffer);

    expect(enqueueSpy).toHaveBeenCalledTimes(1);
    const [turnId, samples, sampleRate] = enqueueSpy.mock.calls[0];
    expect(turnId).toBe("1");
    expect(samples).toBeInstanceOf(Float32Array);
    expect(samples).toHaveLength(3);
    expect(sampleRate).toBe(24000);

    session.handleSocketMessage(JSON.stringify({ type: "assistant_turn_cancelled" }));
    expect(cancelSpy).toHaveBeenCalledWith("1");
    expect(onTurnCancelled).toHaveBeenCalled();
  });
});

// Regression test for a real bug: an AudioWorkletNode with no path onward to
// `destination` is never pulled by Chrome's audio graph, so `process()` is
// never called and no audio ever leaves the browser -- confirmed live (mic
// permission granted, WebSocket open, zero bytes sent). The fix routes the
// worklet through a silent gain node instead of leaving it dangling.
describe("VoiceSession.start", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
    forgetRuntimeConfig();
  });

  it("connects the mic worklet all the way to destination, not left dangling", async () => {
    document.body.innerHTML = `<script id="lab-runtime-config" type="application/json">${JSON.stringify(
      { voiceUrl: "wss://voice.test/ws/voice" },
    )}</script>`;
    forgetRuntimeConfig();

    const edges: string[] = [];
    class FakeNode {
      constructor(public label: string) {}
      connect(target: FakeNode) {
        edges.push(`${this.label}->${target.label}`);
      }
      disconnect() {}
    }
    class FakeGainNode extends FakeNode {
      gain = { value: 1 };
      constructor() {
        super("gain");
      }
    }
    class FakeWorkletNode extends FakeNode {
      port: { onmessage: unknown } = { onmessage: null };
      constructor() {
        super("worklet");
      }
    }
    class FakeAudioContext {
      destination = new FakeNode("destination");
      audioWorklet = { addModule: vi.fn(async () => {}) };
      createMediaStreamSource = vi.fn(() => new FakeNode("source"));
      createGain = vi.fn(() => new FakeGainNode());
      close = vi.fn(async () => {});
    }
    class FakeWebSocket {
      binaryType = "";
      onmessage: unknown;
      onerror: unknown;
      onclose: unknown;
      close() {}
    }
    vi.stubGlobal("AudioContext", FakeAudioContext);
    vi.stubGlobal("AudioWorkletNode", FakeWorkletNode);
    vi.stubGlobal("WebSocket", FakeWebSocket);
    Object.defineProperty(globalThis.navigator, "mediaDevices", {
      value: { getUserMedia: vi.fn(async () => ({ getTracks: () => [] })) },
      configurable: true,
    });

    const session = new VoiceSession();
    await session.start();

    // The worklet must reach `destination` through *some* path -- direct or
    // (as chosen here, to stay silent) via a zero-gain node -- or Chrome
    // never calls its `process()` at all.
    expect(edges).toContain("source->worklet");
    expect(edges).toContain("worklet->gain");
    expect(edges).toContain("gain->destination");
  });
});
