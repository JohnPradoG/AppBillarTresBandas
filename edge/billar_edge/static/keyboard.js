// Teclado en pantalla (la pantalla táctil no tiene teclado físico). Lo usan
// los nombres de los jugadores y la administración.

const ROWS = ["1234567890", "QWERTYUIOP", "ASDFGHJKLÑ", "ZXCVBNMÁÉÍÓÚ"];

// onChar(letra en mayúscula) y onAction("clear" | "space" | "back").
export function build(box, onChar, onAction) {
  ROWS.forEach((row) => {
    const r = document.createElement("div");
    r.className = "kb-row";
    for (const ch of row) {
      const b = document.createElement("button");
      b.textContent = ch;
      b.addEventListener("click", () => onChar(ch));
      r.append(b);
    }
    box.append(r);
  });
  const last = document.createElement("div");
  last.className = "kb-row";
  for (const [action, label, cls] of [["clear", "Borrar todo", "kb-wide"], ["space", "Espacio", "kb-space"], ["back", "⌫ Borrar", "kb-wide"]]) {
    const b = document.createElement("button");
    b.textContent = label;
    b.className = cls;
    b.addEventListener("click", () => onAction(action));
    last.append(b);
  }
  box.append(last);
}

// Mayúscula al empezar cada palabra, minúscula en el resto.
export function append(text, ch) {
  return text + (text === "" || text.endsWith(" ") ? ch : ch.toLowerCase());
}
