// COMPARTIR: prepara el video con la marca de agua y muestra los códigos QR.
// - WhatsApp: el celular en el WiFi del billar abre el enlace, descarga el
//   video y lo comparte desde la galería.
// - Telegram (si el billar configuró su bot): el bot le manda el video.
import qrcode from "./vendor/qrcode.mjs";

const $ = (id) => document.getElementById(id);
const sheet = $("share");
const AUTO_CLOSE_MS = 180000;

let request = 0;
let openedAt = 0;

export function isOpen() {
  return !sheet.hidden;
}

function qrSvg(text) {
  const qr = qrcode(0, "M");
  qr.addData(text);
  qr.make();
  return qr.createSvgTag({ cellSize: 4, margin: 2, scalable: true });
}

// body: { play_id, clip } o, desde el historial, { moment_ms, start_ms, end_ms, clip }
export async function open(body, subtitle) {
  const mine = ++request;
  openedAt = Date.now();
  sheet.hidden = false;
  $("sh-sub").textContent = subtitle || "";
  $("sh-wait").hidden = false;
  $("sh-wait-text").textContent = "Preparando el video con la marca de agua…";
  $("sh-ready").hidden = true;
  let data;
  try {
    const res = await fetch("/api/share", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    data = await res.json();
    if (!res.ok) throw new Error(data.error || "");
  } catch (e) {
    if (mine === request) $("sh-wait-text").textContent = `No se pudo preparar el video. ${e.message}`;
    return;
  }
  if (mine !== request || sheet.hidden) return;
  $("sh-wait").hidden = true;
  $("sh-ready").hidden = false;
  $("sh-qr-web").innerHTML = qrSvg(data.web);
  $("sh-url").textContent = data.web.replace(/^http:\/\//, "");
  $("sh-card-tg").hidden = !data.telegram;
  if (data.telegram) $("sh-qr-tg").innerHTML = qrSvg(data.telegram);
  const mb = (data.bytes / 1024 ** 2).toLocaleString("es-CO", { maximumFractionDigits: 1 });
  const until = new Date(data.expires_at).toLocaleString("es-CO", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  $("sh-foot").textContent = `Video de ${mb} MB con la marca de agua de Vano Systems. El enlace funciona hasta el ${until}.`;
}

export function close() {
  request++;
  sheet.hidden = true;
}

$("sh-close").addEventListener("click", close);
sheet.addEventListener("pointerdown", () => { openedAt = Date.now(); }, true);
setInterval(() => { if (isOpen() && Date.now() - openedAt > AUTO_CLOSE_MS) close(); }, 1000);
