class PcmRecorder extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Float32Array(4096);
    this.offset = 0;
    this.port.onmessage = event => {
      if (event.data === "flush") {
        if (this.offset) this.port.postMessage({ samples: this.buffer.slice(0, this.offset) });
        this.port.postMessage({ flushed: true });
        this.offset = 0;
      }
    };
  }
  process(inputs) {
    const channel = inputs[0]?.[0];
    if (channel) {
      for (const value of channel) {
        this.buffer[this.offset++] = value;
        if (this.offset === this.buffer.length) {
          this.port.postMessage({ samples: this.buffer.slice() });
          this.offset = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor("pcm-recorder", PcmRecorder);
