"""Partidas y jugadores de la mesa.

La pantalla lleva la lógica del marcador (scoreboard.js) y manda el estado
completo después de cada toque; aquí se guarda para que la partida sobreviva
a un reinicio del equipo o del navegador, y para que cada jugada quede
ligada a su partida (número, jugadores, marcador).
"""

from __future__ import annotations

import json
import sqlite3

from .config import Config
from .db import now_ms
from .ids import uuid7

DEFAULT_NAMES = ("Jugador 1", "Jugador 2")
NAME_MAX = 24
MAX_SCORE = 999


class GameError(Exception):
    pass


class StaleGame(GameError):
    """La pantalla manda el marcador de una partida que ya terminó."""


def clean_name(name, default: str) -> str:
    name = " ".join(str(name or "").split())[:NAME_MAX]
    return name or default


def _empty_state(p1: str, p2: str) -> dict:
    return {
        "players": [{"name": n, "score": 0, "innings": 0, "bestRun": 0} for n in (p1, p2)],
        "turn": None,
        "run": 0,
        "startedAt": None,
        "shotStartedAt": None,
    }


def public(row: sqlite3.Row) -> dict:
    state = json.loads(row["state"])
    state.update(id=row["id"], number=row["number"])
    for p, name in zip(state["players"], (row["player1"], row["player2"])):
        p["name"] = name
    return state


def get(conn: sqlite3.Connection, game_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()


def current(conn: sqlite3.Connection, cfg: Config, camera_id: str) -> sqlite3.Row:
    """La partida en curso de la mesa; si no hay, empieza la primera."""
    row = conn.execute(
        "SELECT * FROM games WHERE camera_id = ? AND ended_at IS NULL ORDER BY number DESC LIMIT 1",
        (camera_id,),
    ).fetchone()
    return row or new(conn, cfg, camera_id, *DEFAULT_NAMES)


def new(conn: sqlite3.Connection, cfg: Config, camera_id: str, player1: str, player2: str) -> sqlite3.Row:
    """Termina la partida en curso y empieza la siguiente con esos jugadores."""
    p1 = clean_name(player1, DEFAULT_NAMES[0])
    p2 = clean_name(player2, DEFAULT_NAMES[1])
    table = cfg.camera(camera_id).table_number
    now = now_ms()
    game_id = uuid7()
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("UPDATE games SET ended_at = ? WHERE camera_id = ? AND ended_at IS NULL", (now, camera_id))
        number = conn.execute("SELECT COALESCE(MAX(number), 0) + 1 FROM games WHERE table_number = ?",
                              (table,)).fetchone()[0]
        conn.execute(
            """INSERT INTO games (id, camera_id, table_number, number, player1, player2, state,
                   created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (game_id, camera_id, table, number, p1, p2, json.dumps(_empty_state(p1, p2)), now, now),
        )
        _remember(conn, (p1, p2), now)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return get(conn, game_id)


def _remember(conn: sqlite3.Connection, names, now: int) -> None:
    for name in names:
        if name in DEFAULT_NAMES:
            continue
        conn.execute(
            """INSERT INTO players (name, games, last_played_at) VALUES (?, 1, ?)
               ON CONFLICT (name) DO UPDATE SET games = games + 1, last_played_at = excluded.last_played_at""",
            (name, now),
        )


def rename(conn: sqlite3.Connection, game_id: str, player1: str, player2: str) -> sqlite3.Row:
    row = _open(conn, game_id)
    p1 = clean_name(player1, row["player1"])
    p2 = clean_name(player2, row["player2"])
    now = now_ms()
    conn.execute("UPDATE games SET player1 = ?, player2 = ?, updated_at = ? WHERE id = ?", (p1, p2, now, game_id))
    _remember(conn, [n for n, old in ((p1, row["player1"]), (p2, row["player2"])) if n != old], now)
    return get(conn, game_id)


def _open(conn: sqlite3.Connection, game_id: str) -> sqlite3.Row:
    row = get(conn, str(game_id))
    if row is None:
        raise GameError("Esa partida no existe.")
    if row["ended_at"] is not None:
        raise StaleGame("Esa partida ya terminó.")
    return row


def _int(v, lo: int, hi: int) -> int:
    v = int(v)
    if not lo <= v <= hi:
        raise ValueError(f"valor fuera de rango: {v}")
    return v


def _ms(v) -> int | None:
    return None if v is None else _int(v, 0, 10 ** 14)


def save_state(conn: sqlite3.Connection, game_id: str, state: dict) -> sqlite3.Row:
    """Guarda el marcador que manda la pantalla (validado: nada de texto libre)."""
    row = _open(conn, game_id)
    players = state.get("players")
    if not isinstance(players, list) or len(players) != 2:
        raise ValueError("se esperaban dos jugadores")
    clean = {
        "players": [
            {
                "name": name,
                "score": _int(p.get("score", 0), 0, MAX_SCORE),
                "innings": _int(p.get("innings", 0), 0, MAX_SCORE),
                "bestRun": _int(p.get("bestRun", 0), 0, MAX_SCORE),
            }
            for p, name in zip(players, (row["player1"], row["player2"]))
        ],
        "turn": None if state.get("turn") is None else _int(state["turn"], 0, 1),
        "run": _int(state.get("run", 0), 0, MAX_SCORE),
        "startedAt": _ms(state.get("startedAt")),
        "shotStartedAt": _ms(state.get("shotStartedAt")),
    }
    s1, s2 = (p["score"] for p in clean["players"])
    innings = max(p["innings"] for p in clean["players"])
    conn.execute(
        """UPDATE games SET state = ?, score1 = ?, score2 = ?, innings = ?, started_at = ?, updated_at = ?
           WHERE id = ?""",
        (json.dumps(clean), s1, s2, innings, clean["startedAt"], now_ms(), game_id),
    )
    return get(conn, game_id)


def recent_players(conn: sqlite3.Connection, limit: int = 24) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT name FROM players ORDER BY last_played_at DESC LIMIT ?", (limit,))]
