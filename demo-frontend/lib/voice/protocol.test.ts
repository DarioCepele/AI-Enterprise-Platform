import { describe, expect, it } from "vitest";
import { parseVoiceServerMessage } from "./protocol";

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
