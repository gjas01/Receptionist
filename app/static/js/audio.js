/* Robust JSON parsing: API errors can arrive as plain text (e.g. a 500),
   which must not surface as a raw "is not valid JSON" SyntaxError. */
async function parseJson(response) {
  const text = await response.text();
  try {
    return JSON.parse(text);
  } catch (e) {
    if (!response.ok) {
      const first = (text || "").trim().split("\n")[0];
      throw new Error(`Server error ${response.status}: ${first || "unexpected response"}`);
    }
    throw new Error("Unexpected response from the server.");
  }
}

function resampleFloat32(input, fromRate, toRate) {
  if (fromRate === toRate) return input;
  const ratio = fromRate / toRate;
  const length = Math.round(input.length / ratio);
  const output = new Float32Array(length);
  for (let i = 0; i < length; i++) {
    const pos = i * ratio;
    const idx = Math.floor(pos);
    const frac = pos - idx;
    const a = input[idx] || 0;
    const b = input[Math.min(idx + 1, input.length - 1)] || 0;
    output[i] = a + (b - a) * frac;
  }
  return output;
}

function floatTo16BitPCM(float32) {
  const buffer = new ArrayBuffer(float32.length * 2);
  const view = new DataView(buffer);
  for (let i = 0; i < float32.length; i++) {
    const s = Math.max(-1, Math.min(1, float32[i]));
    view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buffer;
}

function encodeWav(float32, sampleRate) {
  const pcm = floatTo16BitPCM(float32);
  const header = new ArrayBuffer(44);
  const view = new DataView(header);
  const writeString = (offset, str) => {
    for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
  };
  writeString(0, "RIFF");
  view.setUint32(4, 36 + pcm.byteLength, true);
  writeString(8, "WAVE");
  writeString(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(36, "data");
  view.setUint32(40, pcm.byteLength, true);
  return new Blob([header, pcm], { type: "audio/wav" });
}

function int16ToBase64(int16) {
  const bytes = new Uint8Array(int16.buffer, int16.byteOffset, int16.byteLength);
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

function base64ToInt16(b64) {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new Int16Array(bytes.buffer);
}

class PcmPlayer {
  constructor(inputRate = 24000) {
    this.inputRate = inputRate;
    this.ctx = null;
    this.nextTime = 0;
    this.lookahead = 0.1;
    this.primed = false;
  }

  async ensure() {
    if (!this.ctx) {
      try {
        this.ctx = new AudioContext({ sampleRate: this.inputRate });
      } catch (err) {
        this.ctx = new AudioContext();
      }
    }
    if (this.ctx.state === "suspended") await this.ctx.resume();
    if (!this.primed) {
      const frames = Math.max(1, Math.ceil(this.ctx.sampleRate * 0.08));
      const silent = this.ctx.createBuffer(1, frames, this.ctx.sampleRate);
      const src = this.ctx.createBufferSource();
      src.buffer = silent;
      src.connect(this.ctx.destination);
      src.start();
      this.nextTime = this.ctx.currentTime + silent.duration + this.lookahead;
      this.primed = true;
    }
  }

  enqueue(b64, sampleRate) {
    if (!this.ctx || !b64) return;
    const int16 = base64ToInt16(b64);
    if (!int16.length) return;
    const float32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / 32768;
    const sourceRate = sampleRate || this.inputRate;
    const resampled =
      sourceRate === this.ctx.sampleRate
        ? float32
        : resampleFloat32(float32, sourceRate, this.ctx.sampleRate);
    const buffer = this.ctx.createBuffer(1, Math.max(1, resampled.length), this.ctx.sampleRate);
    buffer.getChannelData(0).set(resampled);
    const src = this.ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(this.ctx.destination);
    const now = this.ctx.currentTime;
    if (this.nextTime < now + this.lookahead) {
      this.nextTime = now + this.lookahead;
    }
    src.start(this.nextTime);
    this.nextTime += buffer.duration;
  }

  playBase64(b64, sampleRate) {
    this.enqueue(b64, sampleRate);
  }

  stop() {
    if (this.ctx) {
      this.ctx.close();
      this.ctx = null;
      this.nextTime = 0;
      this.primed = false;
    }
  }
}

class MicCapture {
  constructor() {
    this.stream = null;
    this.ctx = null;
    this.node = null;
    this.onPcm = null;
    this.onLevel = null;
    this.chunks = [];
  }

  async start({ targetRate = 16000, collect = false } = {}) {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    this.ctx = new AudioContext();
    const source = this.ctx.createMediaStreamSource(this.stream);
    await this.ctx.audioWorklet.addModule("/static/js/pcm-worklet.js");
    this.node = new AudioWorkletNode(this.ctx, "capture-processor");
    this.chunks = [];
    this.nativeChunks = [];
    this.node.port.onmessage = (event) => {
      const int16 = new Int16Array(event.data);
      if (this.onLevel) {
        let sum = 0;
        for (let i = 0; i < int16.length; i++) sum += Math.abs(int16[i]);
        this.onLevel(int16.length ? sum / int16.length / 32768 : 0);
      }
      if (this.onPcm) this.onPcm(int16);
    };
    const silent = this.ctx.createGain();
    silent.gain.value = 0;
    source.connect(this.node);
    this.node.connect(silent);
    silent.connect(this.ctx.destination);
    if (collect) {
      const proc = this.ctx.createScriptProcessor(4096, 1, 1);
      proc.onaudioprocess = (event) => {
        this.nativeChunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
      };
      source.connect(proc);
      proc.connect(silent);
      this.collector = proc;
    }
    this.targetRate = targetRate;
    this.sourceRate = this.ctx.sampleRate;
    return this.ctx.sampleRate;
  }

  collectedWav(sampleRate = 24000) {
    let total = 0;
    for (const chunk of this.nativeChunks) total += chunk.length;
    const merged = new Float32Array(total);
    let offset = 0;
    for (const chunk of this.nativeChunks) {
      merged.set(chunk, offset);
      offset += chunk.length;
    }
    const resampled = resampleFloat32(merged, this.sourceRate || 48000, sampleRate);
    return encodeWav(resampled, sampleRate);
  }

  stop() {
    if (this.collector) this.collector.disconnect();
    if (this.node) this.node.disconnect();
    if (this.ctx) this.ctx.close();
    if (this.stream) this.stream.getTracks().forEach((track) => track.stop());
    this.collector = null;
    this.node = null;
    this.ctx = null;
    this.stream = null;
  }
}
