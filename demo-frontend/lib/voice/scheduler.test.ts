import { describe, expect, it, vi } from "vitest";
import { PlaybackScheduler, type PlaybackContext } from "./scheduler";

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
