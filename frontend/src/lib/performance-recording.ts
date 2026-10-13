import { wavBlob } from "./recording";

export type PerformanceCapture = { stop: () => void; cancel: () => void; finished: Promise<Blob> };

export async function startPerformanceCapture(options: {
  seconds: number; guideUrl?: string; guideStart: number; guideVolume: number;
  onLevel: (value: number) => void; onTime: (value: number) => void;
}): Promise<PerformanceCapture> {
  let stream: MediaStream | undefined;
  let context: AudioContext | undefined;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: {
      channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false,
    } });
    context = new AudioContext();
    await context.resume();
    await context.audioWorklet.addModule("/performance-recorder.js");
    const node = new AudioWorkletNode(context, "performance-recorder");
    let guide: AudioBufferSourceNode | undefined;
    if (options.guideUrl) {
      const response = await fetch(options.guideUrl);
      if (!response.ok) throw new Error("No se pudo cargar la musica de referencia.");
      const buffer = await context.decodeAudioData(await response.arrayBuffer());
      if (options.guideStart >= buffer.duration) throw new Error("El inicio esta fuera de la cancion.");
      guide = context.createBufferSource(); guide.buffer = buffer;
      const gain = context.createGain(); gain.gain.value = options.guideVolume;
      guide.connect(gain); gain.connect(context.destination);
    }
    const activeContext = context, activeStream = stream;
    const startAt = context.currentTime + 3;
    const parts: Float32Array[] = [];
    let ended = false;
    let resolve: (value: Blob) => void, reject: (reason: Error) => void;
    const finished = new Promise<Blob>((yes, no) => { resolve = yes; reject = no; });
    const timer = setInterval(() => options.onTime(activeContext.currentTime - startAt), 100);
    const timeout = setTimeout(() => node.port.postMessage("flush"), (options.seconds + 4) * 1000);
    const finish = (cancelled = false) => {
      if (ended) return;
      ended = true; clearInterval(timer); clearTimeout(timeout);
      guide?.stop(); node.disconnect(); activeStream.getTracks().forEach(track => track.stop());
      activeContext.close(); options.onLevel(0);
      if (cancelled) reject(new Error("Grabacion cancelada."));
      else resolve(wavBlob(parts, activeContext.sampleRate));
    };
    node.port.onmessage = event => {
      if (event.data.samples) {
        const data = event.data.samples as Float32Array;
        parts.push(data);
        options.onLevel(Math.min(1, Math.sqrt(data.reduce((sum, value) => sum + value * value, 0) / data.length) * 4));
      }
      if (event.data.done) finish();
    };
    context.createMediaStreamSource(stream).connect(node);
    // The microphone has its own silent output; backing audio never enters this node.
    node.connect(context.destination);
    node.port.postMessage({ startAt, endAt: startAt + options.seconds });
    guide?.start(startAt, options.guideStart, options.seconds);
    options.onTime(-3);
    return { stop: () => node.port.postMessage("flush"), cancel: () => finish(true), finished };
  } catch (error) {
    stream?.getTracks().forEach(track => track.stop()); await context?.close();
    throw error;
  }
}
