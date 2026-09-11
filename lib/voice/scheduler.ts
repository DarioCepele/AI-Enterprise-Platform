/**
 * Queues decoded assistant audio so it plays back to back, with no gaps and
 * no overlap, and can drop what belongs to a turn the server just cancelled
 * (barge-in). Kept independent of a real `AudioContext` — it only needs the
 * handful of members below — so the queueing/cancellation logic can be
 * exercised with a fake in tests, without jsdom having to understand audio.
 */

export interface PlaybackAudioBuffer {
  duration: number;
  copyToChannel?(source: Float32Array, channel: number): void;
  getChannelData?(channel: number): Float32Array;
}

export interface PlaybackBufferSource {
  buffer: PlaybackAudioBuffer | null;
  onended: (() => void) | null;
  connect(destination: unknown): void;
  start(when?: number): void;
  stop(when?: number): void;
}

export interface PlaybackContext {
  readonly currentTime: number;
  readonly destination: unknown;
  createBuffer(channels: number, length: number, sampleRate: number): PlaybackAudioBuffer;
  createBufferSource(): PlaybackBufferSource;
}

interface Queued {
  turnId: string;
  source: PlaybackBufferSource;
}

export class PlaybackScheduler {
  private nextStart = 0;
  private queued: Queued[] = [];

  constructor(private readonly context: PlaybackContext) {}

  /** Schedules `samples` right after whatever is already queued for `turnId`'s turn. */
  enqueue(turnId: string, samples: Float32Array, sampleRate: number): PlaybackBufferSource {
    const buffer = this.context.createBuffer(1, samples.length, sampleRate);
    if (buffer.copyToChannel) {
      buffer.copyToChannel(samples, 0);
    } else if (buffer.getChannelData) {
      buffer.getChannelData(0).set(samples);
    }

    const source = this.context.createBufferSource();
    source.buffer = buffer;
    source.connect(this.context.destination);

    const startAt = Math.max(this.context.currentTime, this.nextStart);
    source.onended = () => {
      this.queued = this.queued.filter((entry) => entry.source !== source);
    };
    source.start(startAt);
    this.nextStart = startAt + buffer.duration;
    this.queued.push({ turnId, source });
    return source;
  }

  /** Stops and drops whatever of `turnId` has not finished playing yet. */
  cancelTurn(turnId: string): void {
    const remaining: Queued[] = [];
    for (const entry of this.queued) {
      if (entry.turnId === turnId) {
        entry.source.stop(0);
      } else {
        remaining.push(entry);
      }
    }
    this.queued = remaining;
    // The turn that follows should not wait for a slot that was reserved for
    // audio that will now never play.
    this.nextStart = this.context.currentTime;
  }

  /** How many buffers are still queued (for a turn, or in total) — mostly for tests. */
  pending(turnId?: string): number {
    return turnId ? this.queued.filter((entry) => entry.turnId === turnId).length : this.queued.length;
  }
}
