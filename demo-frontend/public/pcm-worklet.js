// Runs on the audio rendering thread, loaded via
// `audioContext.audioWorklet.addModule("/pcm-worklet.js")`. It cannot live in
// the app bundle: the worklet global scope has no access to it, and a
// separately-loaded module is exactly what `addModule` expects.
//
// Hands the main thread raw Float32 samples for each 128-sample render
// quantum; the PCM16 conversion happens there (see lib/voice/audio.ts) so it
// stays testable outside the audio thread.
class PCMCaptureProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0]?.[0];
    if (channel) {
      // The underlying buffer is reused by the audio thread on the next
      // quantum, so what crosses to the main thread must be a copy.
      this.port.postMessage(channel.slice());
    }
    return true;
  }
}

registerProcessor("pcm-capture", PCMCaptureProcessor);
