class CaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.targetRate = 16000;
    this.ratio = sampleRate / this.targetRate;
    this.pos = 0;
    this.prev = 0;
  }

  process(inputs) {
    const input = inputs[0] && inputs[0][0];
    if (!input || input.length === 0) return true;

    const out = [];
    let pos = this.pos;
    const ratio = this.ratio;
    while (pos < input.length) {
      const i = Math.floor(pos);
      const frac = pos - i;
      const a = i < input.length ? input[i] : this.prev;
      const b = i + 1 < input.length ? input[i + 1] : a;
      out.push(a + (b - a) * frac);
      pos += ratio;
    }
    this.pos = pos - input.length;
    this.prev = input[input.length - 1];

    const int16 = new Int16Array(out.length);
    for (let i = 0; i < out.length; i++) {
      const s = Math.max(-1, Math.min(1, out[i]));
      int16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    this.port.postMessage(int16.buffer, [int16.buffer]);
    return true;
  }
}

registerProcessor("capture-processor", CaptureProcessor);
