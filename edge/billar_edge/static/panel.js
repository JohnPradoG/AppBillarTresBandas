// Panel de administración desde un computador o celular (por la red del
// billar o la VPN). Usa la misma pantalla de ADMINISTRACIÓN que la mesa.
import * as admin from "./admin.js";

const $ = (id) => document.getElementById(id);
let toastTimer = 0;

function toast(text) {
  const t = $("toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 3500);
}

admin.setup({
  toast,
  onChanged: () => {},
  onExit: () => { $("pn-msg").textContent = "Saliste del panel. Para volver, entra con tu PIN."; },
});
$("pn-enter").addEventListener("click", () => admin.open());
admin.open();
