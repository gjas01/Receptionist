const recordBtn = document.getElementById("record-btn");
const stopBtn = document.getElementById("stop-btn");
const timerEl = document.getElementById("timer");
const statusEl = document.getElementById("status");
const previewEl = document.getElementById("preview-status");
const levelEl = document.getElementById("level");
const tabRecord = document.getElementById("tab-record");
const tabUpload = document.getElementById("tab-upload");
const panelRecord = document.getElementById("panel-record");
const panelUpload = document.getElementById("panel-upload");
const uploadInput = document.getElementById("upload-input");
const uploadAudio = document.getElementById("upload-preview");
const uploadInfo = document.getElementById("upload-info");
const uploadCloneBtn = document.getElementById("upload-clone-btn");
const TARGET_SECONDS = 30;
const MIN_SECONDS = 20;

let capture = null;
let startedAt = 0;
let tick = null;
let recording = false;
let uploadFile = null;

function formatTime(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  return `0:${String(s).padStart(2, "0")} / 0:30`;
}

function setStatus(text) {
  statusEl.textContent = text;
}

function uploadCloneLocked() {
  return uploadCloneBtn.dataset.locked === "1";
}

/* ---------- tabs ---------- */

function selectTab(which) {
  const recordActive = which === "record";
  tabRecord.setAttribute("aria-selected", String(recordActive));
  tabUpload.setAttribute("aria-selected", String(!recordActive));
  panelRecord.hidden = !recordActive;
  panelUpload.hidden = recordActive;
}

tabRecord.addEventListener("click", () => selectTab("record"));
tabUpload.addEventListener("click", () => selectTab("upload"));

/* ---------- microphone recording ---------- */

recordBtn.addEventListener("click", async () => {
  recordBtn.disabled = true;
  previewEl.textContent = "";
  try {
    capture = new MicCapture();
    capture.onLevel = (level) => {
      levelEl.style.width = `${Math.min(100, Math.round(level * 280))}%`;
    };
    await capture.start({ collect: true });
    recording = true;
    startedAt = Date.now();
    stopBtn.disabled = false;
    setStatus("Recording. Read the script in a natural voice.");
    tick = setInterval(() => {
      const elapsed = (Date.now() - startedAt) / 1000;
      timerEl.textContent = formatTime(Math.min(TARGET_SECONDS, elapsed));
      if (elapsed >= MIN_SECONDS) stopBtn.disabled = false;
      if (elapsed >= TARGET_SECONDS) stopRecording();
    }, 200);
  } catch (err) {
    recordBtn.disabled = false;
    setStatus(err.message || "Could not access the microphone.");
  }
});

stopBtn.addEventListener("click", () => stopRecording());

async function stopRecording() {
  if (!recording) return;
  recording = false;
  clearInterval(tick);
  stopBtn.disabled = true;
  const elapsed = (Date.now() - startedAt) / 1000;
  if (elapsed < MIN_SECONDS) {
    capture.stop();
    recordBtn.disabled = false;
    setStatus(`Please record at least ${MIN_SECONDS} seconds so the clone has enough speech.`);
    return;
  }
  const wav = capture.collectedWav(24000);
  capture.stop();
  await submitClone(wav, "clone.wav", "audio/wav");
}

/* ---------- file upload ---------- */

uploadInput.addEventListener("change", () => {
  const file = uploadInput.files && uploadInput.files[0];
  uploadFile = file || null;
  if (!file) {
    uploadAudio.hidden = true;
    uploadInfo.textContent = "";
    uploadCloneBtn.disabled = true;
    return;
  }
  uploadAudio.src = URL.createObjectURL(file);
  uploadAudio.hidden = false;
  uploadCloneBtn.disabled = uploadCloneLocked();
  uploadInfo.textContent = "Checking length…";
  uploadAudio.onloadedmetadata = () => {
    const seconds = Number.isFinite(uploadAudio.duration) ? uploadAudio.duration : 0;
    if (seconds && seconds < MIN_SECONDS) {
      uploadInfo.textContent = `Recording is ${seconds.toFixed(1)}s. Please use at least ${MIN_SECONDS} seconds of clear speech.`;
      uploadCloneBtn.disabled = true;
    } else {
      uploadInfo.textContent = seconds
        ? `Ready: ${seconds.toFixed(1)}s of audio. Preview it below, then clone.`
        : "Ready. Preview the recording, then clone.";
      uploadCloneBtn.disabled = uploadCloneLocked();
    }
  };
  uploadAudio.onerror = () => {
    uploadInfo.textContent = "This browser could not play that file. WAV, MP3 or M4A work best.";
    uploadCloneBtn.disabled = true;
  };
});

uploadCloneBtn.addEventListener("click", async () => {
  if (!uploadFile) return;
  uploadCloneBtn.disabled = true;
  recordBtn.disabled = true;
  const ext = (uploadFile.name.split(".").pop() || "wav").toLowerCase();
  const mime =
    ext === "mp3" ? "audio/mpeg" : ext === "m4a" ? "audio/mp4" : uploadFile.type || "audio/wav";
  await submitClone(uploadFile, uploadFile.name || `upload.${ext}`, mime, () => {
    uploadCloneBtn.disabled = false;
    recordBtn.disabled = false;
  });
});

/* ---------- shared clone + preview flow ---------- */

async function submitClone(blob, filename, mime, onFail) {
  const restore = () => {
    if (onFail) onFail();
    else recordBtn.disabled = false;
  };
  setStatus("Cloning voice with Qwen…");
  const name = document.getElementById("full_name").value.trim();
  const label = (document.getElementById("clone-name") || {}).value || "";
  const body = new FormData();
  body.append("audio", blob, filename);
  body.append("full_name", name);
  body.append("name", label.trim());
  try {
    const response = await fetch("/api/voice/clone", { method: "POST", body });
    const data = await parseJson(response);
    if (!response.ok) throw new Error(data.error || "Cloning failed.");
    const named = data.voice && data.voice.name ? ` as “${data.voice.name}”` : "";
    setStatus(`Voice cloned${named}.`);
    if (window.refreshVoices) window.refreshVoices();
    // Audition the voice just created, not whoever the schedule would pick.
    await playPreview(data.preview_phrase, data.voice_id, data.voice && data.voice.delivery);
  } catch (err) {
    setStatus(err.message);
    restore();
  }
}

async function playPreview(phrase, voice, delivery) {
  previewEl.textContent = `Playing: “${phrase}”`;
  const player = new PcmPlayer(24000);
  await player.ensure();
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const params = new URLSearchParams();
  if (voice) params.set("voice", voice);
  if (delivery) params.set("delivery", delivery);
  const query = params.toString();
  const socket = new WebSocket(
    `${protocol}://${location.host}/ws/voice-preview${query ? `?${query}` : ""}`
  );
  socket.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.type === "audio") {
      player.enqueue(message.data, message.sample_rate || 24000);
    } else if (message.type === "transcript" && message.text) {
      previewEl.textContent = message.text;
    } else if (message.type === "error") {
      previewEl.textContent = message.message;
      recordBtn.disabled = false;
    } else if (message.type === "done") {
      previewEl.textContent = "Playback finished — that is the voice you just cloned.";
      recordBtn.disabled = false;
    }
  };
  socket.onerror = () => {
    previewEl.textContent = "Could not start cloned-voice playback.";
    recordBtn.disabled = false;
  };
}
