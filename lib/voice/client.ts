"use client";

import { runtimeConfig } from "@/lib/runtime-config";
import { float32ToPCM16, pcm16BytesToFloat32 } from "./audio";
import { parseVoiceServerMessage } from "./protocol";
import { PlaybackScheduler, type PlaybackContext } from "./scheduler";

/** Where the voice service answers, read at runtime like everything else. */
export function voiceUrl(): string {
  return runtimeConfig().voiceUrl;
}

/** Empty means "this deployment has no voice service", not "it is broken". */
export function voiceConfigured(): boolean {
  return voiceUrl() !== "";
}

export interface VoiceSessionHandlers {
  onUserTranscript?(text: string): void;
  onAssistantTextChunk?(text: string): void;
  onTurnCancelled?(): void;
  onError?(message: string): void;
  onClose?(): void;
}

/** Loaded separately (it runs on the audio thread): cannot live in the app bundle. */
const WORKLET_URL = "/pcm-worklet.js";
const WORKLET_NAME = "pcm-capture";

/**
 * One live voice session: microphone in (PCM16 @16kHz, binary frames) over
 * `/ws/voice`, transcript + speech back out. `deps.scheduler` exists so tests
 * can drive `handleSocketMessage` directly, without a real `AudioContext`.
 */
export class VoiceSession {
  private ws: WebSocket | null = null;
  private micContext: AudioContext | null = null;
  private playbackContext: AudioContext | null = null;
  private scheduler: PlaybackScheduler | null;
  private stream: MediaStream | null = null;
  private workletNode: AudioWorkletNode | null = null;
  private sourceNode: MediaStreamAudioSourceNode | null = null;
  private turn = 0;
  private pendingSampleRate = 24000;
  private stopped = false;

  constructor(
    private readonly handlers: VoiceSessionHandlers = {},
    deps: { scheduler?: PlaybackScheduler } = {},
  ) {
    this.scheduler = deps.scheduler ?? null;
  }

  async start(): Promise<void> {
    const url = voiceUrl();
    if (!url) throw new Error("nessun servizio voce configurato");

    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1 },
    });

    this.micContext = new AudioContext({ sampleRate: 16000 });
    await this.micContext.audioWorklet.addModule(WORKLET_URL);
    this.sourceNode = this.micContext.createMediaStreamSource(this.stream);
    this.workletNode = new AudioWorkletNode(this.micContext, WORKLET_NAME);
    this.workletNode.port.onmessage = (event) =>
      this.handleMicSamples(event.data as Float32Array);
    this.sourceNode.connect(this.workletNode);

    this.playbackContext = new AudioContext({ sampleRate: 24000 });
    this.scheduler ??= new PlaybackScheduler(this.playbackContext as unknown as PlaybackContext);

    const socket = new WebSocket(url);
    socket.binaryType = "arraybuffer";
    socket.onmessage = (event) => this.handleSocketMessage(event.data as string | ArrayBuffer);
    socket.onerror = () => this.handlers.onError?.("connessione al servizio voce persa");
    socket.onclose = () => this.handlers.onClose?.();
    this.ws = socket;
  }

  private handleMicSamples(samples: Float32Array): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    const pcm = float32ToPCM16(samples);
    this.ws.send(pcm.buffer);
  }

  /** Exposed (not private) so tests can feed it messages without a real socket. */
  handleSocketMessage(data: string | ArrayBuffer): void {
    if (typeof data === "string") {
      const message = parseVoiceServerMessage(data);
      if (!message) return;
      switch (message.type) {
        case "user_transcript":
          this.turn += 1;
          this.handlers.onUserTranscript?.(message.text);
          break;
        case "assistant_text_chunk":
          this.handlers.onAssistantTextChunk?.(message.text);
          break;
        case "assistant_audio_chunk":
          this.pendingSampleRate = message.sample_rate;
          break;
        case "assistant_turn_cancelled":
          this.scheduler?.cancelTurn(String(this.turn));
          this.handlers.onTurnCancelled?.();
          break;
      }
      return;
    }

    const samples = pcm16BytesToFloat32(data);
    this.scheduler?.enqueue(String(this.turn), samples, this.pendingSampleRate);
  }

  stop(): void {
    if (this.stopped) return;
    this.stopped = true;
    this.ws?.close();
    this.workletNode?.disconnect();
    this.sourceNode?.disconnect();
    this.stream?.getTracks().forEach((track) => track.stop());
    void this.micContext?.close().catch(() => {});
    void this.playbackContext?.close().catch(() => {});
  }
}
