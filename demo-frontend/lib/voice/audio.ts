/**
 * PCM16 <-> Float32 conversion, isolated so it is testable without an
 * AudioContext: the microphone worklet hands over Float32 samples, the wire
 * wants raw signed 16-bit little-endian, and the speaker side is the mirror
 * of that.
 */

/** Float32 [-1, 1] samples -> signed 16-bit PCM, clamped like any DAC would. */
export function float32ToPCM16(input: Float32Array): Int16Array {
  const out = new Int16Array(input.length);
  for (let i = 0; i < input.length; i++) {
    const clamped = Math.max(-1, Math.min(1, input[i]));
    out[i] = Math.round(clamped * 32767);
  }
  return out;
}

/** Raw PCM16 little-endian bytes (as they arrive over the socket) -> Float32 [-1, 1]. */
export function pcm16BytesToFloat32(buffer: ArrayBuffer): Float32Array {
  const view = new DataView(buffer);
  const length = Math.floor(buffer.byteLength / 2);
  const out = new Float32Array(length);
  for (let i = 0; i < length; i++) {
    const sample = view.getInt16(i * 2, true);
    out[i] = sample / (sample < 0 ? 32768 : 32767);
  }
  return out;
}
