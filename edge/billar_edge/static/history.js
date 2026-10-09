// HISTORIAL: buscar por día, hora y minuto en los últimos 7 días y verlo con
// el mismo reproductor de la repetición. Los colores muestran lo grabado y
// los huecos (cámara desconectada o equipo apagado).
import * as replay from "./replay.js";

const $ = (id) => document.getElementById(id);
const view = $("history");
const MINUTE = 60000;
const AUTO_RETURN_MS = 180000;

let data = null;        // respuesta de /api/history del día elegido
let hour = null;
let info = {};
let lastTouchAt = 0;

const level = (pct) => (pct >= 90 ? "full" : pct > 0 ? "part" : "none");
const COLORS = { full: "#2FBF71", part: "#E9C341", none: "#3A4744" };
const hm = (ms) => new Date(ms).toLocaleTimeString("es-CO", { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });

export function isOpen() {
  return !view.hidden;
}

export function open(tableInfo) {
  info = tableInfo;
  view.hidden = false;
  lastTouchAt = Date.now();
  $("hs-sub").textContent = `Mesa ${info.table} · últimos 7 días`;
  load(null);
}

export function close() {
  view.hidden = true;
  data = null;
}

async function load(day) {
  $("hs-msg").hidden = true;
  try {
    const res = await fetch(`/api/history${day ? `?date=${day}` : ""}`, { cache: "no-store" });
    const body = await res.json();
    if (!res.ok) throw new Error(body.error);
    data = body;
  } catch (e) {
    $("hs-msg").textContent = `No se pudo leer el historial. ${e.message || ""}`;
    $("hs-msg").hidden = false;
    return;
  }
  // Por defecto, la última hora con grabación de ese día.
  const lastMinute = data.minutes.findLastIndex((m) => m > 0);
  hour = lastMinute >= 0 ? Math.floor(lastMinute / 60) : null;
  render();
}

function render() {
  renderDays();
  renderHours();
  renderMinutes();
}

function renderDays() {
  const today = data.days[0];
  $("hs-days").replaceChildren(...data.days.map((d, i) => {
    const b = document.createElement("button");
    const date = new Date(`${d}T12:00:00`);
    const top = i === 0 ? "Hoy" : i === 1 ? "Ayer" : date.toLocaleDateString("es-CO", { weekday: "short" });
    b.innerHTML = `<span class="d1"></span><span class="d2"></span>`;
    b.querySelector(".d1").textContent = top.charAt(0).toUpperCase() + top.slice(1);
    b.querySelector(".d2").textContent = date.toLocaleDateString("es-CO", { day: "numeric", month: "short" });
    b.classList.toggle("on", d === data.date);
    b.addEventListener("click", () => load(d === today ? null : d));
    return b;
  }));
}

// Barra de 60 minutos de cada hora, con los tramos grabados y los huecos.
function hourGradient(h) {
  const stops = [];
  for (let m = 0; m < 60; m++) {
    const c = COLORS[level(data.minutes[h * 60 + m])];
    stops.push(`${c} ${(m / 60) * 100}%`, `${c} ${((m + 1) / 60) * 100}%`);
  }
  return `linear-gradient(to right, ${stops.join(", ")})`;
}

function renderHours() {
  $("hs-hours").replaceChildren(...Array.from({ length: 24 }, (_, h) => {
    const b = document.createElement("button");
    b.innerHTML = `<span></span><span class="bar"></span>`;
    b.firstChild.textContent = `${String(h).padStart(2, "0")}:00`;
    b.querySelector(".bar").style.background = hourGradient(h);
    b.classList.toggle("on", h === hour);
    b.addEventListener("click", () => { hour = h; renderHours(); renderMinutes(); });
    return b;
  }));
}

function renderMinutes() {
  const grid = $("hs-minutes");
  if (hour === null) {
    grid.replaceChildren();
    $("hs-minutes-title").textContent = "2. Elige el minuto";
    $("hs-msg").textContent = "No hay grabación este día.";
    $("hs-msg").hidden = false;
    return;
  }
  $("hs-msg").hidden = true;
  $("hs-minutes-title").textContent = `2. Elige el minuto (${String(hour).padStart(2, "0")}:00 a ${String(hour).padStart(2, "0")}:59)`;
  grid.replaceChildren(...Array.from({ length: 60 }, (_, m) => {
    const index = hour * 60 + m;
    const start = data.start_ms + index * MINUTE;
    const b = document.createElement("button");
    b.textContent = `:${String(m).padStart(2, "0")}`;
    b.className = level(data.minutes[index]);
    b.disabled = data.minutes[index] === 0 || start > data.now_ms;
    b.setAttribute("aria-label", hm(start));
    b.addEventListener("click", () => play(index));
    return b;
  }));
}

function play(index) {
  if (index < 0 || index >= data.minutes.length) return;
  const start = data.start_ms + index * MINUTE;
  replay.open(start, info, {
    range: [start, start + MINUTE],
    title: "HISTORIAL",
    caption: "Minuto",
    backLabel: "VOLVER AL HISTORIAL",
    onBack: () => { lastTouchAt = Date.now(); },
    autoReturnMs: AUTO_RETURN_MS,
    nav: {
      prev: () => play(previous(index)),
      next: () => play(following(index)),
    },
  });
}

// Minutos vecinos con grabación (salta los huecos).
function previous(index) {
  for (let i = index - 1; i >= 0; i--) if (data.minutes[i] > 0) return i;
  return -1;
}

function following(index) {
  for (let i = index + 1; i < data.minutes.length; i++) {
    if (data.start_ms + i * MINUTE > data.now_ms) break;
    if (data.minutes[i] > 0) return i;
  }
  return -1;
}

$("hs-close").addEventListener("click", close);
view.addEventListener("pointerdown", () => { lastTouchAt = Date.now(); }, true);
setInterval(() => {
  if (isOpen() && !replay.isOpen() && Date.now() - lastTouchAt > AUTO_RETURN_MS) close();
}, 1000);
