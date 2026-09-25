import { describe, expect, it } from "vitest";
import { float32ToPCM16, pcm16BytesToFloat32 } from "./audio";

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
