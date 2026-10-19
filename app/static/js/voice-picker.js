const auditionBtn = document.getElementById("audition-btn");
const saveBtn = document.getElementById("save-voice-btn");
const voiceSelect = document.getElementById("gemini-voice");
const shapingEl = document.getElementById("voice-shaping");
const previewEl = document.getElementById("preview-status");
const statusEl = document.getElementById("status");

function shapingText() {
  return shapingEl.value.trim();
}

let player = null;
let socket = null;

auditionBtn.addEventListener("click", () => {
  if (socket) return; // already auditioning
  const voice = voiceSelect.value;
  auditionBtn.disabled = true;
  previewEl.textContent = `Auditioning ${voice}…`;
  player = new PcmPlayer(24000);
  player.ensure().then(() => {
    const protocol = location.protocol === "https:" ? "wss" : "ws";
    const params = new URLSearchParams({ voice });
    const delivery = shapingText();
    if (delivery) params.set("delivery", delivery);
    socket = new WebSocket(`${protocol}://${location.host}/ws/voice-preview?${params.toString()}`);
    socket.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (message.type === "audio") {
        player.enqueue(message.data, message.sample_rate || 24000);
      } else if (message.type === "transcript" && message.text) {
        previewEl.textContent = message.text;
      } else if (message.type === "error") {
        previewEl.textContent = message.message;
        finish();
      } else if (message.type === "done") {
        previewEl.textContent = `That was ${voice}.`;
        finish();
      }
    };
    socket.onerror = () => {
      previewEl.textContent = "Could not start the audition.";
      finish();
    };
  });
});

function finish() {
  auditionBtn.disabled = false;
  if (socket) {
    try { socket.close(); } catch (e) { /* ignore */ }
    socket = null;
  }
}

voiceSelect.addEventListener("change", () => {
  previewEl.textContent = "";
  finish();
});

saveBtn.addEventListener("click", async () => {
  const voice = voiceSelect.value;
  saveBtn.disabled = true;
  statusEl.textContent = `Saving ${voice} as a named voice…`;
  try {
    const body = new FormData();
    body.append("voice_name", voice);
    const nameEl = document.getElementById("preset-name");
    body.append("name", nameEl ? nameEl.value.trim() : "");
    body.append("delivery", shapingText());
    const response = await fetch("/api/voice/preset", { method: "POST", body });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not save the voice.");
    statusEl.textContent = `“${data.voice.name}” is in your voices list. Add it to the schedule, or leave uncovered hours random.`;
    if (window.refreshVoices) window.refreshVoices();
  } catch (err) {
    statusEl.textContent = err.message;
  } finally {
    saveBtn.disabled = false;
  }
});
