// Mantiene la imagen exactamente N segundos detrás de la cámara.
// `latency` es cuánto va la imagen por detrás del borde en vivo (lo mide hls.js
// con el reloj del equipo). Se corrige de tres formas:
// - muy atrasada: saltar hacia delante;
// - adelantada (al arrancar o al subir el retraso): pausar hasta llegar;
// - pequeñas desviaciones: acelerar o frenar un poco, sin que se note.

export const SEEK_THRESHOLD = 3;   // s de error para saltar en vez de acelerar
export const PAUSE_THRESHOLD = 1;  // s adelantada para congelar la imagen
export const RATE_THRESHOLD = 0.3;
export const SETTLED = 0.1;
// El borde en vivo que mide hls.js va ~1 trozo (1 s) por detrás de la cámara:
// el trozo que se está escribiendo todavía no se puede pedir.
export const EDGE_LAG = 1;

export function correction(latency, target, currentRate = 1) {
  const err = latency + EDGE_LAG - target;  // > 0: la imagen va más atrasada de lo debido
  if (err > SEEK_THRESHOLD) return { action: "seek", by: err };
  if (err < -PAUSE_THRESHOLD) return { action: "wait" };
  if (err > RATE_THRESHOLD) return { action: "rate", rate: 1.08 };
  if (err < -RATE_THRESHOLD) return { action: "rate", rate: 0.92 };
  if (Math.abs(err) <= SETTLED) return { action: "rate", rate: 1 };
  return { action: "rate", rate: currentRate };
}
