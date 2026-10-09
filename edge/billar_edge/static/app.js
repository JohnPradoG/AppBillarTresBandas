// Vista Mesa: video con retraso, marcador, relojes y estado de la grabación.
import { EDGE_LAG, correction } from "./delay.js";
import * as history from "./history.js";
import * as jugadas from "./jugadas.js";
import * as playersSheet from "./players.js";
import * as replay from "./replay.js";
import * as share from "./share.js";
import * as restBalls from "./restballs.js";
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
  replay.setRec(label, ok);
  jugadas.setRec(label, ok);
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
      `Imagen de hace ${Math.round(lat)} s · ${at.toLocaleTimeString("es-CO", { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" })}`;
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

// La partida vive en el equipo (/api/game) para sobrevivir a un reinicio.
// La pantalla la copia en localStorage para seguir marcando si el servidor
// tarda, y la vuelve a mandar en cuanto responde.
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

let recentPlayers = [];
let pushing = false;
let localVersion = 0;       // sube con cada toque en el marcador

function adopt(serverGame) {
  game = { ...serverGame, dirty: false };
  saveGame();
  renderScore();
  renderClocks();
}

async function pullGame() {
  const version = localVersion;
  try {
    const res = await fetch("/api/game", { cache: "no-store" });
    const body = await res.json();
    recentPlayers = body.recent_players;
    // Se tocó el marcador mientras llegaba la respuesta: vale lo de aquí.
    if (version !== localVersion && game.id === body.game.id) return;
    // Si el servidor tiene la misma partida y aquí hay cambios sin mandar, ganan los de aquí.
    if (game.id === body.game.id && game.dirty) pushGame();
    else adopt(body.game);
  } catch { /* se reintenta en el siguiente ciclo */ }
}

async function pushGame() {
  if (!game.id) { pullGame(); return; }
  if (pushing) return;
  pushing = true;
  game.dirty = true;
  saveGame();
  const sent = JSON.stringify(game);
  try {
    const res = await fetch(`/api/game/${game.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ state: game }),
    });
    const body = await res.json();
    if (res.status === 409) {
      // La partida cambió en otro lado: se toma la vigente.
      adopt(body.game);
    } else if (res.ok && JSON.stringify(game) === sent) {
      game.dirty = false;
      saveGame();
    }
  } catch { /* queda pendiente (dirty) */ }
  pushing = false;
  if (game.dirty && JSON.stringify(game) !== sent) pushGame();
}

async function postGame(path, body, method = "POST") {
  const res = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "");
  recentPlayers = data.recent_players;
  adopt(data.game);
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
    // La ayuda "toca tu panel" solo antes del primer toque: luego deja sitio al nombre.
    const hint = game.turn === null;
    badge.className = `turn-badge ${onTurn ? "on" : hint ? "off" : "none"}`;
    badge.textContent = onTurn ? "EN TURNO" : hint ? "Toca tu panel para tomar el turno" : "";
    panel.querySelector("[data-name]").classList.toggle("long", p.name.length > 12);
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
  localVersion++;
  saveGame();
  pushGame();
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
function openPlayers(mode) {
  $("menu").hidden = true;
  playersSheet.open({
    mode,
    current: game.players.map((p) => p.name),
    recent: recentPlayers,
    onDone: async ([player1, player2]) => {
      try {
        if (mode === "new") {
          await postGame("/api/game", { player1, player2 });
          toast(`Partida #${game.number}: ${player1} contra ${player2}`);
        } else {
          await postGame(`/api/game/${game.id}/players`, { player1, player2 }, "PUT");
          toast("Nombres cambiados");
        }
      } catch (e) {
        toast(`No se pudo guardar la partida. ${e.message}`);
      }
    },
  });
}
$("new-game").addEventListener("click", () => openPlayers("new"));
$("rename-players").addEventListener("click", () => openPlayers("rename"));
// El momento de una jugada es la imagen que se estaba viendo (hora − retraso real).
function shownMoment() {
  const lat = hls && Number.isFinite(hls.latency) && video.readyState >= 2 ? hls.latency + EDGE_LAG : target;
  return Date.now() - lat * 1000;
}

