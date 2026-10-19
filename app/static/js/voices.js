/* Named voices list + answering schedule editor. Reads state from /api/voices. */

const voicesBody = document.getElementById("voices-body");
const voicesEmpty = document.getElementById("voices-empty");
const voicesStatus = document.getElementById("voices-status");
const slotsBody = document.getElementById("slots-body");
const slotsEmpty = document.getElementById("slots-empty");
const slotsStatus = document.getElementById("slots-status");
const slotDay = document.getElementById("slot-day");
const slotStart = document.getElementById("slot-start");
const slotEnd = document.getElementById("slot-end");
const slotVoice = document.getElementById("slot-voice");
const addSlotBtn = document.getElementById("add-slot-btn");
const saveSlotsBtn = document.getElementById("save-slots-btn");
const answeringEl = document.getElementById("answering-now");

const DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

let voices = [];
let slots = [];

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = String(text ?? "");
  return div.innerHTML;
}

function voiceLabel(v) {
  return v.name || `Unnamed (${v.voice_id})`;
}

function fmtDay(day) {
  return day === null || day === undefined ? "All days" : DAY_NAMES[day];
}

/* ---------- loading ---------- */

async function loadVoices() {
  const response = await fetch("/api/voices");
  const data = await parseJson(response);
  if (!response.ok) throw new Error(data.error || "Could not load voices.");
  voices = data.voices;
  slots = data.slots;
  renderVoices();
  renderSlots();
  renderVoiceOptions();
  if (answeringEl) {
    const active = voices.find((v) => v.id === data.active_voice_id);
    const label = active ? voiceLabel(active) : "default preset";
    answeringEl.innerHTML =
      `Answering now: <strong>${escapeHtml(label)}</strong> — ${escapeHtml(data.reason)}.` +
      (active && !active.for_active_model ? " (saved for another model)" : "");
  }
}

/* ---------- voices table ---------- */

function renderVoices() {
  voicesBody.innerHTML = "";
  voicesEmpty.hidden = voices.length > 0;
  voices.forEach((v) => {
    const tr = document.createElement("tr");
    const needsName = !(v.name || "").trim();
    tr.innerHTML = `
      <td>${needsName
        ? `<input class="name-input" value="" placeholder="Name this voice…" maxlength="80" data-id="${v.id}">`
        : `<span class="voice-name">${escapeHtml(v.name)}</span>`}</td>
      <td><code>${escapeHtml(v.voice_id)}</code>${v.for_active_model ? "" : ` <span class="pill warn">${escapeHtml(v.target_model)}</span>`}</td>
      <td>${escapeHtml(v.target_model)}</td>
      <td class="row-actions">
        ${needsName ? `<button type="button" class="btn btn-small btn-primary save-name" data-id="${v.id}">Save name</button>`
                    : `<button type="button" class="btn btn-small btn-ghost rename" data-id="${v.id}">Rename</button>`}
        <button type="button" class="btn btn-small btn-ghost preview-voice" data-id="${v.id}">Preview</button>
        <button type="button" class="btn btn-small btn-danger delete-voice" data-id="${v.id}">Delete</button>
      </td>`;
    voicesBody.appendChild(tr);
  });
}

voicesBody.addEventListener("click", async (event) => {
  const btn = event.target.closest("button");
  if (!btn) return;
  const id = Number(btn.dataset.id);
  try {
    if (btn.classList.contains("save-name")) {
      const input = voicesBody.querySelector(`input.name-input[data-id="${id}"]`);
      await renameVoice(id, input.value.trim());
    } else if (btn.classList.contains("rename")) {
      const voice = voices.find((v) => v.id === id);
      const name = window.prompt("New name for this voice:", voice.name || "");
      if (name && name.trim()) await renameVoice(id, name.trim());
    } else if (btn.classList.contains("delete-voice")) {
      const voice = voices.find((v) => v.id === id);
      if (!window.confirm(`Delete “${voiceLabel(voice)}”? Schedule slots using it become random.`)) return;
      const body = new FormData();
      const response = await fetch(`/api/voice/${id}/delete`, { method: "POST", body });
      const data = await parseJson(response);
      if (!response.ok) throw new Error(data.error || "Delete failed.");
      await loadVoices();
    } else if (btn.classList.contains("preview-voice")) {
      await auditionVoice(voices.find((v) => v.id === id));
    }
  } catch (err) {
    voicesStatus.textContent = err.message;
  }
});

async function renameVoice(id, name) {
  if (!name) throw new Error("Enter a name for the voice.");
  const body = new FormData();
  body.append("name", name);
  const response = await fetch(`/api/voice/${id}/update`, { method: "POST", body });
  const data = await parseJson(response);
  if (!response.ok) throw new Error(data.error || "Could not rename the voice.");
  voicesStatus.textContent = `Saved as “${data.voice.name}”.`;
  await loadVoices();
}

