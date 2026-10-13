class PerformanceRecorder extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Float32Array(4096);
    this.offset = 0;
    this.startAt = Infinity;
    this.endAt = Infinity;
    this.done = false;
    this.port.onmessage = event => {
      if (event.data === "flush") this.finish();
      else { this.startAt = event.data.startAt; this.endAt = event.data.endAt; }
    };
  }
  flush() {
    if (this.offset) this.port.postMessage({ samples: this.buffer.slice(0, this.offset) });
    this.offset = 0;
  }
  finish() {
    if (this.done) return;
    this.done = true;
    this.flush();
    this.port.postMessage({ done: true });
  }
  process(inputs) {
    if (this.done) return true;
    const channel = inputs[0]?.[0];
    if (!channel) return true;
    for (let index = 0; index < channel.length; index++) {
      const time = (currentFrame + index) / sampleRate;
      if (time >= this.endAt) { this.finish(); break; }
      if (time < this.startAt) continue;
      this.buffer[this.offset++] = channel[index];
      if (this.offset === this.buffer.length) this.flush();
    }
    return true;
  }
}
registerProcessor("performance-recorder", PerformanceRecorder);
