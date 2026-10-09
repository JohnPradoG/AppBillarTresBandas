// Nombres de los jugadores: hoja con los dos jugadores, los recientes y un
// teclado en pantalla (la pantalla táctil no tiene teclado físico).

const $ = (id) => document.getElementById(id);
const sheet = $("players");
const ROWS = ["1234567890", "QWERTYUIOP", "ASDFGHJKLÑ", "ZXCVBNMÁÉÍÓÚ"];
const MAX = 24;
const DEFAULTS = ["Jugador 1", "Jugador 2"];

let names = ["", ""];
let active = 0;
let fresh = [true, true];   // el primer toque de tecla reemplaza el nombre
let done = () => {};

export function isOpen() {
  return !sheet.hidden;
}

// mode: "new" (nueva partida) o "rename" (cambiar nombres sin tocar el marcador)
export function open({ mode, current, recent, onDone }) {
  names = [...current];
  fresh = [true, true];
  done = onDone;
  $("pl-sheet-title").textContent = mode === "new" ? "Nueva partida" : "Nombres de los jugadores";
  $("pl-sheet-hint").textContent = mode === "new"
    ? "El marcador vuelve a cero. Toca un jugador y escribe su nombre o elige uno reciente."
    : "El marcador no cambia. Toca un jugador y escribe su nombre o elige uno reciente.";
  $("pl-sheet-ok").textContent = mode === "new" ? "Empezar partida" : "Guardar nombres";
  renderRecent(recent);
  select(0);
  sheet.hidden = false;
}

export function close() {
  sheet.hidden = true;
}

function select(i) {
  active = i;
  render();
}

function render() {
  [0, 1].forEach((i) => {
    const slot = $(`pl-slot-${i}`);
    slot.classList.toggle("on", i === active);
    slot.querySelector("[data-value]").textContent = names[i] || DEFAULTS[i];
    slot.querySelector("[data-value]").classList.toggle("empty", !names[i]);
  });
}

function renderRecent(recent) {
  const box = $("pl-recent");
  box.replaceChildren(...recent.slice(0, 16).map((n) => {
    const b = document.createElement("button");
    b.textContent = n;
    b.addEventListener("click", () => {
      names[active] = n;
      fresh[active] = true;
      // Elegido el primero, pasa al segundo.
      select(active === 0 ? 1 : active);
    });
    return b;
  }));
  $("pl-recent-wrap").hidden = recent.length === 0;
}

function type(ch) {
  let v = fresh[active] ? "" : names[active];
  fresh[active] = false;
  if (v.length >= MAX) return;
  // Mayúscula al empezar cada palabra.
  v += v === "" || v.endsWith(" ") ? ch : ch.toLowerCase();
  names[active] = v;
  render();
}

function key(action) {
  if (action === "space") {
    if (!fresh[active] && names[active] && !names[active].endsWith(" ")) names[active] += " ";
  } else if (action === "back") {
    names[active] = fresh[active] ? "" : names[active].slice(0, -1);
    fresh[active] = false;
  } else if (action === "clear") {
    names[active] = "";
    fresh[active] = false;
  }
  render();
}

function buildKeyboard() {
  const kb = $("pl-keyboard");
  ROWS.forEach((row) => {
    const r = document.createElement("div");
    r.className = "kb-row";
    for (const ch of row) {
      const b = document.createElement("button");
      b.textContent = ch;
      b.addEventListener("click", () => type(ch));
      r.append(b);
    }
    kb.append(r);
  });
  const last = document.createElement("div");
  last.className = "kb-row";
  for (const [action, label, cls] of [["clear", "Borrar todo", "kb-wide"], ["space", "Espacio", "kb-space"], ["back", "⌫ Borrar", "kb-wide"]]) {
    const b = document.createElement("button");
    b.textContent = label;
    b.className = cls;
    b.addEventListener("click", () => key(action));
    last.append(b);
  }
  kb.append(last);
}

buildKeyboard();
[0, 1].forEach((i) => $(`pl-slot-${i}`).addEventListener("click", () => { fresh[i] = true; select(i); }));
$("pl-sheet-cancel").addEventListener("click", close);
$("pl-sheet-ok").addEventListener("click", () => {
  close();
  done(names.map((n, i) => n.trim() || DEFAULTS[i]));
});
