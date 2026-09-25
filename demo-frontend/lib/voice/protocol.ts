/**
 * The messages `demo-voice-service` sends back over `/ws/voice`, already
 * implemented and tested server-side (see the contract): a discriminated
 * union by `type`, plus a raw binary frame (the PCM16 audio) that follows an
 * `assistant_audio_chunk` message and is not JSON at all.
 */
export type VoiceServerMessage =
  | { type: "user_transcript"; text: string }
  | { type: "assistant_text_chunk"; text: string }
  | { type: "assistant_audio_chunk"; encoding: string; sample_rate: number }
  | { type: "assistant_turn_cancelled" };

/** Parses one text frame; a malformed or unrecognised payload is `null`, not a throw. */
export function parseVoiceServerMessage(raw: string): VoiceServerMessage | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object" || typeof (parsed as { type?: unknown }).type !== "string") {
    return null;
  }
  return parsed as VoiceServerMessage;
}
