// Vista Mesa: video con retraso, marcador, relojes y estado de la grabación.
import { EDGE_LAG, correction } from "./delay.js";
import * as sb from "./scoreboard.js";

const $ = (id) => document.getElementById(id);
const video = $("video");
const STORE_KEY = "billar.partida";

// ---------- estado del servidor ----------

let server = null;
let target = 20;            // retraso elegido, en segundos
let liveUrl = null;

async function refreshState() {
  try {
    const res = await fetch("/api/state", { cache: "no-store" });
    server = await res.json();
  } catch {
    server = null;
  }
  renderStatus();
  if (!server) return;
  target = server.delay_seconds;
  $("table-name").textContent = `MESA ${server.table_number}`;
  $("establishment").textContent = server.establishment;
  const cam = server.cameras[0];
  if (cam && cam.live_url !== liveUrl) {
    liveUrl = cam.live_url;
    startPlayer();
  }
  renderDelayChoices();
}

function renderStatus() {
  const rec = $("rec");
  let label, ok;
  if (!server) {
    label = "SIN SERVIDOR"; ok = false;
  } else if (!server.status) {
    label = "SIN MONITOR"; ok = false;
  } else {
    label = server.status.label; ok = server.status.ok;
  }
  rec.className = `rec ${ok ? "ok" : "bad"}`;
  $("rec-label").textContent = label;
}

// ---------- video con retraso ----------

let hls = null;
let waitingForDelay = false;
let lastTime = -1;
let lastProgressAt = Date.now();
let restartTimer = null;

function startPlayer() {
  stopPlayer();
  if (!liveUrl || !window.Hls || !Hls.isSupported()) return;
  hls = new Hls({
    liveSyncDuration: Math.max(1, target - EDGE_LAG),
    liveMaxLatencyDuration: 74,     // la señal guarda 75 s
    maxLiveSyncPlaybackRate: 1,     // la deriva la corrige keepDelay()
    liveDurationInfinity: true,
    backBufferLength: 10,
    maxBufferLength: 30,
    lowLatencyMode: false,
  });
  hls.on(Hls.Events.MANIFEST_PARSED, () => video.play().catch(() => {}));
  hls.on(Hls.Events.ERROR, (_e, data) => {
    if (data.fatal) scheduleRestart();
  });
  hls.loadSource(liveUrl);
  hls.attachMedia(video);
  lastProgressAt = Date.now();
}

function stopPlayer() {
  if (hls) {
    hls.destroy();
    hls = null;
  }
  waitingForDelay = false;
}

function scheduleRestart() {
  stopPlayer();
  clearTimeout(restartTimer);
  restartTimer = setTimeout(startPlayer, 2000);
}

function keepDelay() {
  const msg = $("video-msg");
  const cam = server && server.cameras[0];
  const camLive = cam && cam.live_state === "en_vivo";
  // Cuánto va la imagen por detrás de la cámara.
  const lat = hls ? hls.latency + EDGE_LAG : NaN;
  const playing = hls && video.readyState >= 2 && Number.isFinite(lat);

  if (playing) {
    const c = correction(hls.latency, target, video.playbackRate);
    if (c.action === "seek") {
      video.currentTime += c.by;
    } else if (c.action === "wait") {
      if (!video.paused) video.pause();
      waitingForDelay = true;
    } else {
      if (video.paused) video.play().catch(() => {});
      waitingForDelay = false;
      video.playbackRate = c.rate;
    }
    const at = new Date(Date.now() - lat * 1000);
    $("video-age").textContent =
      `Imagen de hace ${Math.round(lat)} s · ${at.toLocaleTimeString("es-CO", { hour12: false })}`;
  } else {
    $("video-age").textContent = "";
  }

  if (!camLive && !playing) {
    msg.textContent = "Esperando la imagen de la cámara…";
    msg.hidden = false;
  } else if (waitingForDelay && Number.isFinite(lat) && target - lat > 2) {
    msg.textContent = `Ajustando el retraso a ${target} s…`;
    msg.hidden = false;
  } else {
    msg.hidden = playing;
  }

  // Imagen detenida sin motivo: reiniciar el reproductor.
  if (video.currentTime !== lastTime || waitingForDelay) {
    lastTime = video.currentTime;
    lastProgressAt = Date.now();
  } else if (hls && Date.now() - lastProgressAt > 10000) {
    scheduleRestart();
    lastProgressAt = Date.now();
  }
}

// ---------- marcador ----------

function loadGame() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORE_KEY));
    if (saved && saved.players) return saved;
  } catch { /* sin almacenamiento: partida nueva */ }
  return sb.newGame();
}

function saveGame() {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(game)); } catch { /* sigue en memoria */ }
}

let game = loadGame();

const fmt3 = (n) => n.toFixed(3).replace(".", ",");
const pad = (n) => String(n).padStart(2, "0");

function renderScore() {
  document.querySelectorAll(".panel").forEach((panel) => {
    const i = Number(panel.dataset.player);
    const p = game.players[i];
    panel.querySelector("[data-name]").textContent = p.name;
    panel.querySelector("[data-score]").textContent = p.score;
    panel.querySelector("[data-best]").textContent = p.bestRun;
    panel.querySelector("[data-avg]").textContent = fmt3(sb.average(p));
    const badge = panel.querySelector("[data-badge]");
    const onTurn = game.turn === i;
    panel.classList.toggle("on-turn", onTurn);
    badge.className = `turn-badge ${onTurn ? "on" : "off"}`;
    badge.textContent = onTurn ? "EN TURNO" : "Toca tu panel para tomar el turno";
  });
  $("innings").textContent = sb.innings(game);
}

