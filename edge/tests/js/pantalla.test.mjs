// Pruebas de la lógica de la pantalla. Ejecutar con: node --test edge/tests/js/*.test.mjs
import assert from "node:assert/strict";
import test from "node:test";

import { EDGE_LAG, correction } from "../../billar_edge/static/delay.js";
import * as sb from "../../billar_edge/static/scoreboard.js";

test("el retraso se corrige saltando, esperando o ajustando la velocidad", () => {
  // `latency` es la distancia al borde en vivo; la cámara va EDGE_LAG por delante.
  const at = (behindCamera) => behindCamera - EDGE_LAG;
  assert.deepEqual(correction(at(30), 20), { action: "seek", by: 10 });
  assert.deepEqual(correction(at(5), 20), { action: "wait" });
  assert.equal(correction(at(20.5), 20).rate, 1.08);
  assert.equal(correction(at(19.5), 20).rate, 0.92);
  assert.equal(correction(at(20.05), 20, 1.08).rate, 1);
  // Entre 0,1 y 0,3 s de error se mantiene la velocidad actual.
  assert.equal(correction(at(20.2), 20, 1.08).rate, 1.08);
});

test("tocar el panel propio toma el turno y reinicia el reloj para tacar", () => {
  const g = sb.newGame();
  sb.touch(g, 0, 1000);
  assert.equal(g.turn, 0);
  assert.equal(g.startedAt, 1000);
  assert.equal(sb.shotRemaining(g, 31000), 10);
  sb.touch(g, 0, 31000);  // otro toque del mismo jugador: reinicia el reloj, misma entrada
  assert.equal(sb.shotRemaining(g, 31000), sb.SHOT_SECONDS);
  assert.equal(g.players[0].innings, 1);
  sb.touch(g, 1, 40000);  // el rival toca su panel: toma el turno
  assert.equal(g.turn, 1);
  assert.equal(g.players[1].innings, 1);
  assert.equal(sb.shotRemaining(g, 200000), 0);
});

test("carambolas, serie mayor, entradas y promedio", () => {
  const g = sb.newGame();
  sb.add(g, 0, 1, 0);
  sb.add(g, 0, 3, 0);      // serie de 4
  sb.add(g, 1, 2, 0);      // el rival marca: toma el turno
  sb.add(g, 0, 1, 0);      // nueva entrada del jugador 1, serie de 1
  sb.subtract(g, 0, 0);
  assert.equal(g.players[0].score, 4);
  assert.equal(g.players[0].bestRun, 4);
  assert.equal(g.players[0].innings, 2);
  assert.equal(sb.innings(g), 2);
  assert.equal(sb.average(g.players[0]), 2);
  assert.equal(g.players[1].bestRun, 2);
  sb.subtract(g, 1, 0); sb.subtract(g, 1, 0); sb.subtract(g, 1, 0);
  assert.equal(g.players[1].score, 0);  // nunca negativo
});

test("cada partida nueva lleva su número, que queda con las jugadas guardadas", () => {
  assert.equal(sb.newGame().number, 1);
  assert.equal(sb.newGame(4).number, 4);
});
