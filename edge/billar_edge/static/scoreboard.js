// Marcador de la partida. Reglas pedidas por John:
// - No hay botón "cambiar turno": el jugador que toca SU panel toma el turno.
// - Cada toque en un panel reinicia el contador para tacar.
// Lógica pura (sin pantalla) para poder probarla; app.js la dibuja.

export const SHOT_SECONDS = 40;

// number: número de la partida en esta mesa; queda con cada jugada guardada.
export function newGame(number = 1) {
  return {
    number,
    players: [
      { name: "Jugador 1", score: 0, innings: 0, bestRun: 0 },
      { name: "Jugador 2", score: 0, innings: 0, bestRun: 0 },
    ],
    turn: null,        // índice del jugador en turno, o null antes del primer toque
    run: 0,            // carambolas de la serie actual
    startedAt: null,   // ms; la partida empieza con el primer toque
    shotStartedAt: null,
  };
}

// Tocar un panel: si es del rival, toma el turno (nueva entrada para él).
export function touch(game, i, now) {
  if (game.turn !== i) {
    game.turn = i;
    game.run = 0;
    game.players[i].innings += 1;
  }
  if (game.startedAt === null) game.startedAt = now;
  game.shotStartedAt = now;
  return game;
}

export function add(game, i, n, now) {
  touch(game, i, now);
  const p = game.players[i];
  p.score += n;
  game.run += n;
  p.bestRun = Math.max(p.bestRun, game.run);
  return game;
}

export function subtract(game, i, now) {
  touch(game, i, now);
  const p = game.players[i];
  if (p.score > 0) {
    p.score -= 1;
    game.run = Math.max(0, game.run - 1);
  }
  return game;
}

// Entradas de la partida: las del jugador con más entradas (el que abrió).
export function innings(game) {
  return Math.max(game.players[0].innings, game.players[1].innings);
}

export function average(player) {
  return player.innings ? player.score / player.innings : 0;
}

export function shotRemaining(game, now) {
  if (game.shotStartedAt === null) return SHOT_SECONDS;
  return Math.max(0, SHOT_SECONDS - Math.floor((now - game.shotStartedAt) / 1000));
}

export function elapsedSeconds(game, now) {
  return game.startedAt === null ? 0 : Math.floor((now - game.startedAt) / 1000);
}
