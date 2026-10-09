// ADMINISTRACIÓN (Fase 8): todo lo delicado pide el PIN de un usuario.
// Los jugadores siguen usando la mesa sin PIN (marcador, repetición, guardar,
// compartir, retraso). Aquí: estado, ajustes, quitar protección, usuarios,
// registro de auditoría y alertas por Telegram.
import * as keyboard from "./keyboard.js";
import qrcode from "./vendor/qrcode.mjs";

const $ = (id) => document.getElementById(id);
const view = $("admin");
const IDLE_MS = 5 * 60000;
const ROLE_INFO = {
  administrador: "Todo: ajustes, usuarios, quitar protección y alertas.",
  encargado: "Estado, ajustes y registro.",
  operador: "Solo ver el estado.",
};
const ROLE_NAME = { administrador: "Administrador", encargado: "Encargado", operador: "Operador" };

let token = null;
let user = null;
let data = null;
let tab = "estado";
let lastTouchAt = 0;
let notify = () => {};
let onChanged = () => {};
let onExit = () => {};

const time = (ms) => new Date(ms).toLocaleTimeString("es-CO", { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const day = (ms) => new Date(ms).toLocaleDateString("es-CO", { day: "2-digit", month: "2-digit" });
const longDay = (ms) => new Date(ms).toLocaleDateString("es-CO", { weekday: "long", day: "numeric", month: "long" });
const size = (bytes) => (bytes < 1024 ** 3
  ? `${Math.round(bytes / 1024 ** 2).toLocaleString("es-CO")} MB`
  : `${(bytes / 1024 ** 3).toLocaleString("es-CO", { maximumFractionDigits: 1 })} GB`);

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function button(text, cls, onClick) {
  const b = el("button", cls, text);
  b.addEventListener("click", onClick);
  return b;
}

export function setup(opts) {
  notify = opts.toast;
  onChanged = opts.onChanged;
  onExit = opts.onExit || onExit;
}

export function isOpen() {
  return !view.hidden || !$("pin").hidden;
}

async function api(method, path, body) {
  const res = await fetch(`/api/admin/${path}`, {
    method,
    headers: { "Content-Type": "application/json", ...(token ? { "X-Sesion": token } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const out = await res.json().catch(() => ({}));
  if (res.status === 401 && out.needs_pin && token) {
    // La sesión venció en el servidor: se vuelve a pedir el PIN.
    shut();
    notify(out.error);
  }
  if (!res.ok) throw new Error(out.error || "No se pudo hacer.");
  return out;
}

// ---------- entrar ----------

export async function open() {
  lastTouchAt = Date.now();
  let start;
  try {
    start = await api("GET", "inicio");
  } catch (e) {
    notify(`No se pudo abrir la administración. ${e.message}`);
    return;
  }
  if (start.has_users) {
    askPin({
      title: "Administración",
      hint: "Pon tu PIN.",
      onPin: async (pin) => enter(await api("POST", "login", { pin })),
    });
  } else {
    askPin({
      title: "Primer uso: crea el PIN del administrador",
      hint: "De 4 a 8 números. Con este PIN se entra a ajustes, usuarios y jugadas protegidas.",
      confirm: true,
      onPin: async (pin) => enter(await api("POST", "primer-uso", { name: "Administrador", pin })),
    });
  }
}

function enter(session) {
  token = session.token;
  user = session.user;
  tab = "estado";
  view.hidden = false;
  lastTouchAt = Date.now();
  $("ad-user").textContent = `${user.name} · ${ROLE_NAME[user.role]}`;
  document.querySelectorAll("#ad-tabs [data-tab]").forEach((b) => {
    b.hidden = Boolean(b.dataset.perm) && !user.permissions.includes(b.dataset.perm);
  });
  show("estado");
}

function shut() {
  token = null;
  user = null;
  view.hidden = true;
  for (const id of ["pin", "ad-text", "ad-dialog"]) $(id).hidden = true;
  onExit();
}

export function exit() {
  if (token) api("POST", "logout").catch(() => {});
  shut();
}

// ---------- teclado de PIN ----------

let pin = "";
let pinOpts = null;
let firstPin = null;

function askPin(opts) {
  pinOpts = opts;
  pin = "";
  firstPin = null;
  $("pin-title").textContent = opts.title;
  $("pin-hint").textContent = opts.hint;
  $("pin-error").textContent = "";
  renderDots();
  $("pin").hidden = false;
}

function renderDots() {
  const n = Math.max(4, pin.length);
  $("pin-dots").replaceChildren(...Array.from({ length: n }, (_, i) => el("span", i < pin.length ? "on" : "")));
}

async function pinEnter() {
  if (pin.length < 4) {
    $("pin-error").textContent = "El PIN tiene al menos 4 números.";
    return;
  }
  if (pinOpts.confirm && firstPin === null) {
    firstPin = pin;
    pin = "";
    $("pin-hint").textContent = "Repite el mismo PIN para confirmarlo.";
    $("pin-error").textContent = "";
    renderDots();
    return;
  }
  if (pinOpts.confirm && pin !== firstPin) {
    firstPin = null;
    pin = "";
    $("pin-hint").textContent = pinOpts.hint;
    $("pin-error").textContent = "Los dos PIN no coinciden. Empieza otra vez.";
    renderDots();
    return;
  }
  const got = pin;
  pin = "";
  renderDots();
  try {
    await pinOpts.onPin(got);
    $("pin").hidden = true;
  } catch (e) {
    firstPin = null;
    if (pinOpts.confirm) $("pin-hint").textContent = pinOpts.hint;
    $("pin-error").textContent = e.message;
  }
}

function buildPinPad() {
  const pad = $("pin-pad");
  for (const k of ["1", "2", "3", "4", "5", "6", "7", "8", "9", "back", "0", "ok"]) {
    const label = k === "back" ? "⌫" : k === "ok" ? "Entrar" : k;
    pad.append(button(label, k === "ok" ? "go" : k === "back" ? "muted" : "", () => {
      $("pin-error").textContent = "";
      if (k === "back") pin = pin.slice(0, -1);
      else if (k === "ok") { pinEnter(); return; }
      else if (pin.length < 8) pin += k;
      renderDots();
    }));
  }
}

// ---------- texto con teclado en pantalla ----------

let text = "";
let textFresh = true;
let textDone = null;

function askText(title, current, max, onDone) {
  text = current;
  textFresh = true;
  textDone = { onDone, max };
  $("ad-text-title").textContent = title;
  renderText();
  $("ad-text").hidden = false;
}

function renderText() {
  $("ad-text-value").textContent = text;
  $("ad-text-value").classList.toggle("fresh", textFresh);
}

// ---------- diálogo genérico ----------

function dialog({ title, hint, body, ok, danger, onOk }) {
  $("ad-dialog-title").textContent = title;
  $("ad-dialog-hint").textContent = hint || "";
  $("ad-dialog-hint").hidden = !hint;
  $("ad-dialog-body").replaceChildren(...body);
  const okBtn = $("ad-dialog-ok");
  okBtn.textContent = ok;
  okBtn.className = `wide ${danger ? "danger" : "go"}`;
  okBtn.disabled = false;
  okBtn.onclick = async () => {
    okBtn.disabled = true;
    try {
      if (await onOk() !== false) $("ad-dialog").hidden = true;
    } catch (e) {
      notify(e.message);
    } finally {
      okBtn.disabled = false;
    }
  };
  $("ad-dialog").hidden = false;
}

// ---------- pestañas ----------

async function show(name) {
  tab = name;
  document.querySelectorAll("#ad-tabs [data-tab]").forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
  try {
    data = await api("GET", "estado");
  } catch (e) {
    if (token) $("ad-body").replaceChildren(el("p", "hs-empty", `No se pudo leer el estado. ${e.message}`));
    return;
  }
  $("ad-sub").textContent = data.settings.establishment_name;
  const render = { estado: renderEstado, ajustes: renderAjustes, jugadas: renderJugadas,
    usuarios: renderUsuarios, registro: renderRegistro, alertas: renderAlertas }[name];
  $("ad-body").replaceChildren(...await render());
}

function card(title, value, note, cls = "") {
  const c = el("div", `ad-card ${cls}`);
  c.append(el("span", "caption", title), el("span", "ad-value", value));
  if (note) c.append(el("span", "ad-card-note", note));
  return c;
}

function renderEstado() {
  const s = data.status;
  const cards = el("div", "ad-cards");
  cards.append(card("Grabación", s ? s.label : "Sin monitor",
    s && s.ok ? "Graba día y noche sin parar." : s ? "Revisa la cámara y el equipo." : "El servicio que vigila la grabación no responde.", s && s.ok ? "ok" : "bad"));
  if (data.disk) {
    const used = 1 - data.disk.free / data.disk.total;
    const c = card("Disco de video", `${size(data.disk.free)} libres`, `de ${size(data.disk.total)}`, used > 0.9 ? "bad" : "");
    const bar = el("div", "ad-bar");
    const fill = el("i");
    fill.style.width = `${Math.round(used * 100)}%`;
    bar.append(fill);
    c.append(bar);
    cards.append(c);
  } else {
    cards.append(card("Disco de video", "No disponible", "No se encontró la carpeta de video.", "bad"));
  }
  cards.append(card("Historial", data.oldest_recording_ms ? `Desde el ${day(data.oldest_recording_ms)}` : "Vacío",
    `Se guarda ${data.retention_days} días y se renueva solo cada día.`));
  cards.append(card("Jugadas protegidas", String(data.protected_plays.count),
    `${size(data.protected_plays.bytes)} · se borran solas a los ${data.protected_days} días.`));
  const remote = el("div", "ad-remote");
  const port = data.remote.panel_url.split(":").pop();
  remote.append(
    el("span", "", "Desde un computador o celular en el WiFi del billar:"), el("b", "", data.remote.panel_url),
    el("span", "", "Desde fuera del billar (VPN):"),
    el("b", "", data.remote.vpn_ip ? `https://${data.remote.vpn_ip}:${port}` : "Sin VPN instalada"),
  );
  const events = el("div", "ad-list");
  events.append(el("h3", "ad-h", "Últimos avisos del sistema"));
  if (!data.events.length) events.append(el("p", "hs-empty", "Sin avisos."));
  for (const ev of data.events) {
    const row = el("div", `ad-row ev-${ev.level}`);
    row.append(el("span", "ad-when", `${day(ev.ts)} ${time(ev.ts)}`), el("i", "ad-dot"), el("span", "ad-text", ev.message));
    events.append(row);
  }
  return [cards, remote, events];
}

function choiceRow(title, note, field, choices, unit) {
  const row = el("div", "ad-setting");
  const label = el("div", "ad-setting-label");
  label.append(el("span", "ad-setting-title", title), el("span", "ad-card-note", note));
  const opts = el("div", "ad-choices");
  for (const v of choices) {
    opts.append(button(`${v} ${unit}`, data.settings[field] === v ? "on" : "", () => save({ [field]: v })));
  }
  row.append(label, opts);
  return row;
}

async function save(changes) {
  try {
    const out = await api("PUT", "ajustes", changes);
    data.settings = out.settings;
    notify("Guardado.");
    onChanged();
    if (tab === "ajustes") $("ad-body").replaceChildren(...renderAjustes());
    $("ad-sub").textContent = data.settings.establishment_name;
  } catch (e) {
    notify(`No se guardó. ${e.message}`);
  }
}

function renderAjustes() {
  const st = data.settings;
  const name = el("div", "ad-setting");
  const label = el("div", "ad-setting-label");
  label.append(el("span", "ad-setting-title", "Nombre del billar"), el("span", "ad-card-note", "Sale en la pantalla, el modo reposo y los videos compartidos."));
  const val = el("div", "ad-choices");
  val.append(el("span", "ad-name", st.establishment_name),
    button("Cambiar", "", () => askText("Nombre del billar", st.establishment_name, 40, (v) => save({ establishment_name: v }))));
  name.append(label, val);
  return [
    name,
    choiceRow("Modo reposo", "Minutos sin tocar la pantalla antes de mostrar la publicidad.", "idle_minutes", st.choices.idle_minutes, "min"),
    choiceRow("Tiempo para tacar", "Cuenta regresiva de cada tiro.", "shot_seconds", st.choices.shot_seconds, "s"),
    choiceRow("Retraso de la pantalla", "Los jugadores también lo cambian desde el menú.", "delay_seconds", st.choices.delay_seconds, "s"),
  ];
}

async function renderJugadas() {
  const body = await api("GET", "jugadas");
  const list = el("div", "ad-list");
  list.append(el("p", "hint ad-intro", "Quitar la protección borra la copia guardada. Si la grabación todavía existe (7 días) la jugada sigue como repetición normal. Queda en el registro con tu nombre y el motivo."));
  if (!body.plays || !body.plays.length) list.append(el("p", "hs-empty", "No hay jugadas protegidas."));
  for (const p of body.plays || []) {
    const row = el("div", "ad-row ad-play");
    const who = p.player1 && p.player2 ? `${p.player1} ${p.score1 ?? ""} – ${p.score2 ?? ""} ${p.player2}` : `Mesa ${p.table_number}`;
    row.append(
      el("span", "ad-when", `${day(p.moment_ms)} ${time(p.moment_ms)}`),
      el("span", "ad-text", who),
      el("span", "ad-card-note", `${p.bytes ? size(p.bytes) : ""}${p.expires_ms ? ` · se borra el ${day(p.expires_ms)}` : ""}`),
      button("Quitar protección", "ad-danger", () => unprotect(p)),
    );
    list.append(row);
  }
  return [list];
}

function unprotect(p) {
  let reason = null;
  const reasons = el("div", "ad-choices ad-reasons");
  for (const r of data.unprotect_reasons) {
    reasons.append(button(r, "", (e) => {
      reason = r;
      reasons.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b === e.currentTarget));
    }));
  }
  dialog({
    title: "Quitar la protección",
    hint: `Jugada del ${longDay(p.moment_ms)} a las ${time(p.moment_ms)}. Elige el motivo:`,
    body: [reasons],
    ok: "Quitar protección",
    danger: true,
    onOk: async () => {
      if (!reason) { notify("Elige el motivo."); return false; }
      await api("POST", `jugadas/${p.id}/desproteger`, { reason });
      notify("Se quitó la protección.");
      show("jugadas");
      return true;
    },
  });
}

function renderUsuarios() {
  const list = el("div", "ad-list");
  const top = el("div", "ad-toprow");
  top.append(el("p", "hint", "Cada usuario entra con su propio PIN. Los jugadores no necesitan usuario."),
    button("Nuevo usuario", "ad-go", () => editUser(null)));
  list.append(top);
  for (const u of data.users || []) {
    const row = el("div", `ad-row ad-user-row${u.active ? "" : " off"}`);
    row.append(el("span", "ad-text ad-strong", u.name), el("span", "ad-text", ROLE_NAME[u.role]),
      el("span", "ad-card-note", u.active ? "Activo" : "Desactivado"), button("Editar", "", () => editUser(u)));
    list.append(row);
  }
  return [list];
}

function editUser(u) {
  const draft = { name: u ? u.name : "", role: u ? u.role : "operador", pin: null, active: u ? u.active : true };
  const body = el("div", "ad-form");
  const render = () => {
    const nameRow = el("div", "ad-form-row");
    nameRow.append(el("span", "caption", "Nombre"), el("span", "ad-name", draft.name || "Sin nombre"),
      button("Escribir", "", () => askText("Nombre del usuario", draft.name, 30, (v) => { draft.name = v; render(); })));
    const roles = el("div", "ad-roles");
    for (const r of data.roles) {
      const b = button("", draft.role === r ? "on" : "", () => { draft.role = r; render(); });
      b.append(el("b", "", ROLE_NAME[r]), el("span", "", ROLE_INFO[r]));
      roles.append(b);
    }
    const pinRow = el("div", "ad-form-row");
    pinRow.append(el("span", "caption", "PIN"),
      el("span", "ad-name", draft.pin ? "Nuevo PIN listo" : u ? "Sin cambiar" : "Falta el PIN"),
      button(u ? "Cambiar PIN" : "Poner PIN", "", () => askPin({
        title: `PIN de ${draft.name || "este usuario"}`,
        hint: "De 4 a 8 números, distinto al de los demás usuarios.",
        confirm: true,
        onPin: async (p) => { draft.pin = p; render(); },
      })));
    const parts = [nameRow, roles, pinRow];
    if (u) {
      const act = el("div", "ad-form-row");
      act.append(el("span", "caption", "Estado"), el("span", "ad-name", draft.active ? "Activo" : "Desactivado"),
        button(draft.active ? "Desactivar" : "Activar", "", () => { draft.active = !draft.active; render(); }));
      parts.push(act);
    }
    body.replaceChildren(...parts);
  };
  render();
  dialog({
    title: u ? `Editar a ${u.name}` : "Nuevo usuario",
    body: [body],
    ok: "Guardar",
    onOk: async () => {
      const send = { name: draft.name, role: draft.role, active: draft.active };
      if (draft.pin) send.pin = draft.pin;
      if (u) await api("PUT", `usuarios/${u.id}`, send);
      else await api("POST", "usuarios", send);
      notify("Usuario guardado.");
      show("usuarios");
      return true;
    },
  });
}

function renderRegistro() {
  const list = el("div", "ad-list");
  list.append(el("p", "hint ad-intro", "Todo lo que se hace en ADMINISTRACIÓN queda aquí con la hora y el nombre de quien lo hizo. No se puede borrar desde la pantalla."));
  if (!data.audit || !data.audit.length) list.append(el("p", "hs-empty", "Todavía no hay registros."));
  for (const a of data.audit || []) {
    const row = el("div", "ad-row");
    row.append(el("span", "ad-when", `${day(a.ts)} ${time(a.ts)}`), el("span", "ad-text ad-strong", a.user_name || "Sistema"),
      el("span", "ad-text ad-wide", a.detail));
    list.append(row);
  }
  return [list];
}

function renderAlertas() {
  const box = el("div", "ad-list");
  const tg = data.telegram;
  box.append(card("Alertas por Telegram", tg.alerts_linked ? "Vinculadas" : "Sin vincular",
    "Llegan al celular del dueño: cámara desconectada, sin señal, disco lleno, apagones, PIN bloqueado y cuando todo vuelve a la normalidad. Gratis.",
    tg.alerts_linked ? "ok" : ""));
  if (!tg.bot) {
    box.append(el("p", "hint ad-intro", "Primero hay que crear el bot de Telegram del billar (gratis, con @BotFather) y ponerlo en la configuración: [share] telegram_token y telegram_bot."));
    return [box];
  }
  box.append(button(tg.alerts_linked ? "Vincular otro celular" : "Vincular mi Telegram", "ad-go ad-link", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      const out = await api("POST", "alertas/vincular");
      const qr = qrcode(0, "M");
      qr.addData(out.url);
      qr.make();
      const wrap = el("div", "ad-qr-wrap");
      const code = el("div", "sh-qr");
      code.innerHTML = qr.createSvgTag({ cellSize: 4, margin: 2, scalable: true });
      const steps = el("ol");
      for (const s of ["Escanea el código con el celular que recibirá las alertas.", "Se abre Telegram: toca Iniciar.", "El bot responde «Listo». El código sirve 15 minutos."]) steps.append(el("li", "", s));
      wrap.append(code, steps);
      btn.replaceWith(wrap);
    } catch (err) {
      notify(err.message);
      btn.disabled = false;
    }
  }));
  return [box];
}

// ---------- arranque ----------

buildPinPad();
keyboard.build($("ad-keyboard"), (ch) => {
  const base = textFresh ? "" : text;
  textFresh = false;
  if (base.length < textDone.max) text = keyboard.append(base, ch);
  renderText();
}, (action) => {
  if (action === "space") { if (!textFresh && text && !text.endsWith(" ")) text += " "; }
  else if (action === "back") text = textFresh ? "" : text.slice(0, -1);
  else if (action === "clear") text = "";
  textFresh = false;
  renderText();
});
// En el panel desde un computador también sirve el teclado físico.
document.addEventListener("keydown", (e) => {
  if (!$("pin").hidden) {
    if (/^[0-9]$/.test(e.key) && pin.length < 8) { pin += e.key; renderDots(); }
    else if (e.key === "Backspace") { pin = pin.slice(0, -1); renderDots(); }
    else if (e.key === "Enter") pinEnter();
    else if (e.key === "Escape") $("pin").hidden = true;
    else return;
  } else if (!$("ad-text").hidden) {
    if (e.key === "Enter") $("ad-text-ok").click();
    else if (e.key === "Escape") $("ad-text").hidden = true;
    else if (e.key === "Backspace") { text = textFresh ? "" : text.slice(0, -1); textFresh = false; renderText(); }
    else if (e.key === " ") { if (!textFresh && text && !text.endsWith(" ")) text += " "; textFresh = false; renderText(); }
    else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey) {
      const base = textFresh ? "" : text;
      textFresh = false;
      if (base.length < textDone.max) text = base + e.key;
      renderText();
    } else return;
  } else return;
  e.preventDefault();
  lastTouchAt = Date.now();
});
$("ad-text-cancel").addEventListener("click", () => { $("ad-text").hidden = true; });
$("ad-text-ok").addEventListener("click", () => {
  const v = text.trim();
  if (!v) { notify("Escribe el nombre."); return; }
  $("ad-text").hidden = true;
  textDone.onDone(v);
});
$("ad-dialog-cancel").addEventListener("click", () => { $("ad-dialog").hidden = true; });
$("pin-cancel").addEventListener("click", () => { $("pin").hidden = true; });
$("ad-exit").addEventListener("click", exit);
document.querySelectorAll("#ad-tabs [data-tab]").forEach((b) => b.addEventListener("click", () => show(b.dataset.tab)));
for (const id of ["admin", "pin", "ad-text", "ad-dialog"]) {
  $(id).addEventListener("pointerdown", () => { lastTouchAt = Date.now(); }, true);
}
setInterval(() => {
  if (isOpen() && Date.now() - lastTouchAt > IDLE_MS) exit();
  else if (!view.hidden && tab === "estado" && $("ad-dialog").hidden && Date.now() - lastTouchAt > 10000) {
    // El estado se actualiza solo mientras se mira.
    if (Math.floor(Date.now() / 1000) % 10 === 0) show("estado");
  }
}, 1000);