// Datos del marcador que quedan con la jugada.
function gameMeta() {
  return {
    game_id: game.id || null,
    game_number: game.number || 1,
    turn_player: game.turn === null ? null : game.players[game.turn].name,
    player1: game.players[0].name,
    player2: game.players[1].name,
    score1: game.players[0].score,
    score2: game.players[1].score,
    innings: sb.innings(game),
  };
}

$("replay-btn").addEventListener("click", () => {
  const turn = game.turn === null ? null : game.players[game.turn].name;
  replay.open(shownMoment(), {
    table: server ? server.table_number : "",
    turnName: turn,
    score: game.players.map((p) => p.score),
    innings: sb.innings(game),
    recLabel: $("rec-label").textContent,
    recOk: $("rec").classList.contains("ok"),
    meta: gameMeta(),
  });
});
// Volver a la partida desde la repetición también cierra el historial y JUGADAS.
replay.setup({ onClose: () => { history.close(); jugadas.close(); lastTouchAt = Date.now(); }, toast });

const tableInfo = () => ({
  table: server ? server.table_number : "",
  recLabel: $("rec-label").textContent,
  recOk: $("rec").classList.contains("ok"),
});
function openHistory() {
  jugadas.close();
  history.open(tableInfo());
}
jugadas.setup({ toast, openHistory });
$("open-history").addEventListener("click", () => { $("menu").hidden = true; openHistory(); });
$("open-plays").addEventListener("click", () => { $("menu").hidden = true; jugadas.open(tableInfo()); });

// GUARDAR JUGADA desde la partida: protege 30 s antes y 15 s después de lo
// que se ve en pantalla, con el marcador de ese momento.
let savingPlay = false;
$("save-btn").addEventListener("click", async () => {
  if (savingPlay) { toast("Ya se está guardando la jugada…"); return; }
  savingPlay = true;
  const btn = $("save-btn");
  btn.classList.add("busy");
  const moment = shownMoment();
  const at = new Date(moment).toLocaleTimeString("es-CO", { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
  toast(`Guardando la jugada de las ${at}…`);
  try {
    const play = await replay.savePlay({ moment_ms: Math.round(moment), source: "pantalla", meta: gameMeta() });
    toast(play.warning || `Jugada de las ${at} guardada. Está en JUGADAS.`);
  } catch (e) {
    toast(`No se pudo guardar la jugada. ${e.message}`);
  } finally {
    savingPlay = false;
    btn.classList.remove("busy");
  }
});
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
  playersSheet.close();
  restMovedAt = 0;
  renderRest();
  restBalls.start($("rest-balls"));
}

function leaveRest() {
  resting = false;
  $("rest").hidden = true;
  restBalls.stop();
}

function renderRest() {
  const now = new Date();
  $("rest-time").textContent = now.toLocaleTimeString("es-CO", { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  $("rest-place").textContent = server ? server.establishment : "";
  $("rest-table").textContent = server ? `Mesa ${server.table_number}` : "";
  const contact = server && server.brand_contact;
  $("rest-contact").hidden = !contact;
  $("rest-contact").textContent = contact ? `¿Lo quieres en tu billar? ${contact}` : "";
  // La grabación no se detiene en reposo; se indica para tranquilidad del dueño.
  $("rest-rec").textContent = $("rec-label").textContent;
  if (Date.now() - restMovedAt >= 60000) {
    restMovedAt = Date.now();
    const card = $("rest-card");
    card.style.left = `${42 + Math.random() * 16}%`;
    card.style.top = `${44 + Math.random() * 12}%`;
  }
}

function checkRest() {
  if (resting) renderRest();
  else if (!replay.isOpen() && !history.isOpen() && !jugadas.isOpen() && !share.isOpen() && Date.now() - lastTouchAt >= idleMs()) enterRest();
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
pullGame();
setInterval(refreshState, 2000);
setInterval(() => { if (game.dirty) pushGame(); else pullGame(); }, 10000);
setInterval(keepDelay, 500);
setInterval(renderClocks, 250);
setInterval(checkRest, 1000);
