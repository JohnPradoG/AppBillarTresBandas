// Pantalla de REPETICIÓN: pide al servidor el clip de 30 s antes y 15 s después
// del momento que se estaba viendo y lo muestra con cámara lenta, saltos, zoom
// y desplazamiento. Vuelve sola a la partida tras 60 s sin tocar.

const $ = (id) => document.getElementById(id);
const view = $("replay");
const video = $("rp-video");
const zoomLayer = $("rp-zoom");
const stage = $("rp-stage");

export const AUTO_RETURN_MS = 60000;
// Al abrir, empieza un poco antes de la jugada: se pulsa después de verla.
export const LEAD_SECONDS = 10;
const ZOOM_STEPS = [1, 1.5, 2, 3, 4, 6];
const MAX_ZOOM = ZOOM_STEPS[ZOOM_STEPS.length - 1];

let clip = null;          // { url, clip, start_ms, end_ms, moment_ms, fps, play_id }
let lastTouchAt = 0;
let onClose = () => {};
let notify = () => {};
let request = 0;
let mode = {};            // opciones de open(): repetición normal o minuto del historial

const time = (ms) => new Date(ms).toLocaleTimeString("es-CO", { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
const rateLabel = (r) => `${String(r).replace(".", ",")}x`;

export function isOpen() {
  return !view.hidden;
}

export function setup(opts) {
  onClose = opts.onClose;
  notify = opts.toast;
}

// moment: hora real (ms) de la imagen que se veía al pulsar.
// info: { table, turnName, score, innings, recLabel, recOk, meta }
// opts:
//   range: [inicio, fin]  tramo del historial (sin marca de jugada)
//   clip: { url, start_ms, end_ms, moment_ms, fps }  jugada guardada, sin armar nada
//   play: { id, protected }  jugada de la lista JUGADAS
//   title, sub, caption, backLabel, onBack, autoReturnMs, nav: { prev, next }
export async function open(momentMs, info, opts = {}) {
  const mine = ++request;
  mode = opts;
  const fromHistory = Boolean(opts.range) && !opts.play;
  view.hidden = false;
  lastTouchAt = Date.now();
  clip = null;
  video.removeAttribute("src");
  video.load();
  resetZoom();
  setRate(1);
  setSaved(Boolean(opts.play && opts.play.protected));
  $("rp-word").textContent = opts.title || "REPETICIÓN";
  $("rp-card-caption").textContent = opts.caption || "Jugada";
  $("rp-back-main").textContent = opts.backLabel || "VOLVER A LA PARTIDA";
  $("rp-nav").hidden = !opts.nav;
  $("rp-mark").hidden = fromHistory;
  $("rp-msg").textContent = fromHistory || opts.play ? "Buscando la grabación…" : "Preparando la repetición…";
  $("rp-msg").hidden = false;
  $("rp-sub").textContent = opts.sub || (fromHistory
    ? `Mesa ${info.table} · ${dayLabel(momentMs)} · ${hm(momentMs)}`
    : `Mesa ${info.table} · Jugada de las ${time(momentMs)}`);
  $("rp-time").textContent = fromHistory ? hm(momentMs) : time(momentMs);
  $("rp-turn").textContent = info.turnName ? `Turno de ${info.turnName}` : "";
  $("rp-score").textContent = info.score
    ? `Marcador ${info.score[0]} – ${info.score[1]} · Entrada ${info.innings}`
    : dayLabel(momentMs);
  setRec(info.recLabel, info.recOk);
  ["rp-from", "rp-to", "rp-moment", "rp-clock"].forEach((id) => { $(id).textContent = ""; });

  let data;
  if (opts.clip) {
    data = opts.clip;
  } else {
    const body = { moment_ms: Math.round(momentMs) };
    if (opts.range) [body.start_ms, body.end_ms] = opts.range.map(Math.round);
    else if (info.meta) body.meta = info.meta;
    try {
      const res = await fetch("/api/replay", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      data = await res.json();
      if (!res.ok) throw new Error(data.error || "error");
    } catch (e) {
      if (mine !== request) return;
      $("rp-msg").textContent = `No se pudo abrir la grabación. ${e.message === "error" ? "" : e.message}`;
      return;
    }
  }
  if (mine !== request || view.hidden) return;
  clip = data;
  if (fromHistory) {
    $("rp-from").textContent = time(clip.start_ms);
    $("rp-to").textContent = time(clip.end_ms);
  } else {
    $("rp-from").textContent = `${time(clip.start_ms)} · −${Math.round((clip.moment_ms - clip.start_ms) / 1000)} s`;
    $("rp-to").textContent = `+${Math.round((clip.end_ms - clip.moment_ms) / 1000)} s · ${time(clip.end_ms)}`;
    $("rp-moment").textContent = `JUGADA ${time(clip.moment_ms)}`;
    const markPct = pct(clip.moment_ms);
    $("rp-mark").style.left = `${markPct}%`;
    $("rp-moment").style.left = `${Math.min(85, Math.max(15, markPct))}%`;
  }
  video.src = clip.url;
  video.addEventListener("loadedmetadata", () => {
    video.currentTime = fromHistory ? 0 : Math.max(0, (clip.moment_ms - clip.start_ms) / 1000 - LEAD_SECONDS);
    video.playbackRate = currentRate;
    video.play().catch(() => {});
    $("rp-msg").hidden = true;
  }, { once: true });
}

// ---------- GUARDAR JUGADA ----------

let saving = false;

function setSaved(saved) {
  const b = $("rp-save");
  b.classList.toggle("done", saved);
  b.querySelector("span").textContent = saved ? "JUGADA GUARDADA" : "GUARDAR JUGADA";
}

// Lo que se guarda depende de dónde se abrió la repetición:
// REPETICIÓN o JUGADAS: esa jugada (ya anotada); HISTORIAL: el tramo que se ve.
function saveBody() {
  const id = (mode.play && mode.play.id) || clip.play_id;
  if (id) return { play_id: id, clip: clip.clip };
  return {
    moment_ms: Math.round(clip.start_ms + video.currentTime * 1000),
    start_ms: clip.start_ms,
    end_ms: clip.end_ms,
    source: "historial",
    clip: clip.clip,
  };
}

async function save() {
  if (!clip) { notify("Espera a que cargue la grabación"); return; }
  if ($("rp-save").classList.contains("done")) { notify("Esta jugada ya está guardada"); return; }
  if (saving) return;
  saving = true;
  const mine = request;
  notify("Guardando la jugada…");
  try {
    const play = await savePlay(saveBody());
    if (mine === request) {
      setSaved(true);
      if (mode.play) mode.play.protected = true;
      if (mode.onSaved) mode.onSaved(play);
    }
    notify(play.warning || "Jugada guardada. No se borra con el historial.");
  } catch (e) {
    notify(`No se pudo guardar la jugada. ${e.message}`);
  } finally {
    saving = false;
  }
}

export async function savePlay(body) {
  const res = await fetch("/api/plays", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || "");
  return data;
}

const hm = (ms) => new Date(ms).toLocaleTimeString("es-CO", { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const dayLabel = (ms) => new Date(ms).toLocaleDateString("es-CO", { weekday: "long", day: "numeric", month: "long" });

// toGame: true al volver a la partida (botón o tiempo sin tocar); false al
// volver al historial.
export function close(toGame = true) {
  request++;
  view.hidden = true;
  video.pause();
  video.removeAttribute("src");
  video.load();
  clip = null;
  const back = mode.onBack;
  mode = {};
  if (toGame || !back) onClose(); else back();
}

export function setRec(label, ok) {
  $("rp-rec").className = `rec ${ok ? "ok" : "bad"}`;
  $("rp-rec-label").textContent = label;
}

function pct(ms) {
  if (!clip) return 0;
  return Math.min(100, Math.max(0, ((ms - clip.start_ms) / (clip.end_ms - clip.start_ms)) * 100));
}

// ---------- reproducción ----------

let currentRate = 1;

function setRate(r) {
  currentRate = r;
  video.playbackRate = r;
  document.querySelectorAll("#rp-speeds button").forEach((b) =>
    b.classList.toggle("on", Number(b.dataset.rate) === r));
}

function step(seconds) {
  if (!clip || !Number.isFinite(video.duration)) return;
  video.currentTime = Math.min(video.duration, Math.max(0, video.currentTime + seconds));
}

// Cuadro a cuadro: para ver si una bola toca a la otra.
function frame(dir) {
  if (!clip || !Number.isFinite(video.duration)) return;
  video.pause();
  const fps = clip.fps || 30;
  const index = Math.floor(video.currentTime * fps + 0.001) + dir;
  // A mitad del cuadro, para que el navegador muestre justo ese.
  video.currentTime = Math.min(video.duration, Math.max(0, (index + 0.5) / fps));
}

const timeMs = (ms) => {
  const d = new Date(ms);
  return `${time(ms)},${String(d.getMilliseconds()).padStart(3, "0")}`;
};

function render() {
  if (!view.hidden) {
    $("rp-play").classList.toggle("paused", video.paused);
    $("rp-play").setAttribute("aria-label", video.paused ? "Reproducir" : "Pausar");
    if (clip && Number.isFinite(video.duration) && video.duration > 0) {
      const p = (video.currentTime / video.duration) * 100;
      $("rp-fill").style.width = `${p}%`;
      $("rp-thumb").style.left = `${p}%`;
      const at = clip.start_ms + video.currentTime * 1000;
      $("rp-clock").textContent = video.paused
        ? `${timeMs(at)} · cuadro a cuadro`
        : `${time(at)} · ${rateLabel(currentRate)}`;
    }
    const limit = mode.autoReturnMs || AUTO_RETURN_MS;
    const left = Math.ceil((limit - (Date.now() - lastTouchAt)) / 1000);
    $("rp-back-sub").textContent = left <= 10
      ? `Vuelve a la partida en ${Math.max(0, left)} s`
      : `Vuelve sola en ${limit / 1000} s sin tocar`;
    if (left <= 0) close(true);
  }
  requestAnimationFrame(render);
}

$("rp-play").addEventListener("click", () => {
  if (video.paused) {
    if (video.ended) video.currentTime = 0;
    video.play().catch(() => {});
  } else {
    video.pause();
  }
});
document.querySelectorAll(".rp-steps [data-step]").forEach((b) =>
  b.addEventListener("click", () => step(Number(b.dataset.step))));
document.querySelectorAll(".rp-steps [data-frame]").forEach((b) =>
  b.addEventListener("click", () => frame(Number(b.dataset.frame))));
document.querySelectorAll("#rp-speeds button").forEach((b) =>
  b.addEventListener("click", () => setRate(Number(b.dataset.rate))));
$("rp-back").addEventListener("click", () => close(!mode.onBack));
$("rp-prev").addEventListener("click", () => mode.nav && mode.nav.prev());
$("rp-next").addEventListener("click", () => mode.nav && mode.nav.next());
$("rp-save").addEventListener("click", save);
view.addEventListener("pointerdown", () => { lastTouchAt = Date.now(); }, true);

// Barra de tiempo: tocar o arrastrar para ir a ese momento.
const bar = $("rp-bar");
let scrubbing = false;
function seekTo(e) {
  if (!clip || !Number.isFinite(video.duration)) return;
  const r = bar.getBoundingClientRect();
  const f = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
  video.currentTime = f * video.duration;
}
bar.addEventListener("pointerdown", (e) => { scrubbing = true; bar.setPointerCapture(e.pointerId); seekTo(e); });
bar.addEventListener("pointermove", (e) => { if (scrubbing) seekTo(e); });
bar.addEventListener("pointerup", () => { scrubbing = false; });
bar.addEventListener("pointercancel", () => { scrubbing = false; });

// ---------- zoom y desplazamiento ----------

let zoom = 1;
let panX = 0;
let panY = 0;

function applyZoom() {
  const w = stage.clientWidth;
  const h = stage.clientHeight;
  // Sin dejar bordes vacíos: la imagen ampliada siempre cubre el recuadro.
  panX = Math.min(0, Math.max(w - w * zoom, panX));
  panY = Math.min(0, Math.max(h - h * zoom, panY));
  zoomLayer.style.transform = `translate(${panX}px, ${panY}px) scale(${zoom})`;
  const chip = $("rp-zoom-chip");
  chip.hidden = zoom <= 1.01;
  chip.textContent = `Zoom ${String(Math.round(zoom * 10) / 10).replace(".", ",")}x · arrastra para mover`;
}

// Ampliar alrededor de un punto del recuadro (cx, cy en px).
function zoomAt(newZoom, cx, cy) {
  newZoom = Math.min(MAX_ZOOM, Math.max(1, newZoom));
  panX = cx - ((cx - panX) / zoom) * newZoom;
  panY = cy - ((cy - panY) / zoom) * newZoom;
  zoom = newZoom;
  applyZoom();
}

function resetZoom() {
  zoom = 1; panX = 0; panY = 0;
  applyZoom();
}

const center = () => [stage.clientWidth / 2, stage.clientHeight / 2];
const nextZoom = () => ZOOM_STEPS.find((z) => z > zoom + 0.01) ?? MAX_ZOOM;
const prevZoom = () => [...ZOOM_STEPS].reverse().find((z) => z < zoom - 0.01) ?? 1;
$("rp-zoom-in").addEventListener("click", () => zoomAt(nextZoom(), ...center()));
$("rp-zoom-out").addEventListener("click", () => zoomAt(prevZoom(), ...center()));
$("rp-zoom-fit").addEventListener("click", resetZoom);

const pointers = new Map();
let pinch = null;
stage.addEventListener("pointerdown", (e) => {
  if (e.target.closest("button")) return;
  stage.setPointerCapture(e.pointerId);
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
  if (pointers.size === 2) {
    const [a, b] = [...pointers.values()];
    pinch = { dist: Math.hypot(a.x - b.x, a.y - b.y), zoom };
  }
});
stage.addEventListener("pointermove", (e) => {
  const prev = pointers.get(e.pointerId);
  if (!prev) return;
  const now = { x: e.clientX, y: e.clientY };
  pointers.set(e.pointerId, now);
  if (pointers.size === 2 && pinch) {
    const [a, b] = [...pointers.values()];
    const r = stage.getBoundingClientRect();
    const dist = Math.hypot(a.x - b.x, a.y - b.y);
    zoomAt(pinch.zoom * (dist / pinch.dist), (a.x + b.x) / 2 - r.left, (a.y + b.y) / 2 - r.top);
  } else if (pointers.size === 1 && zoom > 1) {
    panX += now.x - prev.x;
    panY += now.y - prev.y;
    applyZoom();
  }
});
const lift = (e) => {
  pointers.delete(e.pointerId);
  if (pointers.size < 2) pinch = null;
};
stage.addEventListener("pointerup", lift);
stage.addEventListener("pointercancel", lift);
// Doble toque: ampliar al doble en ese punto, o volver a la mesa completa.
stage.addEventListener("dblclick", (e) => {
  if (e.target.closest("button")) return;
  const r = stage.getBoundingClientRect();
  if (zoom > 1) resetZoom(); else zoomAt(2, e.clientX - r.left, e.clientY - r.top);
});

requestAnimationFrame(render);