function auditionVoice(voice) {
  if (!voice || window.__auditioning) return;
  window.__auditioning = true;
  voicesStatus.textContent = `Playing a preview in “${voiceLabel(voice)}”…`;
  const player = new PcmPlayer(24000);
  player.ensure().then(() => {
    const protocol = location.protocol === "https:" ? "wss" : "ws";
    const params = new URLSearchParams({ voice: voice.voice_id });
    if (voice.delivery) params.set("delivery", voice.delivery);
    const socket = new WebSocket(`${protocol}://${location.host}/ws/voice-preview?${params.toString()}`);
    const finish = (message) => {
      voicesStatus.textContent = message;
      window.__auditioning = false;
      try { socket.close(); } catch (e) { /* ignore */ }
      player.stop();
    };
    socket.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (message.type === "audio") player.enqueue(message.data, message.sample_rate || 24000);
      else if (message.type === "transcript" && message.text) voicesStatus.textContent = message.text;
      else if (message.type === "error") finish(message.message);
      else if (message.type === "done") finish(`That was “${voiceLabel(voice)}”.`);
    };
    socket.onerror = () => finish("Could not start the preview.");
  });
}

/* ---------- schedule ---------- */

function renderVoiceOptions() {
  const current = slotVoice.value;
  slotVoice.innerHTML = '<option value="">Random</option>';
  voices.forEach((v) => {
    const opt = document.createElement("option");
    opt.value = String(v.id);
    opt.textContent = voiceLabel(v);
    slotVoice.appendChild(opt);
  });
  if (current) slotVoice.value = current;
}

function renderSlots() {
  slotsBody.innerHTML = "";
  slotsEmpty.hidden = slots.length > 0;
  slots.forEach((s, index) => {
    const voice = voices.find((v) => v.id === s.voice_id);
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${fmtDay(s.day)}</td>
      <td>${escapeHtml(s.start)}</td>
      <td>${escapeHtml(s.end)}</td>
      <td>${voice ? escapeHtml(voiceLabel(voice)) : "Random"}</td>
      <td class="row-actions"><button type="button" class="btn btn-small btn-ghost remove-slot" data-index="${index}">Remove</button></td>`;
    slotsBody.appendChild(tr);
  });
}

slotsBody.addEventListener("click", (event) => {
  const btn = event.target.closest(".remove-slot");
  if (!btn) return;
  slots.splice(Number(btn.dataset.index), 1);
  renderSlots();
  slotsStatus.textContent = "Removed. Press Save schedule to apply.";
});

function overlaps(a, b) {
  if (a.day !== null && b.day !== null && a.day !== b.day) return false;
  return a.start < b.end && b.start < a.end;
}

addSlotBtn.addEventListener("click", () => {
  slotsStatus.textContent = "";
  const start = slotStart.value;
  const end = slotEnd.value;
  if (!start || !end) {
    slotsStatus.textContent = "Pick a start and end time.";
    return;
  }
  if (end <= start) {
    slotsStatus.textContent = "End time must be after start time.";
    return;
  }
  const day = slotDay.value === "" ? null : Number(slotDay.value);
  const voiceId = slotVoice.value === "" ? null : Number(slotVoice.value);
  const candidate = { day, start, end, voice_id: voiceId };
  for (const s of slots) {
    if (overlaps(candidate, { day: s.day, start: s.start, end: s.end, voice_id: s.voice_id })) {
      slotsStatus.textContent =
        `Overlaps ${fmtDay(s.day)} ${s.start}–${s.end}. Remove or change that slot first.`;
      return;
    }
  }
  slots.push(candidate);
  renderSlots();
  slotsStatus.textContent = "Slot added. Press Save schedule to apply.";
});

saveSlotsBtn.addEventListener("click", async () => {
  saveSlotsBtn.disabled = true;
  slotsStatus.textContent = "Saving schedule…";
  try {
    const response = await fetch("/api/voice/slots", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        slots: slots.map((s) => ({ day: s.day, start: s.start, end: s.end, voice_id: s.voice_id })),
      }),
    });
    const data = await parseJson(response);
    if (!response.ok) throw new Error(data.error || "Could not save the schedule.");
    slots = data.slots;
    renderSlots();
    slotsStatus.textContent = "Schedule saved. Calls now follow it.";
  } catch (err) {
    slotsStatus.textContent = err.message;
  } finally {
    saveSlotsBtn.disabled = false;
  }
});

/* After a successful clone or preset save, refresh the list (clone.js / voice-picker.js). */
window.refreshVoices = loadVoices;

loadVoices().catch((err) => {
  voicesStatus.textContent = err.message;
});
