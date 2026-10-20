const startBtn = document.getElementById("start-call");
const hangupBtn = document.getElementById("hangup");
const statusEl = document.getElementById("call-status");
const transcriptEl = document.getElementById("transcript");

let socket = null;
let capture = null;
let player = null;

function setStatus(text) {
  statusEl.textContent = text;
}

function addLine(who, text) {
  if (!text) return;
  const p = document.createElement("p");
  p.innerHTML = `<span class="who">${who}:</span> ${text}`;
  transcriptEl.appendChild(p);
  transcriptEl.scrollTop = transcriptEl.scrollHeight;
}

startBtn.addEventListener("click", async () => {
  startBtn.disabled = true;
  transcriptEl.innerHTML = "";
  try {
    player = new PcmPlayer(24000);
    await player.ensure();
    capture = new MicCapture();
    const protocol = location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${protocol}://${location.host}/ws/test-call`);
    socket.onopen = async () => {
      hangupBtn.disabled = false;
      setStatus("Call connected. Wait for the greeting, then speak.");
      capture.onPcm = (int16) => {
        if (socket && socket.readyState === WebSocket.OPEN && int16.length) {
          socket.send(JSON.stringify({ type: "audio", data: int16ToBase64(int16) }));
        }
      };
      await capture.start();
    };
    socket.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (message.type === "audio") {
        player.enqueue(message.data, message.sample_rate || 24000);
      } else if (message.type === "ready") {
        setStatus("Call connected. The receptionist is greeting you.");
      } else if (message.type === "agent") {
        addLine("Receptionist", message.text);
      } else if (message.type === "caller") {
        addLine("Caller", message.text);
      } else if (message.type === "ended") {
        addLine("System", "No speech was heard for a while, so the call was closed.");
      } else if (message.type === "error") {
        setStatus(message.message);
      }
    };
    socket.onclose = hangUp;
    socket.onerror = () => setStatus("The test call socket closed unexpectedly.");
  } catch (err) {
    setStatus(err.message || "Could not start the test call.");
    startBtn.disabled = false;
  }
});

hangupBtn.addEventListener("click", () => hangUp());

function hangUp() {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: "hangup" }));
    socket.close();
  }
  socket = null;
  if (capture) capture.stop();
  capture = null;
  if (player) player.stop();
  player = null;
  hangupBtn.disabled = true;
  startBtn.disabled = false;
  setStatus("Call ended.");
}
