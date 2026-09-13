import { describe, expect, it, vi } from "vitest";
import { PlaybackScheduler, type PlaybackContext } from "./scheduler";
import { VoiceSession } from "./client";

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