function renderClocks() {
  const now = Date.now();
  const s = sb.elapsedSeconds(game, now);
  $("game-time").textContent = `${pad(Math.floor(s / 3600))}:${pad(Math.floor(s / 60) % 60)}:${pad(s % 60)}`;
  const left = sb.shotRemaining(game, now);
  $("shot-time").textContent = left;
  const shot = $("shot");
  shot.classList.toggle("warn", game.turn !== null && left <= 10);
  shot.classList.toggle("over", game.turn !== null && left === 0);
}

function update(fn) {
  fn(Date.now());
  saveGame();
  renderScore();
  renderClocks();
}

document.querySelectorAll(".panel").forEach((panel) => {
  const i = Number(panel.dataset.player);
  // Tocar cualquier parte del panel toma el turno y reinicia el reloj para tacar.
  panel.addEventListener("pointerdown", () => update((now) => sb.touch(game, i, now)));
  panel.querySelectorAll("[data-add]").forEach((b) =>
    b.addEventListener("click", () => update((now) => sb.add(game, i, Number(b.dataset.add), now))));
  panel.querySelector("[data-sub]").addEventListener("click", () => update((now) => sb.subtract(game, i, now)));
});

// ---------- menú ----------

function renderDelayChoices() {
  const row = $("delay-row");
  const choices = server ? server.delay_choices : [10, 20, 30, 45, 60];
  if (row.childElementCount !== choices.length) {
    row.replaceChildren(...choices.map((s) => {
      const b = document.createElement("button");
      b.dataset.seconds = s;
      b.textContent = `${s} s`;
      b.addEventListener("click", () => setDelay(s));
      return b;
    }));
  }
  row.querySelectorAll("button").forEach((b) => b.classList.toggle("on", Number(b.dataset.seconds) === target));
}

async function setDelay(seconds) {
  try {
    const res = await fetch("/api/settings/delay", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ seconds }),
    });
    if (!res.ok) throw new Error();
    target = seconds;
    renderDelayChoices();
    toast(`Retraso de la pantalla: ${seconds} s`);
  } catch {
    toast("No se pudo cambiar el retraso");
  }
}

let toastTimer = null;
function toast(text) {
  const t = $("toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 3000);
}

$("menu-btn").addEventListener("click", () => { renderDelayChoices(); $("menu").hidden = false; });
$("menu-close").addEventListener("click", () => { $("menu").hidden = true; });
$("new-game").addEventListener("click", () => { $("menu").hidden = true; $("confirm").hidden = false; });
$("confirm-no").addEventListener("click", () => { $("confirm").hidden = true; });
$("confirm-yes").addEventListener("click", () => {
  $("confirm").hidden = true;
  game = sb.newGame();
  update(() => {});
});
$("replay-btn").addEventListener("click", () => toast("La repetición llega en la próxima actualización"));
$("save-btn").addEventListener("click", () => toast("Guardar jugada llega en una próxima actualización"));
// Sin menú contextual ni zoom con dos dedos en la pantalla táctil.
document.addEventListener("contextmenu", (e) => e.preventDefault());

// ---------- modo reposo ----------
// Tras N minutos sin tocar la pantalla se muestra el nombre del billar y la
// marca Vano Systems. La grabación sigue igual; un toque vuelve a la mesa.

let lastTouchAt = Date.now();
let resting = false;
let restMovedAt = 0;
let wokeAt = 0;

function idleMs() {
  return (server && server.idle_minutes ? server.idle_minutes : 20) * 60000;
}

function enterRest() {
  resting = true;
  $("rest").hidden = false;
  $("menu").hidden = true;
  $("confirm").hidden = true;
  restMovedAt = 0;
  renderRest();
}

function leaveRest() {
  resting = false;
  $("rest").hidden = true;
}

function renderRest() {
  const now = new Date();
  $("rest-time").textContent = now.toLocaleTimeString("es-CO", { hour: "2-digit", minute: "2-digit", hour12: false });
  $("rest-place").textContent = server ? server.establishment : "";
  $("rest-table").textContent = server ? `Mesa ${server.table_number}` : "";
  // La grabación no se detiene en reposo; se indica para tranquilidad del dueño.
  $("rest-rec").textContent = $("rec-label").textContent;
  if (Date.now() - restMovedAt >= 60000) {
    restMovedAt = Date.now();
    const card = $("rest-card");
    card.style.left = `${30 + Math.random() * 40}%`;
    card.style.top = `${35 + Math.random() * 30}%`;
  }
}

function checkRest() {
  if (resting) renderRest();
  else if (Date.now() - lastTouchAt >= idleMs()) enterRest();
}

// Captura: el toque que despierta la pantalla no marca carambolas ni toma el turno.
document.addEventListener("pointerdown", (e) => {
  lastTouchAt = Date.now();
  if (resting) {
    e.stopPropagation();
    e.preventDefault();
    wokeAt = Date.now();
    leaveRest();
  }
}, true);
document.addEventListener("click", (e) => {
  if (Date.now() - wokeAt < 800) e.stopPropagation();
}, true);

// ---------- arranque ----------

renderScore();
renderClocks();
refreshState();
setInterval(refreshState, 2000);
setInterval(keepDelay, 500);
setInterval(renderClocks, 250);
setInterval(checkRest, 1000);
