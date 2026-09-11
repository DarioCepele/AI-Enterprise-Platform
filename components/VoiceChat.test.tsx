import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { float32ToPCM16, pcm16BytesToFloat32 } from "@/lib/voice/audio";
import { parseVoiceServerMessage } from "@/lib/voice/protocol";
import { PlaybackScheduler, type PlaybackContext } from "@/lib/voice/scheduler";
import { VoiceSession } from "@/lib/voice/client";
import { forgetRuntimeConfig } from "@/lib/runtime-config";
import { VoiceChat } from "./VoiceChat";

function withVoiceUrl(url: string): void {
  document.body.innerHTML = `<script id="lab-runtime-config" type="application/json">${JSON.stringify(
    { voiceUrl: url },
  )}</script>`;
  forgetRuntimeConfig();
}

afterEach(() => {
  vi.restoreAllMocks();
  document.body.innerHTML = "";
  forgetRuntimeConfig();
});

// (a) mic capture: Float32 <-> PCM16 conversion, tested as plain functions.
describe("float32ToPCM16", () => {
  it("scales [-1, 1] samples to signed 16-bit, clamped", () => {
    const out = float32ToPCM16(new Float32Array([0, 1, -1, 2, -2]));
    expect(Array.from(out)).toEqual([0, 32767, -32767, 32767, -32767]);
  });
});

describe("pcm16BytesToFloat32", () => {
  it("reads little-endian PCM16 bytes back to [-1, 1] floats", () => {
    const samples = new Int16Array([0, 32767, -32768]);
    const floats = pcm16BytesToFloat32(samples.buffer);
    expect(floats[0]).toBeCloseTo(0);
    expect(floats[1]).toBeCloseTo(1);
    expect(floats[2]).toBeCloseTo(-1);
  });
});

// (b) server messages interpreted by type.
describe("parseVoiceServerMessage", () => {
  it("parses each message type the server sends", () => {
    expect(parseVoiceServerMessage(JSON.stringify({ type: "user_transcript", text: "ciao" }))).toEqual({
      type: "user_transcript",
      text: "ciao",
    });
    expect(
      parseVoiceServerMessage(JSON.stringify({ type: "assistant_text_chunk", text: "risposta" })),
    ).toEqual({ type: "assistant_text_chunk", text: "risposta" });
    expect(
      parseVoiceServerMessage(
        JSON.stringify({ type: "assistant_audio_chunk", encoding: "pcm16", sample_rate: 24000 }),
      ),
    ).toEqual({ type: "assistant_audio_chunk", encoding: "pcm16", sample_rate: 24000 });
    expect(parseVoiceServerMessage(JSON.stringify({ type: "assistant_turn_cancelled" }))).toEqual({
      type: "assistant_turn_cancelled",
    });
  });

  it("degrades to null instead of throwing on garbage", () => {
    expect(parseVoiceServerMessage("{ not json")).toBeNull();
    expect(parseVoiceServerMessage(JSON.stringify({ no: "type" }))).toBeNull();
  });
});

function fakeContext() {
  let currentTime = 0;
  const sources: { buffer: unknown; connect: ReturnType<typeof vi.fn>; start: ReturnType<typeof vi.fn>; stop: ReturnType<typeof vi.fn> }[] = [];
  const context: PlaybackContext = {
    get currentTime() {
      return currentTime;
    },
    destination: {},
    createBuffer: vi.fn((_channels: number, length: number, sampleRate: number) => ({
      duration: length / sampleRate,
    })),
    createBufferSource: vi.fn(() => {
      const source = { buffer: null, onended: null, connect: vi.fn(), start: vi.fn(), stop: vi.fn() };
      sources.push(source);
      return source;
    }),
  };
  return { context, sources, advance: (seconds: number) => (currentTime += seconds) };
}

