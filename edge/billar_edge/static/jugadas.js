// JUGADAS: cada REPETICIÓN queda en esta lista mientras exista su grabación
// (7 días); las protegidas con GUARDAR JUGADA se quedan siempre. Filtros por
// fecha, hora, jugador, partida y solo protegidas.
import * as replay from "./replay.js";

const $ = (id) => document.getElementById(id);
const view = $("plays");
const AUTO_RETURN_MS = 180000;
const LOCK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="11" width="14" height="10" rx="2"></rect><path d="M8 11V7a4 4 0 0 1 8 0v4"></path></svg>';
const PLAY = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"></path></svg>';
const SHARE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="18" cy="5" r="3"></circle><circle cx="6" cy="12" r="3"></circle><circle cx="18" cy="19" r="3"></circle><line x1="8.6" y1="13.5" x2="15.4" y2="17.5"></line><line x1="15.4" y1="6.5" x2="8.6" y2="10.5"></line></svg>';

let info = {};
let notify = () => {};
let onHistory = () => {};
let data = null;
let lastTouchAt = 0;
const filters = { date: null, hour: null, player: null, game: null, protected: false };

const time = (ms) => new Date(ms).toLocaleTimeString("es-CO", { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
const shortDay = (ms) => new Date(ms).toLocaleDateString("es-CO", { day: "2-digit", month: "2-digit" });
const isoToday = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};
const dayName = (iso) => {
  if (iso === isoToday()) return "hoy";
  const d = new Date(`${iso}T12:00:00`);
  return d.toLocaleDateString("es-CO", { weekday: "short", day: "numeric", month: "short" });
};
const size = (bytes) => (bytes < 1024 ** 3
  ? `${Math.round(bytes / 1024 ** 2).toLocaleString("es-CO")} MB`
  : `${(bytes / 1024 ** 3).toLocaleString("es-CO", { maximumFractionDigits: 1 })} GB`);

export function setup(opts) {
  notify = opts.toast;
  onHistory = opts.openHistory;
}

export function isOpen() {
  return !view.hidden;
}

export function open(tableInfo) {
  info = tableInfo;
  view.hidden = false;
  lastTouchAt = Date.now();
  $("pl-sub").textContent = `Mesa ${info.table} · repeticiones de los últimos 7 días y jugadas protegidas`;
  $("pl-f-table").textContent = `Mesa ${info.table}`;
  setRec(info.recLabel, info.recOk);
  load();
}

export function close() {
  view.hidden = true;
  $("pl-picker").hidden = true;
}

export function setRec(label, ok) {
  $("pl-rec").className = `rec ${ok ? "ok" : "bad"}`;
  $("pl-rec-label").textContent = label;
}

async function load() {
  const q = new URLSearchParams();
  if (filters.date) q.set("date", filters.date);
  if (filters.hour !== null) q.set("hour", filters.hour);
  if (filters.player) q.set("player", filters.player);
  if (filters.game !== null) q.set("game", filters.game);
  if (filters.protected) q.set("protected", "1");
  renderFilters();
  try {
    const res = await fetch(`/api/plays?${q}`, { cache: "no-store" });
    const body = await res.json();
    if (!res.ok) throw new Error(body.error);
    data = body;
  } catch (e) {
    $("pl-rows").replaceChildren();
    $("pl-msg").textContent = `No se pudieron leer las jugadas. ${e.message || ""}`;
    $("pl-msg").hidden = false;
    return;
  }
  renderRows();
  renderFoot();
}

function renderFilters() {
  const set = (id, on, text) => {
    $(id).textContent = text;
    $(id).classList.toggle("set", on);
  };
  set("pl-f-date", Boolean(filters.date), `Fecha: ${filters.date ? dayName(filters.date) : "todas"}`);
  set("pl-f-hour", filters.hour !== null, `Hora: ${filters.hour !== null ? `${String(filters.hour).padStart(2, "0")}:00` : "cualquiera"}`);
  set("pl-f-player", Boolean(filters.player), `Jugador: ${filters.player || "todos"}`);
  set("pl-f-game", filters.game !== null, `Partida: ${filters.game !== null ? `#${filters.game}` : "todas"}`);
  $("pl-f-protected").classList.toggle("set", filters.protected);
}

function cell(text, cls) {
  const s = document.createElement("span");
  if (cls) s.className = cls;
  s.textContent = text;
  return s;
}

function renderRows() {
  const rows = data.plays.map((p) => {
    const row = document.createElement("div");
    row.className = "pl-row";
    const when = document.createElement("span");
    when.className = "pl-time";
    when.innerHTML = "<small></small>";
    when.firstChild.textContent = shortDay(p.moment_ms);
    when.append(time(p.moment_ms));
    const state = document.createElement("span");
    if (p.protected) {
      state.className = "pl-saved";
      state.innerHTML = `${LOCK}<span></span>`;
      state.lastChild.textContent = p.expires_ms ? `Protegida hasta el ${shortDay(p.expires_ms)}` : "Protegida";
    } else {
      state.className = "pl-note";
      state.textContent = `Se borra el ${shortDay(p.expires_ms)}`;
    }
    const actions = document.createElement("span");
    actions.className = "pl-actions";
    const play = document.createElement("button");
    play.className = "pl-play";
    play.innerHTML = PLAY;
    play.setAttribute("aria-label", "Ver jugada");
    play.addEventListener("click", () => watch(p));
    const second = document.createElement("button");
    if (p.protected) {
      second.innerHTML = SHARE;
      second.setAttribute("aria-label", "Compartir");
      second.addEventListener("click", () => notify("Compartir por WhatsApp y Telegram llega en la Fase 7"));
    } else {
      second.className = "pl-save";
      second.innerHTML = LOCK;
      second.setAttribute("aria-label", "Proteger jugada");
      second.addEventListener("click", () => protect(p, second));
    }
    actions.append(play, second);
    const score = p.score1 !== null && p.score2 !== null ? `${p.score1} – ${p.score2}` : "—";
    row.append(
      when,
      cell(p.turn_player || (p.source === "historial" ? "Desde el historial" : "—"), p.turn_player ? "" : "pl-dim"),
      cell(String(p.table_number)),
      cell(p.game_number !== null ? `#${p.game_number}` : "—", p.game_number !== null ? "" : "pl-dim"),
      cell(score, "pl-score"),
      state,
      actions,
    );
    return row;
  });
  $("pl-rows").replaceChildren(...rows);
  $("pl-rows").scrollTop = 0;
  const any = Object.values(filters).some((v) => v !== null && v !== false);
  $("pl-msg").textContent = any
    ? "No hay jugadas con estos filtros."
    : "Todavía no hay jugadas. Cada REPETICIÓN aparece aquí; GUARDAR JUGADA la protege.";
  $("pl-msg").hidden = rows.length > 0;
}

function renderFoot() {
  const foot = $("pl-foot");
  const over = data.usage_bytes > data.quota_bytes;
  foot.classList.toggle("warn", over);
  foot.textContent = `Las jugadas protegidas ocupan ${size(data.usage_bytes)} de ${size(data.quota_bytes)} previstos.`
    + (over ? " Conviene copiarlas y borrar las que ya no se necesiten." : "")
    + (data.protected_days > 0 ? ` Se borran solas a los ${data.protected_days} días;` : "")
    + " las demás, con la grabación a los 7 días.";
}

async function protect(p, button) {
  button.disabled = true;
  notify("Guardando la jugada…");
  try {
    const saved = await replay.savePlay({ play_id: p.id });
    notify(saved.warning || "Jugada protegida. No se borra con el historial.");
    await load();
  } catch (e) {
    notify(`No se pudo guardar la jugada. ${e.message}`);
    button.disabled = false;
  }
}

function watch(p) {
  const opts = {
    title: "JUGADA",
    sub: `Mesa ${p.table_number} · ${new Date(p.moment_ms).toLocaleDateString("es-CO", { weekday: "long", day: "numeric", month: "long" })} · ${time(p.moment_ms)}`,
    backLabel: "VOLVER A JUGADAS",
    onBack: () => { lastTouchAt = Date.now(); },
    onSaved: () => load(),
    autoReturnMs: AUTO_RETURN_MS,
    play: { id: p.id, protected: p.protected },
  };
  if (p.protected) {
    opts.clip = { url: p.url, start_ms: p.start_ms, end_ms: p.end_ms, moment_ms: p.moment_ms, fps: p.fps };
  } else {
    opts.range = [p.start_ms, p.end_ms];
  }
  replay.open(p.moment_ms, {
    table: p.table_number,
    turnName: p.turn_player,
    score: p.score1 !== null && p.score2 !== null ? [p.score1, p.score2] : null,
    innings: p.innings ?? 0,
    recLabel: $("pl-rec-label").textContent,
    recOk: $("pl-rec").classList.contains("ok"),
  }, opts);
}

// ---------- filtros ----------

function pick(title, choices, current, onPick, cls = "") {
  $("pl-picker-title").textContent = title;
  $("pl-choices").className = `pl-choices ${cls}`;
  $("pl-choices").replaceChildren(...choices.map(([value, label]) => {
    const b = document.createElement("button");
    b.textContent = label;
    b.classList.toggle("on", value === current);
    b.addEventListener("click", () => {
      $("pl-picker").hidden = true;
      onPick(value);
      load();
    });
    return b;
  }));
  $("pl-picker").hidden = false;
}

const PICKERS = {
  date: () => pick("Fecha", [[null, "Todas"], ...data.options.days.map((d) => [d, dayName(d)])],
    filters.date, (v) => { filters.date = v; }),
  hour: () => pick("Hora", [[null, "Cualquiera"], ...Array.from({ length: 24 }, (_, h) => [h, `${String(h).padStart(2, "0")}:00`])],
    filters.hour, (v) => { filters.hour = v; }, "hours"),
  player: () => pick("Jugador en turno", [[null, "Todos"], ...data.options.players.map((n) => [n, n])],
    filters.player, (v) => { filters.player = v; }),
  game: () => pick("Partida", [[null, "Todas"], ...data.options.games.map((g) => [g, `Partida #${g}`])],
    filters.game, (v) => { filters.game = v; }),
};

document.querySelectorAll(".pl-filters [data-filter]").forEach((b) =>
  b.addEventListener("click", () => { if (data) PICKERS[b.dataset.filter](); }));
$("pl-f-protected").addEventListener("click", () => { filters.protected = !filters.protected; load(); });
$("pl-picker-close").addEventListener("click", () => { $("pl-picker").hidden = true; });
$("pl-to-history").addEventListener("click", () => onHistory());
$("pl-close").addEventListener("click", close);
view.addEventListener("pointerdown", () => { lastTouchAt = Date.now(); }, true);
$("pl-picker").addEventListener("pointerdown", () => { lastTouchAt = Date.now(); }, true);
setInterval(() => {
  if (isOpen() && !replay.isOpen() && Date.now() - lastTouchAt > AUTO_RETURN_MS) close();
}, 1000);
