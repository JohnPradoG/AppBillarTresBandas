// Bolas de tres bandas rodando por la pantalla en modo reposo, con su estela.
// Sin rozamiento: rebotan en las bandas para siempre. 30 cuadros por segundo
// bastan y cuidan el procesador del mini PC.

const COLORS = ["#F4F4EF", "#F2C53D", "#D23A3A"];
const FRAME_MS = 1000 / 30;
let raf = 0;
let last = 0;
let balls = [];

export function start(canvas) {
  stop();
  const ctx = canvas.getContext("2d");
  const fit = () => {
    canvas.width = canvas.clientWidth * devicePixelRatio;
    canvas.height = canvas.clientHeight * devicePixelRatio;
  };
  fit();
  const r = Math.min(canvas.width, canvas.height) * 0.032;
  const speed = Math.min(canvas.width, canvas.height) * 0.0045;
  balls = COLORS.map((color, i) => {
    const a = Math.random() * Math.PI * 2;
    return {
      color,
      x: canvas.width * (0.25 + i * 0.25),
      y: canvas.height * (0.3 + Math.random() * 0.4),
      vx: Math.cos(a) * speed * (0.8 + i * 0.15),
      vy: Math.sin(a) * speed * (0.8 + i * 0.15),
      trail: [],
    };
  });
  const tick = (t) => {
    raf = requestAnimationFrame(tick);
    if (t - last < FRAME_MS) return;
    last = t;
    if (canvas.width !== canvas.clientWidth * devicePixelRatio) fit();
    const w = canvas.width;
    const h = canvas.height;
    const m = r * 1.6;  // banda
    ctx.clearRect(0, 0, w, h);
    ctx.strokeStyle = "rgba(240,213,122,0.18)";
    ctx.lineWidth = Math.max(2, r * 0.12);
    ctx.strokeRect(m * 0.5, m * 0.5, w - m, h - m);
    for (const b of balls) {
      b.x += b.vx; b.y += b.vy;
      if (b.x < m + r) { b.x = m + r; b.vx = Math.abs(b.vx); }
      if (b.x > w - m - r) { b.x = w - m - r; b.vx = -Math.abs(b.vx); }
      if (b.y < m + r) { b.y = m + r; b.vy = Math.abs(b.vy); }
      if (b.y > h - m - r) { b.y = h - m - r; b.vy = -Math.abs(b.vy); }
      b.trail.push([b.x, b.y]);
      if (b.trail.length > 26) b.trail.shift();
      b.trail.forEach(([x, y], i) => {
        ctx.globalAlpha = (i / b.trail.length) * 0.22;
        ctx.fillStyle = b.color;
        ctx.beginPath();
        ctx.arc(x, y, r * (0.4 + 0.6 * (i / b.trail.length)), 0, Math.PI * 2);
        ctx.fill();
      });
      ctx.globalAlpha = 1;
      const g = ctx.createRadialGradient(b.x - r * 0.35, b.y - r * 0.4, r * 0.1, b.x, b.y, r);
      g.addColorStop(0, "#FFFFFF");
      g.addColorStop(0.25, b.color);
      g.addColorStop(1, "rgba(0,0,0,0.55)");
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(b.x, b.y, r, 0, Math.PI * 2);
      ctx.fill();
    }
  };
  raf = requestAnimationFrame(tick);
}

export function stop() {
  cancelAnimationFrame(raf);
  raf = 0;
}