// (playback scheduling/cancellation, isolated from any real AudioContext)
describe("PlaybackScheduler", () => {
  it("queues buffers back to back using each buffer's own duration", () => {
    const { context, sources } = fakeContext();
    const scheduler = new PlaybackScheduler(context);

    scheduler.enqueue("1", new Float32Array(16000), 16000); // 1s
    scheduler.enqueue("1", new Float32Array(8000), 16000); // 0.5s

    expect(sources[0].start).toHaveBeenCalledWith(0);
    expect(sources[1].start).toHaveBeenCalledWith(1);
  });

  it("drops only the buffers that belonged to the cancelled turn", () => {
    const { context, sources } = fakeContext();
    const scheduler = new PlaybackScheduler(context);

    scheduler.enqueue("1", new Float32Array(16000), 16000);
    scheduler.enqueue("2", new Float32Array(16000), 16000);
    scheduler.cancelTurn("1");

    expect(sources[0].stop).toHaveBeenCalledWith(0);
    expect(sources[1].stop).not.toHaveBeenCalled();
    expect(scheduler.pending("1")).toBe(0);
    expect(scheduler.pending("2")).toBe(1);
  });
});

// (b continued) the session dispatches each incoming message to the right
// handler and, for binary frames, to the scheduler under the current turn.
describe("VoiceSession.handleSocketMessage", () => {
  it("routes transcripts, text chunks, audio bytes and cancellation", () => {
    const { context } = fakeContext();
    const scheduler = new PlaybackScheduler(context);
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

// (c) the microphone button only exists when a voice service is configured.
describe("VoiceChat", () => {
  beforeEach(() => {
    withVoiceUrl("");
  });

  it("renders nothing when voiceUrl is empty", () => {
    render(<VoiceChat />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("shows the microphone button when a voice service is configured", () => {
    withVoiceUrl("wss://voice.test/ws/voice");
    render(<VoiceChat />);
    expect(screen.getByRole("button", { name: /parla/ })).toBeInTheDocument();
  });

  it("starts a session, shows the transcript, and stops on click", async () => {
    withVoiceUrl("wss://voice.test/ws/voice");

    class FakeWebSocket {
      static OPEN = 1;
      static instances: FakeWebSocket[] = [];
      readyState = FakeWebSocket.OPEN;
      binaryType = "";
      onmessage: ((event: { data: string | ArrayBuffer }) => void) | null = null;
      onerror: (() => void) | null = null;
      onclose: (() => void) | null = null;
      close = vi.fn(() => this.onclose?.());
      send = vi.fn();
      constructor(public url: string) {
        FakeWebSocket.instances.push(this);
      }
    }
    class FakeAudioContext {
      currentTime = 0;
      audioWorklet = { addModule: vi.fn(async () => {}) };
      createMediaStreamSource = vi.fn(() => ({ connect: vi.fn(), disconnect: vi.fn() }));
      createBuffer = vi.fn((_c: number, length: number, sampleRate: number) => ({
        duration: length / sampleRate,
        copyToChannel: vi.fn(),
      }));
      createBufferSource = vi.fn(() => ({
        buffer: null,
        onended: null,
        connect: vi.fn(),
        start: vi.fn(),
        stop: vi.fn(),
      }));
      close = vi.fn(async () => {});
    }
    class FakeAudioWorkletNode {
      port = { onmessage: null, postMessage: vi.fn() };
      connect = vi.fn();
      disconnect = vi.fn();
    }
    const stream = { getTracks: () => [{ stop: vi.fn() }] };
    vi.stubGlobal("WebSocket", FakeWebSocket);
    vi.stubGlobal("AudioContext", FakeAudioContext);
    vi.stubGlobal("AudioWorkletNode", FakeAudioWorkletNode);
    Object.defineProperty(globalThis.navigator, "mediaDevices", {
      value: { getUserMedia: vi.fn(async () => stream) },
      configurable: true,
    });

    render(<VoiceChat />);
    const button = screen.getByRole("button", { name: /parla/ });
    await act(async () => {
      fireEvent.click(button);
    });

    expect(screen.getByRole("button", { name: /stop/ })).toBeInTheDocument();
    expect(FakeWebSocket.instances).toHaveLength(1);

    await act(async () => {
      FakeWebSocket.instances[0].onmessage?.({
        data: JSON.stringify({ type: "user_transcript", text: "Ciao agente" }),
      });
    });
    expect(await screen.findByText(/Ciao agente/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /stop/ }));
    expect(FakeWebSocket.instances[0].close).toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /parla/ })).toBeInTheDocument();
  });
});
